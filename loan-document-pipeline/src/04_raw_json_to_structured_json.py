#!/usr/bin/env python3
"""
raw_json_to_structured_json.py

Parsing hasil JSON dari pdf_to_raw_json.py (yang dijalankan dengan --with-words,
ini sekarang default) menjadi record terstruktur.

Kenapa versi ini beda dari versi sebelumnya:
Layout invoice punya 2 kolom sejajar ("Billed To" di kiri, "Loan Information"
di kanan). pdfplumber.extract_text() membaca per baris horizontal, jadi field
dari 2 kolom itu ke-interleave (selang-seling) dalam satu baris teks -> regex
lama gagal menangkap nama, alamat, kota, dsb.

Versi ini pakai field "words" (posisi x/y tiap kata) untuk mendeteksi batas
kolom secara otomatis (dari posisi header "Billed To" vs "Loan Information"),
lalu merekonstruksi teks kolom kiri dan kolom kanan secara terpisah sebelum
di-regex. Bagian lain (Reference No, Payment Detail, Summary) full-width jadi
tetap diparse dari teks penuh seperti sebelumnya.

Output JSON sekarang di-nest per customer/loan: field customer & loan (nama,
alamat, loan_id, dst) ada sekali di level luar, lalu tiap pembayaran/halaman
masuk ke array "installments". Grouping-nya berdasarkan loan_id (satu loan_id
= satu customer/loan yang sama, meski datang dari banyak halaman/file PDF
yang berbeda). Output CSV tetap flat seperti sebelumnya (CSV tidak cocok
untuk data nested).

Dependencies: tidak butuh library tambahan (hanya stdlib).

Cara pakai:
    # WAJIB generate JSON dengan koordinat kata (ini sudah default di 03_pdf_to_raw_json.py)
    python pdf_to_raw_json.py document.pdf -o document.json

    python raw_json_to_structured_json.py -i document.json -o output.csv
    python raw_json_to_structured_json.py -i document.json -o output.json
    python raw_json_to_structured_json.py -i document.json -o output.csv --json-output output.json
"""

import argparse
import json
import re
import csv
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Util umum
# ---------------------------------------------------------------------------

def money_to_number(value: str):
    """Ubah string uang seperti '$5,096.01' menjadi float/int. None kalau gagal."""
    if value is None:
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        num = float(cleaned)
        return int(num) if num.is_integer() else num
    except ValueError:
        return None


def safe_search(pattern, text, group=1, flags=0):
    """Wrapper re.search agar tidak error kalau pattern tidak ketemu."""
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


# ---------------------------------------------------------------------------
# Rekonstruksi kolom dari data "words" (x0/x1/top per kata)
# ---------------------------------------------------------------------------

def find_word(words, text, top=None, tol=3, x_min=None):
    """Cari kata tertentu (case-insensitive), opsional dibatasi baris (top) dan
    posisi x minimum, supaya bisa mencari kemunculan ke-2 (mis. 'Loan' muncul
    dua kali: di header kolom kanan & di 'Loan ID:')."""
    for w in words:
        if w["text"].strip().lower() != text.lower():
            continue
        if top is not None and abs(w["top"] - top) > tol:
            continue
        if x_min is not None and w["x0"] < x_min:
            continue
        return w
    return None


def group_words_into_rows(words, tol=3):
    """Kelompokkan kata jadi baris berdasarkan koordinat 'top' yang berdekatan."""
    rows_by_top = []
    for w in sorted(words, key=lambda w: w["top"]):
        placed = False
        for row_top, row_words in rows_by_top:
            if abs(row_top - w["top"]) <= tol:
                row_words.append(w)
                placed = True
                break
        if not placed:
            rows_by_top.append((w["top"], [w]))
    return [row_words for _, row_words in rows_by_top]


def split_row_by_gap(row_words, left_anchor_x, right_anchor_x, gap_threshold=20):
    """Pecah satu baris kata jadi beberapa klaster berdasarkan celah horizontal
    (gap) antar kata yang berdekatan. Klaster dengan celah > gap_threshold
    dianggap kolom berbeda. Tiap klaster ditempelkan ke kolom kiri/kanan
    berdasarkan kedekatan posisi awalnya ke left_anchor_x / right_anchor_x.

    Ini lebih tahan banting dibanding satu garis batas x tetap: kalau isi
    kolom kiri kebetulan sangat panjang (mis. nama/alamat panjang) sampai
    hampir menyentuh kolom kanan, garis batas tetap bisa salah potong -
    tapi celah asli antar kata di ujung kolom kiri dan awal kolom kanan
    akan selalu jauh lebih lebar daripada spasi normal antar kata, jadi
    gap-based split ini akan tetap menempatkannya dengan benar."""
    if not row_words:
        return [], []

    row_sorted = sorted(row_words, key=lambda w: w["x0"])
    clusters = [[row_sorted[0]]]
    for prev, curr in zip(row_sorted, row_sorted[1:]):
        gap = curr["x0"] - prev["x1"]
        if gap > gap_threshold:
            clusters.append([curr])
        else:
            clusters[-1].append(curr)

    left_words, right_words = [], []
    for cluster in clusters:
        start_x = cluster[0]["x0"]
        if abs(start_x - left_anchor_x) <= abs(start_x - right_anchor_x):
            left_words.extend(cluster)
        else:
            right_words.extend(cluster)
    return left_words, right_words


def split_billed_to_columns(words):
    """Deteksi baris 'Billed To ... Loan Information' lalu pisahkan jadi
    left_text (info penerima tagihan) dan right_text (info pinjaman),
    berdasarkan klaster gap per baris (lihat split_row_by_gap).
    Return (left_text, right_text). Return ("", "") kalau header tidak ditemukan
    (mis. layout PDF berbeda / tidak ada koordinat kata)."""
    billed = find_word(words, "Billed")
    if not billed:
        return "", ""

    to = find_word(words, "To", top=billed["top"])
    loan_header = find_word(words, "Loan", top=billed["top"], x_min=(to["x1"] if to else billed["x1"]))
    if not to or not loan_header:
        return "", ""

    left_anchor_x = billed["x0"]
    right_anchor_x = loan_header["x0"]

    # Batas bawah blok: baris "Payment Detail" (atau kalau tidak ada, akhir halaman)
    payment_top = None
    for w in words:
        if w["text"].strip().lower() == "payment":
            same_row = [w2 for w2 in words if w2["text"].strip().lower() == "detail" and abs(w2["top"] - w["top"]) <= 3]
            if same_row:
                payment_top = w["top"]
                break

    block_words = [
        w for w in words
        if w["top"] >= billed["top"] - 3 and (payment_top is None or w["top"] < payment_top - 3)
    ]

    left_lines, right_lines = [], []
    for row in group_words_into_rows(block_words):
        left, right = split_row_by_gap(row, left_anchor_x, right_anchor_x)
        if left:
            left_lines.append(" ".join(w["text"] for w in sorted(left, key=lambda w: w["x0"])))
        if right:
            right_lines.append(" ".join(w["text"] for w in sorted(right, key=lambda w: w["x0"])))

    return "\n".join(left_lines), "\n".join(right_lines)


# ---------------------------------------------------------------------------
# Parsing tiap bagian
# ---------------------------------------------------------------------------

def parse_billed_to(left_text: str) -> dict:
    pattern = (
        r"Billed To\s+"
        r"(?P<name>.+?)\s+"
        r"(?P<address>\d+.+)\s+"
        r"(?P<city>[A-Za-z][A-Za-z ]*?),\s*"   # kota boleh lebih dari 1 kata, mis. "Los Angeles"
        r"(?P<state>[A-Za-z ]+?)\s+"
        r"(?P<postal>\d+)\s+"
        r"(?P<email>\S+@\S+)\s+"
        r"(?P<phone>[\d\-]+)"
    )
    m = re.search(pattern, left_text)
    if not m:
        return {k: None for k in
                ["customer_name", "address", "city", "state", "postal_code", "email", "phone_number"]}
    return {
        "customer_name": m.group("name").strip(),
        "address": m.group("address").strip(),
        "city": m.group("city").strip(),
        "state": m.group("state").strip(),
        "postal_code": int(m.group("postal")),
        "email": m.group("email").strip(),
        "phone_number": m.group("phone").strip(),
    }


def parse_loan_info(right_text: str) -> dict:
    result = {}
    result["loan_id"] = safe_search(r"Loan ID:\s*(\S+)", right_text)
    result["loan_purpose"] = safe_search(r"Purpose:\s*(.+)", right_text)
    result["loan_amount"] = money_to_number(safe_search(r"Principal Amount:\s*(\$[\d,\.]+)", right_text))
    result["total_payable"] = money_to_number(safe_search(r"Total Payable:\s*(\$[\d,\.]+)", right_text))
    loan_term = safe_search(r"Loan Term:\s*(\d+)\s*months", right_text)
    result["loan_duration_months"] = int(loan_term) if loan_term else None
    result["interest_rate"] = safe_search(r"Interest Rate:\s*(\d+%)", right_text)
    return result


def parse_payment_and_summary(full_text: str) -> dict:
    result = {}
    result["reference_number"] = safe_search(r"Reference No:\s*(\S+)", full_text)

    payment_pattern = (
        r"Amount Paid\s+"
        r"(?P<payment_date>\d{2}-\d{2}-\d{4})\s+"
        r"(?P<inst_num>\d+)\s+of\s+(?P<total_inst>\d+)\s+"
        r"(?P<payment_method>.+?)\s+"
        r"\$(?P<amount_paid>[\d,\.]+)\s+"
        r"Summary"
    )
    m = re.search(payment_pattern, full_text)
    if m:
        result["payment_date"] = m.group("payment_date")
        result["installment_number"] = int(m.group("inst_num"))
        result["total_installments"] = int(m.group("total_inst"))
        result["payment_method"] = m.group("payment_method").strip()
        result["payment_amount"] = money_to_number(m.group("amount_paid"))
    else:
        for key in ["payment_date", "installment_number", "total_installments",
                    "payment_method", "payment_amount"]:
            result[key] = None

    result["total_paid_to_date"] = money_to_number(safe_search(r"Total Paid to Date\s*(\$[\d,\.]+)", full_text))
    result["remaining_balance"] = money_to_number(safe_search(r"Remaining Balance\s*(\$[\d,\.]+)", full_text))
    result["current_repayment_status"] = safe_search(r"Current Status\s+(\w+)", full_text)
    return result


def parse_receipt_page(full_text: str, words: list, debug=False, debug_label="") -> dict:
    result = {}
    left_text, right_text = split_billed_to_columns(words) if words else ("", "")

    billed_to = parse_billed_to(left_text)
    if debug and billed_to.get("customer_name") is None:
        print(f"\n[DEBUG] Gagal parsing Billed To -> {debug_label}", file=sys.stderr)
        print(f"[DEBUG] words tersedia: {len(words) if words else 0}", file=sys.stderr)
        print(f"[DEBUG] left_text:\n{left_text!r}", file=sys.stderr)
        print(f"[DEBUG] right_text:\n{right_text!r}", file=sys.stderr)

    result.update(billed_to)
    result.update(parse_loan_info(right_text))
    result.update(parse_payment_and_summary(full_text))
    return result


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def parse_json_file(json_path: str, debug=False):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    filename = data.get("filename")
    records = []

    for page in data.get("pages", []):
        text = page.get("text", "")
        words = page.get("words", [])  # butuh JSON hasil pdf_to_json.py --with-words
        page_number = page.get("pageNumber")
        label = f"{filename} (halaman {page_number})"
        parsed = parse_receipt_page(text, words, debug=debug, debug_label=label)
        parsed["filename"] = filename
        parsed["pageNumber"] = page_number
        records.append(parsed)

    return records


def collect_json_files(input_path: str):
    """Kalau input_path folder, ambil semua file *.json di dalamnya (urut nama).
    Kalau file tunggal, kembalikan sebagai list berisi satu path."""
    p = Path(input_path)
    if p.is_dir():
        return sorted(p.glob("*.json"))
    return [p]


def parse_json_files(json_paths, quiet=False, debug=False):
    """Parsing banyak file JSON sekaligus, gabungkan semua record jadi satu list.
    Kalau satu file gagal (JSON rusak/tidak ada), file itu dilewati dan dicatat
    sebagai warning, bukan menghentikan seluruh proses batch."""
    all_records = []
    failed_files = []

    for json_path in json_paths:
        try:
            records = parse_json_file(str(json_path), debug=debug)
            all_records.extend(records)
            if not quiet:
                print(f"  -> {json_path.name}: {len(records)} halaman diparsing")
        except FileNotFoundError:
            failed_files.append((json_path, "file tidak ditemukan"))
        except json.JSONDecodeError as e:
            failed_files.append((json_path, f"JSON tidak valid ({e})"))

    if failed_files:
        print(f"\nPeringatan: {len(failed_files)} file dilewati karena error:", file=sys.stderr)
        for path, reason in failed_files:
            print(f"  - {path}: {reason}", file=sys.stderr)

    return all_records


# ---------------------------------------------------------------------------
# Nested JSON: kelompokkan record flat per customer/loan
# ---------------------------------------------------------------------------

# Field yang naik ke level luar (sekali per customer/loan)
CUSTOMER_LEVEL_FIELDS = [
    "customer_name", "email", "phone_number", "address", "city", "state", "postal_code",
    "loan_id", "loan_purpose", "loan_amount", "total_payable",
    "loan_duration_months", "interest_rate",
]

# Field yang tetap per pembayaran/halaman, masuk ke dalam "installments"
INSTALLMENT_LEVEL_FIELDS = [
    "reference_number", "payment_date", "installment_number", "total_installments",
    "payment_method", "payment_amount", "total_paid_to_date", "remaining_balance",
    "current_repayment_status", "filename", "pageNumber",
]


def build_nested_records(records: list) -> list:
    """Ubah list record flat (satu per halaman) jadi list nested per customer/loan:
    field customer & loan hanya sekali di level luar, field yang berbeda-beda
    tiap pembayaran masuk ke array "installments".

    Grouping berdasarkan loan_id (satu loan_id dianggap satu customer/loan yang
    sama meski datanya tersebar di beberapa halaman/file PDF). Kalau loan_id
    tidak ada (gagal parse), fallback ke kombinasi customer_name + email supaya
    record itu tidak hilang, hanya tidak ikut ter-grup dengan benar."""
    grouped = {}
    order = []

    for r in records:
        key = r.get("loan_id") or ("__no_loan_id__", r.get("customer_name"), r.get("email"))

        if key not in grouped:
            customer_entry = {}
            for field in CUSTOMER_LEVEL_FIELDS:
                customer_entry[field] = r.get(field)
            customer_entry["installments"] = []
            grouped[key] = customer_entry
            order.append(key)

        installment_entry = {field: r.get(field) for field in INSTALLMENT_LEVEL_FIELDS}
        grouped[key]["installments"].append(installment_entry)

    return [grouped[key] for key in order]


def save_to_csv(records: list, csv_path: str):
    if not records:
        return
    fieldnames = list(records[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def save_to_json(records: list, json_path: str):
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def print_field_summary(records: list):
    """Cetak ringkasan: berapa banyak tiap field bernilai null, plus contoh
    record yang bermasalah supaya gampang ditelusuri filename & halamannya."""
    if not records:
        return

    exclude = {"filename", "pageNumber"}
    fields = [k for k in records[0].keys() if k not in exclude]
    total = len(records)

    null_stats = []
    for f in fields:
        null_count = sum(1 for r in records if r.get(f) is None)
        if null_count > 0:
            null_stats.append((f, null_count))

    print("\n" + "=" * 60)
    print(f"RINGKASAN PARSING ({total} record dari {len({r.get('filename') for r in records})} file)")
    print("=" * 60)

    if not null_stats:
        print("Semua field terisi lengkap, tidak ada nilai null.")
        print("=" * 60)
        return

    null_stats.sort(key=lambda x: -x[1])
    print(f"Ditemukan {len(null_stats)} field dengan nilai null:\n")
    print(f"{'Field':<28}{'Null':>8}{'Dari':>8}{'Persentase':>14}")
    print("-" * 58)
    for f, cnt in null_stats:
        pct = cnt / total * 100
        print(f"{f:<28}{cnt:>8}{total:>8}{pct:>13.1f}%")

    problem_fields = {f for f, _ in null_stats}
    problem_records = [
        r for r in records if any(r.get(f) is None for f in problem_fields)
    ]
    print(f"\nRecord bermasalah: {len(problem_records)} dari {total}")
    max_show = 10
    for r in problem_records[:max_show]:
        missing = [f for f in problem_fields if r.get(f) is None]
        print(f"  - {r.get('filename')} (halaman {r.get('pageNumber')}): {', '.join(missing)}")
    if len(problem_records) > max_show:
        print(f"  ... dan {len(problem_records) - max_show} record lainnya")
        print("  (jalankan ulang dengan --debug untuk lihat detail rekonstruksi kolom tiap record)")

    print("=" * 60)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parsing field 'text'/'words' dari JSON payment receipt hasil PDF extraction."
    )
    parser.add_argument("-i", "--input", required=True, dest="input_path",
                         help="Path file JSON input (misal: input.json), ATAU path folder berisi "
                              "banyak file .json untuk mode batch (semua digabung jadi satu output)")
    parser.add_argument("-o", "--output", dest="output_path",
                         help="Path file output. Ekstensi menentukan format (.csv atau .json). Default: output.csv")
    parser.add_argument("--json-output", dest="json_output_path",
                         help="(Opsional) simpan juga hasil ke file JSON terpisah")
    parser.add_argument("-q", "--quiet", action="store_true",
                         help="Jangan cetak hasil parsing per halaman ke terminal")
    parser.add_argument("--debug", action="store_true",
                         help="Cetak left_text/right_text hasil rekonstruksi kolom setiap kali "
                              "customer_name gagal ke-parse (untuk diagnosa)")
    parser.add_argument("--no-summary", dest="show_summary", action="store_false",
                         help="Matikan ringkasan detail (jumlah null per field) di akhir proses")
    parser.add_argument("--flat-json", action="store_true",
                         help="Simpan JSON dalam bentuk flat (satu record per halaman, seperti versi lama) "
                              "alih-alih nested per customer/loan (default: nested)")
    return parser


def main():
    args = build_arg_parser().parse_args()

    json_paths = collect_json_files(args.input_path)
    is_batch = len(json_paths) > 1 or Path(args.input_path).is_dir()

    if not json_paths:
        print(f"Error: tidak ada file .json ditemukan di {args.input_path}", file=sys.stderr)
        sys.exit(1)

    if is_batch:
        print(f"Mode batch: memproses {len(json_paths)} file JSON dari {args.input_path}")

    output_path = args.output_path or ("output.csv" if not is_batch else "combined_output.csv")

    if is_batch:
        records = parse_json_files(json_paths, quiet=args.quiet, debug=args.debug)
    else:
        try:
            records = parse_json_file(str(json_paths[0]), debug=args.debug)
        except FileNotFoundError:
            print(f"Error: file input tidak ditemukan -> {args.input_path}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Error: file input bukan JSON valid -> {e}", file=sys.stderr)
            sys.exit(1)

    if not records:
        print("Error: tidak ada record yang berhasil diparsing.", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        for r in records:
            print(json.dumps(r, indent=2, ensure_ascii=False))
            print("-" * 60)

    # records tetap flat (dipakai untuk CSV & print_field_summary di atas).
    # Untuk JSON, di-nest per customer/loan kecuali --flat-json dipakai.
    json_records = records if args.flat_json else build_nested_records(records)

    if output_path.lower().endswith(".json"):
        save_to_json(json_records, output_path)
    else:
        save_to_csv(records, output_path)

    if is_batch:
        print(f"\nSelesai. {len(records)} record dari {len(json_paths)} file JSON digabung ke {output_path}")
    else:
        print(f"\nSelesai. {len(records)} record disimpan ke {output_path}")

    if args.json_output_path:
        save_to_json(json_records, args.json_output_path)
        print(f"Hasil JSON tambahan disimpan ke {args.json_output_path}")

    if args.show_summary:
        print_field_summary(records)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
pdf_to_raw_json.py

Convert satu atau banyak file PDF menjadi JSON dengan struktur:
{
  "filename": "...",
  "info": { "pageCount", "isEncrypted", "pdfVersion", ... },
  "pages": [ { "pageNumber", "width", "height", "rotation", "text", ... } ]
}

Dependencies:
    pip install pypdf pdfplumber --break-system-packages

Cara pakai:
    # Satu file, hasil dicetak ke stdout
    python pdf_to_raw_json.py document.pdf

    # Satu file, simpan ke file JSON tertentu
    python pdf_to_raw_json.py document.pdf -o document.json

    # Banyak file / satu folder, hasil disimpan ke folder output
    python pdf_to_raw_json.py folder_pdf/ -o folder_json/

    # Tanpa ekstraksi teks per halaman (lebih cepat, cuma metadata)
    python pdf_to_raw_json.py document.pdf --no-text

    # Sertakan koordinat kata (word bounding boxes) - lebih berat
    python pdf_to_raw_json.py document.pdf --with-words
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader
import pdfplumber


def format_pdf_date(raw):
    """Ubah tanggal format PDF (D:20240101120000+07'00') jadi ISO 8601 kalau bisa."""
    if not raw:
        return None
    s = str(raw)
    try:
        s2 = s.replace("D:", "")
        # Ambil bagian YYYYMMDDHHMMSS
        base = s2[:14]
        dt = datetime.strptime(base, "%Y%m%d%H%M%S")
        return dt.isoformat()
    except Exception:
        return s  # fallback: kembalikan string mentah kalau gagal parse


def get_pdf_version(reader):
    try:
        v = reader.pdf_header  # contoh: "%PDF-1.4"
        if v and "-" in v:
            return v.split("-")[-1]
    except Exception:
        pass
    return None


def extract_metadata(reader, is_encrypted):
    meta = {}
    try:
        m = reader.metadata
    except Exception:
        m = None

    if m:
        meta["title"] = m.title
        meta["author"] = m.author
        meta["subject"] = m.subject
        meta["creator"] = m.creator
        meta["producer"] = m.producer
        meta["keywords"] = getattr(m, "keywords", None)
        meta["creationDate"] = format_pdf_date(m.creation_date_raw if hasattr(m, "creation_date_raw") else None) \
            or format_pdf_date(str(m.creation_date) if m.creation_date else None)
        meta["modificationDate"] = format_pdf_date(m.modification_date_raw if hasattr(m, "modification_date_raw") else None) \
            or format_pdf_date(str(m.modification_date) if m.modification_date else None)
    else:
        for k in ["title", "author", "subject", "creator", "producer", "keywords",
                  "creationDate", "modificationDate"]:
            meta[k] = None

    return meta


def build_page_data(page_number, plumber_page, pypdf_page, extract_text, with_words):
    """Gabungkan data dari pdfplumber (layout/teks) dan pypdf (rotasi/ukuran box)."""
    width = float(plumber_page.width)
    height = float(plumber_page.height)

    # Rotasi asli dari pypdf lebih akurat untuk field /Rotate
    try:
        rotation = int(pypdf_page.get("/Rotate", 0)) % 360
    except Exception:
        rotation = 0

    text = ""
    if extract_text:
        try:
            text = plumber_page.extract_text() or ""
        except Exception:
            text = ""

    words = text.split() if text else []
    lines = text.split("\n") if text else []

    images = []
    try:
        images = plumber_page.images or []
    except Exception:
        pass

    tables_count = 0
    try:
        tables_count = len(plumber_page.find_tables())
    except Exception:
        pass

    annotations_count = 0
    try:
        annots = pypdf_page.get("/Annots")
        annotations_count = len(annots) if annots else 0
    except Exception:
        pass

    page_data = {
        "pageNumber": page_number,
        "width": width,
        "height": height,
        "rotation": rotation,
        "text": text,
        # ---- field tambahan yang berguna ----
        "charCount": len(text),
        "wordCount": len(words),
        "lineCount": len(lines) if text else 0,
        "imageCount": len(images),
        "tableCount": tables_count,
        "annotationCount": annotations_count,
        "isBlank": len(text.strip()) == 0,
    }

    if with_words:
        try:
            page_data["words"] = [
                {
                    "text": w["text"],
                    "x0": round(w["x0"], 2),
                    "x1": round(w["x1"], 2),
                    "top": round(w["top"], 2),
                    "bottom": round(w["bottom"], 2),
                }
                for w in plumber_page.extract_words()
            ]
        except Exception:
            page_data["words"] = []

    return page_data


def pdf_to_raw_json(pdf_path: Path, extract_text=True, with_words=False):
    reader = PdfReader(str(pdf_path))
    is_encrypted = reader.is_encrypted

    result = {
        "filename": pdf_path.name,
        "info": {
            "pageCount": len(reader.pages),
            "isEncrypted": is_encrypted,
            "pdfVersion": get_pdf_version(reader),
            # ---- field tambahan ----
            "fileSizeBytes": pdf_path.stat().st_size,
            "isFormPdf": bool(getattr(reader, "get_fields", lambda: None)()),
            **extract_metadata(reader, is_encrypted),
            "extractedAt": datetime.now(timezone.utc).isoformat(),
        },
        "pages": [],
    }

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for i, plumber_page in enumerate(pdf.pages):
                pypdf_page = reader.pages[i]
                result["pages"].append(
                    build_page_data(i + 1, plumber_page, pypdf_page, extract_text, with_words)
                )
    except Exception as e:
        result["info"]["error"] = f"Gagal mengekstrak sebagian/semua halaman: {e}"

    # ---- ringkasan di level dokumen ----
    total_chars = sum(p.get("charCount", 0) for p in result["pages"])
    total_words = sum(p.get("wordCount", 0) for p in result["pages"])
    blank_pages = sum(1 for p in result["pages"] if p.get("isBlank"))

    result["summary"] = {
        "totalCharacters": total_chars,
        "totalWords": total_words,
        "blankPageCount": blank_pages,
        "nonBlankPageCount": len(result["pages"]) - blank_pages,
    }

    return result


def collect_pdf_files(input_path: Path):
    if input_path.is_dir():
        return sorted(input_path.glob("*.pdf"))
    return [input_path]


def main():
    parser = argparse.ArgumentParser(description="Convert PDF ke JSON (metadata + teks per halaman).")
    parser.add_argument("input", help="Path file PDF atau folder berisi file PDF")
    parser.add_argument("-o", "--output", help="Path file/folder output JSON (default: stdout untuk 1 file)")
    parser.add_argument("--no-text", action="store_true", help="Skip ekstraksi teks (lebih cepat, hanya metadata)")
    parser.add_argument("--with-words", action="store_true", default=True,
                         help="Sertakan koordinat tiap kata per halaman (default: aktif, dibutuhkan untuk parsing layout kolom)")
    parser.add_argument("--no-words", dest="with_words", action="store_false",
                         help="Matikan ekstraksi koordinat kata (lebih ringan, tapi parser kolom tidak akan berfungsi)")
    parser.add_argument("--indent", type=int, default=2, help="Indentasi JSON output (default: 2)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: path tidak ditemukan: {input_path}", file=sys.stderr)
        sys.exit(1)

    pdf_files = collect_pdf_files(input_path)
    if not pdf_files:
        print(f"Error: tidak ada file .pdf ditemukan di {input_path}", file=sys.stderr)
        sys.exit(1)

    extract_text = not args.no_text

    # Mode banyak file -> butuh folder output
    if len(pdf_files) > 1:
        out_dir = Path(args.output) if args.output else input_path / "json_output"
        out_dir.mkdir(parents=True, exist_ok=True)
        for pdf_file in pdf_files:
            print(f"Memproses: {pdf_file.name} ...")
            data = pdf_to_raw_json(pdf_file, extract_text=extract_text, with_words=args.with_words)
            out_file = out_dir / (pdf_file.stem + ".json")
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=args.indent, ensure_ascii=False)
            print(f"  -> {out_file}")
        print(f"\nSelesai. {len(pdf_files)} file diproses ke {out_dir}")
        return

    # Mode satu file
    pdf_file = pdf_files[0]
    data = pdf_to_raw_json(pdf_file, extract_text=extract_text, with_words=args.with_words)
    output_json = json.dumps(data, indent=args.indent, ensure_ascii=False)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Tersimpan ke: {out_path}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()

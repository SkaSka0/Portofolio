"""
receipts_json_to_pdf.py

Merender record di selected_receipts.json menjadi PDF "Payment Receipt".

Dua mode:
  single    -> 1 file PDF per invoice/cicilan (default)
  customer  -> 1 file PDF per customer, isinya semua invoice customer itu
               (tiap invoice = 1 halaman dalam file yang sama)

Cara pakai:
    python receipts_json_to_pdf.py selected_receipts.json output_pdfs/ single
    python receipts_json_to_pdf.py selected_receipts.json output_pdfs/ customer
"""

import argparse
import os
import json
from collections import defaultdict

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_RIGHT, TA_LEFT


def get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RightAlign", parent=styles["Normal"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="BankTitle", parent=styles["Heading1"], fontSize=16, spaceAfter=2))
    styles.add(ParagraphStyle(name="ReceiptTitle", parent=styles["Heading2"], alignment=TA_RIGHT, spaceAfter=2))
    styles.add(ParagraphStyle(name="SectionHeader", parent=styles["Heading4"], spaceBefore=10, spaceAfter=4))
    styles.add(ParagraphStyle(name="SmallGrey", parent=styles["Normal"], textColor=colors.grey, fontSize=9))
    return styles


def build_receipt_story(record, styles):
    """Bangun 1 blok konten receipt (dipakai untuk 1 halaman)."""
    story = []

    # ---- Header: Bank name (left) + "PAYMENT RECEIPT" (right) ----
    header_table = Table(
        [[
            Paragraph(record["bank_name"], styles["BankTitle"]),
            Paragraph("PAYMENT RECEIPT", styles["ReceiptTitle"]),
        ]],
        colWidths=[90 * mm, 70 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(header_table)
    story.append(Paragraph(f"Reference No: {record['reference_number']}", styles["SmallGrey"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    story.append(Spacer(1, 10))

    # ---- Info: Customer (left) & Loan (right) ----
    customer_info = [
        Paragraph("<b>Billed To</b>", styles["SectionHeader"]),
        Paragraph(record["customer_name"], styles["Normal"]),
        Paragraph(record["address"], styles["Normal"]),
        Paragraph(f"{record['city']}, {record['state']} {record['postal_code']}", styles["Normal"]),
        Paragraph(record["email"], styles["Normal"]),
        Paragraph(record["phone_number"], styles["Normal"]),
    ]

    loan_info = [
        Paragraph("<b>Loan Information</b>", styles["SectionHeader"]),
        Paragraph(f"Loan ID: {record['loan_id']}", styles["Normal"]),
        Paragraph(f"Purpose: {record['loan_purpose']}", styles["Normal"]),
        Paragraph(f"Principal Amount: ${record['loan_amount']:,.2f}", styles["Normal"]),
        Paragraph(f"Total Payable: ${record['total_payable']:,.2f}", styles["Normal"]),
        Paragraph(f"Loan Term: {record['loan_duration_months']} months", styles["Normal"]),
        Paragraph(f"Interest Rate: {record['interest_rate']*100:.0f}%", styles["Normal"]),
    ]

    info_table = Table(
        [[customer_info, loan_info]],
        colWidths=[80 * mm, 80 * mm],
    )
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # ---- Payment Detail Table ----
    story.append(Paragraph("<b>Payment Detail</b>", styles["SectionHeader"]))

    data = [
        ["Payment Date", "Installment", "Payment Method", "Amount Paid"],
        [
            record["payment_date"],
            f"{record['installment_number']} of {record['total_installments']}",
            record["payment_method"],
            f"${record['payment_amount']:,.2f}",
        ],
    ]
    detail_table = Table(data, colWidths=[40 * mm, 40 * mm, 45 * mm, 35 * mm])
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 14))

    # ---- Summary ----
    story.append(Paragraph("<b>Summary</b>", styles["SectionHeader"]))
    summary_data = [
        ["Total Paid to Date", f"${record['total_paid_to_date']:,.2f}"],
        ["Remaining Balance", f"${record['remaining_balance']:,.2f}"],
        ["Current Status", record["current_repayment_status"]],
    ]
    summary_table = Table(summary_data, colWidths=[80 * mm, 80 * mm])
    summary_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 24))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "This receipt confirms that the payment above has been received and processed. "
        "Please retain this document for your records.",
        styles["SmallGrey"],
    ))

    return story


def new_doc(output_path):
    return SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )


def generate_single_mode(records, output_dir):
    """1 file PDF per invoice."""
    styles = get_styles()
    for record in records:
        filename = f"{record['reference_number']}.pdf"
        output_path = os.path.join(output_dir, filename)
        doc = new_doc(output_path)
        story = build_receipt_story(record, styles)
        doc.build(story)
    print(f"Total PDF invoice dihasilkan: {len(records)} file (1 file = 1 invoice)")


def generate_customer_mode(records, output_dir):
    """1 file PDF per customer, semua invoice-nya jadi banyak halaman dalam 1 file."""
    styles = get_styles()

    grouped = defaultdict(list)
    for r in records:
        grouped[r["customer_id"]].append(r)

    for customer_id, customer_records in grouped.items():
        # Urutkan per loan_id lalu installment_number biar rapi
        customer_records.sort(key=lambda r: (r["loan_id"], r["installment_number"]))

        customer_name = customer_records[0]["customer_name"]
        safe_name = customer_name.replace(" ", "_")
        filename = f"{customer_id}_{safe_name}.pdf"
        output_path = os.path.join(output_dir, filename)

        doc = new_doc(output_path)
        full_story = []
        for i, record in enumerate(customer_records):
            full_story.extend(build_receipt_story(record, styles))
            if i < len(customer_records) - 1:
                full_story.append(PageBreak())
        doc.build(full_story)

    print(f"Total PDF dihasilkan: {len(grouped)} file (1 file = 1 customer, berisi semua invoice-nya)")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render selected_receipts.json menjadi PDF payment receipt."
    )
    parser.add_argument(
        "-i", "--input", default="selected_receipts.json",
        help="Path file JSON hasil select_sample.py (default: selected_receipts.json)",
    )
    parser.add_argument(
        "-o", "--output-dir", default="output_pdfs",
        help="Folder untuk menyimpan PDF hasil generate (default: output_pdfs)",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "-s", "--single", action="store_true",
        help="1 file PDF per invoice (default kalau tidak ada flag mode yang dipilih)",
    )
    mode_group.add_argument(
        "-c", "--customer", action="store_true",
        help="1 file PDF per customer, berisi semua invoice-nya sebagai halaman terpisah",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    input_path = args.input
    output_dir = args.output_dir
    mode = "customer" if args.customer else "single"

    os.makedirs(output_dir, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if mode == "customer":
        generate_customer_mode(records, output_dir)
    else:
        generate_single_mode(records, output_dir)

    print(f"Tersimpan di folder: {output_dir}/")


if __name__ == "__main__":
    main()

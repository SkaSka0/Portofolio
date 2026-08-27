"""
csv_to_receipts_json.py

Gabungkan Customers.csv + Loans.csv + Repayment_Schedule.csv
menjadi selected_receipts.json, siap dipakai generate_pdf_invoices.py
"""

import csv
import json
import random
from collections import defaultdict

PAYMENT_METHODS = ["Bank Transfer", "Virtual Account", "Auto Debit"]


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(value):
    """Bersihkan string angka (ada '$', titik ribuan, koma desimal) jadi float."""
    if value is None or value == "":
        return 0.0
    cleaned = str(value).strip().replace("$", "")
    # Format sumber: "$170.471,98" -> titik = pemisah ribuan, koma = pemisah desimal
    cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def build_receipts(customers_path, loans_path, schedule_path):
    customers = load_csv(customers_path)
    loans = load_csv(loans_path)
    schedule = load_csv(schedule_path)

    # Index customers & loans by ID biar lookup cepat (mirip XLOOKUP tapi di Python)
    customer_by_id = {c["customer_id"]: c for c in customers}
    loan_by_id = {l["loan_id"]: l for l in loans}

    # Kelompokkan schedule per loan_id, urutkan by installment_no
    # supaya running total (total_paid_to_date) bisa dihitung berurutan
    schedule_by_loan = defaultdict(list)
    for row in schedule:
        schedule_by_loan[row["loan_id"]].append(row)

    for loan_id in schedule_by_loan:
        schedule_by_loan[loan_id].sort(key=lambda r: int(r["installment_no"]))

    records = []

    for loan_id, installments in schedule_by_loan.items():
        loan = loan_by_id.get(loan_id)
        if not loan:
            continue  # skip kalau loan_id tidak ketemu (data tidak konsisten)

        customer = customer_by_id.get(loan["customer_id"])
        if not customer:
            continue

        running_total_paid = 0.0

        for row in installments:
            status = row["payment_status"]

            # Hanya cicilan yang sudah dibayar (Paid atau Late) yang punya receipt
            if status not in ("Paid", "Late"):
                continue

            amount = to_float(row["amount_due"])
            running_total_paid += amount

            record = {
                "reference_number": f"TRX-{loan_id}-{int(row['installment_no']):03d}",
                "bank_name": loan["bank_name"],

                "customer_id": customer["customer_id"],
                "customer_name": f"{customer['first_name']} {customer['last_name']}",
                "address": customer["address"],
                "city": customer["city"],
                "state": customer["state"],
                "postal_code": customer["postal_code"],
                "email": customer["email"],
                "phone_number": customer["phone_number"],

                "loan_id": loan_id,
                "loan_purpose": loan["loan_purpose"],
                "loan_amount": to_float(loan["loan_amount"]),
                "total_payable": to_float(loan["total_payable"]),
                "loan_duration_months": int(loan["loan_duration_months"]),
                "interest_rate": to_float(loan["interest_rate"]),

                "payment_date": row["payment_date"],
                "installment_number": int(row["installment_no"]),
                "total_installments": int(loan["loan_duration_months"]),
                "payment_method": random.choice(PAYMENT_METHODS),
                "payment_amount": amount,

                "total_paid_to_date": round(running_total_paid, 2),
                "remaining_balance": to_float(row["remaining_balance"]),
                "current_repayment_status": status,
            }

            records.append(record)

    return records


def main():
    records = build_receipts(
        "Customers.csv",
        "Loans.csv",
        "Repayment_Schedule.csv",
    )

    with open("selected_receipts.json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Total record receipt: {len(records)}")
    print("Tersimpan sebagai selected_receipts.json")


if __name__ == "__main__":
    main()

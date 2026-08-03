# Loan Document Pipeline

A five-step data engineering pipeline that turns raw loan/customer/repayment CSVs into PDF payment receipts, then parses those PDFs back into structured data and exports the result to Excel. Built as a portfolio piece to demonstrate an end-to-end ETL/document-processing workflow: CSV → JSON → PDF → JSON → structured JSON → Excel.

## Pipeline Overview

```
Customers.csv          ─┐
Loans.csv               ├─► 01_build_receipts_json.py ─► selected_receipts.json
Repayment_Schedule.csv ─┘               │
                                        ▼
                            02_generate_pdf_invoices.py ─► output_pdfs/*.pdf
                                        │
                                        ▼
                            03_pdf_to_json.py ─► json_output/*.json  (raw text + word coordinates)
                                        │
                                        ▼
                            04_receipt_parser.py ─► output.csv / nested_output.json
                                        │
                                        ▼
                            05_nested_json_to_excel.py ─► output.xlsx (Flattened / Customers / Installments)
```

| Step | Script | Input | Output | Purpose |
|---|---|---|---|---|
| 1 | `src/01_build_receipts_json.py` | `Customers.csv`, `Loans.csv`, `Repayment_Schedule.csv` | `selected_receipts.json` | Joins the three raw sources and builds one flat receipt record per paid/late installment |
| 2 | `src/02_generate_pdf_invoices.py` | `selected_receipts.json` | `output_pdfs/*.pdf` | Renders each receipt as a "Payment Receipt" PDF (single-invoice or per-customer multi-page mode) |
| 3 | `src/03_pdf_to_json.py` | `*.pdf` | `*.json` | Extracts per-page text, metadata, and word-level coordinates from the generated PDFs |
| 4 | `src/04_receipt_parser.py` | PDF-to-JSON output | `output.csv`, nested `output.json` | Reconstructs the two-column invoice layout from word coordinates and regex-parses customer/loan/payment fields; groups installments under each loan |
| 5 | `src/05_nested_json_to_excel.py` | nested JSON | `output.xlsx` | Converts nested records into a 3-sheet Excel workbook (`Flattened`, `Customers`, `Installments`) |

## Why this exists

The goal is to simulate a realistic document-processing pipeline: structured source data is turned into unstructured documents (PDFs), and those documents are then parsed back into structured data — the kind of round-trip you'd build when ingesting scanned/generated receipts, invoices, or statements in a real financial data pipeline.

Notable engineering details:
- **Layout-aware PDF parsing**: `04_receipt_parser.py` doesn't rely on `pdfplumber`'s naive text extraction, which interleaves the two side-by-side invoice columns ("Billed To" vs "Loan Information"). Instead it uses word-level x/y coordinates and a gap-based clustering algorithm to reconstruct each column independently before applying regex.
- **Batch-friendly**: steps 3 and 4 accept either a single file or a folder, and gracefully skip unreadable files instead of aborting the whole run.
- **Data-quality reporting**: `04_receipt_parser.py` prints a null-field summary at the end of a run, showing which fields failed to parse and for which files/pages, to make debugging layout issues fast.
- **Multiple shapes, one dataset**: the final Excel export gives the same data in three shapes (denormalized/flattened, customer-only, installment-only with a `loan_id` foreign key) so it's easy to open and filter in Excel without a database.

## Project Structure

```
loan-document-pipeline/
├── src/
│   ├── 01_build_receipts_json.py
│   ├── 02_generate_pdf_invoices.py
│   ├── 03_pdf_to_json.py
│   ├── 04_receipt_parser.py
│   └── 05_nested_json_to_excel.py
├── data/
│   ├── raw/                # Customers.csv, Loans.csv, Repayment_Schedule.csv
│   └── generated/          # selected_receipts.json, all_receipt.csv (intermediate outputs)
├── output/                 # generated PDFs and final .xlsx (sample files only, see below)
└── requirements.txt
```

> **Note on committed artifacts:** Only a representative sample (2–3 files) of generated PDFs is committed to `output/` — the full set can be reproduced from `data/raw/` in a few seconds using the commands below. Intermediate JSON/CSV files in `data/generated/` *are* committed in full, since they're the strongest evidence of the pipeline actually working end-to-end.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Reproducing the pipeline

Run from the `loan-document-pipeline/` directory:

```bash
# 1. Build receipt records from the raw CSVs
python src/01_build_receipts_json.py

# 2. Render PDF invoices (one file per invoice)
python src/02_generate_pdf_invoices.py -i selected_receipts.json -o output_pdfs/ --single

#    ...or one PDF per customer, all their invoices as separate pages:
python src/02_generate_pdf_invoices.py -i selected_receipts.json -o output_pdfs/ --customer

# 3. Extract text + word coordinates from the PDFs (batch mode: pass a folder)
python src/03_pdf_to_json.py output_pdfs/ -o json_output/

# 4. Parse the extracted JSON back into structured records
python src/04_receipt_parser.py -i json_output/ -o output.csv --json-output nested_output.json

# 5. Export the nested JSON to a 3-sheet Excel workbook
python src/05_nested_json_to_excel.py -i nested_output.json -o output.xlsx
```

Each script also has a `-h/--help` flag documenting its own options (e.g. `--flat-json`, `--debug`, `--no-summary` on step 4).

## Tech Stack

- **pandas** / stdlib `csv` — CSV ingestion and joins
- **reportlab** — PDF generation
- **pypdf** + **pdfplumber** — PDF metadata, text, and word-coordinate extraction
- **openpyxl** — styled multi-sheet Excel export
- Pure-stdlib **regex-based parsing** for the PDF → structured data step (no ML/OCR dependency)

## Roadmap

- [ ] Finalize `.gitignore` for generated PDFs/output
- [ ] Add more portfolio projects to this monorepo

"""CSV exports -> the same extraction JSON as a PDF (plan §4.1 "Optional CSV import").

Exact and free: no Claude call. Recognised by their column headings:

- Square Sales Summary (one row per day)   -> one pos_report per row
- Bank account export (Date, Description, Amount, Balance)  -> one bank_statement
- Credit card export (Date, Description, Amount, Type)      -> one credit_card_statement

Lines starting with "#" are comments (the generator writes the account's last
4 digits and the period there, as a bank's export header would).
"""

import re

from books.money import parse_cents, to_decimal_str
from ingest.intake import read_csv_rows


class CsvFormatError(ValueError):
    pass


SQUARE_COLS = {
    "Date": None, "In-store Sales": "sales_instore", "Delivery Sales": "sales_delivery",
    "Delivery Fees": "delivery_fees", "Gross Sales": "gross_sales", "Discounts": "discounts", "Comps": "comps",
    "Refunds": "refunds", "Net Sales": "net_sales", "Tax": "tax", "Tips": "tips",
    "Total Collected": "total_collected", "Card": "tender_card", "Cash": "tender_cash", "Fees": "fees",
    "Net Deposit": "net_card_deposit",
}


def _comments(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"#\s*([^:]+):\s*(.*)", line)
        if m:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _date(value):
    value = value.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return value


def _money(value):
    cents = parse_cents(value)
    return None if cents is None else to_decimal_str(cents)


def detect(text):
    rows = read_csv_rows(text)
    if not rows:
        raise CsvFormatError("The CSV has no rows.")
    cols = set(rows[0].keys())
    if {"Gross Sales", "Net Sales", "Tips"} <= cols:
        return "square", rows
    if {"Date", "Description", "Amount", "Balance"} <= cols:
        return "bank", rows
    if {"Date", "Description", "Amount", "Type"} <= cols:
        return "card", rows
    raise CsvFormatError("Unknown CSV layout. Expected a Square Sales Summary, a bank export "
                         "(Date, Description, Amount, Balance) or a card export (Date, Description, Amount, Type).")


def parse(text):
    """A list of extraction dicts (doc_type + fields), one per document."""
    kind, rows = detect(text)
    meta = _comments(text)
    if kind == "square":
        docs = []
        for row in rows:
            d = {"doc_type": "pos_report", "provider": "Square",
                 "period_start": _date(row["Date"]), "period_end": _date(row["Date"])}
            for col, field in SQUARE_COLS.items():
                if field:
                    d[field] = _money(row.get(col, ""))
            docs.append(d)
        return docs
    txns = []
    for row in rows:
        t = {"date": _date(row["Date"]), "description": row["Description"].strip(),
             "amount": _money(row["Amount"])}
        if kind == "bank":
            t["check_no"] = (row.get("Check Number") or "").strip() or None
        else:
            t["kind"] = (row.get("Type") or "purchase").strip().lower()
        txns.append(t)
    if kind == "bank":
        first_bal = parse_cents(rows[0]["Balance"])
        first_amt = parse_cents(rows[0]["Amount"])
        opening = meta.get("opening balance") or to_decimal_str(first_bal - first_amt)
        closing = meta.get("closing balance") or _money(rows[-1]["Balance"])
        return [{"doc_type": "bank_statement", "bank": meta.get("bank", ""),
                 "account_last4": meta.get("account", "")[-4:] or None,
                 "period_start": meta.get("period start") or txns[0]["date"],
                 "period_end": meta.get("period end") or txns[-1]["date"],
                 "opening_balance": _money(opening), "closing_balance": closing, "transactions": txns}]
    return [{"doc_type": "credit_card_statement", "issuer": meta.get("issuer", ""),
             "card_last4": meta.get("card", "")[-4:] or None,
             "period_start": meta.get("period start") or txns[0]["date"],
             "period_end": meta.get("period end") or txns[-1]["date"],
             "opening_balance": _money(meta.get("opening balance", "0")),
             "closing_balance": _money(meta.get("closing balance", "0")), "transactions": txns}]

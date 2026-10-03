"""CT sales and use tax worksheet for Form OS-114 (plan §10.2).

Computed from the GL for one filing period, then compared with what the books
say was collected (CT sales tax payable, 2200). A difference is flagged, not
hidden: rounding, a register programmed with the wrong rate, or a booking error.
"""

import json
from calendar import monthrange

from books.money import parse_cents, pct_of
from books.post import balances
from tax import rules_loader


def period_dates(month):
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{monthrange(y, m)[1]:02d}"


def _credits(bals, accounts):
    return -sum(bals.get(a, 0) for a in accounts)


def collected(conn, start, end):
    """Tax credited to 2200 in the period (not the opening balance), less tax reversals.
    A debit to 2200 in an entry that moves money out of the bank (1010) is the payment to
    DRS, not a reversal."""
    row = conn.execute(
        "SELECT COALESCE(SUM(l.credit),0) AS cr FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
        "WHERE l.account='2200' AND e.kind <> 'opening' AND e.date BETWEEN ? AND ?", (start, end)).fetchone()
    row2 = conn.execute(
        "SELECT COALESCE(SUM(l.debit),0) AS dr FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id "
        "WHERE l.account='2200' AND e.kind <> 'opening' AND e.date BETWEEN ? AND ? AND NOT EXISTS "
        "(SELECT 1 FROM journal_lines x WHERE x.entry_id=e.id AND x.account IN ('1010','1000'))",
        (start, end)).fetchone()
    return row["cr"] - row2["dr"]


def use_tax_items(conn, start, end, rate):
    """Posted receipts with taxable items and no CT tax charged (plan §10.2 "Use tax")."""
    out = []
    for d in conn.execute("SELECT * FROM documents WHERE doc_type='purchase_receipt' AND status='posted' "
                          "AND doc_date BETWEEN ? AND ?", (start, end)):
        flags = json.loads(d["flags"])
        if any("use tax" in f["message"] for f in flags):
            ext = json.loads(d["extraction"])
            taxable = sum(parse_cents(l.get("amount")) or 0 for l in ext.get("lines") or [] if l.get("taxable"))
            out.append({"document_id": d["id"], "vendor": d["party"], "date": d["doc_date"], "taxable": taxable,
                        "use_tax": pct_of(taxable, rate)})
    return out


def worksheet(conn, month, year=None):
    year = year or int(month[:4])
    rules = rules_loader.load(year, "sales_tax")
    start, end = period_dates(month)
    bals = balances(conn, start=start, end=end)
    taxable_accts = rules["taxable_revenue_accounts"]
    market_accts = rules["marketplace_revenue_accounts"]
    ded_accts = rules["deduction_accounts"]
    gross_own = _credits(bals, taxable_accts)
    marketplace = _credits(bals, market_accts)
    deductions = sum(bals.get(a, 0) for a in ded_accts)          # debit balance
    gross_receipts = gross_own + marketplace
    taxable = gross_own - deductions
    rate = rules["rates"]["meals"]
    tax_due = pct_of(taxable, rate)
    in_books = collected(conn, start, end)
    use = use_tax_items(conn, start, end, rules["rates"]["general"])
    use_tax = sum(u["use_tax"] for u in use)
    lines = [
        {"label": "Gross receipts (all food & beverage sales, own delivery fees, platform sales)", "amount": gross_receipts,
         "source": ", ".join(taxable_accts + market_accts)},
        {"label": "Less: sales through marketplace facilitators (tax collected and remitted by the platform)",
         "amount": -marketplace, "source": ", ".join(market_accts)},
        {"label": "Less: discounts, comps and refunds", "amount": -deductions, "source": ", ".join(ded_accts)},
        {"label": f"Taxable gross receipts at {rate}% (meals)", "amount": taxable, "source": "computed"},
        {"label": f"Sales tax due at {rate}%", "amount": tax_due, "source": "computed"},
        {"label": f"Use tax on untaxed purchases at {rules['rates']['general']}%", "amount": use_tax,
         "source": f"{len(use)} receipt(s)"},
        {"label": "Total tax due with this return", "amount": tax_due + use_tax, "source": "computed"},
    ]
    difference = in_books - tax_due
    flags = []
    if abs(difference) > 100:
        flags.append(f"Tax collected per the books ({in_books / 100:,.2f}) differs from the tax computed on the "
                     f"taxable receipts ({tax_due / 100:,.2f}) by {difference / 100:,.2f}. Check the register's tax "
                     "rate, tax-exempt sales, or bookings to 2200.")
    elif difference:
        flags.append(f"Rounding difference of {difference / 100:,.2f} between tax collected per transaction and tax "
                     "on the period total (normal).")
    if use:
        flags.append(f"{len(use)} purchase(s) had taxable items with no CT tax charged: use tax is included.")
    return {"form": "OS-114", "period": month, "start": start, "end": end, "lines": lines,
            "gross_receipts": gross_receipts, "marketplace": marketplace, "deductions": deductions,
            "taxable": taxable, "rate": rate, "tax_due": tax_due, "use_tax": use_tax,
            "total_due": tax_due + use_tax, "collected_per_books": in_books, "difference": difference,
            "use_tax_items": use, "flags": flags, "open_questions": rules.get("open_questions", []),
            "rules_version": rules.get("version"), "verified": rules.get("verified", False)}

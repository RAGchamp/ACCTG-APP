"""Code checks after extraction (plan §5.3). No Claude.

Adapted from the analyzer's ingest\\ocr_quality.py tie-outs: a document's own
arithmetic confirms its figures. Nothing here changes an extraction; it only
returns flags:

    {"level": "error" | "warn" | "info", "field": "total", "message": "..."}

"error" flags must be resolved (confirmed or corrected) before posting;
"warn" flags are shown in yellow on the Review screen.
"""

from datetime import date

from books.money import parse_cents, to_decimal_str

DOC_TYPES = ("purchase_receipt", "credit_card_statement", "bank_statement", "check", "cash_log",
             "pos_report", "platform_statement", "payroll_report", "other", "not_financial")
CT_RATES = ("6.35", "7.35")
TAX_TOLERANCE = 2          # cents
LOW_CONFIDENCE_TYPES = ("cash_log",)


def c(value):
    return parse_cents(value)


def _flag(flags, level, field, message):
    flags.append({"level": level, "field": field, "message": message})


def _unknown(flags, doc, fields):
    """Figures that are missing or unreadable ([?])."""
    for f in fields:
        v = doc.get(f)
        if v is None or (isinstance(v, str) and "[?]" in v):
            _flag(flags, "error", f, f"{f} is unreadable or missing - confirm it")


def _date_ok(flags, value, field, start=None, end=None):
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        _flag(flags, "error", field, f"{field} {value!r} is not a date")
        return
    if d > date.today():
        _flag(flags, "warn", field, f"{field} {value} is in the future")
    if start and str(value) < start:
        _flag(flags, "warn", field, f"{field} {value} is before the books start ({start})")


def _tie(flags, field, expected, stated, what):
    if expected is None or stated is None:
        return
    if expected != stated:
        _flag(flags, "error", field,
              f"{what}: expected {to_decimal_str(expected)}, the document says {to_decimal_str(stated)} "
              f"(difference {to_decimal_str(stated - expected)})")


def _sum(values):
    vals = [c(v) for v in values]
    return None if any(v is None for v in vals) else sum(vals)


def check(doc, books_start=None):
    flags = []
    t = doc.get("doc_type")
    if t not in DOC_TYPES:
        _flag(flags, "error", "doc_type", f"unknown document type {t!r}")
        return flags
    fn = globals().get(f"_check_{t}")
    if fn:
        fn(doc, flags, books_start)
    if t in LOW_CONFIDENCE_TYPES:
        _flag(flags, "info", "*", "handwritten document: confirm every figure")
    text = str(doc)
    if "[?]" in text and not any(f["level"] == "error" and "unreadable" in f["message"] for f in flags):
        _flag(flags, "error", "*", "the document has unreadable characters [?] - confirm the figures")
    return flags


def _check_purchase_receipt(doc, flags, start):
    _unknown(flags, doc, ["total"])
    _date_ok(flags, doc.get("date"), "date", start)
    lines = doc.get("lines") or []
    subtotal, tax, tip, total = c(doc.get("subtotal")), c(doc.get("tax")) or 0, c(doc.get("tip")) or 0, c(doc.get("total"))
    if lines:
        s = _sum(l.get("amount") for l in lines)
        _tie(flags, "subtotal", s, subtotal, "sum of the item lines vs subtotal")
    if subtotal is not None and total is not None:
        _tie(flags, "total", subtotal + tax + tip, total, "subtotal + tax + tip vs total")
    taxable = _sum(l.get("amount") for l in lines if l.get("taxable")) if lines else None
    if tax and taxable:
        from books.money import pct_of
        if not any(abs(pct_of(abs(taxable), r) - abs(tax)) <= TAX_TOLERANCE for r in CT_RATES):
            rate = abs(tax) * 10000 // max(abs(taxable), 1) / 100
            _flag(flags, "warn", "tax", f"unusual sales tax rate ({rate}% of the taxable items; CT is 6.35% or 7.35%)")
    elif not tax and taxable and taxable > 0:
        _flag(flags, "warn", "tax", "taxable items but no sales tax charged - possible CT use tax")
    pay = (doc.get("payment") or {}).get("method")
    if pay in (None, "unknown"):
        _flag(flags, "warn", "payment", "payment method not shown on the receipt")


def _statement(doc, flags, start, kind):
    _unknown(flags, doc, ["opening_balance", "closing_balance"])
    txns = doc.get("transactions") or []
    opening, closing = c(doc.get("opening_balance")), c(doc.get("closing_balance"))
    total = _sum(t.get("amount") for t in txns)
    if total is None:
        _flag(flags, "error", "transactions", "a transaction amount is unreadable")
    elif opening is not None and closing is not None:
        _tie(flags, "closing_balance", opening + total, closing, "opening balance + transactions vs closing balance")
    for i, t in enumerate(txns, 1):
        if not t.get("date") or (doc.get("period_start") and not
                                 (doc["period_start"] <= t["date"] <= doc.get("period_end", "9999"))):
            _flag(flags, "warn", f"transactions[{i}]", f"txn {i} date {t.get('date')} outside the statement period")


def _check_credit_card_statement(doc, flags, start):
    _statement(doc, flags, start, "card")


def _check_bank_statement(doc, flags, start):
    _statement(doc, flags, start, "bank")


def _check_check(doc, flags, start):
    _date_ok(flags, doc.get("date"), "date", start)
    if doc.get("void"):
        _flag(flags, "info", "void", "the check is marked VOID - nothing to post")
        return
    _unknown(flags, doc, ["amount_numeric"])
    words = doc.get("amount_words")
    num = c(doc.get("amount_numeric"))
    if words and num is not None:
        spelled = words_to_cents(words)
        if spelled is not None and spelled != num:
            _flag(flags, "error", "amount_words",
                  f"the written amount ({to_decimal_str(spelled)}) differs from the figures ({to_decimal_str(num)}); "
                  "the bank pays the written amount - confirm")


def _check_cash_log(doc, flags, start):
    _date_ok(flags, doc.get("date"), "date", start)
    entries = doc.get("entries") or []
    for i, e in enumerate(entries, 1):
        if c(e.get("amount")) is None:
            _flag(flags, "error", f"entries[{i}]", f"line {i} ({e.get('description')}) amount is unreadable")
    sales = _sum(e.get("amount") for e in entries if e.get("kind") in ("cash_sale", "sales_tax"))
    stated = c(doc.get("total_stated"))
    if stated is not None and sales is not None:
        _tie(flags, "total_stated", sales, stated, "cash sales + tax vs the total written on the sheet")
    sale = _sum(e.get("amount") for e in entries if e.get("kind") == "cash_sale")
    tax = _sum(e.get("amount") for e in entries if e.get("kind") == "sales_tax")
    if sale and tax is not None:
        from books.money import pct_of
        if abs(pct_of(sale, "7.35") - tax) > TAX_TOLERANCE:
            _flag(flags, "warn", "sales_tax", "sales tax on the cash sales is not 7.35%")


def _check_pos_report(doc, flags, start):
    fields = ["sales_instore", "sales_delivery", "delivery_fees", "gross_sales", "discounts", "comps", "refunds",
              "net_sales", "tax", "tips", "total_collected", "tender_card", "tender_cash", "fees", "net_card_deposit"]
    _unknown(flags, doc, fields)
    v = {f: c(doc.get(f)) or 0 for f in fields}
    _tie(flags, "gross_sales", v["sales_instore"] + v["sales_delivery"] + v["delivery_fees"], v["gross_sales"],
         "in-store + delivery sales + delivery fees vs gross sales")
    _tie(flags, "net_sales", v["gross_sales"] - v["discounts"] - v["comps"] - v["refunds"], v["net_sales"],
         "gross sales - discounts - comps - refunds vs net sales")
    _tie(flags, "total_collected", v["net_sales"] + v["tax"] + v["tips"], v["total_collected"],
         "net sales + tax + tips vs total collected")
    _tie(flags, "tender_card", v["total_collected"] - v["tender_cash"], v["tender_card"],
         "total collected - cash vs card")
    _tie(flags, "net_card_deposit", v["tender_card"] - v["fees"], v["net_card_deposit"], "card - fees vs deposit")
    from books.money import pct_of
    if v["net_sales"] and abs(pct_of(v["net_sales"], "7.35") - v["tax"]) > max(TAX_TOLERANCE, v["net_sales"] // 10000):
        _flag(flags, "warn", "tax", "sales tax is not 7.35% of net sales (non-taxable items?)")


def _check_platform_statement(doc, flags, start):
    fields = ["subtotal", "commission", "marketing_fees", "promotions", "adjustments", "payout"]
    _unknown(flags, doc, ["subtotal", "payout"])
    v = {f: c(doc.get(f)) or 0 for f in fields}
    _tie(flags, "payout", v["subtotal"] - v["commission"] - v["marketing_fees"] - v["promotions"] + v["adjustments"],
         v["payout"], "subtotal - commission - marketing - promotions + adjustments vs payout")


def _check_payroll_report(doc, flags, start):
    fields = ["gross_wages", "tips_paid", "employee_withholding", "net_pay", "employer_taxes", "total_debit"]
    _unknown(flags, doc, ["gross_wages", "net_pay", "employer_taxes"])
    v = {f: c(doc.get(f)) or 0 for f in fields}
    _tie(flags, "net_pay", v["gross_wages"] - v["employee_withholding"], v["net_pay"],
         "gross wages - withholding vs net pay")
    _tie(flags, "total_debit", v["gross_wages"] + v["employer_taxes"], v["total_debit"],
         "gross wages + employer taxes vs total debited")


def _check_other(doc, flags, start):
    _flag(flags, "warn", "doc_type", "not one of the known document types - review closely")


# ------------------------------------------------------------------ amounts in words

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def words_to_cents(text):
    """"Two thousand eight hundred and 00/100" -> 280000; None if it can't be read."""
    import re
    t = text.lower().replace("-", " ").replace(",", " ")
    cents = 0
    m = re.search(r"(\d{1,2})\s*/\s*100", t)
    if m:
        cents = int(m.group(1))
        t = t[:m.start()]
    total, current, seen = 0, 0, False
    for word in re.findall(r"[a-z]+", t):
        if word in ("and", "dollars", "dollar", "only", "xx", "no"):
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current *= 100
        elif word in _SCALES:
            total += current * _SCALES[word]
            current = 0
        else:
            return None
        seen = True
    if not seen:
        return None
    return (total + current) * 100 + cents


def summary(doc):
    """(date, party, total_cents) shown in lists."""
    t = doc.get("doc_type")
    if t == "purchase_receipt":
        return doc.get("date"), doc.get("vendor"), c(doc.get("total"))
    if t in ("credit_card_statement", "bank_statement"):
        return doc.get("period_end"), doc.get("issuer") or doc.get("bank"), c(doc.get("closing_balance"))
    if t == "check":
        party = doc.get("payee") if doc.get("direction") == "outgoing" else doc.get("payer")
        return doc.get("date"), party, c(doc.get("amount_numeric"))
    if t == "cash_log":
        return doc.get("date"), "Cash log", c(doc.get("total_stated"))
    if t == "pos_report":
        return doc.get("period_end"), doc.get("provider") or "POS", c(doc.get("total_collected"))
    if t == "platform_statement":
        return doc.get("payout_date") or doc.get("period_end"), doc.get("platform"), c(doc.get("payout"))
    if t == "payroll_report":
        return doc.get("pay_date"), doc.get("provider"), c(doc.get("total_debit"))
    return doc.get("date"), doc.get("party"), c(doc.get("amount"))

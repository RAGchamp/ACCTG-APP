"""Where a document landed (INFO\\DOC-REVIEW-REPROCESS-PLAN.md §3): its ledger lines, the
financial statement lines they feed and the tax worksheet lines they reach.

Pure code, never Claude (P4). Each journal line is mapped through the account metadata
(type, subtype, cash_flow_class, tax_line) and the tax_rules\\<year> files that
reports\\statements.py and tax\\*.py use, so this screen and the reports can't disagree.

tag_document() returns integer cents (`*_cents`) next to "84.98"-style strings, so the same
dict feeds the template and the feedback JSON file.
"""

import json

from books.coa import account_map, normal_sign
from books.match import CLEARING
from books.money import parse_cents, to_decimal_str
from books.post import balances
from reports.statements import month_ends
from tax import rules_loader
from tax.federal import SERVICE_ACCOUNTS_1099

FOOD_COST_ACCOUNTS = ("5010", "5020")


def _label(doc_type):
    return (doc_type or "document").replace("_", " ")


def _month_range(iso):
    return month_ends(int(iso[:4]))[int(iso[5:7]) - 1]


def _money(cents):
    return to_decimal_str(cents or 0)


class _Totals:
    """Statement line totals per month, computed once per account and month."""

    def __init__(self, conn):
        self.conn, self.flow, self.end = conn, {}, {}

    def period(self, iso):
        start, end = _month_range(iso)
        if start not in self.flow:
            self.flow[start] = balances(self.conn, start=start, end=end)
        return start[:7], self.flow[start]

    def balance(self, iso):
        _, end = _month_range(iso)
        if end not in self.end:
            self.end[end] = balances(self.conn, end=end)
        return end, self.end[end]


# ------------------------------------------------------------------ the ledger side

def _posted_entries(conn, where, args):
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM journal_entries WHERE {where} ORDER BY id", args)]
    out = []
    for e in rows:
        reversed_by = conn.execute("SELECT id FROM journal_entries WHERE reverses=?", (e["id"],)).fetchone()
        lines = [{"line_id": l["id"], "account": l["account"], "debit_cents": l["debit"], "credit_cents": l["credit"],
                  "memo": l["memo"] or "", "party": l["party"] or "", "ref": l["ref"] or ""}
                 for l in conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (e["id"],))]
        out.append({"entry_id": e["id"], "document_id": e["document_id"], "date": e["date"],
                    "memo": e["memo"] or "", "kind": e["kind"],
                    "reverses": e["reverses"], "reversed_by": reversed_by[0] if reversed_by else None,
                    "active": e["kind"] != "reversal" and not reversed_by, "posted": True, "lines": lines})
    return out


def _proposal_entries(conn, document_id):
    p = conn.execute("SELECT * FROM proposals WHERE document_id=? AND status='open' ORDER BY version DESC LIMIT 1",
                     (document_id,)).fetchone()
    if not p:
        return None, None
    out = []
    for e in json.loads(p["entries"]):
        lines = []
        for l in e.get("lines") or []:
            lines.append({"line_id": None, "account": str(l.get("account", "")),
                          "debit_cents": parse_cents(l.get("debit") or 0) or 0,
                          "credit_cents": parse_cents(l.get("credit") or 0) or 0,
                          "memo": l.get("memo") or "", "party": l.get("party") or "", "ref": e.get("ref") or ""})
        out.append({"entry_id": None, "date": e.get("date") or "", "memo": e.get("memo") or "", "kind": "proposed",
                    "reverses": None, "reversed_by": None, "active": True, "posted": False, "lines": lines})
    return out, dict(p)


def _booked_from(conn, document_id):
    """A support document's money was booked from another document: those entries."""
    out, notes = [], []
    for m in conn.execute("SELECT * FROM matches WHERE document_id=? AND kind='support'", (document_id,)):
        other = conn.execute("SELECT id, doc_type, party FROM documents WHERE id=?",
                             (m["other_document_id"],)).fetchone()
        ref = m["other_ref"] or ""
        if ref.startswith("link:jl:"):
            entries = _posted_entries(conn, "id = (SELECT entry_id FROM journal_lines WHERE id=?)", (int(ref[8:]),))
            ref = "its entry"
        else:
            entries = _posted_entries(conn, "document_id=? AND kind <> 'reversal'", (m["other_document_id"],))
            if ref.startswith("txn"):
                entries = [e for e in entries if any(l["ref"] == ref for l in e["lines"])]
        if other:
            notes.append(f"{_label(other['doc_type'])} #{other['id']} {ref} {other['party'] or ''}".strip())
        out += entries
    return out, notes


def _settlement(conn, document_id, line):
    """For a clearing-account line: what settled it, what it settles, or since when it is open."""
    if line["account"] not in CLEARING or not line["line_id"]:
        return None
    rows = conn.execute(
        "SELECT m.*, d.doc_type, d.party, d.doc_date FROM matches m JOIN documents d ON d.id=m.document_id "
        "WHERE m.other_ref IN (?, ?)", (f"jl:{line['line_id']}", f"link:jl:{line['line_id']}")).fetchall()
    for m in rows:
        if m["other_ref"].startswith("link:"):
            continue
        return {"kind": "settled_by", "document_id": m["document_id"],
                "text": f"settled by {_label(m['doc_type'])} #{m['document_id']} {m['doc_ref'] or ''} "
                        f"({m['party'] or ''}{', ' + m['doc_date'] if m['doc_date'] else ''})"}
    if line["ref"]:
        for m in conn.execute("SELECT m.*, d.doc_type, d.party FROM matches m JOIN documents d "
                              "ON d.id=m.other_document_id WHERE m.document_id=? AND m.doc_ref=? AND "
                              "m.kind <> 'support'", (document_id, line["ref"])):
            other_ref = m["other_ref"] or ""
            if other_ref.startswith("jl:"):
                item = conn.execute("SELECT account FROM journal_lines WHERE id=?", (int(other_ref[3:]),)).fetchone()
                if item and item["account"] != line["account"]:
                    continue
            return {"kind": "settles", "document_id": m["other_document_id"],
                    "text": f"settles {_label(m['doc_type'])} #{m['other_document_id']} ({m['party'] or ''})"}
    links = [m for m in rows if m["other_ref"].startswith("link:")]
    extra = "; ".join(f"{_label(m['doc_type'])} #{m['document_id']} linked as support" for m in links)
    return {"kind": "open", "document_id": None, "text": "open item: waiting for the statement line / deposit "
                                                        "that settles it" + (f" ({extra})" if extra else "")}


# ------------------------------------------------------------------ financial statements

def _statement_tags(a, line, entry_date, totals):
    dr, cr = line["debit_cents"], line["credit_cents"]
    effect = (dr - cr) * normal_sign(a["type"])
    t, sub, name, num = a["type"], a["subtype"], a["name"], a["number"]
    tags = []

    def add(statement, section, label, amount, total=None, period=None, note=""):
        tags.append({"statement": statement, "section": section, "line": label, "amount_cents": amount,
                     "amount": _money(amount), "line_total_cents": total,
                     "line_total": None if total is None else _money(total), "period": period, "note": note})

    if t in ("revenue", "cogs", "expense"):
        period, flow = totals.period(entry_date)
        month_total = flow.get(num, 0) * normal_sign(t)
        ni = cr - dr
        ni_note = f"net income {'+' if ni >= 0 else '-'}{_money(abs(ni))} (Balance sheet and Owner's equity: net income)"
        if t == "revenue" and sub == "contra_revenue":
            add("Income statement", "Less: discounts, comps & refunds", name, dr - cr, -month_total, period,
                "reduces net sales; " + ni_note)
        elif t == "revenue":
            add("Income statement", "Revenue", name, effect, month_total, period, "gross and net sales; " + ni_note)
        elif t == "cogs":
            add("Income statement", "Cost of goods sold", name, effect, month_total, period,
                ("in food & beverage cost %; " if num in FOOD_COST_ACCOUNTS else "") + ni_note)
        else:
            add("Income statement", "Operating expenses", name, effect, month_total, period, ni_note)
        return tags

    end, bal = totals.balance(entry_date)
    sign = normal_sign(t) if not (t == "asset" and sub == "contra_asset") else 1
    closing = bal.get(num, 0) * sign
    if t == "asset":
        section = "Fixed assets" if sub in ("fixed_asset", "contra_asset") else "Current assets"
        add("Balance sheet", section, name, effect if sub != "contra_asset" else dr - cr, closing, f"as of {end}")
    elif t == "liability":
        add("Balance sheet", "Liabilities", name, effect, closing, f"as of {end}")
    else:
        add("Balance sheet", "Owner's equity", name, effect, closing, f"as of {end}")
        eq = {"capital": "Owner contributions", "draws": "Owner draws", "opening": "Opening balances"}.get(sub)
        if eq:
            add("Statement of owner's equity", "", eq, abs(dr - cr))

    cls = a["cash_flow_class"]
    cash_effect = cr - dr                      # a non-cash asset going up uses cash, a liability going up gives it
    if cls == "cash":
        add("Cash flow", "Cash at the end (cash account)", name, dr - cr, note="ties to ending cash")
    elif cls in ("operating", "investing", "financing"):
        if num == "1590":
            label = "Depreciation (non-cash)"
        elif sub == "capital":
            label = "Owner contributions"
        elif sub == "draws":
            label = "Owner draws"
        elif sub == "loan":
            label = "Loan proceeds / principal repaid"
        else:
            label = f"Change in {name}"
        note = "settles to zero when matched" if num in CLEARING else ""
        add("Cash flow", f"{cls.capitalize()} activities", label, cash_effect, note=note)
    return tags


# ------------------------------------------------------------------ tax

def _rules(year):
    try:
        return rules_loader.load(year, "federal"), rules_loader.load(year, "sales_tax"), None
    except rules_loader.TaxRulesMissing as exc:
        return None, None, str(exc)


def _tax_tags(a, line, entry, doc_ext, doc_type, fed, st, fixed, f1099):
    dr, cr = line["debit_cents"], line["credit_cents"]
    year, month = int(entry["date"][:4]), entry["date"][:7]
    num, t = a["number"], a["type"]
    tags = []

    def add(form, where, label, amount, note=""):
        tags.append({"form": form, "line": where, "label": label, "amount_cents": amount, "amount": _money(amount),
                     "period": str(year) if form.startswith(("Schedule", "Form", "1099")) else month, "note": note})

    if fed and a["tax_line"] and t in ("revenue", "cogs", "expense"):
        labels = fed["schedule_c"]["lines"]
        amount = (cr - dr) if (t == "revenue" and a["subtype"] != "contra_revenue") else (dr - cr)
        if a["tax_line"] == "36":
            add("Schedule C", "Part III line 36 → 42 → line 4", "Purchases (cost of goods sold)", amount)
        else:
            note = f"{fed['schedule_c']['meals_deductible_pct']}% deductible" if a["tax_line"] == "24b" else ""
            add("Schedule C", f"line {a['tax_line']}", labels.get(a["tax_line"], ""), amount, note)
    elif fed and t in ("revenue", "cogs", "expense"):
        add("Schedule C", "not mapped", "This account has no Schedule C line", dr - cr,
            "listed under 'Accounts with activity but no Schedule C line' on the Taxes screen")
    if st:
        rate = st["rates"]["meals"]
        if num in st["taxable_revenue_accounts"]:
            add("OS-114", "Gross receipts", "Gross receipts", cr - dr)
            add("OS-114", f"Taxable gross receipts at {rate}%", "Taxable gross receipts", cr - dr)
        elif num in st["marketplace_revenue_accounts"]:
            add("OS-114", "Gross receipts", "Gross receipts", cr - dr)
            add("OS-114", "Less: marketplace facilitator sales", "Tax collected and remitted by the platform", cr - dr)
        elif num in st["deduction_accounts"]:
            add("OS-114", "Less: discounts, comps and refunds", "Deduction", dr - cr)
        elif num == st["liability_account"]:
            if cr:
                add("OS-114", f"Tax collected per the books ({num})", "Compared with the tax due", cr)
            elif any(l["account"] in ("1010", "1000") for l in entry["lines"]):
                add("OS-114", "Payment to CT DRS", "Pays a return; not a line on it", dr)
            else:
                add("OS-114", f"Tax collected per the books ({num})", "Reduces tax collected", -dr)
    if line["line_id"] in fixed:
        f = fixed[line["line_id"]]
        add("Form 4562", "Fixed asset register", f["description"], f["cost"],
            f"placed in service {f['placed_in_service']}; depreciated monthly (6600 / 1590)")
    method = ((doc_ext or {}).get("payment") or {}).get("method")
    if (num in SERVICE_ACCOUNTS_1099 and dr > 0 and line["party"] and doc_type != "credit_card_statement"
            and method != "card"):
        row = f1099.get(line["party"])
        note = (f"{year} total {_money(row['total'])}, threshold {_money(row['threshold'])}"
                if row else "paid by check or bank transfer")
        add("1099-NEC", "Candidate payee", line["party"], dr, note)
    return tags


# ------------------------------------------------------------------ the whole document

def tag_document(conn, document_id):
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if not doc:
        raise ValueError(f"No document #{document_id}")
    accts = account_map(conn)
    ext = json.loads(doc["extraction"]) if doc["extraction"] else {}
    flags = json.loads(doc["flags"]) if doc["flags"] else []
    notes = []
    proposal = None

    entries = _posted_entries(conn, "document_id=?", (document_id,))
    source = "posted" if entries else "none"
    if doc["status"] == "support":
        booked, from_notes = _booked_from(conn, document_id)
        if booked:
            entries, source = booked, "booked_from"
            notes.append("Support only: this document's money was booked from " + "; ".join(from_notes)
                         + ". Nothing was posted from it, so nothing is counted twice.")
    if not any(e["active"] for e in entries) and doc["status"] in ("extracted", "proposed", "needs_review"):
        prop_entries, proposal = _proposal_entries(conn, document_id)
        if prop_entries:
            entries = [e for e in entries if not e["active"]] + prop_entries
            source = "proposal"
            notes.append(f"Not posted yet: these are the open proposal (v{proposal['version']}). "
                         "They take effect only when the proposal is accepted on the Review screen.")
    if doc["status"] == "rejected":
        notes.append(f"Rejected: {doc['reject_reason'] or ''}. It has no effect on the books.")
    if source == "none" and doc["status"] != "rejected":
        notes.append("Nothing booked from this document yet.")

    years = sorted({int(e["date"][:4]) for e in entries if e["date"][:4].isdigit()})
    rules = {y: _rules(y) for y in years}
    fixed = {r["journal_line_id"]: dict(r) for r in conn.execute("SELECT * FROM fixed_assets")} if entries else {}
    f1099 = {}
    if any(l["account"] in SERVICE_ACCOUNTS_1099 for e in entries for l in e["lines"]):
        from tax.federal import form_1099_candidates
        for y in years:
            if rules[y][0]:
                c = form_1099_candidates(conn, y)
                f1099.update({r["payee"]: dict(r, threshold=c["threshold"]) for r in c["rows"]})
    totals = _Totals(conn)
    tax_errors, profit = set(), {}
    for e in entries:
        fed, st, err = rules.get(int(e["date"][:4]), (None, None, None)) if e["date"][:4].isdigit() else (None, None, None)
        if err:
            tax_errors.add(err)
        for l in e["lines"]:
            a = accts.get(l["account"])
            l["name"] = a["name"] if a else "(unknown account)"
            l["debit"], l["credit"] = _money(l["debit_cents"]), _money(l["credit_cents"])
            l["settlement"] = _settlement(conn, e["document_id"], l) if e["posted"] and e["active"] else None
            if not a or not e["active"] or not e["date"]:
                l["statements"], l["tax"] = [], []
                continue
            l["statements"] = _statement_tags(a, l, e["date"], totals)
            l["tax"] = _tax_tags(a, l, e, ext, doc["doc_type"], fed, st, fixed, f1099)
            if a["type"] in ("revenue", "cogs", "expense") and a["tax_line"]:
                amount = l["credit_cents"] - l["debit_cents"]
                if a["tax_line"] == "24b" and fed:
                    amount = amount * fed["schedule_c"]["meals_deductible_pct"] // 100
                y = e["date"][:4]
                profit[y] = profit.get(y, 0) + amount

    doc_tax = []
    if any("use tax" in f.get("message", "") for f in flags) and doc["status"] == "posted":
        doc_tax.append({"form": "OS-114", "line": "Use tax on untaxed purchases", "label": "Taxable items bought "
                        "without CT sales tax", "amount_cents": None, "amount": None,
                        "period": (doc["doc_date"] or "")[:7], "note": "computed on the OS-114 worksheet"})
    for y, amount in sorted(profit.items()):
        doc_tax.append({"form": "Schedule C / SE", "line": "line 31 → Schedule SE",
                        "label": "Changes net profit (and so self-employment tax)", "amount_cents": amount,
                        "amount": _money(amount), "period": y, "note": "SE tax is figured on 92.35% of line 31"})
    return {"document_id": document_id, "status": doc["status"], "source": source, "notes": notes,
            "entries": entries, "document_tax": doc_tax, "tax_errors": sorted(tax_errors),
            "proposal": None if not proposal else {"version": proposal["version"], "source": proposal["source"],
                                                   "confidence": proposal["confidence"]}}

"""Federal worksheets for a single-member LLC (plan §10.3, decision D1):
Schedule C (with Part III), Schedule SE, Form 4562 figures, the 1099-NEC list
and a 1040-ES estimate helper. Every line lists the accounts it came from.
"""

import json
from decimal import ROUND_HALF_UP, Decimal

from books.coa import account_map
from books.money import parse_cents
from books.post import balances
from reports.statements import day_before
from tax import rules_loader


def _round(d):
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def schedule_c(conn, year):
    rules = rules_loader.load(year, "federal")
    labels = rules["schedule_c"]["lines"]
    start, end = f"{year}-01-01", f"{year}-12-31"
    accts = account_map(conn)
    bals = balances(conn, start=start, end=end)
    by_line, sources = {}, {}
    for num, a in accts.items():
        if a["tax_line"] and a["type"] in ("revenue", "cogs", "expense"):
            amount = bals.get(num, 0) * (-1 if a["type"] == "revenue" else 1)
            if a["subtype"] == "contra_revenue":
                amount = bals.get(num, 0)
            if amount:
                by_line[a["tax_line"]] = by_line.get(a["tax_line"], 0) + amount
                sources.setdefault(a["tax_line"], []).append(f"{num} {a['name']}")
    meals_pct = rules["schedule_c"]["meals_deductible_pct"]
    if "24b" in by_line:
        by_line["24b"] = by_line["24b"] * meals_pct // 100
        sources["24b"] = [s + f" x {meals_pct}%" for s in sources["24b"]]

    # Part III - cost of goods sold. The books keep purchases in the COGS accounts and
    # adjust to the counted inventory (1200) at year end.
    inv_begin = balances(conn, end=day_before(start)).get("1200", 0)
    inv_end = balances(conn, end=end).get("1200", 0)
    cogs = by_line.pop("36", 0)
    cogs_sources = sources.pop("36", [])
    purchases = cogs + inv_end - inv_begin
    part3 = {"35": inv_begin, "36": purchases, "37": 0, "38": 0, "39": 0,
             "40": inv_begin + purchases, "41": inv_end, "42": inv_begin + purchases - inv_end}

    L = {}
    L["1"] = by_line.pop("1", 0)
    L["2"] = by_line.pop("2", 0)
    L["3"] = L["1"] - L["2"]
    L["4"] = part3["42"]
    L["5"] = L["3"] - L["4"]
    L["6"] = 0
    L["7"] = L["5"] + L["6"]
    expense_lines = ["8", "9", "10", "11", "13", "15", "16b", "17", "20b", "21", "22", "23", "24b", "25", "26", "27a"]
    for k in expense_lines:
        L[k] = by_line.pop(k, 0)
    L["28"] = sum(L[k] for k in expense_lines)
    L["29"] = L["7"] - L["28"]
    L["30"] = 0
    L["31"] = L["29"] - L["30"]
    other_detail = [{"account": num, "name": a["name"], "amount": bals.get(num, 0)} for num, a in sorted(accts.items())
                    if a["tax_line"] == "27a" and bals.get(num, 0)]
    lines = [{"line": k, "label": labels.get(k, k), "amount": L[k],
              "source": ", ".join(sources.get(k, [])) or ("computed" if k in ("3", "5", "7", "28", "29", "31") else
                                                          ("Part III line 42" if k == "4" else ""))}
             for k in ["1", "2", "3", "4", "5", "6", "7"] + expense_lines + ["28", "29", "30", "31"]]
    questions = [
        "Business use of home (line 30): does the owner use part of the home regularly and only for the business?",
        "Vehicle (Part IV / line 9): date placed in service, business miles and total miles for the year.",
        "Were all wages paid to employees (not the owner)? A single-member LLC owner takes draws, not wages.",
        "Section 179 / bonus depreciation choices for this year's fixed assets (Form 4562).",
        "Health insurance premiums paid for the owner are not a Schedule C expense (they may be deductible on Form 1040).",
    ]
    unmapped = [f"{num} {a['name']}" for num, a in accts.items()
                if a["type"] in ("expense", "cogs", "revenue") and not a["tax_line"] and bals.get(num, 0)]
    return {"form": "Schedule C (Form 1040)", "year": year, "lines": lines, "L": L, "part3": part3,
            "part3_labels": rules["schedule_c"]["part_iii"], "cogs_sources": cogs_sources,
            "other_expenses": other_detail, "questions": questions, "unmapped": unmapped,
            "rules_version": rules.get("version"), "verified": rules.get("verified", False),
            "notes": rules.get("notes", [])}


def schedule_se(net_profit, year):
    rules = rules_loader.load(year, "federal")["self_employment_tax"]
    factor = Decimal(rules["net_earnings_factor"])
    net_earnings = _round(Decimal(net_profit) * factor)
    minimum = parse_cents(rules["minimum_net_earnings"])
    if net_earnings < minimum:
        return {"net_earnings": net_earnings, "se_tax": 0, "deduction": 0, "social_security": 0, "medicare": 0,
                "note": "Net earnings under $400: no self-employment tax."}
    base = parse_cents(rules["social_security_wage_base"])
    ss = _round(Decimal(min(net_earnings, base)) * Decimal(rules["social_security_rate"]) / 100)
    medicare = _round(Decimal(net_earnings) * Decimal(rules["medicare_rate"]) / 100)
    se_tax = ss + medicare
    return {"net_earnings": net_earnings, "social_security": ss, "medicare": medicare, "se_tax": se_tax,
            "deduction": _round(Decimal(se_tax) * Decimal(rules["deduction_share"])),
            "note": "Additional Medicare tax (0.9% over the threshold) is figured on the owner's Form 1040 if it applies."}


LIVES = {"1500": 7, "1510": 7, "1520": 5}


def form_4562_figures(conn, year):
    rows = [dict(r) for r in conn.execute("SELECT * FROM fixed_assets ORDER BY placed_in_service")]
    this_year = [r for r in rows if r["placed_in_service"].startswith(str(year))]
    return {"assets": rows, "placed_this_year": this_year, "cost_this_year": sum(r["cost"] for r in this_year),
            "book_depreciation": balances(conn, start=f"{year}-01-01", end=f"{year}-12-31").get("6600", 0),
            "note": "Section 179, bonus and MACRS choices are made by the CPA; the register gives cost, date and class."}


def monthly_depreciation(conn, month):
    """A suggested straight-line book depreciation entry for a month (plan §9, manual entry)."""
    total, detail = 0, []
    for a in conn.execute("SELECT * FROM fixed_assets"):
        start = a["placed_in_service"][:7]
        if start >= month:
            continue
        life = LIVES.get(a["account"], a["life_years"] or 7)
        amount = a["cost"] // (life * 12)
        if amount:
            total += amount
            detail.append({"asset": a["description"], "amount": amount})
    if not total:
        return None
    from reports.statements import month_ends
    end = dict((s[:7], e) for s, e in month_ends(int(month[:4])))[month]
    return {"date": end, "memo": f"Straight-line depreciation {month}",
            "lines": [{"account": "6600", "debit_cents": total, "memo": "; ".join(d["asset"] for d in detail)},
                      {"account": "1590", "credit_cents": total}], "detail": detail}


# Service accounts whose non-card payments may need a 1099-NEC.
SERVICE_ACCOUNTS_1099 = {"6300", "6410", "6520", "6400", "6900"}


def form_1099_candidates(conn, year):
    """Payees paid by check / bank transfer (not card) for services, with totals (plan §10.3)."""
    rules = rules_loader.load(year, "federal")
    threshold = parse_cents(rules["form_1099_nec_threshold"])
    services = SERVICE_ACCOUNTS_1099
    totals = {}
    for r in conn.execute(
            "SELECT l.party, l.account, l.debit, d.doc_type, d.extraction FROM journal_lines l "
            "JOIN journal_entries e ON e.id=l.entry_id JOIN documents d ON d.id=e.document_id "
            "WHERE e.date BETWEEN ? AND ? AND l.debit > 0", (f"{year}-01-01", f"{year}-12-31")):
        if r["account"] not in services or not r["party"]:
            continue
        ext = json.loads(r["extraction"])
        method = (ext.get("payment") or {}).get("method")
        if r["doc_type"] == "credit_card_statement" or method == "card":
            continue
        totals[r["party"]] = totals.get(r["party"], 0) + r["debit"]
    vendors = {v["name"]: v for v in conn.execute("SELECT * FROM vendors")}
    return {"threshold": threshold, "rows": [
        {"payee": p, "total": t, "over_threshold": t >= threshold,
         "marked_1099": bool(vendors.get(p, {}) and vendors[p]["is_1099"])}
        for p, t in sorted(totals.items(), key=lambda x: -x[1])],
        "note": "Card and third-party network payments are reported by the card processor (Form 1099-K), not by the shop. "
                "Payments to corporations are usually exempt. Mark the payees that need a 1099-NEC."}


def estimates(net_profit_ytd, months, year):
    """A simple 1040-ES helper: annualise the profit to date and show the SE tax part.
    Income tax depends on the owner's whole return and is left to the owner/CPA."""
    if months <= 0:
        return None
    annual = net_profit_ytd * 12 // months
    se = schedule_se(annual, year)
    return {"annualized_profit": annual, "se_tax": se["se_tax"], "per_quarter_se": se["se_tax"] // 4,
            "note": "Add the owner's income tax on the profit (from their whole Form 1040) to find each payment."}

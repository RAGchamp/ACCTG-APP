"""Financial statements from the posted GL (plan §9). Pure code, never Claude (P4).

All functions take a connection and ISO dates and return plain dicts of
integer cents, which the templates, the CSV export, the tax worksheets and the
test-data evaluator all use. Built-in checks:

- the trial balance nets to zero;
- the balance sheet balances (assets = liabilities + equity incl. profit);
- the cash flow statement's ending cash equals the GL cash accounts.
"""

from datetime import date, timedelta

from books import db
from books.coa import account_map, normal_sign
from books.post import balances

REVENUE_ORDER = ("revenue",)


def day_before(iso):
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def fiscal_start(conn, as_of):
    return f"{as_of[:4]}-01-01"


def _rows(accts, bals, types, sign=None):
    out = []
    for num, a in sorted(accts.items()):
        if a["type"] in types:
            amount = bals.get(num, 0) * (sign if sign is not None else normal_sign(a["type"]))
            if amount or a["active"]:
                out.append({"account": num, "name": a["name"], "amount": amount, "subtype": a["subtype"]})
    return out


def trial_balance(conn, end):
    accts = account_map(conn)
    bals = balances(conn, end=end)
    rows = []
    for num, a in sorted(accts.items()):
        b = bals.get(num, 0)
        if b:
            rows.append({"account": num, "name": a["name"], "type": a["type"],
                         "debit": b if b > 0 else 0, "credit": -b if b < 0 else 0})
    total_d, total_c = sum(r["debit"] for r in rows), sum(r["credit"] for r in rows)
    return {"as_of": end, "rows": rows, "total_debit": total_d, "total_credit": total_c,
            "balanced": total_d == total_c}


def income_statement(conn, start, end):
    accts = account_map(conn)
    bals = balances(conn, start=start, end=end)
    revenue = [r for r in _rows(accts, bals, ("revenue",)) if r["subtype"] != "contra_revenue"]
    contra = [dict(r, amount=-r["amount"]) for r in _rows(accts, bals, ("revenue",)) if r["subtype"] == "contra_revenue"]
    gross_sales = sum(r["amount"] for r in revenue)
    deductions = sum(r["amount"] for r in contra)          # positive = reduces sales
    net_sales = gross_sales - deductions
    cogs = _rows(accts, bals, ("cogs",))
    total_cogs = sum(r["amount"] for r in cogs)
    gross_profit = net_sales - total_cogs
    expenses = _rows(accts, bals, ("expense",))
    total_exp = sum(r["amount"] for r in expenses)
    net_income = gross_profit - total_exp
    food = sum(r["amount"] for r in cogs if r["account"] in ("5010", "5020"))
    return {"start": start, "end": end, "revenue": revenue, "deductions": contra, "gross_sales": gross_sales,
            "total_deductions": deductions, "net_sales": net_sales, "cogs": cogs, "total_cogs": total_cogs,
            "gross_profit": gross_profit, "expenses": expenses, "total_expenses": total_exp,
            "net_income": net_income,
            "food_cost_pct": round(100 * food / net_sales, 1) if net_sales else None,
            "cogs_pct": round(100 * total_cogs / net_sales, 1) if net_sales else None}


def net_income(conn, start, end):
    if end < start:
        return 0
    return income_statement(conn, start, end)["net_income"]


def balance_sheet(conn, as_of):
    accts = account_map(conn)
    bals = balances(conn, end=as_of)
    fy = fiscal_start(conn, as_of)
    assets = _rows(accts, bals, ("asset",), sign=1)
    liabilities = _rows(accts, bals, ("liability",), sign=-1)
    equity = _rows(accts, bals, ("equity",), sign=-1)
    prior_profit = net_income(conn, "0000-01-01", day_before(fy))
    current_profit = net_income(conn, fy, as_of)
    for r in equity:
        if r["account"] == "3900":
            r["amount"] += prior_profit
    total_assets = sum(r["amount"] for r in assets)
    total_liab = sum(r["amount"] for r in liabilities)
    total_equity = sum(r["amount"] for r in equity) + current_profit
    return {"as_of": as_of, "assets": [r for r in assets if r["amount"]],
            "current_assets": [r for r in assets if r["amount"] and r["subtype"] not in ("fixed_asset", "contra_asset")],
            "fixed_assets": [r for r in assets if r["amount"] and r["subtype"] in ("fixed_asset", "contra_asset")],
            "liabilities": [r for r in liabilities if r["amount"]],
            "equity": [r for r in equity if r["amount"]], "current_profit": current_profit,
            "total_assets": total_assets, "total_liabilities": total_liab, "total_equity": total_equity,
            "balanced": total_assets == total_liab + total_equity}


def cash_accounts(conn):
    return [n for n, a in account_map(conn).items() if a["cash_flow_class"] == "cash"]


def cash_flow(conn, start, end):
    """Indirect method (plan §9)."""
    accts = account_map(conn)
    before = balances(conn, end=day_before(start))
    after = balances(conn, end=end)
    ni = net_income(conn, start, end)
    sections = {"operating": [], "investing": [], "financing": [], "opening": []}
    for num, a in sorted(accts.items()):
        if a["type"] not in ("asset", "liability", "equity"):
            continue
        cls = a["cash_flow_class"]
        if cls == "cash":
            continue
        change = after.get(num, 0) - before.get(num, 0)
        effect = -change
        if not effect:
            continue
        if num == "1590":
            label = "Depreciation (non-cash)"
        elif a["type"] == "asset":
            label = f"{'Increase' if change > 0 else 'Decrease'} in {a['name']}"
        elif a["subtype"] in ("capital",):
            label = "Owner contributions" if effect > 0 else "Owner capital withdrawn"
        elif a["subtype"] == "draws":
            label = "Owner draws"
        elif a["subtype"] == "loan":
            label = "Loan proceeds" if effect > 0 else "Loan principal repaid"
        else:
            label = f"{'Increase' if effect > 0 else 'Decrease'} in {a['name']}"
        key = cls if cls in sections else "opening"
        sections[key].append({"account": num, "label": label, "amount": effect})
    operating = ni + sum(r["amount"] for r in sections["operating"])
    investing = sum(r["amount"] for r in sections["investing"])
    financing = sum(r["amount"] for r in sections["financing"])
    opening = sum(r["amount"] for r in sections["opening"])
    cash = cash_accounts(conn)
    begin_cash = sum(before.get(n, 0) for n in cash)
    end_cash = sum(after.get(n, 0) for n in cash)
    net_change = operating + investing + financing + opening
    return {"start": start, "end": end, "net_income": ni, "operating": sections["operating"],
            "investing": sections["investing"], "financing": sections["financing"],
            "opening": sections["opening"], "total_operating": operating, "total_investing": investing,
            "total_financing": financing, "total_opening": opening, "net_change": net_change,
            "begin_cash": begin_cash, "end_cash": end_cash, "ties": begin_cash + net_change == end_cash}


def equity_statement(conn, start, end):
    before = balances(conn, end=day_before(start))
    after = balances(conn, end=end)
    period = balances(conn, start=start, end=end)
    prior_profit = net_income(conn, "0000-01-01", day_before(start))
    opening = -sum(before.get(n, 0) for n in ("3000", "3100", "3200", "3900")) + prior_profit
    contributions = -period.get("3000", 0)
    opening_bal = -period.get("3200", 0)
    draws = period.get("3100", 0)
    ni = net_income(conn, start, end)
    closing = opening + contributions + opening_bal + ni - draws
    check = -sum(after.get(n, 0) for n in ("3000", "3100", "3200", "3900")) + prior_profit + ni
    return {"start": start, "end": end, "opening": opening, "opening_balances": opening_bal,
            "contributions": contributions, "net_income": ni, "draws": draws, "closing": closing,
            "ties": closing == check}


def general_ledger(conn, start, end, account=None):
    accts = account_map(conn)
    before = balances(conn, end=day_before(start))
    sql = ("SELECT l.*, e.date, e.memo AS entry_memo, e.document_id, e.kind, e.id AS entry_id, e.reverses "
           "FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE e.date BETWEEN ? AND ?")
    args = [start, end]
    if account:
        sql += " AND l.account=?"
        args.append(account)
    sql += " ORDER BY l.account, e.date, e.id, l.line_no"
    out = {}
    for r in conn.execute(sql, args):
        acct = out.setdefault(r["account"], {"account": r["account"], "name": accts[r["account"]]["name"],
                                             "opening": before.get(r["account"], 0), "lines": []})
        running = (acct["lines"][-1]["balance"] if acct["lines"] else acct["opening"]) + r["debit"] - r["credit"]
        acct["lines"].append(dict(r, balance=running))
    for a in out.values():
        a["closing"] = a["lines"][-1]["balance"] if a["lines"] else a["opening"]
    return [out[k] for k in sorted(out)]


def month_ends(year):
    out = []
    for m in range(1, 13):
        start = date(year, m, 1)
        nxt = date(year + (m == 12), m % 12 + 1, 1)
        out.append((start.isoformat(), (nxt - timedelta(days=1)).isoformat()))
    return out


def summary(conn, start, end):
    """Everything the evaluator and the Reports screen compare, in one dict."""
    return {"trial_balance": trial_balance(conn, end), "income_statement": income_statement(conn, start, end),
            "balance_sheet": balance_sheet(conn, end), "cash_flow": cash_flow(conn, start, end),
            "equity": equity_statement(conn, start, end)}


def books_period(conn):
    p = db.profile(conn)
    return p["books_start"], f"{p['tax_year']}-12-31"

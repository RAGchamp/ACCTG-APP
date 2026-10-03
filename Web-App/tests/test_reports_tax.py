"""Statements and tax worksheets from a small hand-built ledger (plan §9, §10, §14)."""

from books import post
from reports import statements
from tax import federal, sales_tax_ct


def E(conn, date, *lines, kind="document"):
    post.post_entry(conn, {"date": date, "memo": "t", "lines": [
        {"account": a, "debit": d, "credit": c, "memo": "m"} for a, d, c in lines]}, kind=kind)


def book(conn):
    E(conn, "2026-01-01", ("1010", "10000", "0"), ("1500", "5000", "0"), ("2500", "0", "4000"), ("3200", "0", "11000"),
      kind="opening")
    # A Square day: 1000 sales, 73.50 tax, 50 tips, 200 cash, fees 23.84
    E(conn, "2026-01-05", ("1000", "200", "0"), ("1100", "899.66", "0"), ("6200", "23.84", "0"),
      ("4000", "0", "1000"), ("2200", "0", "73.50"), ("2210", "0", "50"))
    E(conn, "2026-01-06", ("1010", "899.66", "0"), ("1100", "0", "899.66"))
    E(conn, "2026-01-07", ("5010", "300", "0"), ("2150", "0", "300"))
    E(conn, "2026-01-08", ("2150", "300", "0"), ("2100", "0", "300"))
    E(conn, "2026-01-10", ("6100", "2000", "0"), ("1010", "0", "2000"))
    E(conn, "2026-01-12", ("2500", "400", "0"), ("6710", "50", "0"), ("1010", "0", "450"))
    E(conn, "2026-01-20", ("3100", "500", "0"), ("1010", "0", "500"))
    E(conn, "2026-01-31", ("6600", "60", "0"), ("1590", "0", "60"))


def test_statements_tie(biz):
    with biz.session() as conn:
        book(conn)
        s = statements.summary(conn, "2026-01-01", "2026-01-31")
    inc = s["income_statement"]
    assert inc["net_sales"] == 100000 and inc["total_cogs"] == 30000
    assert inc["net_income"] == 100000 - 30000 - (2384 + 200000 + 5000 + 6000)
    assert s["trial_balance"]["balanced"]
    assert s["balance_sheet"]["balanced"]
    cf = s["cash_flow"]
    assert cf["ties"] and cf["end_cash"] == 1000000 + 20000 + 89966 - 200000 - 45000 - 50000
    assert s["equity"]["ties"]


def test_os114_and_schedule_c(biz):
    with biz.session() as conn:
        book(conn)
        ws = sales_tax_ct.worksheet(conn, "2026-01")
        sch = federal.schedule_c(conn, 2026)
    assert ws["taxable"] == 100000 and ws["tax_due"] == 7350 and ws["collected_per_books"] == 7350
    assert ws["difference"] == 0
    L = sch["L"]
    assert L["1"] == 100000 and L["4"] == 30000 and L["20b"] == 200000 and L["16b"] == 5000 and L["13"] == 6000
    assert L["31"] == L["7"] - L["28"]
    se = federal.schedule_se(L["31"], 2026)
    assert se["se_tax"] == 0          # a loss: no self-employment tax
    se2 = federal.schedule_se(5_000_000, 2026)
    assert se2["net_earnings"] == 4_617_500 and se2["se_tax"] == round(4_617_500 * 0.153)

"""Extraction checks, CSV import and the proposal checks (plan §5.3, §6.1)."""

import json

from books import match, post
from books.propose import build_prompt, check_proposal
from ingest import checks, csv_import


def receipt(**kw):
    d = {"doc_type": "purchase_receipt", "vendor": "Restaurant Depot", "date": "2026-01-14",
         "lines": [{"description": "MOZZ", "amount": "84.98", "taxable": False},
                   {"description": "BOX", "amount": "32.50", "taxable": True}],
         "subtotal": "117.48", "tax": "2.06", "tip": None, "total": "119.54",
         "payment": {"method": "card", "card_last4": "4417", "check_no": None}}
    d.update(kw)
    return d


def test_receipt_ties_and_flags():
    assert [f for f in checks.check(receipt()) if f["level"] == "error"] == []
    bad = checks.check(receipt(total="119.45"))
    assert any("subtotal + tax + tip vs total" in f["message"] for f in bad)
    unread = checks.check(receipt(total="11[?].54"))
    assert any("unreadable" in f["message"] for f in unread)
    rate = checks.check(receipt(tax="3.00", total="120.48"))
    assert any("unusual sales tax rate" in f["message"] for f in rate)
    use = checks.check(receipt(tax="0.00", total="117.48"))
    assert any("use tax" in f["message"] for f in use)


def test_statement_and_check_checks():
    st = {"doc_type": "bank_statement", "period_start": "2026-01-01", "period_end": "2026-01-31",
          "opening_balance": "100.00", "closing_balance": "150.00",
          "transactions": [{"date": "2026-01-05", "amount": "60.00"}, {"date": "2026-01-06", "amount": "-10.00"}]}
    assert not [f for f in checks.check(st) if f["level"] == "error"]
    st["closing_balance"] = "151.00"
    assert any("closing balance" in f["message"] for f in checks.check(st))
    assert checks.words_to_cents("Two thousand eight hundred and 00/100") == 280000
    assert checks.words_to_cents("One hundred fifty-eight and 25/100") == 15825
    chk = {"doc_type": "check", "direction": "outgoing", "date": "2026-01-09", "amount_numeric": "185.00",
           "amount_words": "One hundred fifty-eight and 00/100"}
    assert any("written amount" in f["message"] for f in checks.check(chk))


def test_pos_and_platform_ties():
    pos = json.loads(open("schemas/pos_report.json", encoding="utf-8").read())
    assert not [f for f in checks.check(pos) if f["level"] == "error"]
    plat = json.loads(open("schemas/platform_statement.json", encoding="utf-8").read())
    assert not [f for f in checks.check(plat) if f["level"] == "error"]
    plat["payout"] = "1600.00"
    assert any("payout" in f["message"] for f in checks.check(plat))


def test_csv_import_formats():
    square = ("# SYNTHETIC TEST DATA\nDate,In-store Sales,Delivery Sales,Delivery Fees,Gross Sales,Discounts,Comps,Refunds,"
              "Net Sales,Tax,Tips,Total Collected,Card,Cash,Fees,Net Deposit\n"
              "01/05/2026,1000.00,0.00,0.00,1000.00,0.00,0.00,0.00,1000.00,73.50,20.00,1093.50,893.50,200.00,24.57,868.93\n")
    docs = csv_import.parse(square)
    assert docs[0]["doc_type"] == "pos_report" and docs[0]["period_end"] == "2026-01-05"
    assert not [f for f in checks.check(docs[0]) if f["level"] == "error"]
    bank = ("# Account: ...0931\n# Opening balance: 100.00\nDate,Description,Amount,Check Number,Balance\n"
            "01/02/2026,RENT,-50.00,,50.00\n01/03/2026,CHECK 1041,-20.00,1041,30.00\n")
    b = csv_import.parse(bank)[0]
    assert b["doc_type"] == "bank_statement" and b["closing_balance"] == "30.00"
    assert b["transactions"][1]["check_no"] == "1041" and b["account_last4"] == "0931"


def _doc(conn, biz, ext, doc_type):
    conn.execute("INSERT INTO files(sha256, filename, stored_path, kind, source, status, added_at) "
                 "VALUES ('x'||hex(randomblob(8)), 'f', 'p', 'pdf', 'upload', 'extracted', 'now')")
    fid = conn.execute("SELECT MAX(id) FROM files").fetchone()[0]
    conn.execute("INSERT INTO documents(file_id, pages, doc_type, party, extraction, flags, status, created_at) "
                 "VALUES (?, '[1]', ?, ?, ?, '[]', 'extracted', 'now')",
                 (fid, doc_type, ext.get("vendor"), json.dumps(ext)))
    return dict(conn.execute("SELECT * FROM documents WHERE id=(SELECT MAX(id) FROM documents)").fetchone())


def test_proposal_checks_catch_bad_entries(biz):
    with biz.session() as conn:
        doc = _doc(conn, biz, receipt(), "purchase_receipt")
        good = {"entries": [{"ref": "document", "date": "2026-01-14", "lines": [
            {"account": "5010", "debit": "84.98"}, {"account": "5030", "debit": "34.56"},
            {"account": "2150", "credit": "119.54"}]}]}
        assert check_proposal(conn, doc, good, []) == []
        short = {"entries": [{"ref": "document", "date": "2026-01-14", "lines": [
            {"account": "5010", "debit": "84.98"}, {"account": "2150", "credit": "84.98"}]}]}
        assert any("receipt total" in e for e in check_proposal(conn, doc, short, []))
        unbalanced = {"entries": [{"date": "2026-01-14", "lines": [
            {"account": "5010", "debit": "119.54"}, {"account": "2150", "credit": "119.00"}]}]}
        assert any("debits" in e for e in check_proposal(conn, doc, unbalanced, []))
        assert any("support_only without a match hint" in e
                   for e in check_proposal(conn, doc, {"support_only": True, "entries": []}, []))


def test_card_statement_must_settle_the_matched_receipt(biz):
    """The double-counting rule (plan §4.2): a statement line for a booked receipt debits 2150."""
    with biz.session() as conn:
        rdoc = _doc(conn, biz, receipt(), "purchase_receipt")
        post.post_entry(conn, {"date": "2026-01-14", "memo": "RD", "lines": [
            {"account": "5010", "debit": "119.54", "party": "Restaurant Depot"},
            {"account": "2150", "credit": "119.54", "party": "Restaurant Depot"}]}, document_id=rdoc["id"])
        st = {"doc_type": "credit_card_statement", "card_last4": "4417", "period_start": "2026-01-01",
              "period_end": "2026-01-31", "opening_balance": "0.00", "closing_balance": "119.54",
              "transactions": [{"date": "2026-01-15", "description": "RESTAURANT DEPOT #312 HARTFORD CT",
                                "amount": "119.54", "kind": "purchase"}]}
        sdoc = _doc(conn, biz, st, "credit_card_statement")
        hints = match.hints(conn, sdoc)
        assert hints and hints[0]["clears_account"] == "2150" and hints[0]["ref"] == "txn 1"
        double = {"entries": [{"ref": "txn 1", "date": "2026-01-15", "lines": [
            {"account": "5010", "debit": "119.54"}, {"account": "2100", "credit": "119.54"}]}]}
        errs = check_proposal(conn, sdoc, double, hints)
        assert any("match hint" in e for e in errs), errs
        right = {"entries": [{"ref": "txn 1", "date": "2026-01-15", "lines": [
            {"account": "2150", "debit": "119.54"}, {"account": "2100", "credit": "119.54"}]}]}
        assert check_proposal(conn, sdoc, right, hints) == []
        prompt = build_prompt(conn, sdoc, hints)
        assert "MATCH HINTS" in prompt and "debit 2150" in prompt and "5010 | COGS - Food purchases" in prompt


def test_vendor_rule_reaches_the_next_prompt(biz):
    from books import review
    review.add_rule(biz, "CVS", "Book CVS purchases to 6310.", "6310")
    with biz.session() as conn:
        doc = _doc(conn, biz, receipt(vendor="CVS"), "purchase_receipt")
        assert "Book CVS purchases to 6310" in build_prompt(conn, doc, [])
        other = _doc(conn, biz, receipt(vendor="Sysco Connecticut"), "purchase_receipt")
        assert "Book CVS purchases" not in build_prompt(conn, other, [])

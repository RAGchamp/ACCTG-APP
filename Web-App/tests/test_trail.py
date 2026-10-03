"""The ledger trail: every change to the ledger is recorded, append-only, with its origin."""

import sqlite3

import pytest

from books import business, db, post, trail
from test_web import client  # noqa: F401  (the app client fixture)


def _entry(date, *lines):
    return {"date": date, "memo": "t", "lines": [{"account": a, "debit": d, "credit": c} for a, d, c in lines]}


def test_post_reverse_close_reopen_are_recorded(biz):
    with biz.session() as conn:
        eid = post.post_entry(conn, _entry("2026-01-05", ("5010", "84.98", "0"), ("1000", "0", "84.98")),
                              kind="manual", origin="test: manual")
        rid = post.reverse_entry(conn, eid, origin="test: reverse", note="wrong vendor")
        post.close_month(conn, "2026-01", note="reconciled")
        post.reopen_month(conn, "2026-01", "late receipt")
        rows = trail.rows(conn)
    assert [r["action"] for r in rows] == ["reopen_month", "close_month", "reverse", "post"]
    posted, reversal = rows[3], rows[2]
    assert posted["entry_id"] == eid and posted["origin"] == "test: manual" and posted["total_cents"] == 8498
    assert {l["account"] for l in posted["lines"]} == {"5010", "1000"}
    assert reversal["entry_id"] == rid and reversal["reverses"] == eid and reversal["note"] == "wrong vendor"
    assert rows[0]["period"] == "2026-01" and rows[0]["note"] == "late receipt"
    with biz.session() as conn:
        assert trail.summary(conn)["counts"] == {"post": 1, "reverse": 1, "close_month": 1, "reopen_month": 1}
        assert len(trail.rows(conn, action="reverse")) == 1 and len(trail.rows(conn, q="late receipt")) == 1


def test_trail_is_append_only(biz):
    with biz.session() as conn:
        post.post_entry(conn, _entry("2026-01-05", ("5010", "1", "0"), ("1000", "0", "1")))
    with pytest.raises(sqlite3.DatabaseError):
        with biz.session() as conn:
            conn.execute("UPDATE ledger_trail SET memo='x'")
    with pytest.raises(sqlite3.DatabaseError):
        with biz.session() as conn:
            conn.execute("DELETE FROM ledger_trail")


def test_backfill_for_books_made_before_the_trail(biz):
    with biz.session() as conn:
        eid = post.post_entry(conn, _entry("2026-01-05", ("5010", "2", "0"), ("1000", "0", "2")))
        post.reverse_entry(conn, eid)
        post.close_month(conn, "2026-01")
        # An old book: no trail table at all.
        conn.executescript("DROP TRIGGER no_delete_trail; DROP TRIGGER no_update_trail; DROP TABLE ledger_trail;")
    db._READY.discard(str(biz.db_path))
    business.sandbox(biz.name)                      # loading the books upgrades them
    with biz.session() as conn:
        rows = trail.rows(conn)
    assert [r["action"] for r in rows] == ["close_month", "reverse", "post"]
    assert all("back-filled" in r["origin"] for r in rows)


def test_page_link_csv_and_origins(client):  # noqa: F811
    assert client.post("/api/setup/opening", json={"balances": {"1010": "1000.00"}}).json["ok"]
    eid = client.post("/api/entries/manual", json={"date": "2026-02-01", "memo": "rent", "lines": [
        {"account": "6100", "debit": "10"}, {"account": "1010", "credit": "10"}]}).json["entry"]
    assert client.post(f"/api/entries/{eid}/reverse", json={}).json["ok"]
    assert b'href="/ledger-trail"' in client.get("/ledger").data
    page = client.get("/ledger-trail")
    assert page.status_code == 200 and page.data.count(b'<section class="sba-overview"') == 1
    for origin in (b"Setup: opening balances", b"Ledger: new manual journal entry", b"Ledger: Reverse button"):
        assert origin in page.data
    assert b"undoes #" in page.data
    csv = client.get("/ledger-trail?format=csv")
    assert csv.mimetype == "text/csv" and csv.data.count(b"\n") == 4      # header + 3 changes
    assert b"Reversed" in client.get("/ledger-trail?action=reverse").data


def test_reverse_a_reversal_reinstates_the_entry(client):  # noqa: F811
    from books import match
    assert client.post("/api/setup/opening", json={"balances": {"1010": "1000.00"}}).json["ok"]
    eid = client.post("/api/entries/manual", json={"date": "2026-02-01", "memo": "rent", "lines": [
        {"account": "6100", "debit": "10"}, {"account": "1010", "credit": "10"}]}).json["entry"]
    rid = client.post(f"/api/entries/{eid}/reverse", json={}).json["entry"]
    ledger = client.get("/ledger").data
    assert ledger.count(b">Reverse</button>") >= 2          # the reversal has its own Reverse button now
    r = client.post(f"/api/entries/{rid}/reverse", json={})
    assert r.json["ok"], r.json
    new_id = r.json["entry"]
    biz = business.active()
    with biz.session() as conn:
        new = conn.execute("SELECT * FROM journal_entries WHERE id=?", (new_id,)).fetchone()
        assert new["kind"] == "manual" and new["reverses"] == rid and new["date"] == "2026-02-01"
        assert post.balances(conn).get("6100") == 1000           # the rent counts once again
        assert post.is_reversed(conn, rid)
        last = trail.rows(conn)[0]
        assert last["action"] == "reinstate" and last["reverses"] == rid and "undo a reversal" in last["origin"]
        assert match.open_items(conn) == []
    again = client.post(f"/api/entries/{rid}/reverse", json={})
    assert not again.json["ok"] and "already" in again.json["error"]
    assert b"Reinstated" in client.get("/ledger-trail").data
    # The reinstated entry can itself be reversed like any entry.
    assert client.post(f"/api/entries/{new_id}/reverse", json={}).json["ok"]

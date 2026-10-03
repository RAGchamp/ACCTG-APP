"""Money and ledger invariants (plan §8, §14)."""

import random
import sqlite3

import pytest

from books import post
from books.money import allocate, fmt, parse_cents, pct_of, to_decimal_str


def test_parse_cents():
    assert parse_cents("$1,204.50") == 120450
    assert parse_cents("(12.00)") == -1200
    assert parse_cents("-") == 0
    assert parse_cents("12.5") == 1250
    assert parse_cents("4[?].10") is None
    assert parse_cents("25.00 CR") == -2500
    assert parse_cents(0.1) == 10
    assert parse_cents(3) == 300
    assert to_decimal_str(-1205) == "-12.05"
    assert fmt(-120450) == "($1,204.50)"
    assert pct_of(10000, "7.35") == 735
    assert sum(allocate(1001, [1, 1, 1])) == 1001


def _entry(date, *lines):
    return {"date": date, "memo": "t", "lines": [
        {"account": a, "debit": d, "credit": c} for a, d, c in lines]}


def test_post_and_reverse(biz):
    with biz.session() as conn:
        eid = post.post_entry(conn, _entry("2026-01-05", ("5010", "84.98", "0"), ("1000", "0", "84.98")))
        assert post.balances(conn)["5010"] == 8498
        post.reverse_entry(conn, eid)
        assert post.balances(conn)["5010"] == 0
        assert post.trial_balance_nets_to_zero(conn)
        with pytest.raises(post.PostingError):
            post.reverse_entry(conn, eid)


def test_unbalanced_and_bad_accounts_refused(biz):
    with biz.session() as conn:
        with pytest.raises(post.PostingError, match="debits"):
            post.post_entry(conn, _entry("2026-01-05", ("5010", "10", "0"), ("1000", "0", "9.99")))
        with pytest.raises(post.PostingError, match="does not exist"):
            post.post_entry(conn, _entry("2026-01-05", ("9999", "10", "0"), ("1000", "0", "10")))
        with pytest.raises(post.PostingError, match="before the books start"):
            post.post_entry(conn, _entry("2025-12-31", ("5010", "10", "0"), ("1000", "0", "10")))
        with pytest.raises(post.PostingError, match="needs a memo"):
            post.post_entry(conn, _entry("2026-01-05", ("6900", "10", "0"), ("1000", "0", "10")))


def test_closed_month_refuses_posting(biz):
    with biz.session() as conn:
        post.close_month(conn, "2026-01")
        with pytest.raises(post.PostingError, match="closed"):
            post.post_entry(conn, _entry("2026-01-20", ("5010", "10", "0"), ("1000", "0", "10")))
        post.reopen_month(conn, "2026-01", "late receipt")
        post.post_entry(conn, _entry("2026-01-20", ("5010", "10", "0"), ("1000", "0", "10")))


def test_posted_lines_are_append_only(biz):
    with biz.session() as conn:
        eid = post.post_entry(conn, _entry("2026-01-05", ("5010", "1", "0"), ("1000", "0", "1")))
    with pytest.raises(sqlite3.IntegrityError):
        with biz.session() as conn:
            conn.execute("UPDATE journal_lines SET debit=5 WHERE entry_id=?", (eid,))
    with pytest.raises(sqlite3.IntegrityError):
        with biz.session() as conn:
            conn.execute("DELETE FROM journal_entries WHERE id=?", (eid,))


def test_random_postings_keep_trial_balance_zero(biz):
    rng = random.Random(4)
    accts = ["1000", "1010", "5010", "6100", "4000", "2200", "2100", "3100"]
    with biz.session() as conn:
        for _ in range(200):
            amount = rng.randint(1, 500_00)
            a, b = rng.sample(accts, 2)
            eid = post.post_entry(conn, {"date": f"2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}", "memo": "r",
                                         "lines": [{"account": a, "debit_cents": amount},
                                                   {"account": b, "credit_cents": amount}]})
            if rng.random() < 0.1:
                post.reverse_entry(conn, eid)
        assert post.trial_balance_nets_to_zero(conn)


def test_fixed_asset_registered_over_threshold(biz):
    with biz.session() as conn:
        post.post_entry(conn, {"date": "2026-02-01", "memo": "Oven",
                               "lines": [{"account": "1500", "debit": "4200.00", "memo": "Deck oven"},
                                         {"account": "2150", "credit": "4200.00"}]})
        rows = conn.execute("SELECT * FROM fixed_assets").fetchall()
        assert len(rows) == 1 and rows[0]["cost"] == 420000

"""Review input documents (INFO\\DOC-REVIEW-REPROCESS-PLAN.md): tagging, feedback files,
un-posting for re-processing, and the screen's routes."""

import json
import re
from datetime import datetime

import pytest

import config
from books import business, feedback_files, match, review
from books.post import trial_balance_nets_to_zero
from reports.tagging import tag_document
from test_web import client, wait  # noqa: F401  (the app client fixture and the job waiter)


@pytest.fixture
def booked(client):  # noqa: F811
    """The card-month scenario loaded into sandbox 'dr1' and accepted in order, like the owner."""
    from testdata import generate
    generate.generate("card-month", "dr1", seed=2)
    business.set_active("live")                    # the screen must work without opening the sandbox
    r = client.post("/api/testdata/sandbox/dr1/load", json={})
    assert r.json["ok"], r.json
    wait(r.json["job"])
    business.set_active("live")
    biz = business.sandbox("dr1")
    with biz.session() as conn:
        docs = [d["id"] for d in conn.execute(
            "SELECT d.id FROM documents d JOIN files f ON f.id=d.file_id ORDER BY f.seq, d.part")]
    for d in docs:
        review.accept(biz, d, confirm_flags=True)
    return biz


def _doc(biz, doc_type, party=None):
    with biz.session() as conn:
        for d in conn.execute("SELECT * FROM documents WHERE doc_type=? ORDER BY id", (doc_type,)):
            if party is None or party.lower() in (d["party"] or "").lower():
                return d["id"]
    raise AssertionError(f"no {doc_type} {party}")


def _receipt_with_dependant(biz):
    with biz.session() as conn:
        for d in conn.execute("SELECT id FROM documents WHERE doc_type='purchase_receipt' AND status='posted'"):
            if review.dependants(conn, [d["id"]]):
                return d["id"]
    raise AssertionError("no receipt settled by a statement line")


# ------------------------------------------------------------------ tagging

def test_tagging_receipt_and_statement(booked):
    receipt = _receipt_with_dependant(booked)
    with booked.session() as conn:
        t = tag_document(conn, receipt)
        [statement] = review.dependants(conn, [receipt])
        ts = tag_document(conn, statement)
    assert t["source"] == "posted"
    lines = [l for e in t["entries"] for l in e["lines"]]
    cogs = [l for l in lines if l["account"] in ("5010", "5030")]
    assert cogs and all(any(x["form"] == "Schedule C" and x["line"].startswith("Part III") for x in l["tax"])
                        for l in cogs)
    assert all(l["statements"][0]["section"] == "Cost of goods sold" for l in cogs)
    clearing = [l for l in lines if l["account"] == "2150"]
    assert clearing and clearing[0]["settlement"]["kind"] == "settled_by"
    assert clearing[0]["settlement"]["document_id"] == statement
    assert any(l["settlement"] and l["settlement"]["kind"] == "settles" and l["settlement"]["document_id"] == receipt
               for e in ts["entries"] for l in e["lines"])
    assert any(x["form"] == "Schedule C / SE" for x in t["document_tax"])


# ------------------------------------------------------------------ feedback files

def test_feedback_file_name_and_content(booked):
    receipt = _doc(booked, "purchase_receipt")
    when = datetime(2026, 10, 2, 14, 35, 12)
    fb = {"categories": ["wrong_account"], "text": "x", "reprocess": {"action": "none"}}
    p1 = feedback_files.save(booked, receipt, fb, when)
    p2 = feedback_files.save(booked, receipt, fb, when)
    assert p1.parent == config.FEEDBACK_DIR
    assert re.fullmatch(r".+-20261002-143512\.json", p1.name) and p2.name == p1.name[:-5] + "-2.json"
    rec = json.loads(p1.read_text(encoding="utf-8"))
    for key in ("input_file", "extracted_data", "tagging", "feedback", "books", "document", "reprocess_result"):
        assert key in rec
    from pathlib import Path
    assert Path(rec["input_file"]["path"]).exists() and Path(rec["input_file"]["source_path"]).exists()
    assert rec["books"]["name"] == "dr1" and rec["tagging"]["entries"]
    assert feedback_files.safe_stem('a:b*c?.pdf') == "a_b_c_"


# ------------------------------------------------------------------ un-posting

def test_unpost_blocked_by_dependant_then_together(booked):
    receipt = _receipt_with_dependant(booked)
    with booked.session() as conn:
        [statement] = review.dependants(conn, [receipt])
        assert review.reprocess_impact(conn, receipt, "repropose")["blocked"]
        before = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
    from webcommon import UserError
    with pytest.raises(UserError):
        review.unpost_document(booked, receipt, "repropose")
    out = review.unpost_document(booked, receipt, "repropose", include_dependants=True)
    assert out["reversals"]
    with booked.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == before + len(out["reversals"])
        assert trial_balance_nets_to_zero(conn)
        assert {r["status"] for r in conn.execute("SELECT status FROM documents WHERE id IN (?,?)",
                                                   (receipt, statement))} == {"extracted"}
        assert not conn.execute("SELECT 1 FROM matches WHERE document_id IN (?,?)", (receipt, statement)).fetchone()
        assert not review.active_entries(conn, receipt) and not review.active_entries(conn, statement)
        assert not [i for i in match.open_items(conn) if i["document_id"] == receipt]   # reversed: not an open item


def test_closed_month_is_refused(booked, client):  # noqa: F811
    r = client.post("/api/business/switch", json={"name": "dr1"})
    assert client.post("/api/months/2026-01/close", json={"override": "test"}).json["ok"]
    receipt = _doc(booked, "purchase_receipt")
    with booked.session() as conn:
        imp = review.reprocess_impact(conn, receipt, "repropose", include_dependants=True)
    assert imp["blocked"] and "closed" in imp["blocked"]
    assert r.json["ok"]


def test_reextract_in_place_keeps_document_ids(booked):
    stmt = _doc(booked, "credit_card_statement")
    review.unpost_document(booked, stmt, "reextract", include_dependants=True)
    from ingest import extract
    from books import propose
    with booked.session() as conn:
        file_id, stored = conn.execute("SELECT f.id, f.stored_path FROM documents d JOIN files f ON f.id=d.file_id "
                                       "WHERE d.id=?", (stmt,)).fetchone()
    ids = extract.extract_file(booked, file_id, owner_note="txn 3 is 41.20", in_place=True)
    assert stmt in ids
    propose.propose(booked, stmt)
    with booked.session() as conn:
        assert conn.execute("SELECT status FROM documents WHERE id=?", (stmt,)).fetchone()[0] in ("proposed",
                                                                                                   "needs_review")
        assert conn.execute("SELECT COUNT(*) FROM proposals WHERE document_id=? AND status='superseded'",
                            (stmt,)).fetchone()[0] >= 1
    from pathlib import Path
    prompt = sorted((Path(stored).parent / "calls").glob("*-extract.prompt.txt"))[-1].read_text(encoding="utf-8")
    assert "NOTE FROM THE OWNER" in prompt and "txn 3 is 41.20" in prompt


# ------------------------------------------------------------------ the screen

def test_list_show_image_and_feedback_routes(booked, client):  # noqa: F811
    assert client.get("/doc-review").status_code == 200
    live = client.get("/api/doc-review/documents?books=live").json["documents"]
    sand = client.get("/api/doc-review/documents?books=sandbox&sandbox=all").json["documents"]
    both = client.get("/api/doc-review/documents?books=live,sandbox").json["documents"]
    assert live == [] and sand and len(both) == len(sand)
    assert {d["books"] for d in sand} == {"dr1"} and all(d["key"].startswith("dr1:") for d in sand)
    keys = "&".join(f"d={d['key']}" for d in sand)
    page = client.get(f"/doc-review/show?books=sandbox&{keys}")
    assert page.status_code == 200 and page.data.count(b'class="card docbox') == len(sand)
    assert b"Ledger tagging" in page.data and b"Tax documents" in page.data
    pdf = next(d for d in sand if d["filename"].endswith(".pdf"))
    img = client.get(f"/doc-review/dr1/files/{pdf['file_id']}/page/1.jpg")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    assert client.get("/doc-review/no-such/files/1/page/1.jpg").status_code == 404
    too_many = client.get("/doc-review/show?" + "&".join(f"d=dr1:{i}" for i in range(1, config.DOC_REVIEW_MAX + 3)))
    assert b"only the first" in too_many.data
    assert business.active_name() == "live"

    # Feedback without re-processing: a file, and the books unchanged.
    with booked.session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
    r = client.post(f"/api/doc-review/dr1/{pdf['id']}/feedback", json={"categories": ["other"], "text": "check it"})
    assert r.json["ok"] and (config.FEEDBACK_DIR / r.json["file"]).exists()
    with booked.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == n
    bad = client.post(f"/api/doc-review/dr1/{pdf['id']}/feedback", json={})
    assert not bad.json["ok"]


def test_corrected_figures_job_and_blocked(booked, client):  # noqa: F811
    receipt = _receipt_with_dependant(booked)
    with booked.session() as conn:
        ext = json.loads(conn.execute("SELECT extraction FROM documents WHERE id=?", (receipt,)).fetchone()[0])
    body = {"categories": ["figures_wrong"], "text": "fixed", "action": "corrected", "corrected_extraction": ext}
    r = client.post(f"/api/doc-review/dr1/{receipt}/feedback", json=body)
    assert r.json["ok"] and r.json.get("blocked") and not r.json.get("job")
    rec = json.loads((config.FEEDBACK_DIR / r.json["file"]).read_text(encoding="utf-8"))
    assert rec["reprocess_result"]["blocked"]
    r = client.post(f"/api/doc-review/dr1/{receipt}/feedback", json=dict(body, together=True))
    assert r.json["ok"] and r.json["job"], r.json
    result = wait(r.json["job"])
    assert result["ok"], result
    rec = json.loads((config.FEEDBACK_DIR / r.json["file"]).read_text(encoding="utf-8"))
    res = rec["reprocess_result"]
    assert res["ok"] and res["reversed_entries"] and res["new_proposal"]["document_status"] in ("proposed",
                                                                                                "needs_review")
    assert res["dependants_to_propose"]
    with booked.session() as conn:
        assert conn.execute("SELECT status FROM documents WHERE id=?", (receipt,)).fetchone()[0] != "posted"
    # Accepting again works like any proposal (P2: only the owner posts).
    review.accept(booked, receipt, confirm_flags=True)


def test_repropose_with_live_claude_override(booked, client, monkeypatch):  # noqa: F811
    import ai
    receipt = _receipt_with_dependant(booked)
    with booked.session() as conn:
        entries = json.loads(conn.execute("SELECT entries FROM proposals WHERE document_id=? AND status='accepted'",
                                          (receipt,)).fetchone()[0])
    calls = []

    def fake_claude(prompt, system, timeout, effort=None):
        calls.append(prompt)
        return ai.as_reply({"support_only": False, "entries": entries, "confidence": "high",
                            "reason": "owner feedback", "questions": [], "rule_suggestion": None})
    monkeypatch.setattr(ai, "run_claude", fake_claude)
    r = client.post(f"/api/doc-review/dr1/{receipt}/feedback",
                    json={"categories": ["wrong_account"], "text": "The cheese is food, keep 5010.",
                          "action": "repropose", "live_claude": True, "together": True})
    result = wait(r.json["job"])
    assert result["ok"], result
    assert calls and "The cheese is food, keep 5010." in calls[0]
    rec = json.loads((config.FEEDBACK_DIR / r.json["file"]).read_text(encoding="utf-8"))
    assert rec["reprocess_result"]["claude_mode"] == "live"
    assert rec["reprocess_result"]["new_proposal"]["source"] == "revised"


def test_undo_reversal_keeps_the_statement_match(booked):
    from books import post
    receipt = _receipt_with_dependant(booked)
    with booked.session() as conn:
        [eid] = [e["id"] for e in review.active_entries(conn, receipt)]
        rid = post.reverse_entry(conn, eid)
        new_id = post.reinstate_entry(conn, rid)
        assert not [i for i in match.open_items(conn) if i["document_id"] == receipt]   # still settled, not open
        lines = [l for e in tag_document(conn, receipt)["entries"] if e["entry_id"] == new_id for l in e["lines"]]
        assert [l["settlement"]["kind"] for l in lines if l["account"] == "2150"] == ["settled_by"]
        assert trial_balance_nets_to_zero(conn)

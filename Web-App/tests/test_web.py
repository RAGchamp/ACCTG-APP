"""Every screen renders, and the owner's main actions work through the API (plan §11)."""

import time

import pytest

from books import business
from webcommon import JOBS, JOBS_LOCK

PAGES = ["/", "/inbox", "/review", "/ledger", "/reconcile", "/reports", "/reports?kind=balance",
         "/reports?kind=cashflow", "/reports?kind=trial", "/reports?kind=equity", "/reports?kind=gl",
         "/reports?kind=income&compare=prior", "/reports?kind=trial&format=csv", "/taxes", "/setup", "/test-data",
         "/test-data?coverage=1", "/docs"]


@pytest.fixture
def client():
    from app import create_app
    return create_app().test_client()


def wait(job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        with JOBS_LOCK:
            job = dict(JOBS[job_id])
        if job["status"] != "running":
            assert job["status"] == "done", job.get("error")
            return job["result"]
        time.sleep(0.1)
    raise AssertionError("job timed out")


def test_pages_on_empty_live_books(client):
    for url in PAGES:
        r = client.get(url)
        assert r.status_code == 200, url


def test_sandbox_walkthrough(client):
    from testdata import generate
    generate.generate("card-month", "web1", seed=2)
    assert client.post("/api/business/switch", json={"name": "web1"}).json["ok"]
    assert business.active().name == "web1"
    r = client.post("/api/testdata/sandbox/web1/load", json={})
    assert r.json["ok"], r.json
    wait(r.json["job"])
    for url in PAGES + ["/test-data/sandbox/web1"]:
        assert client.get(url).status_code == 200, url
    biz = business.active()
    with biz.session() as conn:
        docs = [d["id"] for d in conn.execute("SELECT id FROM documents ORDER BY id")]
    assert docs
    page = client.get(f"/review/{docs[0]}")
    assert page.status_code == 200 and b"Proposed entries" in page.data
    # Accept everything in order, like the owner.
    for d in docs:
        r = client.post(f"/api/documents/{d}/accept", json={"confirm": True})
        assert r.json["ok"], r.json
    with biz.session() as conn:
        statuses = {r["status"] for r in conn.execute("SELECT status FROM documents")}
    assert statuses <= {"posted", "support"}
    assert client.get("/api/cpa-package?year=2026").status_code == 200
    assert client.get("/api/backup").status_code == 200
    r = client.post("/api/months/2026-01/close", json={})
    assert r.json["ok"], r.json
    # Posting into the closed month is refused.
    r = client.post("/api/entries/manual", json={"date": "2026-01-20", "memo": "x", "lines": [
        {"account": "6900", "debit": "1", "memo": "x"}, {"account": "1000", "credit": "1"}]})
    assert not r.json["ok"] and "closed" in r.json["error"]


def test_manual_entry_reverse_and_setup(client):
    r = client.post("/api/setup/opening", json={"balances": {"1010": "1000.00", "1590": "200.00"}})
    assert r.json["ok"], r.json
    r = client.post("/api/entries/manual", json={"date": "2026-02-01", "memo": "t", "lines": [
        {"account": "6100", "debit": "10"}, {"account": "1010", "credit": "10"}]})
    eid = r.json["entry"]
    assert client.post(f"/api/entries/{eid}/reverse", json={}).json["ok"]
    assert client.post("/api/setup/profile", json={"card_last4": "1234, 5678"}).json["ok"]
    assert client.post("/api/setup/accounts", json={"number": "6330", "name": "Uniforms", "type": "expense",
                                                   "tax_line": "27a"}).json["ok"]
    assert client.post("/api/rules", json={"vendor": "CVS", "rule": "to 6310", "account": "6310"}).json["ok"]
    bad = client.post("/api/entries/manual", json={"date": "2026-02-01", "memo": "t", "lines": [
        {"account": "6100", "debit": "10"}, {"account": "1010", "credit": "9"}]})
    assert not bad.json["ok"]
    assert client.get("/setup").status_code == 200


def test_upload_refuses_synthetic_file_in_live_books(client, tmp_path):
    from testdata import generate
    out = generate.generate("receipt-cash-split", "web2", seed=1)
    business.set_active("live")
    pdf = next(p for p in (business.sandbox("web2").root / "inputs").iterdir())
    with open(pdf, "rb") as fh:
        r = client.post("/api/upload", data={"files": (fh, pdf.name)}, content_type="multipart/form-data")
    assert r.json["ok"] and "SYNTHETIC" in " ".join(r.json["messages"])
    assert out["files"] == 1


def test_every_page_has_the_shell_and_one_overview(client):
    """UI-REVAMP-PLAN.md: top bar, centered menu with the books dropdown, one light-yellow overview box."""
    for url in [u for u in PAGES if "format=csv" not in u] + ["/doc-review", "/doc-review/show"]:
        html = client.get(url).data
        assert html.count(b'<header class="sba-topbar">') == 1, url
        assert b'class="navbar navbar-expand-lg sba-menu"' in html and b"sba-books" in html, url
        assert html.count(b'<section class="sba-overview"') == 1, url
        assert b'aria-current="page"' in html or url.startswith("/doc-review/show"), url
    for asset in ("/static/app.css", "/static/logo.svg"):
        assert client.get(asset).status_code == 200

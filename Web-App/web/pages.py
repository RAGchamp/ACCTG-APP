"""The screens (plan §11). Every page works on the active business: the live
books or a sandbox (shown in orange at the top of every page)."""

import csv
import io
import json
from datetime import date
from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request, send_file

import config
from books import business, coa, db, match, post, reconcile, review, trail
from books.money import fmt
from reports import statements
from tax import ct_income, federal, rules_loader, sales_tax_ct
from testdata import coverage, evaluate, generate, simulate

bp = Blueprint("pages", __name__)

DOC_LABELS = {"purchase_receipt": "Receipt", "credit_card_statement": "Card statement", "bank_statement": "Bank statement",
              "check": "Check", "cash_log": "Cash sheet", "pos_report": "Square report",
              "platform_statement": "Platform statement", "payroll_report": "Payroll report", "other": "Other",
              "not_financial": "Not financial"}
STATUS_LABELS = {"extracted": "to propose", "proposed": "to review", "needs_review": "needs attention",
                 "posted": "posted", "support": "linked (support)", "rejected": "rejected"}


def register_filters(app):
    app.jinja_env.filters["money"] = lambda c, **kw: fmt(c, **kw)
    app.jinja_env.filters["doclabel"] = lambda t: DOC_LABELS.get(t, t)
    app.jinja_env.filters["statuslabel"] = lambda s: STATUS_LABELS.get(s, s)
    app.jinja_env.filters["fromjson"] = lambda s: json.loads(s) if s else None

    @app.context_processor
    def inject():
        biz = business.active()
        return {"biz": biz, "sandboxes": business.list_sandboxes(), "today": date.today().isoformat()}


def _period_args(conn):
    """start/end from the query string; by default the books start to the end of the
    month of the latest posted entry."""
    books_start, _ = statements.books_period(conn)
    s = request.args.get("start") or books_start
    e = request.args.get("end")
    if not e:
        last = conn.execute("SELECT MAX(date) FROM journal_entries").fetchone()[0] or s
        last = max(last, s)
        e = statements.month_ends(int(last[:4]))[int(last[5:7]) - 1][1]
    return s, e


def latest_sales_month(conn, fallback):
    row = conn.execute("SELECT MAX(e.date) FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
                       "JOIN accounts a ON a.number=l.account WHERE a.type='revenue'").fetchone()
    return (row[0] or fallback)[:7]


# ------------------------------------------------------------------ dashboard

@bp.get("/")
def dashboard():
    biz = business.active()
    with biz.session() as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM documents GROUP BY status")}
        files_new = conn.execute("SELECT COUNT(*) FROM files WHERE status IN ('new','failed')").fetchone()[0]
        questions = 0
        for p in conn.execute("SELECT questions FROM proposals WHERE status='open'"):
            questions += len(json.loads(p["questions"]))
        start, end = _period_args(conn)
        month = latest_sales_month(conn, end)
        month_start = f"{month}-01"
        month_end = statements.month_ends(int(month[:4]))[int(month[5:7]) - 1][1]
        inc = statements.income_statement(conn, month_start, month_end)
        ytd = statements.income_statement(conn, start, end)
        bals = post.balances(conn)
        unmatched = match.unmatched_report(conn, as_of=end)
        rules_pending = conn.execute("SELECT COUNT(*) FROM feedback WHERE rule_status='proposed'").fetchone()[0]
        prof = db.profile(conn)
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd),0), COUNT(*) FROM ai_calls").fetchone()
    cal = [c for c in ct_income.calendar(prof["tax_year"]) if not c["past"]][:6]
    return render_template("dashboard.html", counts=counts, files_new=files_new, questions=questions, inc=inc, ytd=ytd,
                           bank=bals.get("1010", 0), cash=bals.get("1000", 0) + bals.get("1020", 0),
                           card=-bals.get("2100", 0), sales_tax=-bals.get("2200", 0), unmatched=unmatched,
                           calendar=cal, month=month, rules_pending=rules_pending, profile=prof, cost=cost)


# ------------------------------------------------------------------ inbox

@bp.get("/inbox")
def inbox():
    biz = business.active()
    with biz.session() as conn:
        files = [dict(f) for f in conn.execute("SELECT * FROM files ORDER BY seq DESC, id DESC")]
        docs = {}
        for d in conn.execute("SELECT id, file_id, part, doc_type, doc_date, party, total_cents, status, flags "
                              "FROM documents ORDER BY file_id, part"):
            docs.setdefault(d["file_id"], []).append(dict(d))
        costs = {r["file_id"]: r for r in conn.execute(
            "SELECT file_id, SUM(cost_usd) usd, SUM(seconds) secs, COUNT(*) n FROM ai_calls GROUP BY file_id")}
    waiting = sorted(p.name for p in config.INBOX_DIR.iterdir() if p.is_file()) if config.INBOX_DIR.is_dir() else []
    # Overview tiles (UI-REVAMP-PLAN.md §2).
    statuses = [d["status"] for ds in docs.values() for d in ds]
    stats = {"files": len(files), "new_failed": sum(f["status"] in ("new", "failed") for f in files),
             "documents": len(statuses), "to_propose": statuses.count("extracted"),
             "to_review": statuses.count("proposed") + statuses.count("needs_review"),
             "calls": sum(c["n"] or 0 for c in costs.values()), "usd": sum(c["usd"] or 0 for c in costs.values())}
    return render_template("inbox.html", files=files, docs=docs, costs=costs, waiting=waiting,
                           inbox_dir=str(config.INBOX_DIR), stats=stats)


@bp.get("/files/<int:file_id>/page/<int:page_no>.png")
def page_image(file_id, page_no):
    biz = business.active()
    with biz.session() as conn:
        row = conn.execute("SELECT image_path FROM pages WHERE file_id=? AND page_no=?", (file_id, page_no)).fetchone()
    if not row or not row["image_path"] or not Path(row["image_path"]).exists():
        abort(404)
    return send_file(row["image_path"], mimetype="image/png", max_age=0)


@bp.get("/files/<int:file_id>/original")
def original(file_id):
    biz = business.active()
    with biz.session() as conn:
        row = conn.execute("SELECT stored_path, filename, kind FROM files WHERE id=?", (file_id,)).fetchone()
    if not row:
        abort(404)
    return send_file(row["stored_path"], download_name=row["filename"],
                     mimetype="application/pdf" if row["kind"] == "pdf" else "text/csv")


# ------------------------------------------------------------------ review

def review_queue(conn, filt=None):
    rows = conn.execute(
        "SELECT d.*, f.seq, f.filename FROM documents d JOIN files f ON f.id=d.file_id "
        "WHERE d.status IN ('extracted','proposed','needs_review') ORDER BY f.seq, d.part").fetchall()
    out = []
    for d in rows:
        p = review.open_proposal(conn, d["id"])
        item = dict(d, proposal=p)
        item["n_questions"] = len(json.loads(p["questions"])) if p else 0
        item["n_errors"] = len(json.loads(p["errors"])) if p else 0
        item["flag_errors"] = len([f for f in json.loads(d["flags"]) if f["level"] == "error"])
        if filt == "questions" and not item["n_questions"]:
            continue
        if filt == "attention" and not (item["n_errors"] or item["flag_errors"]):
            continue
        out.append(item)
    return out


@bp.get("/review")
@bp.get("/review/<int:doc_id>")
def review_page(doc_id=None):
    biz = business.active()
    filt = request.args.get("filter")
    with biz.session() as conn:
        queue = review_queue(conn, filt)
        if doc_id is None and queue:
            doc_id = queue[0]["id"]
        doc = prop = f = None
        pages_, history, feedback_rows = [], [], []
        stale = False
        entries_posted = []
        if doc_id:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                abort(404)
            doc = dict(row)
            doc["extraction_obj"] = json.loads(doc["extraction"])
            doc["flags_obj"] = json.loads(doc["flags"])
            f = dict(conn.execute("SELECT * FROM files WHERE id=?", (doc["file_id"],)).fetchone())
            pages_ = json.loads(doc["pages"])
            prop = review.open_proposal(conn, doc_id)
            if prop:
                prop["entries_obj"] = json.loads(prop["entries"])
                prop["questions_obj"] = json.loads(prop["questions"])
                prop["errors_obj"] = json.loads(prop["errors"])
                prop["matches_obj"] = json.loads(prop["matches"])
                last = conn.execute("SELECT COALESCE(MAX(id),0) FROM journal_entries").fetchone()[0]
                stale = last > prop["ledger_mark"] and doc["doc_type"] in (
                    "purchase_receipt", "credit_card_statement", "bank_statement", "check")
            history = [dict(r) for r in conn.execute(
                "SELECT id, version, source, status, confidence, created_at FROM proposals WHERE document_id=? "
                "ORDER BY version DESC", (doc_id,))]
            feedback_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM feedback WHERE document_id=? ORDER BY id DESC", (doc_id,))]
            for fb in feedback_rows:
                fb["rule"] = json.loads(fb["rule_text"]) if fb["rule_text"] else None
            entries_posted = post.entries_json(conn, doc_id)
        accounts = coa.accounts(conn)
        earlier_open = 0
        if doc:
            earlier_open = conn.execute(
                "SELECT COUNT(*) FROM documents d JOIN files x ON x.id=d.file_id WHERE d.status IN "
                "('extracted','proposed','needs_review') AND (x.seq < ? OR (x.seq = ? AND d.part < ?))",
                (f["seq"], f["seq"], doc["part"])).fetchone()[0]
        bulk = review.bulk_candidates(conn)
    return render_template("review.html", queue=queue, doc=doc, prop=prop, file=f, pages=pages_, accounts=accounts,
                           history=history, feedback=feedback_rows, stale=stale, entries_posted=entries_posted,
                           earlier_open=earlier_open, bulk=bulk, filt=filt,
                           feedback_max=config.FEEDBACK_MAX_CHARS)


# ------------------------------------------------------------------ ledger

@bp.get("/ledger")
def ledger():
    biz = business.active()
    account = request.args.get("account") or None
    q = (request.args.get("q") or "").strip()
    with biz.session() as conn:
        start, end = _period_args(conn)
        sql = ("SELECT e.*, d.doc_type, d.file_id, d.pages FROM journal_entries e LEFT JOIN documents d ON "
               "d.id=e.document_id WHERE e.date BETWEEN ? AND ?")
        args = [start, end]
        if account:
            sql += " AND e.id IN (SELECT entry_id FROM journal_lines WHERE account=?)"
            args.append(account)
        if q:
            sql += " AND (e.memo LIKE ? OR e.id IN (SELECT entry_id FROM journal_lines WHERE party LIKE ? OR memo LIKE ?))"
            args += [f"%{q}%"] * 3
        sql += " ORDER BY e.date DESC, e.id DESC LIMIT 400"
        entries = []
        reversed_ids = {r[0] for r in conn.execute("SELECT reverses FROM journal_entries WHERE reverses IS NOT NULL")}
        for e in conn.execute(sql, args):
            item = dict(e, lines=post.entry_lines(conn, e["id"]), is_reversed=e["id"] in reversed_ids)
            entries.append(item)
        accounts = coa.accounts(conn, active_only=False)
        gl = statements.general_ledger(conn, start, end, account) if account else None
        dep = federal.monthly_depreciation(conn, end[:7])
        # Overview tiles (UI-REVAMP-PLAN.md §2): the whole period, not just the 400 shown.
        t = conn.execute("SELECT COUNT(DISTINCT e.id) n, COALESCE(SUM(l.debit),0) dr, COALESCE(SUM(l.credit),0) cr "
                         "FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id WHERE e.date BETWEEN ? AND ?",
                         (start, end)).fetchone()
        totals = {"entries": t["n"], "debit": t["dr"], "credit": t["cr"],
                  "reversed": sum(1 for e in entries if e["is_reversed"])}
    return render_template("ledger.html", entries=entries, accounts=accounts, account=account, q=q, start=start,
                           end=end, gl=gl, dep=dep, totals=totals)


@bp.get("/ledger-trail")
def ledger_trail():
    """Every change to the ledger, newest first (books/trail.py)."""
    biz = business.active()
    start = request.args.get("start") or ""
    end = request.args.get("end") or ""
    action = request.args.get("action") or ""
    q = (request.args.get("q") or "").strip()
    with biz.session() as conn:
        rows = trail.rows(conn, start or None, end or None, action or None, q or None)
        summary = trail.summary(conn)
        docs = {r["id"]: dict(r) for r in conn.execute("SELECT id, doc_type, party FROM documents")}
    if request.args.get("format") == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Changed at", "Action", "Entry", "Reverses", "Kind", "Entry date", "Period", "Memo", "Document",
                    "Total", "Origin", "Note", "Lines"])
        for r in rows:
            w.writerow([r["at"], r["action_label"], r["entry_id"] or "", r["reverses"] or "", r["kind"] or "",
                        r["entry_date"] or "", r["period"] or "", r["memo"] or "", r["document_id"] or "",
                        "" if r["total_cents"] is None else f"{r['total_cents'] / 100:.2f}", r["origin"], r["note"] or "",
                        "; ".join(f"{l['account']} Dr {l['debit_cents'] / 100:.2f} Cr {l['credit_cents'] / 100:.2f}"
                                  for l in r["lines"])])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=ledger-trail-{biz.name}.csv"})
    return render_template("ledger_trail.html", rows=rows, summary=summary, docs=docs, start=start, end=end,
                           action=action, q=q, actions=trail.ACTIONS)


# ------------------------------------------------------------------ reconcile

@bp.get("/reconcile")
def reconcile_page():
    biz = business.active()
    with biz.session() as conn:
        start, end = _period_args(conn)
        months = []
        m = start[:7]
        while m <= end[:7]:
            months.append(m)
            y, mo = int(m[:4]), int(m[5:7])
            m = f"{y + (mo == 12)}-{mo % 12 + 1:02d}"
        month = request.args.get("month") or months[-1]
        rec = reconcile.reconcile_month(conn, month)
        history = [dict(r) for r in conn.execute("SELECT * FROM reconciliations WHERE month=? ORDER BY id DESC LIMIT 6",
                                                  (month,))]
        statuses = {m: post.period_status(conn, m) for m in months}
    return render_template("reconcile.html", rec=rec, months=months, month=month, history=history, statuses=statuses)


# ------------------------------------------------------------------ reports

@bp.get("/reports")
def reports_page():
    biz = business.active()
    kind = request.args.get("kind", "income")
    with biz.session() as conn:
        start, end = _period_args(conn)
        data = {"income": lambda: statements.income_statement(conn, start, end),
                "balance": lambda: statements.balance_sheet(conn, end),
                "cashflow": lambda: statements.cash_flow(conn, start, end),
                "trial": lambda: statements.trial_balance(conn, end),
                "equity": lambda: statements.equity_statement(conn, start, end),
                "gl": lambda: statements.general_ledger(conn, start, end)}[kind]()
        compare = None
        if kind == "income" and request.args.get("compare") == "prior":
            y = int(start[:4]) - 1
            compare = statements.income_statement(conn, f"{y}{start[4:]}", f"{y}{end[4:]}")
        prof = db.profile(conn)
    if request.args.get("format") == "csv":
        return _csv(kind, data, start, end)
    return render_template("reports.html", kind=kind, data=data, start=start, end=end, compare=compare, profile=prof)


def _csv(kind, data, start, end):
    out = io.StringIO()
    w = csv.writer(out)
    d = lambda c: f"{c / 100:.2f}"  # noqa: E731
    if kind == "trial":
        w.writerow(["Account", "Name", "Debit", "Credit"])
        for r in data["rows"]:
            w.writerow([r["account"], r["name"], d(r["debit"]), d(r["credit"])])
    elif kind == "gl":
        w.writerow(["Account", "Name", "Date", "Entry", "Memo", "Party", "Debit", "Credit", "Balance"])
        for a in data:
            for l in a["lines"]:
                w.writerow([a["account"], a["name"], l["date"], l["entry_id"], l["entry_memo"], l["party"],
                            d(l["debit"]), d(l["credit"]), d(l["balance"])])
    else:
        w.writerow(["Section", "Line", "Amount"])
        for key, value in data.items():
            if isinstance(value, list):
                for r in value:
                    if isinstance(r, dict) and "amount" in r:
                        w.writerow([key, r.get("name") or r.get("label"), d(r["amount"])])
            elif isinstance(value, int) and not isinstance(value, bool):
                w.writerow(["total", key, d(value)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={kind}-{start}-{end}.csv"})


# ------------------------------------------------------------------ taxes

@bp.get("/taxes")
def taxes_page():
    biz = business.active()
    with biz.session() as conn:
        prof = db.profile(conn)
        year = int(request.args.get("year") or prof["tax_year"])
        start, end = _period_args(conn)
        month = request.args.get("month") or latest_sales_month(conn, end)
        try:
            os114 = sales_tax_ct.worksheet(conn, month)
            sch_c = federal.schedule_c(conn, year)
            se = federal.schedule_se(sch_c["L"]["31"], year)
            f4562 = federal.form_4562_figures(conn, year)
            f1099 = federal.form_1099_candidates(conn, year)
            ct = ct_income.ct_figures(sch_c, se)
            cal = ct_income.calendar(year, prof["sales_tax_frequency"])
            months_done = int(end[5:7]) if end[:4] == str(year) else 12
            est = federal.estimates(sch_c["L"]["31"], months_done, year)
            error = None
        except rules_loader.TaxRulesMissing as exc:
            os114 = sch_c = se = f4562 = f1099 = ct = cal = est = None
            error = str(exc)
        returns = [dict(r) for r in conn.execute("SELECT * FROM tax_returns ORDER BY id DESC LIMIT 20")]
    return render_template("taxes.html", os114=os114, sch_c=sch_c, se=se, f4562=f4562, f1099=f1099, ct=ct, cal=cal,
                           est=est, year=year, month=month, error=error, profile=prof, returns=returns,
                           years=rules_loader.years())


# ------------------------------------------------------------------ setup

@bp.get("/docs")
def docs_page():
    """The user's guide, with a few figures showing where these books stand."""
    biz = business.active()
    with biz.session() as conn:
        prof = db.profile(conn)
        status = {
            "opening": conn.execute("SELECT COUNT(*) FROM journal_entries WHERE kind='opening'").fetchone()[0] > 0,
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "to_review": conn.execute("SELECT COUNT(*) FROM documents WHERE status IN "
                                      "('extracted','proposed','needs_review')").fetchone()[0],
            "closed": conn.execute("SELECT COUNT(*) FROM periods WHERE status='closed'").fetchone()[0],
            "profile_done": bool(prof["card_last4"] and prof["bank_last4"]),
        }
    return render_template("docs.html", status=status, profile=prof, inbox_dir=str(config.INBOX_DIR))


@bp.get("/setup")
def setup_page():
    biz = business.active()
    with biz.session() as conn:
        prof = db.profile(conn)
        accounts = coa.accounts(conn, active_only=False)
        rules = [dict(r) for r in conn.execute("SELECT * FROM vendor_rules WHERE active=1 ORDER BY vendor")]
        vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors ORDER BY name")]
        opening = conn.execute("SELECT COUNT(*) FROM journal_entries WHERE kind='opening'").fetchone()[0]
        audit = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 60")]
    return render_template("setup.html", profile=prof, accounts=accounts, rules=rules, vendors=vendors,
                           opening_posted=opening, audit=audit)


# ------------------------------------------------------------------ test data

@bp.get("/test-data")
def test_data_page():
    scen = generate.load_scenarios()
    areas = {}
    for sid, sc in scen.items():
        areas.setdefault(sc.get("area", "Other"), []).append(dict(sc, id=sid, n_events=len(sc.get("events", []))))
    boxes = []
    for name in business.list_sandboxes():
        b = business.sandbox(name)
        key = {}
        try:
            key = json.loads(b.answer_key_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        with b.session() as conn:
            n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            n_posted = conn.execute("SELECT COUNT(*) FROM documents WHERE status IN ('posted','support')").fetchone()[0]
        boxes.append({"name": name, "ai_mode": b.ai_mode, "scenario": key.get("scenario"), "title": key.get("title"),
                      "files": len(key.get("files", {})), "docs": n_docs, "posted": n_posted,
                      "difficulty": key.get("difficulty"), "seed": key.get("seed")})
    return render_template("test_data.html", areas=areas, presets=simulate.PRESETS, boxes=boxes,
                           results=evaluate.list_results()[:40], coverage=coverage.report() if
                           request.args.get("coverage") else None)


@bp.get("/test-data/results/<name>")
def scorecard(name):
    path = (config.RESULTS_DIR / name).resolve()
    if path.parent != config.RESULTS_DIR.resolve() or not path.exists() or path.suffix != ".html":
        abort(404)
    return send_file(path, mimetype="text/html")


@bp.get("/test-data/sandbox/<name>")
def sandbox_view(name):
    """Every generated file of a sandbox next to its answer key (plan §15.7)."""
    b = business.sandbox(name)
    key = json.loads(b.answer_key_path.read_text(encoding="utf-8"))
    files = [dict(key["files"][sha], sha=sha) for sha in key["file_order"]]
    pick = request.args.get("file")
    current = next((x for x in files if x["sha"] == pick), files[0] if files else None)
    return render_template("sandbox_view.html", name=name, key=key, files=files, current=current,
                           expected=key.get("expected", {}))


@bp.get("/test-data/sandbox/<name>/input/<sha>")
def sandbox_input(name, sha):
    b = business.sandbox(name)
    key = json.loads(b.answer_key_path.read_text(encoding="utf-8"))
    f = key["files"].get(sha)
    if not f:
        abort(404)
    path = Path(f["path"])
    if not path.exists():
        abort(404)
    if request.args.get("png") and path.suffix == ".pdf":
        import pymupdf
        try:
            doc = pymupdf.open(path)
            n = int(request.args.get("page", 1))
            png = doc[n - 1].get_pixmap(dpi=90).tobytes("png")
            doc.close()
            return Response(png, mimetype="image/png")
        except Exception:
            abort(404)
    return send_file(path, download_name=f["filename"])

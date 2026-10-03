"""JSON actions and background jobs behind the screens (plan §11).

Long steps (extraction, the Bookkeeper, feedback, test data generation and
evaluation) run as background jobs the browser polls (webcommon.py, from the
analyzer). Everything acts on the active business.
"""

import io
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

import config
from ai import AI
from books import business, db, post, propose, reconcile, review
from books.money import parse_cents
from claude_client import ClaudeError
from ingest import extract, intake
from reports import statements
from tax import ct_income, federal, sales_tax_ct
from testdata import evaluate, generate, simulate
from webcommon import JOBS, JOBS_LOCK, UserError, current_job_id, job_cancelled, start_job, update_job

bp = Blueprint("api", __name__, url_prefix="/api")


def ok(**kw):
    return jsonify(dict(ok=True, **kw))


@bp.errorhandler(UserError)
@bp.errorhandler(ValueError)
@bp.errorhandler(post.PostingError)
def user_error(exc):
    return jsonify(ok=False, error=str(exc)), 400


def body():
    return request.get_json(silent=True) or request.form.to_dict() or {}


# ------------------------------------------------------------------ jobs

@bp.get("/jobs/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return jsonify(ok=False, error="No such job"), 404
    return jsonify(ok=True, job=job)


@bp.post("/jobs/<job_id>/cancel")
def job_cancel(job_id):
    update_job(job_id, cancel=True)
    return ok()


def _progress(done, total, text):
    update_job(current_job_id(), progress={"done": done, "total": total, "text": text})


# ------------------------------------------------------------------ business

@bp.post("/business/switch")
def switch_business():
    business.set_active(body().get("name", "live"))
    return ok()


# ------------------------------------------------------------------ inbox, extraction, proposals

def _process(biz_name, file_ids, auto_propose=True):
    """Extract files, then run the Bookkeeper for their documents (in processing order)."""
    biz = business.get(biz_name)
    jid = current_job_id()
    ai = AI(biz)
    done, errors, doc_ids = 0, [], []
    for fid in file_ids:
        if job_cancelled(jid):
            break
        _progress(done, len(file_ids), f"Extracting file #{fid}")
        try:
            doc_ids += extract.extract_file(biz, fid, ai=ai)
        except (ClaudeError, UserError, ValueError) as exc:
            errors.append(f"file #{fid}: {exc}")
        done += 1
    proposed = 0
    if auto_propose:
        proposed, errs = _propose_docs(biz, ai, doc_ids, jid)
        errors += errs
    return {"files": done, "documents": len(doc_ids), "proposed": proposed, "errors": errors}


def _propose_docs(biz, ai, doc_ids, jid):
    with biz.session() as conn:
        rows = conn.execute(
            f"SELECT d.id FROM documents d JOIN files f ON f.id=d.file_id WHERE d.id IN ({','.join('?' * len(doc_ids))}) "
            "AND d.status IN ('extracted','proposed','needs_review') ORDER BY f.seq, d.part", doc_ids).fetchall() \
            if doc_ids else []
    n, errors = 0, []
    for i, r in enumerate(rows):
        if job_cancelled(jid):
            break
        _progress(i, len(rows), f"Bookkeeper: document #{r['id']}")
        try:
            propose.propose(biz, r["id"], ai=ai)
            n += 1
        except (ClaudeError, UserError, ValueError) as exc:
            errors.append(f"document #{r['id']}: {exc}")
    return n, errors


@bp.post("/upload")
def upload():
    biz = business.active()
    added, messages = [], []
    for f in request.files.getlist("files"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(f.filename).name
            f.save(path)
            try:
                added.append(intake.add_file(biz, path, source="upload", filename=Path(f.filename).name))
                messages.append(f"{f.filename}: added")
            except UserError as exc:
                messages.append(f"{f.filename}: {exc}")
    job = start_job("process", _process, biz.name, added, request.form.get("propose", "1") == "1") if added else None
    return ok(added=added, messages=messages, job=job)


@bp.post("/inbox/scan")
def inbox_scan():
    biz = business.active()
    added, messages = intake.scan_inbox(biz)
    job = start_job("process", _process, biz.name, added, True) if added else None
    return ok(added=added, messages=messages, job=job)


@bp.post("/files/<int:file_id>/extract")
def extract_again(file_id):
    biz = business.active()
    dpi = config.RETRY_DPI if body().get("higher_dpi") else None

    def run(name, fid, dpi_):
        b = business.get(name)
        ai = AI(b)
        ids = extract.extract_file(b, fid, ai=ai, dpi=dpi_)
        n, errs = _propose_docs(b, ai, ids, current_job_id())
        return {"documents": len(ids), "proposed": n, "errors": errs}
    return ok(job=start_job("extract", run, biz.name, file_id, dpi))


@bp.post("/propose")
def propose_all():
    """Run the Bookkeeper for the given documents, or every document still to propose."""
    biz = business.active()
    ids = body().get("document_ids")
    if not ids:
        with biz.session() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE status='extracted'")]
    refresh = bool(body().get("refresh"))

    def run(name, doc_ids):
        b = business.get(name)
        n, errs = _propose_docs(b, AI(b), doc_ids, current_job_id())
        return {"proposed": n, "errors": errs}
    if refresh:
        with biz.session() as conn:
            conn.executemany("UPDATE proposals SET status='superseded' WHERE document_id=? AND status='open'",
                             [(i,) for i in ids])
    return ok(job=start_job("propose", run, biz.name, ids))


# ------------------------------------------------------------------ review actions

@bp.post("/documents/<int:doc_id>/accept")
def accept(doc_id):
    b = body()
    entries = b.get("entries")
    ids = review.accept(business.active(), doc_id, confirm_flags=bool(b.get("confirm")), edited_entries=entries,
                        note=b.get("note"))
    return ok(entries=ids)


@bp.post("/documents/<int:doc_id>/feedback")
def feedback(doc_id):
    text = body().get("text", "")
    biz = business.active()

    def run(name, d, t):
        pid = propose.revise(business.get(name), d, t)
        return {"proposal": pid}
    return ok(job=start_job("revise", run, biz.name, doc_id, text))


@bp.post("/documents/<int:doc_id>/reject")
def reject(doc_id):
    review.reject(business.active(), doc_id, body().get("reason"))
    return ok()


@bp.post("/documents/<int:doc_id>/reopen")
def reopen_doc(doc_id):
    review.reopen(business.active(), doc_id)
    return ok()


@bp.post("/documents/<int:doc_id>/extraction")
def edit_extraction(doc_id):
    data = body().get("extraction")
    if isinstance(data, str):
        data = json.loads(data)
    flags = extract.update_extraction(business.active(), doc_id, data)
    return ok(flags=flags)


@bp.post("/feedback/<int:fb_id>/rule")
def rule_decision(fb_id):
    b = body()
    review.decide_rule(business.active(), fb_id, b.get("decision", "once"), b.get("vendor"), b.get("rule"),
                       b.get("account"))
    return ok()


@bp.post("/bulk-accept")
def bulk_accept():
    biz = business.active()
    ids = body().get("document_ids")
    if not ids:
        with biz.session() as conn:
            ids = review.bulk_candidates(conn)
    done, failed = review.bulk_accept(biz, [int(i) for i in ids])
    return ok(done=done, failed=failed)


# ------------------------------------------------------------------ ledger

@bp.post("/entries/<int:entry_id>/reverse")
def reverse(entry_id):
    b = body()
    with business.active().session() as conn:
        row = conn.execute("SELECT kind FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
        if row and row["kind"] == "reversal":
            # Reversing a reversal undoes it: the reversed entry is posted again.
            new_id = post.reinstate_entry(conn, entry_id, b.get("date") or None,
                                          origin="Ledger: Reverse button (undo a reversal)")
        else:
            new_id = post.reverse_entry(conn, entry_id, b.get("date") or None, b.get("memo") or None,
                                        origin="Ledger: Reverse button")
    return ok(entry=new_id)


@bp.post("/entries/manual")
def manual_entry():
    b = body()
    kind = b.get("kind", "manual")
    if kind not in ("manual", "adjustment", "opening"):
        raise UserError("kind must be manual, adjustment or opening")
    entry = {"date": b.get("date"), "memo": b.get("memo") or "Manual entry", "lines": b.get("lines") or []}
    with business.active().session() as conn:
        eid = post.post_entry(conn, entry, kind=kind, origin=f"Ledger: new {kind} journal entry")
    return ok(entry=eid)


@bp.post("/depreciation")
def depreciation():
    month = body().get("month")
    with business.active().session() as conn:
        dep = federal.monthly_depreciation(conn, month)
        if not dep:
            raise UserError("No fixed assets to depreciate for that month.")
        eid = post.post_entry(conn, dep, kind="adjustment", origin="Ledger: suggested depreciation")
    return ok(entry=eid)


@bp.post("/months/<month>/close")
def close_month(month):
    rec = reconcile.close(business.active(), month, body().get("override"))
    return ok(reconciled=rec["reconciled"])


@bp.post("/months/<month>/reopen")
def reopen_month(month):
    with business.active().session() as conn:
        post.reopen_month(conn, month, body().get("note"))
    return ok()


# ------------------------------------------------------------------ setup

PROFILE_FIELDS = ("name", "address", "ein", "ct_tax_registration", "owner_name", "books_start", "tax_year", "basis",
                  "sales_tax_frequency", "capitalization_threshold", "pos", "payroll_provider")


@bp.post("/setup/profile")
def save_profile():
    b = body()
    with business.active().session() as conn:
        prof = db.profile(conn)
        for k in PROFILE_FIELDS:
            if k in b and b[k] != "":
                prof[k] = int(b[k]) if k == "tax_year" else b[k]
        for k in ("card_last4", "bank_last4", "platforms"):
            if k in b:
                v = b[k] if isinstance(b[k], list) else [x.strip() for x in str(b[k]).split(",") if x.strip()]
                prof[k] = v
        if parse_cents(prof["capitalization_threshold"]) is None:
            raise UserError("The capitalization threshold must be an amount.")
        db.set_setting(conn, "profile", prof)
        db.audit(conn, "profile", fields=list(b))
    return ok()


@bp.post("/setup/accounts")
def save_account():
    b = body()
    num = str(b.get("number", "")).strip()
    if not num.isdigit():
        raise UserError("Account numbers are digits, e.g. 6330.")
    if b.get("type") not in ("asset", "liability", "equity", "revenue", "cogs", "expense"):
        raise UserError("Choose the account type.")
    with business.active().session() as conn:
        conn.execute(
            "INSERT INTO accounts(number, name, type, subtype, tax_line, cash_flow_class, description, active) "
            "VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(number) DO UPDATE SET name=excluded.name, type=excluded.type, "
            "subtype=excluded.subtype, tax_line=excluded.tax_line, cash_flow_class=excluded.cash_flow_class, "
            "description=excluded.description",
            (num, b.get("name", "").strip(), b["type"], b.get("subtype") or None, b.get("tax_line") or None,
             b.get("cash_flow_class") or ("operating" if b["type"] in ("expense", "asset", "liability") else None),
             b.get("description", "")))
        db.audit(conn, "account", number=num)
    return ok()


@bp.post("/setup/accounts/<number>/active")
def account_active(number):
    with business.active().session() as conn:
        conn.execute("UPDATE accounts SET active=? WHERE number=?", (1 if body().get("active") else 0, number))
    return ok()


@bp.post("/setup/opening")
def opening_balances():
    """The opening balances wizard (plan §9): balanced against 3200 Opening balance equity."""
    b = body()
    lines, total = [], 0
    for acct, amount in (b.get("balances") or {}).items():
        cents = parse_cents(amount)
        if not cents:
            continue
        with business.active().session() as conn:
            a = conn.execute("SELECT type, subtype FROM accounts WHERE number=?", (acct,)).fetchone()
        if not a:
            raise UserError(f"Unknown account {acct}")
        debit_normal = a["type"] == "asset" and a["subtype"] != "contra_asset"
        if debit_normal:
            lines.append({"account": acct, "debit_cents": cents, "memo": "opening balance"})
            total += cents
        else:
            lines.append({"account": acct, "credit_cents": cents, "memo": "opening balance"})
            total -= cents
    if not lines:
        raise UserError("Enter at least one opening balance.")
    lines.append({"account": "3200", "credit_cents": total, "memo": "opening balance equity"} if total >= 0 else
                 {"account": "3200", "debit_cents": -total, "memo": "opening balance equity"})
    with business.active().session() as conn:
        start = db.profile(conn)["books_start"]
        eid = post.post_entry(conn, {"date": start, "memo": "Opening balances", "lines": lines}, kind="opening",
                              origin="Setup: opening balances")
    return ok(entry=eid)


@bp.post("/rules")
def add_rule():
    b = body()
    if not b.get("vendor") or not b.get("rule"):
        raise UserError("A rule needs a vendor and an instruction.")
    review.add_rule(business.active(), b["vendor"], b["rule"], b.get("account"))
    return ok()


@bp.post("/rules/<int:rule_id>/delete")
def delete_rule(rule_id):
    review.delete_rule(business.active(), rule_id)
    return ok()


@bp.post("/vendors")
def save_vendor():
    b = body()
    with business.active().session() as conn:
        conn.execute("INSERT INTO vendors(name, is_1099) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET "
                     "is_1099=excluded.is_1099", (b["name"], 1 if b.get("is_1099") else 0))
    return ok()


# ------------------------------------------------------------------ taxes and exports

@bp.post("/taxes/save")
def save_return():
    b = body()
    form, period = b.get("form"), b.get("period")
    with business.active().session() as conn:
        if form == "OS-114":
            figures = sales_tax_ct.worksheet(conn, period)
            version = figures["rules_version"]
        elif form == "Schedule C":
            figures = federal.schedule_c(conn, int(period))
            figures["se"] = federal.schedule_se(figures["L"]["31"], int(period))
            version = figures["rules_version"]
        else:
            raise UserError("form must be OS-114 or Schedule C")
        conn.execute("INSERT INTO tax_returns(form, period, figures, rules_version, generated_at, status) "
                     "VALUES (?,?,?,?,?,?)", (form, period, json.dumps(figures, default=str), version, db.now(),
                                              b.get("status", "draft")))
    return ok()


@bp.get("/cpa-package")
def cpa_package():
    """The CPA handoff package (decision D10): GL, TB, statements, worksheets, assets, 1099 list, questions."""
    biz = business.active()
    year = int(request.args.get("year") or 2026)
    buf = io.BytesIO()
    with biz.session() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        start, end = f"{year}-01-01", f"{year}-12-31"
        prof = db.profile(conn)
        summary = statements.summary(conn, max(start, prof["books_start"]), end)
        z.writestr("README.txt", f"CPA handoff package - {prof['name']} - tax year {year}\n"
                                 f"Generated {datetime.now():%Y-%m-%d %H:%M} by the Small Business Accounting Agent.\n"
                                 "Amounts in JSON files are integer cents. Worksheets are DRAFTS built from the books.\n"
                                 + ("THIS IS A SANDBOX WITH SYNTHETIC TEST DATA.\n" if biz.is_sandbox else ""))
        z.writestr("statements.json", json.dumps(summary, indent=1, default=str))
        gl = statements.general_ledger(conn, start, end)
        lines = ["account,name,date,entry,memo,party,debit,credit,balance"]
        for a in gl:
            for l in a["lines"]:
                memo = (l["entry_memo"] or "").replace(",", " ")
                party = (l["party"] or "").replace(",", " ")
                lines.append(f"{a['account']},{a['name'].replace(',', ' ')},{l['date']},{l['entry_id']},{memo},{party},"
                             f"{l['debit'] / 100:.2f},{l['credit'] / 100:.2f},{l['balance'] / 100:.2f}")
        z.writestr("general_ledger.csv", "\n".join(lines))
        tb = statements.trial_balance(conn, end)
        z.writestr("trial_balance.csv", "account,name,debit,credit\n" + "\n".join(
            f"{r['account']},{r['name'].replace(',', ' ')},{r['debit'] / 100:.2f},{r['credit'] / 100:.2f}"
            for r in tb["rows"]))
        sch = federal.schedule_c(conn, year)
        se = federal.schedule_se(sch["L"]["31"], year)
        z.writestr("schedule_c.json", json.dumps({"schedule_c": sch, "schedule_se": se}, indent=1, default=str))
        z.writestr("ct_figures.json", json.dumps(ct_income.ct_figures(sch, se), indent=1))
        os114 = {f"{year}-{m:02d}": sales_tax_ct.worksheet(conn, f"{year}-{m:02d}") for m in range(1, 13)}
        z.writestr("os114_worksheets.json", json.dumps(os114, indent=1, default=str))
        z.writestr("fixed_assets_4562.json", json.dumps(federal.form_4562_figures(conn, year), indent=1, default=str))
        z.writestr("form_1099_candidates.json", json.dumps(federal.form_1099_candidates(conn, year), indent=1))
        z.writestr("open_questions.txt", "\n".join(sch["questions"] + os114[f"{year}-01"]["open_questions"]))
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=cpa-package-{biz.name}-{year}.zip"})


@bp.get("/backup")
def backup():
    """Zip the active business folder (plan §16 Backup)."""
    biz = business.active()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in biz.root.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(biz.root))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=books-{biz.name}-{stamp}.zip"})


# ------------------------------------------------------------------ test data

def _generate_job(scenario_ids, preset, difficulty, seed, ai_mode, evaluate_after):
    out = []
    targets = [("preset", preset)] if preset else [("scenario", s) for s in scenario_ids]
    for i, (kind, name) in enumerate(targets):
        if job_cancelled(current_job_id()):
            break
        _progress(i, len(targets), f"Generating {name}")
        sc = simulate.preset(name, seed) if kind == "preset" else name
        g = generate.generate(sc, name, seed=seed, difficulty=difficulty or None, ai_mode=ai_mode,
                              progress=lambda d, t, text: _progress(i, len(targets), f"{name}: {text}"))
        item = {"sandbox": g["sandbox"], "files": g["files"], "documents": g["documents"]}
        if evaluate_after:
            _progress(i, len(targets), f"Evaluating {name}")
            r = evaluate.run(g["sandbox"])
            item.update(passed=r["passed"], scorecard=Path(r["files_saved"][-1]).name)
        out.append(item)
    return {"generated": out}


@bp.post("/testdata/generate")
def testdata_generate():
    b = body()
    ids = b.get("scenarios") or []
    if isinstance(ids, str):
        ids = [ids]
    preset = b.get("preset") or None
    if not ids and not preset:
        raise UserError("Choose scenarios or a simulation preset.")
    job = start_job("generate", _generate_job, ids, preset, b.get("difficulty"), int(b.get("seed") or 1),
                    b.get("ai_mode", "replay"), bool(b.get("evaluate")))
    return ok(job=job)


@bp.post("/testdata/evaluate")
def testdata_evaluate():
    b = body()
    name = b["sandbox"]
    mode = b.get("mode") or None

    def run(n, m):
        r = evaluate.run(n, m, progress=lambda text: _progress(0, 0, text))
        return {"passed": r["passed"], "scorecard": Path(r["files_saved"][-1]).name, "stages": r["stages"]}
    return ok(job=start_job("evaluate", run, name, mode))


@bp.post("/testdata/sandbox/<name>/load")
def sandbox_load(name):
    """Reset the sandbox and put its generated files through the app like the owner would
    (upload in order, extract, propose), to review them by hand on the Review screen."""
    biz = business.reset_sandbox(name)
    business.set_active(name)
    key = json.loads(biz.answer_key_path.read_text(encoding="utf-8"))
    added, messages = [], []
    for sha in key["file_order"]:
        f = key["files"][sha]
        try:
            added.append(intake.add_file(biz, f["path"], source="testdata", seq=f["seq"]))
        except UserError as exc:
            messages.append(f"{f['filename']}: {exc}")
    job = start_job("process", _process, name, added, True) if added else None
    return ok(added=added, messages=messages, job=job)


@bp.post("/testdata/sandbox/<name>/delete")
def sandbox_delete(name):
    business.delete_sandbox(name)
    return ok()


@bp.post("/testdata/sandbox/<name>/mode")
def sandbox_mode(name):
    business.set_ai_mode(business.sandbox(name), body().get("mode", "replay"))
    return ok()

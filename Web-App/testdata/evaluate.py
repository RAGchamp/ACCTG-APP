"""Evaluation - the scorecard (plan §15.6).

Runs the real pipeline on a generated sandbox, file by file in the order the
owner would get them, acting out the owner script (accept, confirm flags,
feedback, corrections, rejections, month close), then compares every stage
with the answer key:

  intake     refused / duplicate files refused, everything else accepted
  classify   document count and types per file
  extract    field accuracy; figures wrong without a flag ("silently wrong", must be 0)
  propose    entries identical to the expected ones; owner edits needed
  match      support-only documents, double-counted money (must be 0)
  reports    TB, IS, BS, CF, equity identical to the cent
  tax        OS-114, Schedule C / SE identical to the cent
  cost       Claude time and cost

mode "replay" (mocked, free) replays Claude from the answer key; "live" calls
Claude Code for real.
"""

import json
import time
from collections import Counter
from datetime import datetime

import config
from ai import AI, AnswerKey
from books import business, post, propose, reconcile, review
from books.money import parse_cents
from claude_client import ClaudeError
from ingest import extract, intake
from reports import statements
from tax import federal, sales_tax_ct
from webcommon import UserError


# ------------------------------------------------------------------ comparisons

def _norm_text(x):
    return " ".join(str(x or "").lower().replace(",", " ").replace(".", " ").split())


def _money_eq(a, b):
    ca, cb = parse_cents(a) if a is not None else None, parse_cents(b) if b is not None else None
    if isinstance(a, str) and "[?]" in a or isinstance(b, str) and "[?]" in b:
        return str(a) == str(b)
    return ca == cb


FIELDS = {
    "purchase_receipt": {"money": ["subtotal", "tax", "total"], "text": ["vendor"], "exact": ["date", "is_refund"]},
    "credit_card_statement": {"money": ["opening_balance", "closing_balance"], "exact": ["card_last4", "period_end"]},
    "bank_statement": {"money": ["opening_balance", "closing_balance"], "exact": ["account_last4", "period_end"]},
    "check": {"money": ["amount_numeric"], "text": ["payee", "payer"], "exact": ["check_no", "direction", "date", "void"]},
    "cash_log": {"money": ["total_stated"], "exact": ["date"]},
    "pos_report": {"money": ["gross_sales", "discounts", "comps", "refunds", "net_sales", "tax", "tips",
                             "tender_card", "tender_cash", "fees", "net_card_deposit"], "exact": ["period_end"]},
    "platform_statement": {"money": ["subtotal", "commission", "marketing_fees", "promotions", "adjustments", "payout"],
                           "text": ["platform"], "exact": ["period_end"]},
    "payroll_report": {"money": ["gross_wages", "tips_paid", "employee_withholding", "net_pay", "employer_taxes",
                                 "total_debit"], "exact": ["pay_date"]},
}


def compare_extraction(got, want):
    """[(field, ok)] for the key fields of a document type."""
    t = want.get("doc_type")
    spec = FIELDS.get(t, {})
    out = [("doc_type", got.get("doc_type") == t)]
    for f in spec.get("money", []):
        out.append((f, _money_eq(got.get(f), want.get(f))))
    for f in spec.get("text", []):
        a, b = _norm_text(got.get(f)), _norm_text(want.get(f))
        out.append((f, a == b or (a and b and (a in b or b in a))))
    for f in spec.get("exact", []):
        out.append((f, str(got.get(f)) == str(want.get(f)) if want.get(f) is not None else True))
    if t == "purchase_receipt":
        out.append(("payment.method", (got.get("payment") or {}).get("method") == (want.get("payment") or {}).get("method")))
        gl, wl = got.get("lines") or [], want.get("lines") or []
        out.append(("lines", len(gl) == len(wl) and all(_money_eq(a.get("amount"), b.get("amount")) for a, b in zip(gl, wl))))
    if t in ("credit_card_statement", "bank_statement"):
        gt, wt = got.get("transactions") or [], want.get("transactions") or []
        out.append(("transactions.count", len(gt) == len(wt)))
        for i, w in enumerate(wt):
            g = gt[i] if i < len(gt) else {}
            out.append((f"transactions[{i + 1}]", _money_eq(g.get("amount"), w.get("amount"))
                        and str(g.get("date")) == str(w.get("date"))))
    if t == "cash_log":
        ge, we = got.get("entries") or [], want.get("entries") or []
        out.append(("entries", len(ge) == len(we) and all(_money_eq(a.get("amount"), b.get("amount"))
                                                           and a.get("kind") == b.get("kind") for a, b in zip(ge, we))))
    return out


def entry_multiset(entries):
    """The net effect per (date, account): the same bookkeeping whether the lines are
    netted ("Dr 1000 280.59") or kept gross ("Dr 1000 64.41, Cr 1000 45.00, Cr 1000 300.00")."""
    net = Counter()
    for e in entries:
        n = post.normalize(e)
        for l in n["lines"]:
            net[(n["date"], l["account"])] += l["debit_cents"] - l["credit_cents"]
    return Counter({(d, a, max(v, 0), max(-v, 0)): 1 for (d, a), v in net.items() if v})


def flatten(prefix, obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = v.get("account") if isinstance(v, dict) and v.get("account") else str(i)
            flatten(f"{prefix}[{key}]", v, out)
    elif isinstance(obj, bool) or isinstance(obj, int):
        out[prefix] = obj
    return out


REPORT_KEYS = ("trial_balance.total_debit", "trial_balance.balanced", "income_statement.gross_sales",
               "income_statement.net_sales", "income_statement.total_cogs", "income_statement.gross_profit",
               "income_statement.total_expenses", "income_statement.net_income", "balance_sheet.total_assets",
               "balance_sheet.total_liabilities", "balance_sheet.total_equity", "balance_sheet.balanced",
               "cash_flow.total_operating", "cash_flow.total_investing", "cash_flow.total_financing",
               "cash_flow.net_change", "cash_flow.end_cash", "cash_flow.ties", "equity.closing", "equity.ties")


def compare_numbers(got, want, keys=None):
    g, w = flatten("", got, {}), flatten("", want, {})
    keys = keys or sorted(set(w))
    return [{"field": k, "expected": w.get(k), "actual": g.get(k), "ok": g.get(k) == w.get(k)} for k in keys]


# ------------------------------------------------------------------ running the owner script

class Run:
    def __init__(self, biz, mode, progress=None):
        self.biz = biz
        self.ai = AI(biz, mode)
        self.mode = self.ai.mode
        self.key = AnswerKey(biz.answer_key_path).data
        self.progress = progress
        self.log = []

    def say(self, text):
        self.log.append(text)
        if self.progress:
            self.progress(text)

    def ensure_proposal(self, doc_id, errors_seen):
        with self.biz.session() as conn:
            p = review.open_proposal(conn, doc_id)
        if not p:
            pid = propose.propose(self.biz, doc_id, ai=self.ai)
            with self.biz.session() as conn:
                p = dict(conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())
        errors_seen.extend(json.loads(p["errors"]))
        return p

    def owner(self, doc_id, exp, rec):
        errors_seen = []
        steps = exp["owner"] or []
        try:
            self.ensure_proposal(doc_id, errors_seen)
        except (UserError, ClaudeError) as exc:
            rec["errors"].append(f"propose: {exc}")
        for step in steps:
            a = step["action"]
            try:
                if a == "correct":
                    extract.update_extraction(self.biz, doc_id, dict(exp["truth_extraction"]))
                    rec["owner_edits"] += 1
                    self.ensure_proposal(doc_id, errors_seen)
                elif a == "accept":
                    self.ensure_proposal(doc_id, errors_seen)
                    review.accept(self.biz, doc_id, confirm_flags=bool(step.get("confirm")))
                elif a in ("feedback", "answer"):
                    self.ensure_proposal(doc_id, errors_seen)
                    pid = propose.revise(self.biz, doc_id, step["text"], ai=self.ai)
                    rec["owner_edits"] += 1
                    with self.biz.session() as conn:
                        p = conn.execute("SELECT errors FROM proposals WHERE id=?", (pid,)).fetchone()
                        errors_seen.extend(json.loads(p["errors"]))
                        fb = conn.execute("SELECT id, rule_text FROM feedback WHERE proposal_after=?", (pid,)).fetchone()
                    if step.get("rule") and fb and fb["rule_text"]:
                        review.decide_rule(self.biz, fb["id"], "always")
                        rec["rules_created"] += 1
                elif a == "reject":
                    review.reject(self.biz, doc_id, step.get("reason", "rejected by the owner"))
                elif a == "reopen":
                    with self.biz.session() as conn:
                        post.reopen_month(conn, step["month"], step.get("note", "reopened for a late document"))
                elif a == "close":
                    reconcile.close(self.biz, step["month"], override_note=step.get("override"))
                elif a == "reverse_and_repost":
                    with self.biz.session() as conn:
                        ids = [e["id"] for e in conn.execute(
                            "SELECT id FROM journal_entries WHERE document_id=? AND kind='document'", (doc_id,))]
                        for eid in ids:
                            post.reverse_entry(conn, eid)
                        post.post_entry(conn, step["entry"], kind="adjustment", document_id=doc_id)
                    rec["owner_edits"] += 1
                elif a == "bulk":
                    self.ensure_proposal(doc_id, errors_seen)
                    with self.biz.session() as conn:
                        cands = review.bulk_candidates(conn)
                    rec["bulk_offered"] = doc_id in cands
                    review.bulk_accept(self.biz, [doc_id] if doc_id in cands else [])
            except (UserError, ClaudeError, ValueError) as exc:
                rec["errors"].append(f"{a}: {exc}")
        rec["propose_errors"] = errors_seen


def run(sandbox_name, mode=None, progress=None, reset=True):
    biz = business.reset_sandbox(sandbox_name) if reset else business.sandbox(sandbox_name)
    r = Run(biz, mode, progress)
    key = r.key
    started = time.monotonic()
    closes = sorted(key.get("close_months") or [], key=lambda c: c["after"])
    manual = sorted(key.get("manual") or [], key=lambda m: m["date"])
    files_out, docs_out = [], []
    for sha in key["file_order"]:
        f = key["files"][sha]
        while manual and manual[0]["date"] < f["available"]:
            m = manual.pop(0)
            with biz.session() as conn:
                post.post_entry(conn, m["entry"], kind="manual")
        while closes and closes[0]["after"] < f["available"]:
            c = closes.pop(0)
            try:
                reconcile.close(biz, c["month"])
                r.say(f"closed {c['month']}")
            except UserError as exc:
                files_out.append({"filename": f"(close {c['month']})", "ok": False, "notes": [str(exc)]})
        frec = {"filename": f["filename"], "expect": f["expect"], "ok": True, "notes": [], "documents": 0}
        files_out.append(frec)
        r.say(f"{f['filename']}")
        try:
            file_id = intake.add_file(biz, f["path"], source="testdata", seq=f["seq"])
        except UserError as exc:
            frec["intake"] = "refused"
            frec["ok"] = f["expect"] == "refuse"
            frec["notes"].append(str(exc))
            continue
        if f["expect"] == "refuse":
            frec["ok"] = False
            frec["notes"].append("expected the file to be refused at intake")
        if f.get("duplicate_upload"):
            try:
                intake.add_file(biz, f["path"], source="testdata", filename="copy-of-" + f["filename"])
                frec["ok"] = False
                frec["notes"].append("the duplicate upload was NOT refused")
            except intake.DuplicateFile:
                frec["notes"].append("duplicate upload refused (expected)")
        try:
            extract.extract_file(biz, file_id, ai=r.ai)
        except (ClaudeError, UserError, ValueError) as exc:
            frec["ok"] = False
            frec["notes"].append(f"extraction failed: {exc}")
            continue
        with biz.session() as conn:
            docs = [dict(d) for d in conn.execute("SELECT * FROM documents WHERE file_id=? ORDER BY part", (file_id,))]
        frec["documents"] = len(docs)
        want_docs = f["documents"]
        if f["expect"] == "reject":
            frec["ok"] = all(d["status"] == "rejected" for d in docs)
            if not frec["ok"]:
                frec["notes"].append("a non-financial page was not rejected")
            continue
        wanted_reply = f["extract_reply"]["documents"]
        if len(docs) != len(want_docs):
            frec["ok"] = False
            frec["notes"].append(f"{len(docs)} document(s) found, {len(want_docs)} expected")
        for d in docs:
            exp = want_docs.get(str(d["part"]))
            if not exp:
                continue
            got_ext = json.loads(d["extraction"])
            want_shown = {k: v for k, v in wanted_reply[d["part"] - 1].items() if k not in ("pages", "confidence")}
            fields = compare_extraction(got_ext, want_shown)
            flags = json.loads(d["flags"])
            flagged = any(fl["level"] in ("error", "warn") for fl in flags)
            rec = {"file": f["filename"], "part": d["part"], "document_id": d["id"], "key": exp["key"],
                   "doc_type": exp["doc_type"], "expect": exp["expect"], "features": exp["features"],
                   "fields_total": len(fields), "fields_ok": sum(1 for _, ok in fields if ok),
                   "fields_wrong": [n for n, ok in fields if not ok], "flagged": flagged,
                   "flags": [fl["message"] for fl in flags], "owner_edits": 0, "rules_created": 0, "errors": []}
            rec["silently_wrong"] = 0 if flagged else len(rec["fields_wrong"])
            missing_flags = [x for x in exp["flags_expected"] if not any(x.lower() in m.lower() for m in rec["flags"])]
            rec["missing_flags"] = missing_flags
            if d["status"] == "rejected":
                rec["status"] = "rejected"
            else:
                r.owner(d["id"], exp, rec)
            with biz.session() as conn:
                row = conn.execute("SELECT status FROM documents WHERE id=?", (d["id"],)).fetchone()
                rec["status"] = row["status"]
                got_entries = post.entries_json(conn, d["id"])
            want_status = {"post": "posted", "flag": "posted", "support": "support", "reject": "rejected"}[exp["expect"]]
            rec["status_ok"] = rec["status"] == want_status
            want_entries = exp["entries"] if exp["expect"] in ("post", "flag") else []
            rec["entries_ok"] = entry_multiset(got_entries) == entry_multiset(want_entries)
            if not rec["entries_ok"]:
                g, w = entry_multiset(got_entries), entry_multiset(want_entries)
                rec["entries_diff"] = {"missing": [list(k) for k in (w - g).elements()][:12],
                                       "extra": [list(k) for k in (g - w).elements()][:12]}
            exp_errs = exp.get("propose_errors_expected") or []
            rec["expected_errors_ok"] = all(any(x in e for e in rec.get("propose_errors", [])) for x in exp_errs)
            rec["ok"] = (rec["status_ok"] and rec["entries_ok"] and not rec["missing_flags"] and not rec["silently_wrong"]
                         and rec["expected_errors_ok"])
            docs_out.append(rec)
            if not rec["ok"]:
                frec["ok"] = False
    for m in manual:
        with biz.session() as conn:
            post.post_entry(conn, m["entry"], kind="manual")
    for c in closes:
        try:
            reconcile.close(biz, c["month"])
        except UserError as exc:
            files_out.append({"filename": f"(close {c['month']})", "ok": False, "notes": [str(exc)]})

    exp = key["expected"]
    period = exp["period"]
    with biz.session() as conn:
        summary = statements.summary(conn, period["start"], period["end"])
        report_cmp = compare_numbers(summary, exp["reports"], list(REPORT_KEYS))
        os_cmp = []
        for month, want in exp["os114"].items():
            ws = sales_tax_ct.worksheet(conn, month)
            for k, v in want.items():
                os_cmp.append({"field": f"{month}.{k}", "expected": v, "actual": ws[k], "ok": ws[k] == v})
        year = int(period["start"][:4])
        sch = federal.schedule_c(conn, year)
        sc_cmp = [{"field": f"line {k}", "expected": v, "actual": sch["L"].get(k), "ok": sch["L"].get(k) == v}
                  for k, v in exp["schedule_c"].items()]
        se = federal.schedule_se(sch["L"]["31"], year)
        sc_cmp.append({"field": "SE tax", "expected": exp["schedule_se"]["se_tax"], "actual": se["se_tax"],
                       "ok": se["se_tax"] == exp["schedule_se"]["se_tax"]})
        recs = []
        want_rec = exp.get("reconciled", {})
        for m in exp["gl_month_end"]:
            rec = reconcile.reconcile_month(conn, m)
            for a in rec["accounts"]:
                k = f"{m} {a['account']}"
                if a["statement_closing"] is not None or k in want_rec:
                    recs.append({"field": f"{k} reconciled", "expected": want_rec.get(k, True),
                                 "actual": a["reconciled"], "ok": a["reconciled"] == want_rec.get(k, True),
                                 "statement": a["statement_closing"], "gl": a["gl_balance"]})
        calls = [dict(x) for x in conn.execute("SELECT kind, seconds, cost_usd, ok FROM ai_calls")]
        tb_zero = post.trial_balance_nets_to_zero(conn)
        double = double_counting(conn)
    seconds = round(time.monotonic() - started, 1)
    result = scorecard(sandbox_name, r.mode, key, files_out, docs_out, report_cmp, os_cmp, sc_cmp, recs, calls,
                       seconds, tb_zero, double)
    save(result)
    return result


def double_counting(conn):
    """Money booked twice: an expense and a settled clearing item for the same receipt shows up
    as a clearing account (2150) with the wrong sign or statement lines matching the same item twice."""
    dup = conn.execute("SELECT other_ref, COUNT(*) n FROM matches WHERE other_ref LIKE 'jl:%' GROUP BY other_ref "
                       "HAVING n > 1").fetchall()
    return len(dup)


def pct(a, b):
    return round(100.0 * a / b, 1) if b else 100.0


def scorecard(sandbox, mode, key, files_out, docs_out, report_cmp, os_cmp, sc_cmp, recs, calls, seconds, tb_zero,
              double):
    by_type = {}
    for d in docs_out:
        t = by_type.setdefault(d["doc_type"], {"docs": 0, "fields": 0, "fields_ok": 0, "entries_ok": 0, "ok": 0})
        t["docs"] += 1
        t["fields"] += d["fields_total"]
        t["fields_ok"] += d["fields_ok"]
        t["entries_ok"] += int(d["entries_ok"])
        t["ok"] += int(d["ok"])
    stages = {
        "intake": {"ok": sum(1 for f in files_out if f.get("ok", True) or f.get("intake")), "total": len(files_out)},
        "extract": {"fields_ok": sum(d["fields_ok"] for d in docs_out), "fields": sum(d["fields_total"] for d in docs_out),
                    "silently_wrong": sum(d["silently_wrong"] for d in docs_out),
                    "missing_flags": sum(len(d["missing_flags"]) for d in docs_out)},
        "propose": {"entries_ok": sum(1 for d in docs_out if d["entries_ok"]), "docs": len(docs_out),
                    "owner_edits": sum(d["owner_edits"] for d in docs_out),
                    "status_ok": sum(1 for d in docs_out if d["status_ok"])},
        "match": {"support_ok": sum(1 for d in docs_out if d["expect"] == "support" and d["status_ok"]),
                  "support": sum(1 for d in docs_out if d["expect"] == "support"), "double_counted": double},
        "reports": {"ok": sum(1 for x in report_cmp if x["ok"]), "total": len(report_cmp), "tb_zero": tb_zero},
        "reconcile": {"ok": sum(1 for x in recs if x["ok"]), "total": len(recs)},
        "tax": {"ok": sum(1 for x in os_cmp + sc_cmp if x["ok"]), "total": len(os_cmp + sc_cmp)},
        "cost": {"calls": len(calls), "seconds": round(sum(c["seconds"] or 0 for c in calls), 1),
                 "usd": round(sum(c["cost_usd"] or 0 for c in calls), 4), "failed": sum(1 for c in calls if not c["ok"])},
    }
    passed = (all(f.get("ok", True) for f in files_out) and all(d["ok"] for d in docs_out)
              and all(x["ok"] for x in report_cmp + os_cmp + sc_cmp + recs) and tb_zero and not double)
    return {"sandbox": sandbox, "scenario": key.get("scenario"), "title": key.get("title"), "mode": mode,
            "difficulty": key.get("difficulty"), "seed": key.get("seed"), "at": datetime.now().isoformat(timespec="seconds"),
            "seconds": seconds, "passed": passed, "stages": stages, "by_type": by_type, "files": files_out,
            "documents": docs_out, "reports": report_cmp, "os114": os_cmp, "schedule_c": sc_cmp, "reconciliation": recs,
            "features": key.get("features", [])}


def save(result):
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{result['at'][:19].replace(':', '').replace('-', '')}-{result['sandbox']}-{result['mode']}"
    (config.RESULTS_DIR / f"{stem}.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    from testdata import scorecard_html
    (config.RESULTS_DIR / f"{stem}.html").write_text(scorecard_html.render(result), encoding="utf-8")
    result["files_saved"] = [str(config.RESULTS_DIR / f"{stem}.json"), str(config.RESULTS_DIR / f"{stem}.html")]
    return result


def list_results():
    out = []
    for p in sorted(config.RESULTS_DIR.glob("*.json"), reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"file": p.name, "html": p.with_suffix(".html").name, "sandbox": r["sandbox"],
                    "scenario": r.get("scenario"), "mode": r["mode"], "at": r["at"], "passed": r["passed"],
                    "stages": r["stages"]})
    return out

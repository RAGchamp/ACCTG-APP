"""Review input documents (INFO\\DOC-REVIEW-REPROCESS-PLAN.md): every input document of the
live books and the sandboxes, its scan next to what was extracted and where it landed
(ledger, financial statements, tax), owner feedback saved as JSON, and re-processing.

Unlike the other screens, every route names its books explicitly (`live` or a sandbox
name); this screen never reads or changes the open books (data\\active.json).
"""

import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file

import config
from ai import AI
from books import business, db, feedback_files, propose, review
from books.post import PostingError
from claude_client import ClaudeError
from ingest import extract
from ingest.pdf_utils import PdfError, open_pdf, pdf_lock
from reports.tagging import tag_document
from web.pages import DOC_LABELS, STATUS_LABELS
from webcommon import UserError, start_job

bp = Blueprint("doc_review", __name__)
api_bp = Blueprint("doc_review_api", __name__, url_prefix="/api/doc-review")


@api_bp.errorhandler(UserError)
@api_bp.errorhandler(ValueError)
@api_bp.errorhandler(PostingError)
def user_error(exc):
    return jsonify(ok=False, error=str(exc)), 400


def _books(name):
    """The named books, or 404 for a page / 400 for the API (business.get validates the name)."""
    try:
        return business.get(name)
    except ValueError:
        abort(404)


def _title(d):
    parts = [DOC_LABELS.get(d["doc_type"], d["doc_type"]), d["party"], d["doc_date"]]
    if d["total_cents"] is not None:
        parts.append(f"${d['total_cents'] / 100:,.2f}")
    return " — ".join(p for p in parts if p)


def _rows(biz):
    with biz.session() as conn:
        rows = conn.execute(
            "SELECT d.id, d.part, d.doc_type, d.doc_date, d.party, d.total_cents, d.status, d.pages, "
            "f.id AS file_id, f.filename, f.seq, f.kind FROM documents d JOIN files f ON f.id=d.file_id "
            "ORDER BY f.seq, f.id, d.part").fetchall()
    return [{"key": f"{biz.name}:{r['id']}", "books": biz.name, "books_kind": biz.kind, "id": r["id"],
             "title": _title(r), "doc_type": r["doc_type"], "type_label": DOC_LABELS.get(r["doc_type"], r["doc_type"]),
             "date": r["doc_date"] or "", "month": (r["doc_date"] or "")[:7],
             "total": None if r["total_cents"] is None else f"{r['total_cents'] / 100:,.2f}",
             "status": r["status"], "status_label": STATUS_LABELS.get(r["status"], r["status"]),
             "filename": r["filename"], "file_id": r["file_id"], "pages": json.loads(r["pages"] or "[1]")}
            for r in rows]


# ------------------------------------------------------------------ pages

@bp.get("/doc-review")
def doc_review():
    return render_template("doc_review.html", sandboxes_all=business.list_sandboxes(),
                           max_docs=config.DOC_REVIEW_MAX)


@api_bp.get("/documents")
def documents():
    wanted = set((request.args.get("books") or "").split(","))
    which = request.args.get("sandbox") or "all"
    out = []
    if "live" in wanted:
        out += _rows(business.live())
    if "sandbox" in wanted:
        names = business.list_sandboxes() if which == "all" else [which]
        for name in names:
            out += _rows(business.sandbox(name))
    return jsonify(ok=True, documents=out)


def _parse_keys(values):
    keys, seen = [], set()
    for v in values:
        books, _, doc_id = (v or "").rpartition(":")
        if books and doc_id.isdigit() and (books, int(doc_id)) not in seen:
            seen.add((books, int(doc_id)))
            keys.append((books, int(doc_id)))
    return keys


def _earlier_feedback(biz, filename, doc_id):
    out = []
    for p in sorted(config.FEEDBACK_DIR.glob(f"{feedback_files.safe_stem(filename)}-*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("books", {}).get("name") == biz.name and rec.get("document", {}).get("id") == doc_id:
            res = rec.get("reprocess_result") or {}
            out.append({"file": p.name, "saved_at": rec.get("saved_at"), "text": rec["feedback"].get("text"),
                        "action": rec["feedback"].get("reprocess", {}).get("action"),
                        "result": "running" if rec["feedback"].get("reprocess", {}).get("action") not in (None, "none")
                        and not res else ("ok" if res.get("ok") else res.get("error"))})
    return out


def _box(books, doc_id):
    biz = business.get(books)
    with biz.session() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return {"key": f"{books}:{doc_id}", "missing": True}
        f = dict(conn.execute("SELECT * FROM files WHERE id=?", (doc["file_id"],)).fetchone())
        csv_text = None
        if f["kind"] == "csv":
            row = conn.execute("SELECT text FROM pages WHERE file_id=? AND page_no=1", (f["id"],)).fetchone()
            csv_text = row["text"] if row else ""
        siblings = conn.execute("SELECT COUNT(*) FROM documents WHERE file_id=?", (f["id"],)).fetchone()[0]
        tagging = tag_document(conn, doc_id)
        has_proposal = bool(conn.execute("SELECT 1 FROM proposals WHERE document_id=?", (doc_id,)).fetchone())
    d = dict(doc)
    return {"key": f"{books}:{doc_id}", "books": books, "biz": biz, "doc": d, "file": f,
            "title": _title(doc), "pages": json.loads(d["pages"] or "[1]"),
            "extraction": json.loads(d["extraction"]) if d["extraction"] else {},
            "flags": json.loads(d["flags"]) if d["flags"] else [], "tagging": tagging, "csv_text": csv_text,
            "siblings": siblings, "has_proposal": has_proposal,
            "earlier": _earlier_feedback(biz, f["filename"], doc_id)}


@bp.get("/doc-review/show")
def show():
    keys = _parse_keys(request.args.getlist("d"))
    too_many = len(keys) > config.DOC_REVIEW_MAX
    boxes = []
    for books, doc_id in keys[:config.DOC_REVIEW_MAX]:
        try:
            boxes.append(_box(books, doc_id))
        except ValueError:
            boxes.append({"key": f"{books}:{doc_id}", "missing": True})
    return render_template("doc_review_show.html", boxes=boxes, too_many=too_many, selected=len(keys),
                           max_docs=config.DOC_REVIEW_MAX, categories=feedback_files.CATEGORIES,
                           text_max=config.FEEDBACK_TEXT_MAX, active_name=business.active_name(),
                           back=request.query_string.decode())


@bp.get("/doc-review/<books>/files/<int:file_id>/page/<int:page_no>.jpg")
def view_image(books, file_id, page_no):
    """A page resized to about config.VIEW_IMAGE_WIDTH px (JPEG), cached next to the page PNG."""
    biz = _books(books)
    with biz.session() as conn:
        f = conn.execute("SELECT stored_path, kind FROM files WHERE id=?", (file_id,)).fetchone()
    if not f or f["kind"] != "pdf" or not Path(f["stored_path"]).exists():
        abort(404)
    cache = Path(f["stored_path"]).parent / f"page-{page_no:03d}.view.jpg"
    if not cache.exists():
        with pdf_lock:
            try:
                pdf = open_pdf(f["stored_path"])
            except PdfError:
                abort(404)
            try:
                if not 1 <= page_no <= pdf.page_count:
                    abort(404)
                page = pdf[page_no - 1]
                dpi = max(40, min(200, int(config.VIEW_IMAGE_WIDTH * 72 / max(page.rect.width, 1))))
                data = page.get_pixmap(dpi=dpi).tobytes("jpg", jpg_quality=80)
            finally:
                pdf.close()
        cache.write_bytes(data)
    return send_file(cache, mimetype="image/jpeg", max_age=0)


@bp.get("/doc-review/<books>/files/<int:file_id>/original")
def original(books, file_id):
    biz = _books(books)
    with biz.session() as conn:
        f = conn.execute("SELECT stored_path, filename, kind FROM files WHERE id=?", (file_id,)).fetchone()
    if not f:
        abort(404)
    return send_file(f["stored_path"], download_name=f["filename"],
                     mimetype="application/pdf" if f["kind"] == "pdf" else "text/csv")


# ------------------------------------------------------------------ feedback and re-processing

def _flag(value):
    return value in (True, 1, "1", "true", "on", "yes")


@api_bp.get("/<books>/<int:doc_id>/impact")
def impact(books, doc_id):
    biz = business.get(books)
    action = request.args.get("action", "repropose")
    if action not in feedback_files.ACTIONS or action == "none":
        raise UserError("Choose a re-process option.")
    with biz.session() as conn:
        out = review.reprocess_impact(conn, doc_id, action, _flag(request.args.get("together")))
    return jsonify(ok=True, impact=out)


@api_bp.post("/<books>/<int:doc_id>/feedback")
def feedback(books, doc_id):
    biz = business.get(books)
    b = request.get_json(silent=True) or {}
    categories = [c for c in (b.get("categories") or []) if c in feedback_files.CATEGORIES]
    text = (b.get("text") or "").strip()
    action = b.get("action") or "none"
    together = _flag(b.get("together"))
    higher_dpi = _flag(b.get("higher_dpi"))
    live = _flag(b.get("live_claude")) and biz.ai_mode == "replay"
    if action not in feedback_files.ACTIONS:
        raise UserError("Unknown re-process option.")
    if not categories and not text:
        raise UserError("Tick what is wrong or write your feedback.")
    if len(text) > config.FEEDBACK_TEXT_MAX:
        raise UserError(f"Feedback is limited to {config.FEEDBACK_TEXT_MAX} characters.")
    if action == "repropose" and not text:
        raise UserError("Write what should change: the Bookkeeper re-proposes from your words.")
    corrected = None
    if action == "corrected":
        corrected = b.get("corrected_extraction")
        if isinstance(corrected, str):
            try:
                corrected = json.loads(corrected)
            except ValueError as exc:
                raise UserError(f"The corrected figures are not valid JSON: {exc}")
        if not isinstance(corrected, dict):
            raise UserError("Paste the corrected extracted data (a JSON object).")
    with biz.session() as conn:
        if not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            raise UserError(f"No document #{doc_id} in {books}.")
        blocked = review.reprocess_impact(conn, doc_id, action, together)["blocked"] if action != "none" else None
    fb = {"categories": categories, "category_labels": [feedback_files.CATEGORIES[c] for c in categories],
          "text": text, "corrected_extraction": corrected,
          "reprocess": {"action": action, "higher_dpi": higher_dpi, "live_claude": live, "together": together}}
    path = feedback_files.save(biz, doc_id, fb)        # always first: feedback is never lost
    with biz.session() as conn:
        db.audit(conn, "doc_feedback", document_id=doc_id, file=path.name, reprocess=action)
    if action == "none":
        return jsonify(ok=True, file=path.name, path=str(path))
    if blocked:
        feedback_files.update_result(path, {"ok": False, "blocked": True, "error": blocked,
                                            "finished_at": datetime.now().isoformat(timespec="seconds")})
        return jsonify(ok=True, file=path.name, path=str(path), blocked=blocked)
    job = start_job("doc_reprocess", reprocess, books, doc_id, action, text, higher_dpi, live, together, corrected,
                    str(path))
    return jsonify(ok=True, file=path.name, path=str(path), job=job)


def _latest_proposal(biz, doc_id):
    with biz.session() as conn:
        p = conn.execute("SELECT * FROM proposals WHERE document_id=? ORDER BY version DESC LIMIT 1",
                         (doc_id,)).fetchone()
        status = conn.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
    if not p:
        return {"document_status": status}
    return {"document_status": status, "version": p["version"], "source": p["source"], "status": p["status"],
            "confidence": p["confidence"], "reason": p["reason"], "entries": json.loads(p["entries"]),
            "errors": json.loads(p["errors"]), "questions": json.loads(p["questions"])}


def reprocess(books, doc_id, action, text, higher_dpi, live, together, corrected, path):
    """The re-process job (plan §5.3). Never posts: the result waits on the Review screen (R4)."""
    biz = business.get(books)
    ai = AI(biz, mode="live" if live else None)
    result = {"started_at": datetime.now().isoformat(timespec="seconds"), "action": action,
              "claude_mode": ai.mode, "ok": False, "error": None, "reversed_entries": [],
              "requeued_documents": [], "dependants_to_propose": [], "new_proposal": None}
    try:
        unposted = review.unpost_document(biz, doc_id, action, together, note=f"feedback {Path(path).name}")
        result["reversed_entries"] = unposted["reversals"]
        if together:
            result["dependants_to_propose"] = [d["id"] for d in unposted["dependants"]]
        if action == "repropose":
            with biz.session() as conn:
                had = conn.execute("SELECT 1 FROM proposals WHERE document_id=?", (doc_id,)).fetchone()
            if had:
                propose.revise(biz, doc_id, text, ai=ai, limit=config.FEEDBACK_TEXT_MAX)
            else:
                propose.propose(biz, doc_id, ai=ai, extra=f"\nTHE OWNER'S FEEDBACK ON THIS DOCUMENT\n\"{text}\"\n")
            requeued = [doc_id]
        elif action == "reextract":
            with biz.session() as conn:
                file_id = conn.execute("SELECT file_id FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
            requeued = extract.extract_file(biz, file_id, ai=ai, dpi=config.RETRY_DPI if higher_dpi else None,
                                            owner_note=text, in_place=True)
            with biz.session() as conn:
                to_propose = [r[0] for r in conn.execute(
                    f"SELECT id FROM documents WHERE id IN ({','.join('?' * len(requeued))}) AND status='extracted' "
                    "ORDER BY part", requeued)] if requeued else []
            for i in to_propose:
                propose.propose(biz, i, ai=ai)
        else:
            extract.update_extraction(biz, doc_id, corrected)
            propose.propose(biz, doc_id, ai=ai)
            requeued = [doc_id]
        result.update(ok=True, requeued_documents=requeued, new_proposal=_latest_proposal(biz, doc_id))
    except (ClaudeError, UserError, ValueError, PostingError) as exc:
        result["error"] = str(exc)
        result["new_proposal"] = _latest_proposal(biz, doc_id)
    finally:
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        feedback_files.update_result(path, result)
    msgs = [] if result["ok"] else [f"Re-processing failed: {result['error']}"]
    if result["ok"] and result["dependants_to_propose"]:
        msgs.append("Accept the re-processed document on the Review screen first, then run the Bookkeeper on "
                    + ", ".join(f"#{i}" for i in result["dependants_to_propose"]) + ".")
    return {"ok": result["ok"], "errors": msgs, "file": Path(path).name}

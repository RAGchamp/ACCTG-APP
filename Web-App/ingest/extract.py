"""Step 2 - Extract (plan §5.2): a file -> its documents as checked JSON.

Adapted from the analyzer's ingest\\ocr_transcribe.py: page images (or the
text layer of a downloaded PDF) go to Claude with one prompt that finds every
document in the file (the Classifier and Extractor steps in one call), the
reply is parsed from its ```json block, and code checks follow (checks.py).
CSV exports are parsed without Claude (csv_import.py).
"""

import json
import logging
from pathlib import Path

import config
from ai import AI
from books import db
from claude_client import ClaudeError, fill_prompt, system_prompt
from ingest import checks, csv_import
from ingest.intake import render_pages
from webcommon import UserError

log = logging.getLogger(__name__)
MAX_PAGES = 12


def schemas_text():
    parts = []
    for t in checks.DOC_TYPES:
        path = config.SCHEMAS_DIR / f"{t}.json"
        if path.exists():
            parts.append(f"{t}:\n{path.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def owner_note_text(note):
    """The OWNER NOTE slot of extract.txt: the owner's feedback on an earlier reading of
    this file (Review input documents, re-extract). Empty for a normal extraction."""
    note = (note or "").strip()
    if not note:
        return ""
    return ("- NOTE FROM THE OWNER about an earlier reading of this file (read those figures again with care; "
            f"still copy only what is printed or written): {note}\n")


def build_prompt(file_row, pages, year, owner_note=""):
    if file_row["scanned"]:
        names = ", ".join(Path(p["image_path"]).name for p in pages)
        source = (f"Read the image files {names} in the current folder. They are the {len(pages)} page(s), in order, "
                  f"of one scanned PDF ({file_row['filename']}) from the shop's paperwork.")
    else:
        body = "\n\n".join(f"--- PAGE {p['page_no']} ---\n{p['text']}" for p in pages)
        source = (f"Below is the text of a {len(pages)}-page PDF ({file_row['filename']}) downloaded or scanned "
                  f"from the shop's paperwork, page by page.\n\n{body}\n\n--- END OF PDF ---")
    return fill_prompt("extract.txt", SOURCE_INSTRUCTION=source, SCHEMAS=schemas_text(), YEAR=year,
                       OWNER_NOTE=owner_note_text(owner_note))


def _load(biz, file_id):
    with biz.session() as conn:
        f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if not f:
            raise UserError(f"No file #{file_id}")
        pages = [dict(p) for p in conn.execute("SELECT * FROM pages WHERE file_id=? ORDER BY page_no", (file_id,))]
        prof = db.profile(conn)
    return dict(f), pages, prof


def clear_documents(conn, file_id):
    """Before a re-extraction: forget the file's documents, unless one is booked."""
    booked = conn.execute(
        "SELECT COUNT(*) FROM documents d JOIN journal_entries e ON e.document_id=d.id WHERE d.file_id=?",
        (file_id,)).fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM documents d JOIN matches m ON m.document_id=d.id OR m.other_document_id=d.id "
        "WHERE d.file_id=?", (file_id,)).fetchone()[0]
    if booked or matched:
        raise UserError("This file's documents are already booked or matched; reverse those entries first.")
    ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE file_id=?", (file_id,))]
    for doc_id in ids:
        conn.execute("DELETE FROM proposals WHERE document_id=?", (doc_id,))
        conn.execute("DELETE FROM feedback WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE file_id=?", (file_id,))


def check_in_place(conn, file_id):
    """Before re-extracting a file whose documents have ledger history: nothing of it may
    still be booked (not reversed) or matched. books.review.unpost_document() does that."""
    ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE file_id=?", (file_id,))]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    active = conn.execute(
        f"SELECT COUNT(*) FROM journal_entries e WHERE e.document_id IN ({marks}) AND e.kind <> 'reversal' "
        "AND e.id NOT IN (SELECT reverses FROM journal_entries WHERE reverses IS NOT NULL)", ids).fetchone()[0]
    matched = conn.execute(f"SELECT COUNT(*) FROM matches WHERE document_id IN ({marks}) OR other_document_id IN "
                           f"({marks})", ids + ids).fetchone()[0]
    if active or matched:
        raise UserError("This file's documents are still booked or matched; un-post them first.")


def extract_file(biz, file_id, ai=None, dpi=None, owner_note="", in_place=False):
    """Extract every document of a file. Returns the new document ids.

    in_place=True (re-processing from the Review input documents screen): the file's
    document rows are kept and updated part by part, because reversed entries and
    feedback still refer to them; see store_documents()."""
    ai = ai or AI(biz)
    f, pages, prof = _load(biz, file_id)
    if dpi and f["kind"] == "pdf":
        render_pages(biz, file_id, dpi)
        f, pages, prof = _load(biz, file_id)
    with biz.session() as conn:
        if in_place:
            check_in_place(conn, file_id)
        else:
            clear_documents(conn, file_id)
        conn.execute("UPDATE files SET status='extracting', error=NULL WHERE id=?", (file_id,))
    try:
        if f["kind"] == "csv":
            docs = csv_import.parse(pages[0]["text"])
            for d in docs:
                d.setdefault("pages", [1])
                d.setdefault("confidence", "high")
        else:
            if len(pages) > MAX_PAGES:
                raise UserError(f"{f['filename']} has {len(pages)} pages; split it into files of at most "
                                f"{MAX_PAGES} pages.")
            prompt = build_prompt(f, pages, prof["tax_year"], owner_note)
            data, _meta = ai.call("extract", prompt, system_prompt("extract_system.txt"),
                                  folder=Path(f["stored_path"]).parent, file_id=file_id,
                                  images=[Path(p["image_path"]).name for p in pages] if f["scanned"] else None,
                                  replay=(f["sha256"], None))
            docs = data.get("documents") if isinstance(data, dict) else None
            if not isinstance(docs, list) or not docs:
                raise ClaudeError("Claude found no documents in the reply.")
    except (ClaudeError, UserError, csv_import.CsvFormatError) as exc:
        with biz.session() as conn:
            conn.execute("UPDATE files SET status='failed', error=? WHERE id=?", (str(exc)[:1000], file_id))
        raise
    return store_documents(biz, file_id, docs, prof, in_place=in_place)


def _prev_month(m):
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}"


def continuity_flags(conn, d, prof):
    """A statement's opening balance must be the previous statement's closing balance,
    and no month may be missing (plan §5.3)."""
    t = d.get("doc_type")
    if t not in ("credit_card_statement", "bank_statement"):
        return []
    last4 = d.get("card_last4") or d.get("account_last4")
    start = d.get("period_start") or ""
    if not start:
        return []
    prev = None
    for row in conn.execute("SELECT extraction FROM documents WHERE doc_type=? AND status <> 'rejected'", (t,)):
        e = json.loads(row["extraction"])
        if (e.get("card_last4") or e.get("account_last4")) != last4:
            continue
        if (e.get("period_end") or "") < start and (not prev or e["period_end"] > prev["period_end"]):
            prev = e
    what = "card" if t == "credit_card_statement" else "bank"
    if not prev:
        if start[:7] > prof["books_start"][:7]:
            return [{"level": "warn", "field": "period_start",
                     "message": f"gap in {what} statements: no earlier statement since the books start "
                                f"({prof['books_start']}) - is one missing?"}]
        return []
    flags = []
    if prev["period_end"][:7] != _prev_month(start[:7]):
        flags.append({"level": "warn", "field": "period_start",
                      "message": f"gap in {what} statements: nothing between {prev['period_end']} and {start}"})
    if checks.c(prev.get("closing_balance")) != checks.c(d.get("opening_balance")):
        flags.append({"level": "warn", "field": "opening_balance",
                      "message": f"opening balance {d.get('opening_balance')} is not the previous statement's closing "
                                 f"balance {prev.get('closing_balance')}"})
    return flags


def store_documents(biz, file_id, docs, prof, in_place=False):
    ids = []
    with biz.session() as conn:
        existing = {}
        if in_place:
            existing = {r["part"]: r["id"] for r in conn.execute("SELECT id, part FROM documents WHERE file_id=?",
                                                                (file_id,))}
            conn.execute("UPDATE proposals SET status='superseded' WHERE status IN ('open','accepted') AND "
                         "document_id IN (SELECT id FROM documents WHERE file_id=?)", (file_id,))
        for part, d in enumerate(docs, 1):
            d = dict(d)
            pages = d.pop("pages", None) or [1]
            doc_type = d.get("doc_type") or "other"
            if doc_type not in checks.DOC_TYPES:
                doc_type = d["doc_type"] = "other"
            if doc_type == "not_financial":
                flags, status, reason = [], "rejected", "not a financial document"
            else:
                flags, status, reason = checks.check(d, prof["books_start"]), "extracted", None
                flags += continuity_flags(conn, d, prof)
            if doc_type == "check" and d.get("void"):
                status, reason = "rejected", "voided check - nothing to post"
            date, party, total = checks.summary(d)
            if part in existing:
                conn.execute(
                    "UPDATE documents SET pages=?, doc_type=?, doc_date=?, party=?, total_cents=?, extraction=?,"
                    " flags=?, status=?, reject_reason=? WHERE id=?",
                    (json.dumps(pages), doc_type, date, party, total, json.dumps(d), json.dumps(flags), status,
                     reason, existing.pop(part)))
                ids.append(conn.execute("SELECT id FROM documents WHERE file_id=? AND part=?",
                                        (file_id, part)).fetchone()[0])
                continue
            cur = conn.execute(
                "INSERT INTO documents(file_id, part, pages, doc_type, doc_date, party, total_cents, extraction,"
                " flags, status, reject_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, part, json.dumps(pages), doc_type, date, party, total, json.dumps(d), json.dumps(flags),
                 status, reason, db.now()))
            ids.append(cur.lastrowid)
        for doc_id in existing.values():
            # A document the earlier reading found but this one did not.
            conn.execute("UPDATE documents SET status='rejected', reject_reason=? WHERE id=?",
                         ("not found when the file was extracted again", doc_id))
        conn.execute("UPDATE files SET status='extracted' WHERE id=?", (file_id,))
        db.audit(conn, "extract", file_id=file_id, documents=ids)
    return ids


def update_extraction(biz, document_id, extraction):
    """The owner corrected an extracted figure on the Review screen: re-run the checks."""
    with biz.session() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if doc["status"] in ("posted", "support"):
            raise UserError("This document is already booked; reverse its entries first.")
        prof = db.profile(conn)
        extraction["doc_type"] = extraction.get("doc_type") or doc["doc_type"]
        flags = checks.check(extraction, prof["books_start"])
        date, party, total = checks.summary(extraction)
        conn.execute("UPDATE documents SET extraction=?, flags=?, doc_type=?, doc_date=?, party=?, total_cents=?,"
                     " status='extracted' WHERE id=?",
                     (json.dumps(extraction), json.dumps(flags), extraction["doc_type"], date, party, total,
                      document_id))
        conn.execute("UPDATE proposals SET status='superseded' WHERE document_id=? AND status='open'", (document_id,))
        db.audit(conn, "edit_extraction", document_id=document_id)
    return flags

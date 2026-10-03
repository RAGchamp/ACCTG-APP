"""Owner feedback on an input document, one JSON file each (INFO\\DOC-REVIEW-REPROCESS-PLAN.md §4.4).

    <config.FEEDBACK_DIR>\\<input file name without extension>-<YYYYMMDD-HHMMSS>.json

The timestamp is the local time the owner clicked Save. The file holds the path to the
input file, the extracted data, the tagging as the owner saw it (ledger, statements, tax)
and the feedback. When a re-process job ends, its result is added to the same file.
"""

import json
import re
from datetime import datetime
from pathlib import Path

import config
from books.money import to_decimal_str
from reports.tagging import tag_document

SCHEMA_VERSION = 1
CATEGORIES = {
    "figures_wrong": "figures read wrong",
    "wrong_account": "wrong account / debit-credit",
    "wrong_statement_line": "wrong financial statement line",
    "wrong_tax": "wrong tax treatment",
    "not_business": "not a business document",
    "other": "other",
}
ACTIONS = ("none", "repropose", "reextract", "corrected")
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_stem(filename):
    # Replace first: on Windows "a:b.pdf" would otherwise read as drive "a:".
    return Path(UNSAFE.sub("_", filename or "document")).stem.strip(" .") or "document"


def create_path(filename, when, folder=None):
    """A new, unused feedback file path; '-2', '-3' ... is added if the name is taken
    (two documents of one file saved in the same second). The file is created empty."""
    folder = Path(folder or config.FEEDBACK_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{safe_stem(filename)}-{when:%Y%m%d-%H%M%S}"
    n = 1
    while True:
        path = folder / (f"{base}.json" if n == 1 else f"{base}-{n}.json")
        try:
            with open(path, "x", encoding="utf-8"):
                return path
        except FileExistsError:
            n += 1


def source_path(biz, file_row):
    """Where the input file came from, when known: the generated file of a sandbox, or the
    copy moved to Inbox\\processed\\. An upload has no other copy (None)."""
    if biz.is_sandbox and biz.answer_key_path.exists():
        try:
            key = json.loads(biz.answer_key_path.read_text(encoding="utf-8"))
            f = key.get("files", {}).get(file_row["sha256"])
            if f and f.get("path"):
                return f["path"]
        except (OSError, ValueError):
            pass
    if file_row["source"] == "inbox":
        p = config.INBOX_DIR / "processed" / file_row["filename"]
        return str(p) if p.exists() else None
    return None


def build_record(biz, document_id, feedback, when):
    """Everything about the document as it is now, plus the owner's feedback."""
    with biz.session() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if not doc:
            raise ValueError(f"No document #{document_id}")
        f = conn.execute("SELECT * FROM files WHERE id=?", (doc["file_id"],)).fetchone()
        pages = json.loads(doc["pages"] or "[1]")
        images = [r["image_path"] for r in conn.execute(
            "SELECT page_no, image_path FROM pages WHERE file_id=? ORDER BY page_no", (f["id"],))
            if r["page_no"] in pages and r["image_path"]]
        prop = conn.execute("SELECT * FROM proposals WHERE document_id=? ORDER BY version DESC LIMIT 1",
                            (document_id,)).fetchone()
        tagging = tag_document(conn, document_id)
    proposal = None
    if prop:
        proposal = {"version": prop["version"], "source": prop["source"], "status": prop["status"],
                    "confidence": prop["confidence"], "reason": prop["reason"],
                    "questions": json.loads(prop["questions"]), "errors": json.loads(prop["errors"]),
                    "match_hints": json.loads(prop["matches"])}
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": when.isoformat(timespec="seconds"),
        "books": {"name": biz.name, "kind": biz.kind, "ai_mode": biz.ai_mode},
        "input_file": {"filename": f["filename"], "path": f["stored_path"], "source_path": source_path(biz, f),
                       "source": f["source"], "sha256": f["sha256"], "kind": f["kind"],
                       "scanned": None if f["scanned"] is None else bool(f["scanned"]),
                       "synthetic": bool(f["synthetic"]), "pages": pages, "page_images": images},
        "document": {"id": doc["id"], "part": doc["part"], "doc_type": doc["doc_type"], "status": doc["status"],
                     "party": doc["party"], "date": doc["doc_date"],
                     "total": None if doc["total_cents"] is None else to_decimal_str(doc["total_cents"]),
                     "reject_reason": doc["reject_reason"]},
        "extracted_data": json.loads(doc["extraction"]) if doc["extraction"] else None,
        "flags": json.loads(doc["flags"]) if doc["flags"] else [],
        "proposal": proposal,
        "tagging": tagging,
        "feedback": feedback,
        "reprocess_result": None,
    }


def write(path, record):
    Path(path).write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")


def save(biz, document_id, feedback, when=None):
    """Write a new feedback file. Returns its path."""
    when = when or datetime.now()
    record = build_record(biz, document_id, feedback, when)
    path = create_path(record["input_file"]["filename"], when)
    write(path, record)
    return path


def update_result(path, result):
    """Add the re-process job's outcome to the feedback file it came from."""
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["reprocess_result"] = result
    write(path, record)
    return record

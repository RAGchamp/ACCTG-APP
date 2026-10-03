"""Step 1 - Ingest (plan §5.1): upload or inbox, SHA-256 dedupe, page images.

Each file is hashed like the analyzer's rptpkg\\store.py keys its packages, so
the same scan uploaded twice (under any name) is refused. Stored under
<business>\\documents\\<yyyy>\\<sha16>\\: the original, one PNG per page
(what Claude reads and what the Review screen shows) and, later, the prompts
and replies.
"""

import csv
import hashlib
import io
import shutil
from datetime import datetime
from pathlib import Path

import config
from books import db
from ingest.pdf_utils import PdfError, SYNTHETIC_MARK, is_scanned, is_synthetic, open_pdf, pdf_lock, render_page
from webcommon import UserError

ALLOWED = {".pdf": "pdf", ".csv": "csv"}
DISPLAY_DPI = 110                 # text PDFs: the image is only for the owner to look at


class DuplicateFile(UserError):
    def __init__(self, message, file_id):
        super().__init__(message)
        self.file_id = file_id


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_dir(biz, file_row):
    return Path(file_row["stored_path"]).parent


def page_image(biz, file_id, page_no):
    with biz.session() as conn:
        row = conn.execute("SELECT image_path FROM pages WHERE file_id=? AND page_no=?", (file_id, page_no)).fetchone()
    return Path(row["image_path"]) if row and row["image_path"] else None


def csv_is_synthetic(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return SYNTHETIC_MARK in handle.readline()


def add_file(biz, src_path, source="upload", filename=None, seq=None):
    """Store a PDF or CSV in the business's books. Returns the new file id."""
    src_path = Path(src_path)
    filename = filename or src_path.name
    kind = ALLOWED.get(Path(filename).suffix.lower())
    if not kind:
        raise UserError(f"{filename}: only PDF and CSV files can be added.")
    sha = sha256_of(src_path)
    with biz.session() as conn:
        dup = conn.execute("SELECT id, filename, added_at FROM files WHERE sha256=?", (sha,)).fetchone()
    if dup:
        raise DuplicateFile(f"{filename} was already added on {dup['added_at'][:10]} as {dup['filename']} "
                            f"(file #{dup['id']}); it was not added again.", dup["id"])

    synthetic, page_count, scanned = False, None, None
    if kind == "pdf":
        with pdf_lock:
            try:
                doc = open_pdf(src_path)
            except PdfError as exc:
                raise UserError(f"{filename}: {exc}")
            try:
                synthetic = is_synthetic(doc)
                page_count = doc.page_count
                scanned = is_scanned(doc)
            finally:
                doc.close()
    else:
        synthetic = csv_is_synthetic(src_path)
    if synthetic and not biz.is_sandbox:
        raise UserError(f"{filename} is SYNTHETIC TEST DATA and can't go into the real books. "
                        "Open a sandbox on the Test data screen.")
    if not synthetic and biz.is_sandbox:
        raise UserError(f"{filename} is a real document; a sandbox only takes generated test data. "
                        "Switch to the live books to add it.")

    target_dir = biz.documents_dir / datetime.now().strftime("%Y") / sha[:16]
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"original{Path(filename).suffix.lower()}"
    shutil.copyfile(src_path, stored)

    with biz.session() as conn:
        cur = conn.execute(
            "INSERT INTO files(sha256, filename, stored_path, kind, source, synthetic, page_count, scanned,"
            " status, added_at, seq) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sha, filename, str(stored), kind, source, int(synthetic), page_count,
             None if scanned is None else int(scanned), "new", db.now(), seq))
        file_id = cur.lastrowid
        if seq is None:
            conn.execute("UPDATE files SET seq=? WHERE id=?", (file_id * 10, file_id))
        db.audit(conn, "add_file", file_id=file_id, filename=filename, source=source)
    if kind == "pdf":
        render_pages(biz, file_id)
    else:
        with biz.session() as conn:
            text = stored.read_text(encoding="utf-8-sig", errors="replace")
            conn.execute("INSERT INTO pages(file_id, page_no, image_path, text) VALUES (?,?,?,?)",
                         (file_id, 1, None, text))
    return file_id


def render_pages(biz, file_id, dpi=None):
    """Page PNGs (+ text layer) for a stored PDF. Scans get config.PAGE_DPI or
    more; text PDFs a small display image."""
    with biz.session() as conn:
        f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    folder = Path(f["stored_path"]).parent
    with pdf_lock:
        doc = open_pdf(f["stored_path"])
        try:
            use_dpi = dpi or (config.PAGE_DPI if f["scanned"] else DISPLAY_DPI)
            rows = []
            for pno in range(1, doc.page_count + 1):
                image = render_page(doc, pno, folder / f"page-{pno:03d}.png", use_dpi)
                text = doc[pno - 1].get_text().strip()
                rows.append((file_id, pno, str(image), text))
        finally:
            doc.close()
    with biz.session() as conn:
        conn.executemany("INSERT OR REPLACE INTO pages(file_id, page_no, image_path, text) VALUES (?,?,?,?)", rows)


def scan_inbox(biz, inbox=None):
    """Add every PDF/CSV in the inbox; move each one to Inbox\\processed\\ (or
    Inbox\\refused\\ with a .txt saying why). Returns (added ids, messages)."""
    inbox = Path(inbox or config.INBOX_DIR)
    added, messages = [], []
    for path in sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED):
        try:
            added.append(add_file(biz, path, source="inbox"))
            dest = inbox / "processed"
            messages.append(f"{path.name}: added")
        except UserError as exc:
            dest = inbox / "refused"
            messages.append(f"{path.name}: {exc}")
            dest.mkdir(exist_ok=True)
            (dest / (path.name + ".txt")).write_text(str(exc), encoding="utf-8")
        dest.mkdir(exist_ok=True)
        shutil.move(str(path), str(dest / path.name))
    return added, messages


def read_csv_rows(text):
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))

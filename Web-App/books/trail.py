"""The ledger trail: every change to the ledger, for review (Ledger → Ledger trail).

One append-only row per change, written in the same transaction as the change itself:

    post          an entry was posted (from a document, by hand, opening balances, an adjustment)
    reverse       an entry was reversed (the mirror-image entry that undoes it)
    reinstate     a reversal was undone: the reversed entry was posted again
    close_month   a month was closed (no more posting into it)
    reopen_month  a closed month was reopened (with the reason)

Each row keeps where in the app the change came from (`origin`) and a snapshot of the
entry's lines, so the trail reads on its own even years later.
"""

import json

from books import db

ACTIONS = {"post": "Posted", "reverse": "Reversed", "reinstate": "Reinstated", "close_month": "Month closed",
           "reopen_month": "Month reopened"}
DEFAULT_ORIGINS = {
    "document": "Review: proposal accepted",
    "manual": "Ledger: manual entry",
    "adjustment": "Ledger: adjustment",
    "opening": "Setup: opening balances",
    "reversal": "Ledger: Reverse button",
}


def record(conn, action, origin, entry_id=None, reverses=None, kind=None, entry_date=None, period=None, memo=None,
           document_id=None, lines=None, note=None, at=None):
    total = sum(l.get("debit_cents", 0) for l in lines) if lines else None
    conn.execute(
        "INSERT INTO ledger_trail(at, action, entry_id, reverses, kind, entry_date, period, memo, document_id,"
        " total_cents, lines, origin, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (at or db.now(), action, entry_id, reverses, kind, entry_date, period, memo, document_id, total,
         json.dumps(lines) if lines is not None else None, origin, note))


def backfill(conn):
    """Books created before the trail existed: rebuild it once from the journal (each entry's
    posted_at) and the closed months. Marked as back-filled."""
    if conn.execute("SELECT 1 FROM ledger_trail LIMIT 1").fetchone():
        return 0
    n = 0
    for e in conn.execute("SELECT * FROM journal_entries ORDER BY id").fetchall():
        lines = [{"account": l["account"], "debit_cents": l["debit"], "credit_cents": l["credit"],
                  "memo": l["memo"] or "", "party": l["party"] or "", "ref": l["ref"] or ""}
                 for l in conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (e["id"],))]
        record(conn, "reverse" if e["kind"] == "reversal" else "post",
               DEFAULT_ORIGINS.get(e["kind"], e["kind"]) + " (back-filled)", entry_id=e["id"], reverses=e["reverses"],
               kind=e["kind"], entry_date=e["date"], period=e["period"], memo=e["memo"], document_id=e["document_id"],
               lines=lines, note="recorded before the ledger trail existed", at=e["posted_at"])
        n += 1
    for p in conn.execute("SELECT * FROM periods WHERE status='closed' AND closed_at IS NOT NULL"):
        record(conn, "close_month", "Reconcile (back-filled)", period=p["month"], note=p["note"], at=p["closed_at"])
        n += 1
    return n


def rows(conn, start=None, end=None, action=None, q=None, limit=2000):
    """The trail, newest first, filtered by when the change was made, action and text."""
    sql, args = "SELECT * FROM ledger_trail WHERE 1=1", []
    if start:
        sql += " AND substr(at, 1, 10) >= ?"
        args.append(start)
    if end:
        sql += " AND substr(at, 1, 10) <= ?"
        args.append(end)
    if action:
        sql += " AND action=?"
        args.append(action)
    if q:
        sql += (" AND (memo LIKE ? OR origin LIKE ? OR note LIKE ? OR lines LIKE ? OR CAST(entry_id AS TEXT)=? "
                "OR CAST(document_id AS TEXT)=?)")
        args += [f"%{q}%"] * 4 + [q, q]
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in conn.execute(sql, args):
        item = dict(r)
        item["lines"] = json.loads(r["lines"]) if r["lines"] else []
        item["action_label"] = ACTIONS.get(r["action"], r["action"])
        out.append(item)
    return out


def summary(conn):
    counts = {r["action"]: r["n"] for r in conn.execute("SELECT action, COUNT(*) n FROM ledger_trail GROUP BY action")}
    first = conn.execute("SELECT MIN(at) FROM ledger_trail").fetchone()[0]
    return {"total": sum(counts.values()), "counts": counts, "first": first}

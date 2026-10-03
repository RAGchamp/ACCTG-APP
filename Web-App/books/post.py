"""Posting to the General Ledger (plan §8): checks, posting, reversal, period locks.

An entry, as proposals and the answer key write it:

    {"date": "2026-01-14", "memo": "Restaurant Depot",
     "lines": [{"account": "5010", "debit": "84.98", "credit": "0", "memo": "", "party": "", "ref": "line 1"},
               {"account": "2150", "debit": "0", "credit": "84.98"}]}

Amounts may be strings ("84.98"), numbers or cents (`debit_cents`). Posting
converts them to integer cents and enforces the invariants: every entry
balances, accounts exist and are active, no posting into a closed month or
before the books start. Posted entries are never changed (the database
refuses), only reversed.
"""

import json
from datetime import date as _date

from books import db, trail
from books.coa import account_map
from books.money import MoneyError, parse_cents, to_decimal_str


class PostingError(ValueError):
    pass


def period_of(iso_date):
    return iso_date[:7]


def valid_date(text):
    try:
        _date.fromisoformat(str(text))
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ entries in cents

def line_cents(line):
    if "debit_cents" in line or "credit_cents" in line:
        return int(line.get("debit_cents") or 0), int(line.get("credit_cents") or 0)
    debit = parse_cents(line.get("debit") or 0, allow_none=False)
    credit = parse_cents(line.get("credit") or 0, allow_none=False)
    # A negative debit is a credit and vice versa.
    if debit < 0:
        debit, credit = 0, credit - debit
    if credit < 0:
        credit, debit = 0, debit - credit
    return debit, credit


def normalize(entry):
    """The entry with integer-cent lines; raises PostingError for unreadable amounts."""
    lines = []
    for i, line in enumerate(entry.get("lines") or [], 1):
        try:
            debit, credit = line_cents(line)
        except MoneyError as exc:
            raise PostingError(f"line {i}: {exc}")
        if debit and credit:        # net a line that has both
            debit, credit = max(debit - credit, 0), max(credit - debit, 0)
        lines.append({"account": str(line.get("account", "")).strip(), "debit_cents": debit,
                      "credit_cents": credit, "memo": line.get("memo") or "",
                      "party": line.get("party") or "", "ref": line.get("ref") or ""})
    ref = str(entry.get("ref") or "")
    if ref:
        for l in lines:
            l["ref"] = l["ref"] or ref
    return {"date": str(entry.get("date", "")), "memo": entry.get("memo") or "", "lines": lines}


def to_json_entry(entry):
    """Cents -> the "84.98" string form (for proposals, the answer key, JSON views)."""
    e = normalize(entry)
    return {"date": e["date"], "memo": e["memo"],
            "lines": [{"account": l["account"], "debit": to_decimal_str(l["debit_cents"]),
                       "credit": to_decimal_str(l["credit_cents"]), "memo": l["memo"],
                       "party": l["party"], "ref": l["ref"]} for l in e["lines"]]}


def entry_totals(entry):
    e = normalize(entry)
    return (sum(l["debit_cents"] for l in e["lines"]), sum(l["credit_cents"] for l in e["lines"]))


# ------------------------------------------------------------------ checks

def period_status(conn, month):
    row = conn.execute("SELECT status FROM periods WHERE month=?", (month,)).fetchone()
    return row["status"] if row else "open"


def check_entry(conn, entry, accounts=None, allow_closed=False):
    """A list of problems (empty = the entry may be posted)."""
    errors = []
    try:
        e = normalize(entry)
    except PostingError as exc:
        return [str(exc)]
    accounts = accounts or account_map(conn)
    profile = db.profile(conn)
    if not valid_date(e["date"]):
        errors.append(f"invalid date {e['date']!r}")
    else:
        if e["date"] < profile["books_start"] and not allow_closed:
            errors.append(f"date {e['date']} is before the books start ({profile['books_start']})")
        if not allow_closed and period_status(conn, period_of(e["date"])) == "closed":
            errors.append(f"{period_of(e['date'])} is closed; post in an open month or reopen it")
    if len(e["lines"]) < 2:
        errors.append("an entry needs at least two lines")
    for i, line in enumerate(e["lines"], 1):
        acct = accounts.get(line["account"])
        if not acct:
            errors.append(f"line {i}: account {line['account']!r} does not exist")
        elif not acct["active"]:
            errors.append(f"line {i}: account {line['account']} is inactive")
        if line["debit_cents"] == 0 and line["credit_cents"] == 0:
            errors.append(f"line {i}: no amount")
        if line["account"] == "6900" and not line["memo"].strip():
            errors.append(f"line {i}: Miscellaneous (6900) needs a memo")
    debit, credit = sum(l["debit_cents"] for l in e["lines"]), sum(l["credit_cents"] for l in e["lines"])
    if debit != credit:
        errors.append(f"debits {to_decimal_str(debit)} ≠ credits {to_decimal_str(credit)}")
    return errors


# ------------------------------------------------------------------ posting

def post_entry(conn, entry, kind="document", document_id=None, proposal_id=None, reverses=None,
               allow_closed=False, origin=None, note=None, trail_action=None):
    """Post one entry. Also writes its ledger trail row (books/trail.py): `origin` says where in
    the app it came from (default by kind), `note` any reason given."""
    errors = check_entry(conn, entry, allow_closed=allow_closed)
    if errors:
        raise PostingError("; ".join(errors))
    e = normalize(entry)
    cur = conn.execute(
        "INSERT INTO journal_entries(date, period, memo, kind, document_id, proposal_id, reverses, posted_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (e["date"], period_of(e["date"]), e["memo"], kind, document_id, proposal_id, reverses, db.now()))
    entry_id = cur.lastrowid
    accounts = account_map(conn)
    threshold = parse_cents(db.profile(conn)["capitalization_threshold"]) or 0
    for n, line in enumerate(e["lines"], 1):
        cur = conn.execute(
            "INSERT INTO journal_lines(entry_id, line_no, account, debit, credit, memo, party, ref)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (entry_id, n, line["account"], line["debit_cents"], line["credit_cents"],
             line["memo"], line["party"], line["ref"]))
        acct = accounts[line["account"]]
        if (acct["subtype"] == "fixed_asset" and line["debit_cents"] >= threshold and kind != "reversal"
                and kind != "opening"):
            conn.execute(
                "INSERT INTO fixed_assets(description, account, cost, placed_in_service, document_id, journal_line_id)"
                " VALUES (?,?,?,?,?,?)",
                (line["memo"] or e["memo"] or acct["name"], line["account"], line["debit_cents"], e["date"],
                 document_id, cur.lastrowid))
    db.audit(conn, "post", entry_id=entry_id, kind=kind, document_id=document_id)
    trail.record(conn, trail_action or ("reverse" if kind == "reversal" else "post"),
                 origin or trail.DEFAULT_ORIGINS.get(kind, kind),
                 entry_id=entry_id, reverses=reverses, kind=kind, entry_date=e["date"], period=period_of(e["date"]),
                 memo=e["memo"], document_id=document_id, lines=e["lines"], note=note)
    return entry_id


def entry_lines(conn, entry_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,))]


def reverse_entry(conn, entry_id, on_date=None, memo=None, origin=None, note=None):
    """Post the mirror image of an entry (plan P6). Returns the reversal's id."""
    row = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise PostingError(f"No entry {entry_id}")
    if conn.execute("SELECT 1 FROM journal_entries WHERE reverses=?", (entry_id,)).fetchone():
        raise PostingError(f"Entry {entry_id} is already reversed")
    on_date = on_date or row["date"]
    lines = [{"account": l["account"], "debit_cents": l["credit"], "credit_cents": l["debit"],
              "memo": l["memo"], "party": l["party"], "ref": l["ref"]} for l in entry_lines(conn, entry_id)]
    new_id = post_entry(conn, {"date": on_date, "memo": memo or f"Reversal of entry {entry_id}: {row['memo']}",
                               "lines": lines}, kind="reversal", document_id=row["document_id"], reverses=entry_id,
                        origin=origin, note=note)
    conn.execute("DELETE FROM fixed_assets WHERE journal_line_id IN "
                 "(SELECT id FROM journal_lines WHERE entry_id=?)", (entry_id,))
    return new_id


def reinstate_entry(conn, reversal_id, on_date=None, origin=None, note=None):
    """Undo a reversal made by mistake: post the reversed entry again (same kind, document,
    lines; by default its original date). The new entry records `reverses` = the reversal, so
    the reversal shows as undone and can't be undone twice; the matches the original entry's
    lines had (e.g. settled by a statement line) carry over to the new lines. Returns the new id."""
    rev = conn.execute("SELECT * FROM journal_entries WHERE id=?", (reversal_id,)).fetchone()
    if not rev or rev["kind"] != "reversal" or not rev["reverses"]:
        raise PostingError(f"Entry {reversal_id} is not a reversal")
    if is_reversed(conn, reversal_id):
        raise PostingError(f"Reversal {reversal_id} is already undone")
    orig = conn.execute("SELECT * FROM journal_entries WHERE id=?", (rev["reverses"],)).fetchone()
    old_lines = entry_lines(conn, orig["id"])
    new_id = post_entry(
        conn, {"date": on_date or orig["date"], "memo": orig["memo"],
               "lines": [{"account": l["account"], "debit_cents": l["debit"], "credit_cents": l["credit"],
                          "memo": l["memo"], "party": l["party"], "ref": l["ref"]} for l in old_lines]},
        kind=orig["kind"], document_id=orig["document_id"], proposal_id=orig["proposal_id"], reverses=reversal_id,
        origin=origin, note=note or f"undoes reversal #{reversal_id}; entry #{orig['id']} posted again",
        trail_action="reinstate")
    for old, new in zip(old_lines, entry_lines(conn, new_id)):
        for prefix in ("jl:", "link:jl:"):
            conn.execute(
                "INSERT INTO matches(kind, document_id, doc_ref, other_document_id, other_ref, amount, method, confidence,"
                " confirmed, created_at) SELECT kind, document_id, doc_ref, other_document_id, ?, amount, method,"
                " confidence, confirmed, ? FROM matches WHERE other_ref=?", (f"{prefix}{new['id']}", db.now(),
                                                                              f"{prefix}{old['id']}"))
    return new_id


def is_reversed(conn, entry_id):
    return bool(conn.execute("SELECT 1 FROM journal_entries WHERE reverses=?", (entry_id,)).fetchone())


# ------------------------------------------------------------------ periods

def close_month(conn, month, note=None, origin="Reconcile: Close month"):
    conn.execute("INSERT INTO periods(month, status, closed_at, note) VALUES (?, 'closed', ?, ?) "
                 "ON CONFLICT(month) DO UPDATE SET status='closed', closed_at=excluded.closed_at, note=excluded.note",
                 (month, db.now(), note))
    db.audit(conn, "close_month", month=month, note=note)
    trail.record(conn, "close_month", origin, period=month, note=note)


def reopen_month(conn, month, note, origin="Reconcile: Reopen month"):
    if not (note or "").strip():
        raise PostingError("Say why the month is reopened.")
    conn.execute("INSERT INTO periods(month, status, note) VALUES (?, 'open', ?) "
                 "ON CONFLICT(month) DO UPDATE SET status='open', closed_at=NULL, note=excluded.note",
                 (month, note))
    db.audit(conn, "reopen_month", month=month, note=note)
    trail.record(conn, "reopen_month", origin, period=month, note=note)


# ------------------------------------------------------------------ balances

def balances(conn, end=None, start=None):
    """{account: debits - credits} for entries dated start..end (inclusive)."""
    sql = ("SELECT l.account, SUM(l.debit) - SUM(l.credit) AS bal FROM journal_lines l "
           "JOIN journal_entries e ON e.id = l.entry_id WHERE 1=1")
    args = []
    if start:
        sql += " AND e.date >= ?"
        args.append(start)
    if end:
        sql += " AND e.date <= ?"
        args.append(end)
    sql += " GROUP BY l.account"
    return {r["account"]: r["bal"] for r in conn.execute(sql, args)}


def trial_balance_nets_to_zero(conn):
    return sum(balances(conn).values()) == 0


def entries_json(conn, document_id):
    """Posted (not reversed) entries of a document, in the JSON form."""
    out = []
    for e in conn.execute("SELECT * FROM journal_entries WHERE document_id=? AND kind='document' ORDER BY id",
                          (document_id,)):
        if is_reversed(conn, e["id"]):
            continue
        out.append(to_json_entry({"date": e["date"], "memo": e["memo"],
                                  "lines": [{"account": l["account"], "debit_cents": l["debit"],
                                             "credit_cents": l["credit"], "memo": l["memo"],
                                             "party": l["party"], "ref": l["ref"]}
                                            for l in entry_lines(conn, e["id"])]}))
    return out


def dumps(obj):
    return json.dumps(obj, indent=1, default=str)

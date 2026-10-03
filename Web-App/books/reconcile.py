"""Reconciliation and month close (plan §7).

For a month: the closing balance printed on the bank / card statement vs the
GL balance at month end (1010 business checking, 2100 business credit card),
plus the clearing accounts' open items. A month can be closed only when both
statements reconcile, or with a written override (audit-logged).
"""

import json

from books import db, match
from books.money import parse_cents
from books.post import balances, close_month, period_status
from reports.statements import month_ends
from webcommon import UserError

ACCOUNTS = {"1010": "bank_statement", "2100": "credit_card_statement"}


def month_range(month):
    for start, end in month_ends(int(month[:4])):
        if start[:7] == month:
            return start, end
    raise ValueError(month)


def statement_for(conn, doc_type, month):
    """The booked statement document for that month (posted, not a support copy)."""
    for d in conn.execute("SELECT * FROM documents WHERE doc_type=? AND status='posted' ORDER BY id DESC", (doc_type,)):
        ext = json.loads(d["extraction"])
        if (ext.get("period_end") or "")[:7] == month:
            return d, ext
    return None, None


def reconcile_month(conn, month):
    start, end = month_range(month)
    bals = balances(conn, end=end)
    out = []
    for acct, doc_type in ACCOUNTS.items():
        doc, ext = statement_for(conn, doc_type, month)
        gl = bals.get(acct, 0)
        gl_as_statement = gl if acct == "1010" else -gl          # card: the GL credit balance is what is owed
        closing = parse_cents(ext.get("closing_balance")) if ext else None
        out.append({"account": acct, "doc_type": doc_type, "document_id": doc["id"] if doc else None,
                    "statement_closing": closing, "gl_balance": gl_as_statement,
                    "difference": None if closing is None else closing - gl_as_statement,
                    "reconciled": closing is not None and closing == gl_as_statement,
                    "has_activity": bool(gl) or closing is not None})
    items = [i for i in match.open_items(conn) if i["date"] <= end]
    return {"month": month, "status": period_status(conn, month), "accounts": out, "open_items": items,
            "reconciled": all(a["reconciled"] or not a["has_activity"] for a in out)}


def save(conn, rec):
    for a in rec["accounts"]:
        conn.execute("INSERT INTO reconciliations(account, month, statement_closing, gl_balance, difference, document_id,"
                     " at) VALUES (?,?,?,?,?,?,?)", (a["account"], rec["month"], a["statement_closing"], a["gl_balance"],
                                                     a["difference"], a["document_id"], db.now()))


def close(biz, month, override_note=None):
    with biz.session() as conn:
        rec = reconcile_month(conn, month)
        save(conn, rec)
        if not rec["reconciled"] and not (override_note or "").strip():
            problems = [f"{a['account']}: statement {a['statement_closing']} vs GL {a['gl_balance']}"
                        for a in rec["accounts"] if a["has_activity"] and not a["reconciled"]]
            raise UserError(f"{month} does not reconcile ({'; '.join(problems)}). Fix it, or close with a written "
                            "override note.")
        close_month(conn, month, note=override_note or "reconciled",
                    origin="Reconcile: Close month" + (" with an override note" if override_note else ""))
    return rec

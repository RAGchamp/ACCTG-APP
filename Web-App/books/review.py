"""Step 4 - Review (plan §6.2, §6.3): accept, edit, reject, bulk accept, rules.

Nothing is posted without the owner's approval (P2). Accepting a proposal
re-runs every code check against the ledger as it is *now*, posts the entries,
records the matches it relied on and marks the document posted (or
"support" when it only links to entries booked from another document).
"""

import json

from books import db, match, post
from books.post import PostingError, post_entry
from books.propose import _doc, _save, check_proposal
from webcommon import UserError


def open_proposal(conn, document_id):
    row = conn.execute("SELECT * FROM proposals WHERE document_id=? AND status='open' ORDER BY version DESC LIMIT 1",
                       (document_id,)).fetchone()
    return dict(row) if row else None


def blocking_flags(doc):
    return [f for f in json.loads(doc["flags"]) if f["level"] == "error"]


def accept(biz, document_id, confirm_flags=False, edited_entries=None, note=None, origin=None):
    """Post the open proposal (or the owner's edited entries). Returns the posted entry ids."""
    with biz.session() as conn:
        doc = _doc(conn, document_id)
        if doc["status"] in ("posted", "support", "rejected"):
            raise UserError(f"Document #{document_id} is already {doc['status']}.")
        prop = open_proposal(conn, document_id)
        if edited_entries is not None:
            hints = match.hints(conn, doc)
            reply = {"support_only": not edited_entries and bool([h for h in hints if h["kind"] == "support"]),
                     "entries": edited_entries, "confidence": "high", "reason": note or "edited by the owner"}
            errors = check_proposal(conn, doc, reply, hints)
            if errors:
                raise UserError("The edited entries fail the checks: " + "; ".join(errors))
            pid = _save(conn, doc, reply, hints, [], "manual")
            prop = dict(conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone())
        if not prop:
            raise UserError("There is no proposal to accept; run the Bookkeeper first.")
        flags = blocking_flags(doc)
        if flags and not confirm_flags:
            raise UserError("Confirm the flagged figures first: " + "; ".join(f["message"] for f in flags))
        hints = json.loads(prop["matches"])
        # The ledger may have moved since the proposal was made: check again now.
        current = match.hints(conn, doc)
        reply = {"support_only": bool(prop["support_only"]), "entries": json.loads(prop["entries"])}
        errors = check_proposal(conn, doc, reply, current)
        if errors:
            raise UserError("The proposal no longer passes the checks (refresh it): " + "; ".join(errors))
        ids = []
        try:
            for e in reply["entries"]:
                ids.append(post_entry(conn, e, kind="document", document_id=document_id, proposal_id=prop["id"],
                                      origin=origin or ("Review: edited entries accepted" if edited_entries is not None
                                                        else "Review: proposal accepted"),
                                      note=f"proposal v{prop['version']} ({prop['source']})"))
        except PostingError as exc:
            raise UserError(str(exc))
        match.record(conn, document_id, current, db.now())
        conn.execute("UPDATE proposals SET status='accepted' WHERE id=?", (prop["id"],))
        conn.execute("UPDATE documents SET status=? WHERE id=?",
                     ("support" if reply["support_only"] else "posted", document_id))
        db.audit(conn, "accept", document_id=document_id, proposal_id=prop["id"], entries=ids,
                 confirmed_flags=[f["message"] for f in flags], stale_hints=hints != current)
    return ids


def reject(biz, document_id, reason):
    reason = (reason or "").strip()
    if not reason:
        raise UserError("Say why the document is rejected (not business, duplicate, unreadable ...).")
    with biz.session() as conn:
        doc = _doc(conn, document_id)
        if doc["status"] in ("posted", "support"):
            raise UserError("This document is booked; reverse its entries on the Ledger screen instead.")
        conn.execute("UPDATE documents SET status='rejected', reject_reason=? WHERE id=?", (reason, document_id))
        conn.execute("UPDATE proposals SET status='rejected' WHERE document_id=? AND status='open'", (document_id,))
        db.audit(conn, "reject", document_id=document_id, reason=reason)


def reopen(biz, document_id):
    """A rejected document back to the queue."""
    with biz.session() as conn:
        conn.execute("UPDATE documents SET status='extracted', reject_reason=NULL WHERE id=? AND status='rejected'",
                     (document_id,))


def bulk_candidates(conn):
    """Documents safe to accept in one click (plan §6.2): high confidence, no failed
    checks, no error flags, no questions."""
    out = []
    for d in conn.execute("SELECT * FROM documents WHERE status='proposed' ORDER BY id"):
        p = open_proposal(conn, d["id"])
        if (p and p["confidence"] == "high" and not json.loads(p["errors"]) and not json.loads(p["questions"])
                and not blocking_flags(d)):
            out.append(d["id"])
    return out


def bulk_accept(biz, document_ids):
    done, failed = [], []
    for doc_id in document_ids:
        try:
            accept(biz, doc_id, origin="Review: bulk accept")
            done.append(doc_id)
        except UserError as exc:
            failed.append((doc_id, str(exc)))
    return done, failed


def decide_rule(biz, feedback_id, decision, vendor=None, rule_text=None, account=None):
    """The owner's answer to "Remember this rule?": always (a vendor rule) or once."""
    with biz.session() as conn:
        fb = conn.execute("SELECT * FROM feedback WHERE id=?", (feedback_id,)).fetchone()
        if not fb:
            raise UserError("No such feedback.")
        rule = json.loads(fb["rule_text"]) if fb["rule_text"] else {}
        if decision == "always":
            vendor = vendor or rule.get("vendor")
            rule_text = rule_text or rule.get("rule")
            if not vendor or not rule_text:
                raise UserError("A rule needs a vendor and an instruction.")
            conn.execute("INSERT INTO vendor_rules(vendor, rule_text, account, feedback_id, created_at) "
                         "VALUES (?,?,?,?,?)", (vendor, rule_text, account or rule.get("account"), feedback_id, db.now()))
        conn.execute("UPDATE feedback SET rule_status=? WHERE id=?", ("accepted" if decision == "always" else "once",
                                                                      feedback_id))


def add_rule(biz, vendor, rule_text, account=None):
    with biz.session() as conn:
        conn.execute("INSERT INTO vendor_rules(vendor, rule_text, account, created_at) VALUES (?,?,?,?)",
                     (vendor.strip(), rule_text.strip(), account or None, db.now()))


def delete_rule(biz, rule_id):
    with biz.session() as conn:
        conn.execute("UPDATE vendor_rules SET active=0 WHERE id=?", (rule_id,))


# ------------------------------------------------------------------ re-processing (Review input documents)
# INFO\DOC-REVIEW-REPROCESS-PLAN.md §4.3: a posted document is made re-processable by
# reversing its entries (P6) and removing the matches it recorded, never by deleting.

def dependants(conn, document_ids):
    """Documents whose booking relied on these ones (and, in turn, on those): a card
    statement line that settled a receipt's 2150 item, a receipt linked as support to a
    statement line ... They must be un-posted first, or a clearing account goes wrong."""
    found, todo = set(), set(document_ids)
    while todo:
        marks = ",".join("?" * len(todo))
        rows = conn.execute(f"SELECT DISTINCT document_id FROM matches WHERE other_document_id IN ({marks})",
                            list(todo)).fetchall()
        todo = {r[0] for r in rows} - found - set(document_ids)
        found |= todo
    return sorted(found)


def active_entries(conn, document_id):
    """The document's posted entries that are not reversed."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM journal_entries WHERE document_id=? AND kind <> 'reversal' AND id NOT IN "
        "(SELECT reverses FROM journal_entries WHERE reverses IS NOT NULL) ORDER BY id", (document_id,))]


def reprocess_impact(conn, document_id, action, include_dependants=False):
    """What re-processing would do, for the confirmation dialog, and whether it is blocked."""
    doc = conn.execute("SELECT d.*, f.seq FROM documents d JOIN files f ON f.id=d.file_id WHERE d.id=?",
                       (document_id,)).fetchone()
    if not doc:
        raise UserError(f"No document #{document_id}")
    base = [document_id]
    if action == "reextract":
        base = [r[0] for r in conn.execute("SELECT id FROM documents WHERE file_id=? ORDER BY part",
                                           (doc["file_id"],))]
    deps = dependants(conn, base)
    targets = base + (deps if include_dependants else [])
    entries, closed = [], set()
    for d in targets:
        for e in active_entries(conn, d):
            entries.append({"id": e["id"], "date": e["date"], "memo": e["memo"], "document_id": d})
            if post.period_status(conn, e["period"]) == "closed":
                closed.add(e["period"])

    def describe(i):
        r = conn.execute("SELECT id, doc_type, party, doc_date, status FROM documents WHERE id=?", (i,)).fetchone()
        return dict(r)
    blocked = None
    if closed:
        blocked = (f"{', '.join(sorted(closed))} is closed. Reopen it on the Reconcile screen first "
                   "(the reopen is audit-logged), then re-process.")
    elif deps and not include_dependants:
        blocked = ("Other documents were booked against this one: "
                   + "; ".join(f"#{d['id']} {d['doc_type']} {d['party'] or ''} {d['doc_date'] or ''}".strip()
                               for d in map(describe, deps))
                   + ". Choose 'Re-process together' to un-post them too.")
    return {"documents": [describe(i) for i in base], "dependants": [describe(i) for i in deps],
            "entries": entries, "closed_months": sorted(closed), "blocked": blocked}


def reprocess_label(action):
    return {"repropose": "re-propose", "reextract": "re-extract", "corrected": "corrected figures"}.get(action, action)


def unpost_document(biz, document_id, action, include_dependants=False, note=""):
    """Reverse the entries of the document (or, for a re-extraction, of every document of its
    file) and of its dependants, drop the matches they recorded and put them back to
    'extracted'. One transaction: all of it happens or none. Returns the impact dict."""
    with biz.session() as conn:
        impact = reprocess_impact(conn, document_id, action, include_dependants)
        if impact["blocked"]:
            raise UserError(impact["blocked"])
        targets = [d["id"] for d in impact["documents"]]
        deps = [d["id"] for d in impact["dependants"]] if include_dependants else []
        order = conn.execute(
            f"SELECT d.id FROM documents d JOIN files f ON f.id=d.file_id WHERE d.id IN ({','.join('?' * len(deps))}) "
            "ORDER BY f.seq DESC, d.part DESC", deps).fetchall() if deps else []
        reversed_ids = []
        for d in [r[0] for r in order] + targets:          # dependants first, newest first
            for e in active_entries(conn, d):
                try:
                    reversed_ids.append(post.reverse_entry(conn, e["id"], memo=f"Reversal for re-processing: {e['memo']}"
                                                                          + (f" ({note})" if note else ""),
                                                           origin=f"Input docs: re-process ({reprocess_label(action)})",
                                                           note=note or None))
                except PostingError as exc:
                    raise UserError(str(exc))
            conn.execute("DELETE FROM matches WHERE document_id=?", (d,))
            conn.execute("UPDATE proposals SET status='superseded' WHERE document_id=? AND status IN "
                         "('accepted','open')", (d,))
            conn.execute("UPDATE documents SET status='extracted', reject_reason=NULL WHERE id=?", (d,))
        db.audit(conn, "unpost", document_id=document_id, reprocess=action, documents=targets, dependants=deps,
                 reversals=reversed_ids, note=note)
    impact["reversals"] = reversed_ids
    return impact

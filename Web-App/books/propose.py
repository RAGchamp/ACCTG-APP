"""Step 3 - Propose (plan §6.1): the Bookkeeper and Reviser agent steps.

Claude proposes the journal entries for one document; code then checks the
proposal (P1 "Claude proposes, code disposes"):

- each entry balances, uses active accounts, lands in an open month;
- the entries tie to the document (the receipt total, each statement line,
  the Square deposit, tax and tips, the platform payout ...);
- every MATCH HINT is followed (the clearing account is settled with the
  hinted amount), and a document is "support only" exactly when a hint says so.

A proposal that fails is sent back once with the errors (live mode). If it
still fails it is stored with its errors and the Review screen asks for a
manual edit.
"""

import json
from pathlib import Path

from ai import AI
from books import db, match
from books.coa import account_map, coa_text
from books.money import parse_cents, to_decimal_str
from books.post import check_entry, normalize
from claude_client import ClaudeError, fill_prompt, system_prompt
from webcommon import UserError

STATEMENT_TYPES = ("credit_card_statement", "bank_statement")


# ------------------------------------------------------------------ context

def profile_text(p):
    return (f"{p['name']}, {p['address']}. Take-out and delivery pizza shop. Single-member LLC "
            f"(Schedule C). Books start {p['books_start']}, accrual basis. POS: {p['pos']}. Delivery platforms: "
            f"{', '.join(p['platforms'])}. Capitalization threshold ${p['capitalization_threshold']} per item. "
            f"CT sales tax 7.35% on meals (6.35% general). Own delivery fee is taxable. Business card ending "
            f"{', '.join(p['card_last4']) or 'n/a'}; business checking ending {', '.join(p['bank_last4']) or 'n/a'}.")


def rules_for(conn, party):
    rows = conn.execute("SELECT * FROM vendor_rules WHERE active=1 ORDER BY id").fetchall()
    out = [r for r in rows if not party or match.similar(r["vendor"], party) > 0 or r["vendor"] == "*"]
    return [f"- {r['vendor']}: {r['rule_text']}" + (f" (account {r['account']})" if r["account"] else "")
            for r in out]


def similar_entries(conn, doc, limit=5):
    """Accepted entries for the same vendor (or, failing that, the same document type)."""
    rows = conn.execute(
        "SELECT d.id, d.party, d.doc_type, p.entries FROM proposals p JOIN documents d ON d.id=p.document_id "
        "WHERE p.status='accepted' AND d.id<>? AND p.support_only=0 ORDER BY p.id DESC LIMIT 200",
        (doc["id"],)).fetchall()
    same_party = [r for r in rows if doc["party"] and match.similar(r["party"], doc["party"]) > 0.5]
    picked = (same_party or [r for r in rows if r["doc_type"] == doc["doc_type"]])[:limit]
    out = []
    for r in picked:
        entries = json.loads(r["entries"])[:3]
        for e in entries:
            lines = "; ".join(f"{l['account']} {'Dr' if parse_cents(l.get('debit') or 0) else 'Cr'} "
                              f"{l.get('debit') if parse_cents(l.get('debit') or 0) else l.get('credit')}"
                              for l in e["lines"])
            out.append(f"- {r['party']} ({r['doc_type']}): {e.get('memo', '')}: {lines}")
    return out[:limit * 2]


def hints_text(hints):
    if not hints:
        return "(none)"
    return "\n".join(f"- {h['ref']}: {h['note']}" for h in hints)


def build_prompt(conn, doc, hints, extra=""):
    ext = json.loads(doc["extraction"])
    flags = json.loads(doc["flags"])
    return fill_prompt(
        "bookkeeper.txt",
        PROFILE=profile_text(db.profile(conn)), COA=coa_text(conn),
        RULES="\n".join(rules_for(conn, doc["party"])) or "(none)",
        SIMILAR="\n".join(similar_entries(conn, doc)) or "(none)",
        HINTS=hints_text(hints),
        FLAGS="\n".join(f"- [{f['level']}] {f['message']}" for f in flags) or "(none)",
        DOC_ID=doc["id"], DOC_TYPE=doc["doc_type"], DOCUMENT=json.dumps(ext, indent=1), EXTRA=extra)


# ------------------------------------------------------------------ checks

def _net(entry, account):
    """debit - credit on one account in an entry (cents)."""
    e = normalize(entry)
    return sum(l["debit_cents"] - l["credit_cents"] for l in e["lines"] if l["account"] == account)


def check_proposal(conn, doc, reply, hints):
    errors = []
    if not isinstance(reply, dict):
        return ["the reply is not a JSON object"]
    ext = json.loads(doc["extraction"]) if isinstance(doc["extraction"], str) else doc["extraction"]
    t = doc["doc_type"]
    support_hint = [h for h in hints if h["kind"] == "support"]
    entries = reply.get("entries") or []
    if reply.get("support_only"):
        if not support_hint:
            errors.append("support_only without a match hint: nothing shows this document is already booked")
        if entries:
            errors.append("a support-only proposal must have no entries")
        return errors
    if support_hint:
        errors.append(f"this document is already booked ({support_hint[0]['note']}); it must be support_only")
    if not entries:
        txns = ext.get("transactions") or []
        # A statement with no transactions (only a carried balance) or only card payments
        # (booked from the bank statement) has nothing to book.
        nothing_to_book = t in STATEMENT_TYPES and all(
            t == "credit_card_statement" and (x.get("kind") or "").lower() == "payment" for x in txns)
        return errors if nothing_to_book else errors + ["no entries proposed"]
    accounts = account_map(conn)
    for i, e in enumerate(entries, 1):
        for err in check_entry(conn, e, accounts):
            errors.append(f"entry {i} ({e.get('ref', '')}): {err}")
    if errors:
        return errors
    by_ref = {}
    for e in entries:
        by_ref.setdefault(str(e.get("ref") or "document"), []).append(e)

    def tie(what, got, want):
        if want is not None and got != want:
            errors.append(f"{what}: {to_decimal_str(got)} proposed, the document says {to_decimal_str(want)}")

    c = parse_cents
    if t == "purchase_receipt":
        total = c(ext.get("total"))
        if total is not None:
            got = sum(sum(l["debit_cents"] for l in normalize(e)["lines"]) for e in entries)
            tie("receipt total", got, abs(total))
    elif t in STATEMENT_TYPES:
        cash_acct = "2100" if t == "credit_card_statement" else "1010"
        for n, txn in enumerate(ext.get("transactions") or [], 1):
            amt = c(txn.get("amount"))
            ref = f"txn {n}"
            is_payment = t == "credit_card_statement" and (txn.get("kind") or "").lower() == "payment"
            es = by_ref.pop(ref, [])
            if is_payment:
                if es:
                    errors.append(f"{ref} is a card payment: it is booked from the bank statement, not here")
                continue
            if not es:
                errors.append(f"{ref} ({txn.get('description')}, {txn.get('amount')}) has no entry")
                continue
            net = sum(_net(e, cash_acct) for e in es)
            want = -amt if t == "credit_card_statement" else amt     # card purchase = credit 2100
            if amt is not None:
                tie(f"{ref} {cash_acct}", net, want)
        for ref in by_ref:
            if ref.startswith("txn"):
                errors.append(f"entry {ref} does not match a transaction on the statement")
    elif t == "pos_report":
        e = entries
        tie("cash (1000)", sum(_net(x, "1000") for x in e), c(ext.get("tender_cash")))
        tie("card clearing (1100)", sum(_net(x, "1100") for x in e), c(ext.get("net_card_deposit")))
        tie("sales tax (2200)", -sum(_net(x, "2200") for x in e), c(ext.get("tax")))
        tie("tips (2210)", -sum(_net(x, "2210") for x in e), c(ext.get("tips")))
    elif t == "platform_statement":
        tie("platform receivable (1110)", sum(_net(x, "1110") for x in entries), c(ext.get("payout")))
        tie("platform sales (4020)", -sum(_net(x, "4020") for x in entries), c(ext.get("subtotal")))
        if sum(_net(x, "2200") for x in entries):
            errors.append("marketplace facilitator tax must not be booked to 2200")
    elif t == "payroll_report":
        tie("payroll liabilities (2300)", -sum(_net(x, "2300") for x in entries), c(ext.get("total_debit")))
    elif t == "check":
        amt = c(ext.get("amount_numeric"))
        if ext.get("direction") == "incoming":
            tie("undeposited funds (1050)", sum(_net(x, "1050") for x in entries), amt)
        else:
            tie("awaiting statement (2150)", -sum(_net(x, "2150") for x in entries), amt)
    elif t == "cash_log":
        sales_tax = sum(c(x.get("amount")) or 0 for x in ext.get("entries") or [] if x.get("kind") == "sales_tax")
        tie("sales tax (2200)", -sum(_net(x, "2200") for x in entries), sales_tax)

    # Every match hint must be followed.
    for h in hints:
        if h["kind"] == "support":
            continue
        es = [e for e in entries if str(e.get("ref")) == h["ref"]]
        settle_debit = match.CLEARING[h["clears_account"]] == "credit"   # a credit item is settled by a debit
        got = sum(_net(e, h["clears_account"]) for e in es)
        amount = h["amount"]
        if h["kind"] == "receipt_card":
            # a refund line settles a refund receipt, the other way round
            txn_amt = None
            try:
                txn_amt = c((ext.get("transactions") or [])[int(h["ref"].split()[-1]) - 1].get("amount"))
            except (ValueError, IndexError):
                pass
            if txn_amt is not None and txn_amt < 0:
                settle_debit = not settle_debit
        want = amount if settle_debit else -amount
        if got != want:
            errors.append(f"{h['ref']}: the match hint says {'debit' if want > 0 else 'credit'} "
                          f"{h['clears_account']} {to_decimal_str(amount)} ({h['note']})")
    return errors


# ------------------------------------------------------------------ run

def _doc(conn, document_id):
    doc = conn.execute("SELECT d.*, f.sha256, f.stored_path FROM documents d JOIN files f ON f.id=d.file_id "
                       "WHERE d.id=?", (document_id,)).fetchone()
    if not doc:
        raise UserError(f"No document #{document_id}")
    return dict(doc)


def _save(conn, doc, reply, hints, errors, source):
    version = (conn.execute("SELECT MAX(version) FROM proposals WHERE document_id=?", (doc["id"],)).fetchone()[0]
               or 0) + 1
    conn.execute("UPDATE proposals SET status='superseded' WHERE document_id=? AND status='open'", (doc["id"],))
    mark = conn.execute("SELECT COALESCE(MAX(id), 0) FROM journal_entries").fetchone()[0]
    entries = reply.get("entries") or [] if isinstance(reply, dict) else []
    clean = []
    for e in entries:
        try:
            ne = normalize(e)
            clean.append({"ref": str(e.get("ref") or "document"), "date": ne["date"], "memo": ne["memo"],
                          "lines": [{"account": l["account"], "debit": to_decimal_str(l["debit_cents"]),
                                     "credit": to_decimal_str(l["credit_cents"]), "memo": l["memo"],
                                     "party": l["party"]} for l in ne["lines"]]})
        except ValueError:
            clean.append(e)
    rule = reply.get("rule_suggestion") if isinstance(reply, dict) else None
    cur = conn.execute(
        "INSERT INTO proposals(document_id, version, source, entries, support_only, confidence, reason, questions,"
        " errors, matches, rule_suggestion, ledger_mark, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc["id"], version, source, json.dumps(clean), int(bool(reply.get("support_only"))),
         reply.get("confidence") or "medium", reply.get("reason") or "",
         json.dumps([q for q in (reply.get("questions") or []) if q]), json.dumps(errors), json.dumps(hints),
         json.dumps(rule) if rule else None, mark, "open", db.now()))
    conn.execute("UPDATE documents SET status=? WHERE id=?",
                 ("needs_review" if errors else "proposed", doc["id"]))
    return cur.lastrowid


def propose(biz, document_id, ai=None, extra=""):
    """Run the Bookkeeper for one document. Returns the proposal id."""
    ai = ai or AI(biz)
    with biz.session() as conn:
        doc = _doc(conn, document_id)
        if doc["status"] in ("posted", "support", "rejected"):
            raise UserError(f"Document #{document_id} is {doc['status']}; nothing to propose.")
        hints = match.hints(conn, doc)
        prompt = build_prompt(conn, doc, hints, extra)
    system = system_prompt("bookkeeper_system.txt")
    reply, _ = ai.call("propose", prompt, system, folder=Path(doc["stored_path"]).parent, file_id=doc["file_id"],
                       document_id=document_id, replay=(doc["sha256"], doc["part"]))
    with biz.session() as conn:
        errors = check_proposal(conn, doc, reply, hints)
    if errors and ai.mode == "live":
        retry = prompt + ("\n\nYOUR PREVIOUS PROPOSAL FAILED THESE CODE CHECKS - fix them:\n"
                          + "\n".join(f"- {e}" for e in errors)
                          + "\n\nYOUR PREVIOUS PROPOSAL:\n```json\n" + json.dumps(reply, indent=1) + "\n```\n")
        try:
            reply2, _ = ai.call("propose", retry, system, folder=Path(doc["stored_path"]).parent,
                                file_id=doc["file_id"], document_id=document_id)
            with biz.session() as conn:
                errors2 = check_proposal(conn, doc, reply2, hints)
            if len(errors2) <= len(errors):
                reply, errors = reply2, errors2
        except ClaudeError:
            pass
    with biz.session() as conn:
        pid = _save(conn, doc, reply, hints, errors, "claude" if ai.mode == "live" else "replay")
        db.audit(conn, "propose", document_id=document_id, proposal_id=pid, errors=len(errors))
    return pid


def revise(biz, document_id, feedback_text, ai=None, limit=None):
    """The Reviser: a new proposal following the owner's feedback (plan §6.2 Feedback...).
    `limit`: the feedback length allowed (the Review screen's box by default; the Review
    input documents screen allows config.FEEDBACK_TEXT_MAX)."""
    from config import FEEDBACK_MAX_CHARS
    limit = limit or FEEDBACK_MAX_CHARS
    feedback_text = (feedback_text or "").strip()
    if not feedback_text:
        raise UserError("Write what should change.")
    if len(feedback_text) > limit:
        raise UserError(f"Feedback is limited to {limit} characters.")
    ai = ai or AI(biz)
    with biz.session() as conn:
        doc = _doc(conn, document_id)
        prev = conn.execute("SELECT * FROM proposals WHERE document_id=? ORDER BY version DESC LIMIT 1",
                            (document_id,)).fetchone()
        if not prev:
            raise UserError("There is no proposal to give feedback on yet.")
        hints = match.hints(conn, doc)
        previous = {"support_only": bool(prev["support_only"]), "entries": json.loads(prev["entries"]),
                    "reason": prev["reason"]}
        extra = fill_prompt("reviser.txt", PREVIOUS=json.dumps(previous, indent=1), FEEDBACK=feedback_text)
        prompt = build_prompt(conn, doc, hints, extra)
    reply, _ = ai.call("revise", prompt, system_prompt("bookkeeper_system.txt"),
                       folder=Path(doc["stored_path"]).parent, file_id=doc["file_id"], document_id=document_id,
                       replay=(doc["sha256"], doc["part"]))
    with biz.session() as conn:
        errors = check_proposal(conn, doc, reply, hints)
        pid = _save(conn, doc, reply, hints, errors, "revised")
        rule = reply.get("rule_suggestion") if isinstance(reply, dict) else None
        conn.execute("INSERT INTO feedback(document_id, proposal_before, proposal_after, text, rule_text, rule_status,"
                     " created_at) VALUES (?,?,?,?,?,?,?)",
                     (document_id, prev["id"], pid, feedback_text, json.dumps(rule) if rule else None,
                      "proposed" if rule else None, db.now()))
        db.audit(conn, "feedback", document_id=document_id, proposal_id=pid, text=feedback_text)
    return pid

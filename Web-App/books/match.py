"""Matching (plan §4.2, §7): the code that keeps money from being booked twice.

Clearing accounts hold "open items" until the statement line that settles them
arrives:

    2150 Purchases awaiting statement   receipt / check booked, card or bank line not yet seen
    1100 Card sales clearing            Square card sales, Square payout not yet deposited
    1110 Platform receivable            platform statement, payout not yet deposited
    1050 Undeposited funds              check received / cash drop, deposit not yet seen
    2300 Payroll liabilities            payroll report, provider debit not yet seen

hints(conn, document) looks at a document about to be proposed and returns
match hints: "txn 3 of this card statement is receipt #12, already booked to
2150 - debit 2150, not an expense", or "this receipt was already booked from
the card statement - link it, post nothing". The hints go into the
Bookkeeper prompt and the proposal checks enforce them. They are recorded in
the matches table when the proposal is accepted.
"""

import json
import re
from datetime import date, timedelta
from itertools import combinations

import config
from books.money import parse_cents

CLEARING = {
    "2150": "credit",   # the open item is a credit; the settling line debits it
    "1100": "debit",
    "1110": "debit",
    "1050": "debit",
    "2300": "credit",
}
STOP_WORDS = {"INC", "LLC", "CO", "THE", "CORP", "STORE", "CT", "HARTFORD", "POS", "DEBIT", "PURCHASE", "CARD",
              "WWW", "COM", "ACH", "DES", "ID", "INDN", "CO"}


def words(text):
    return {w for w in re.findall(r"[A-Z]{3,}", (text or "").upper()) if w not in STOP_WORDS}


def similar(a, b):
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _d(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def matched_line_ids(conn):
    ids = set()
    for r in conn.execute("SELECT other_ref FROM matches WHERE other_ref LIKE 'jl:%'"):
        ids.add(int(r[0][3:]))
    return ids


def open_items(conn, accounts=None):
    """Unsettled clearing lines: [{id, account, amount (>0), side, date, party, document_id, doc_type,
    payment, check_no, card_last4, memo}]."""
    accounts = accounts or list(CLEARING)
    done = matched_line_ids(conn)
    # Lines that were reversed don't count.
    reversed_entries = {r[0] for r in conn.execute("SELECT reverses FROM journal_entries WHERE reverses IS NOT NULL")}
    items = []
    q = ("SELECT l.*, e.date, e.document_id, e.kind, e.id AS eid, d.doc_type, d.extraction FROM journal_lines l "
         "JOIN journal_entries e ON e.id=l.entry_id LEFT JOIN documents d ON d.id=e.document_id "
         f"WHERE l.account IN ({','.join('?' * len(accounts))}) ORDER BY e.date, l.id")
    for r in conn.execute(q, accounts):
        if r["id"] in done or r["eid"] in reversed_entries or r["kind"] == "reversal":
            continue
        if r["doc_type"] in ("credit_card_statement", "bank_statement"):
            continue            # a statement line settles items; it is never one
        side = CLEARING[r["account"]]
        amount = r["credit"] - r["debit"] if side == "credit" else r["debit"] - r["credit"]
        if amount == 0:
            continue
        ext = json.loads(r["extraction"]) if r["extraction"] else {}
        pay = ext.get("payment") or {}
        items.append({
            "id": r["id"], "account": r["account"], "amount": amount, "side": side, "date": r["date"],
            "party": r["party"] or ext.get("vendor") or ext.get("payee") or ext.get("platform") or "",
            "document_id": r["document_id"], "doc_type": r["doc_type"], "memo": r["memo"] or "",
            "payment": pay.get("method") or ("check" if r["doc_type"] == "check" else None),
            "check_no": str(pay.get("check_no") or ext.get("check_no") or "") or None,
            "card_last4": pay.get("card_last4"),
            "ref": r["ref"] or "",
        })
    return items


def _window(item_date, txn_date, before=None, after=None):
    """The item (receipt) should be dated from `before` days after to `after` days
    before the statement line (cards post 0-3 days later, checks clear later)."""
    a, b = _d(item_date), _d(txn_date)
    if not a or not b:
        return False
    before = config.MATCH_DAYS_BEFORE if before is None else before
    after = config.MATCH_DAYS_AFTER if after is None else after
    return b - timedelta(days=after) <= a <= b + timedelta(days=before)


def _pick(cands, txn):
    if not cands:
        return None
    return max(cands, key=lambda it: (similar(it["party"], txn.get("description")),
                                      -abs(((_d(it["date"]) or date.min) - (_d(txn.get("date")) or date.min)).days)))


def _hint(ref, kind, item, note, amount=None):
    return {"ref": ref, "kind": kind, "clears_account": item["account"], "open_item": f"jl:{item['id']}",
            "items": [f"jl:{item['id']}"], "amount": amount if amount is not None else item["amount"],
            "other_document_id": item["document_id"], "party": item["party"], "date": item["date"], "note": note}


def hints(conn, doc):
    """Match hints for a document about to be proposed."""
    ext = json.loads(doc["extraction"]) if isinstance(doc["extraction"], str) else doc["extraction"]
    t = doc["doc_type"]
    if t in ("credit_card_statement", "bank_statement"):
        dup = _duplicate_statement(conn, doc, ext)
        if dup:
            return dup
    if t == "credit_card_statement":
        return _card_hints(conn, ext)
    if t == "bank_statement":
        return _bank_hints(conn, ext)
    if t == "purchase_receipt":
        return _receipt_support_hints(conn, doc, ext)
    if t == "check" and ext.get("direction") == "outgoing":
        return _check_support_hints(conn, doc, ext)
    return []


def _duplicate_statement(conn, doc, ext):
    """The same month's statement booked already (e.g. the CSV export, then the PDF)."""
    last4 = ext.get("card_last4") or ext.get("account_last4")
    for d in conn.execute("SELECT id, extraction FROM documents WHERE doc_type=? AND status='posted' AND id<>?",
                          (doc["doc_type"], doc["id"])):
        e = json.loads(d["extraction"])
        if ((e.get("card_last4") or e.get("account_last4")) == last4 and e.get("period_end") == ext.get("period_end")
                and e.get("closing_balance") == ext.get("closing_balance")):
            return [{"ref": "document", "kind": "support", "other_document_id": d["id"], "other_ref": "statement",
                     "amount": parse_cents(ext.get("closing_balance")) or 0, "party": "", "date": ext.get("period_end"),
                     "note": f"this statement period was already booked from document #{d['id']} "
                             "(the same statement in another format): support_only, no entry"}]
    return []


def _card_hints(conn, ext):
    out, used = [], set()
    items = [i for i in open_items(conn, ["2150"])]
    last4 = ext.get("card_last4")
    for n, txn in enumerate(ext.get("transactions") or [], 1):
        kind = (txn.get("kind") or "purchase").lower()
        amt = parse_cents(txn.get("amount"))
        if amt is None or kind not in ("purchase", "refund"):
            continue
        want_side_sign = 1 if amt > 0 else -1     # a refund settles a refund receipt (a debit item)
        cands = [i for i in items if i["id"] not in used and i["amount"] * want_side_sign == abs(amt) * 1
                 and i["payment"] in ("card", None, "unknown") and i["doc_type"] == "purchase_receipt"
                 and (not last4 or not i["card_last4"] or i["card_last4"] == last4)
                 and _window(i["date"], txn.get("date"), after=config.MATCH_DAYS_AFTER)]
        best = _pick(cands, txn)
        if best:
            used.add(best["id"])
            out.append(_hint(f"txn {n}", "receipt_card", best,
                             f"receipt document #{best['document_id']} ({best['party']}, {best['date']}) is already "
                             f"booked to 2150: {'debit' if amt > 0 else 'credit'} 2150 and "
                             f"{'credit' if amt > 0 else 'debit'} 2100, no expense line", abs(amt)))
    return out


def _bank_hints(conn, ext):
    out, used = [], set()
    items = open_items(conn)
    by_acct = lambda a: [i for i in items if i["account"] == a and i["id"] not in used]  # noqa: E731
    for n, txn in enumerate(ext.get("transactions") or [], 1):
        amt = parse_cents(txn.get("amount"))
        if amt is None:
            continue
        ref, desc = f"txn {n}", (txn.get("description") or "").upper()
        if amt < 0:
            want = -amt
            check_no = str(txn.get("check_no") or "").strip() or None
            if not check_no:
                m = re.search(r"\bCHECK\s*#?\s*(\d{3,6})\b", desc)
                check_no = m.group(1) if m else None
            cands = []
            if check_no:
                cands = [i for i in by_acct("2150") if i["check_no"] == check_no and i["amount"] == want]
            if not cands and not check_no:
                cands = [i for i in by_acct("2150") if i["amount"] == want and i["payment"] in ("debit", "check")
                         and _window(i["date"], txn.get("date"), after=30)]
            best = _pick(cands, txn)
            if best:
                used.add(best["id"])
                out.append(_hint(ref, "receipt_bank" if best["doc_type"] == "purchase_receipt" else "check_bank",
                                 best, f"{best['doc_type']} #{best['document_id']} ({best['party']}, {best['date']}) "
                                       "is already booked to 2150: debit 2150, credit 1010", want))
                continue
            cands = [i for i in by_acct("2300") if i["amount"] == want and _window(i["date"], txn.get("date"), 7, 10)]
            best = _pick(cands, txn)
            if best:
                used.add(best["id"])
                out.append(_hint(ref, "payroll", best, f"payroll report #{best['document_id']} owes this: "
                                                       "debit 2300, credit 1010", want))
            continue
        # Deposits: Square payouts (possibly several days), platform payouts, undeposited funds.
        for acct, kind, label in (("1100", "payout", "Square card sales"), ("1110", "payout", "platform payout"),
                                  ("1050", "deposit", "undeposited check/cash")):
            cands = [i for i in by_acct(acct) if _window(i["date"], txn.get("date"), 0, 12)]
            combo = None
            exact = [i for i in cands if i["amount"] == amt]
            if exact:
                combo = [_pick(exact, txn)]
            elif acct == "1100":
                for size in (2, 3):
                    for c in combinations(sorted(cands, key=lambda i: i["date"]), size):
                        if sum(i["amount"] for i in c) == amt:
                            combo = list(c)
                            break
                    if combo:
                        break
            if combo:
                for i in combo:
                    used.add(i["id"])
                h = _hint(ref, kind, combo[0], f"{label} of {', '.join(i['date'] for i in combo)} "
                                               f"(document(s) #{', #'.join(str(i['document_id']) for i in combo)}): "
                                               f"debit 1010, credit {acct}", amt)
                h["items"] = [f"jl:{i['id']}" for i in combo]
                out.append(h)
                break
    return out


def _posted_statement_lines(conn):
    """Posted card/bank statement lines that booked an expense directly (not through 2150),
    and are not yet linked to a receipt or check."""
    linked = {(r["other_document_id"], r["other_ref"]) for r in conn.execute(
        "SELECT other_document_id, other_ref FROM matches WHERE kind='support'")}
    rows = conn.execute(
        "SELECT e.document_id, l.ref, e.date, d.extraction, d.doc_type, "
        "(SELECT GROUP_CONCAT(x.account) FROM journal_lines x WHERE x.entry_id=e.id) AS accts "
        "FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
        "JOIN documents d ON d.id=e.document_id WHERE d.doc_type IN ('credit_card_statement','bank_statement') "
        "AND e.kind='document' AND e.id NOT IN (SELECT reverses FROM journal_entries WHERE reverses IS NOT NULL) "
        "AND l.account IN ('2100','1010') GROUP BY e.id").fetchall()
    out = []
    for r in rows:
        if (r["document_id"], r["ref"]) in linked or "2150" in (r["accts"] or ""):
            continue
        ext = json.loads(r["extraction"])
        try:
            n = int(str(r["ref"]).split()[-1])
            txn = (ext.get("transactions") or [])[n - 1]
        except (ValueError, IndexError):
            continue
        out.append({"document_id": r["document_id"], "ref": r["ref"], "date": r["date"],
                    "amount": abs(parse_cents(txn.get("amount")) or 0), "description": txn.get("description"),
                    "check_no": str(txn.get("check_no") or "") or None, "doc_type": r["doc_type"]})
    return out


def _receipt_support_hints(conn, doc, ext):
    pay = (ext.get("payment") or {}).get("method")
    if pay in ("cash", "petty_cash"):
        return []
    total = abs(parse_cents(ext.get("total")) or 0)
    lines = [l for l in _posted_statement_lines(conn) if l["amount"] == total
             and _window(ext.get("date"), l["date"], before=0, after=config.MATCH_DAYS_AFTER + 20)]
    if pay == "check" and (ext.get("payment") or {}).get("check_no"):
        lines = [l for l in lines if l["check_no"] == str(ext["payment"]["check_no"])] or lines
    best = max(lines, key=lambda l: similar(ext.get("vendor"), l["description"]), default=None)
    if best and similar(ext.get("vendor"), best["description"]) > 0:
        return [{"ref": "document", "kind": "support", "other_document_id": best["document_id"],
                 "other_ref": best["ref"], "amount": total, "party": ext.get("vendor"), "date": best["date"],
                 "note": f"already booked from {best['doc_type'].replace('_', ' ')} #{best['document_id']} "
                         f"{best['ref']} ({best['description']}, {best['date']}): support_only, no entry"}]
    return []


def _check_support_hints(conn, doc, ext):
    check_no = str(ext.get("check_no") or "")
    amount = abs(parse_cents(ext.get("amount_numeric")) or 0)
    if not check_no:
        return []
    # A receipt/invoice paid by this check is already booked to 2150.
    for i in open_items(conn, ["2150"]):
        if i["doc_type"] == "purchase_receipt" and i["check_no"] == check_no and i["amount"] == amount:
            return [{"ref": "document", "kind": "support", "other_document_id": i["document_id"],
                     # "link:" - the receipt's 2150 item stays open for the bank line to settle.
                     "other_ref": f"link:jl:{i['id']}", "amount": amount, "party": i["party"],
                     "date": i["date"],
                     "note": f"receipt/invoice #{i['document_id']} paid by check {check_no} is already booked: "
                             "support_only, no entry"}]
    for l in _posted_statement_lines(conn):
        if l["check_no"] == check_no and l["amount"] == amount:
            return [{"ref": "document", "kind": "support", "other_document_id": l["document_id"],
                     "other_ref": l["ref"], "amount": amount, "party": ext.get("payee"), "date": l["date"],
                     "note": f"check {check_no} was already booked from the bank statement #{l['document_id']} "
                             f"{l['ref']}: support_only, no entry"}]
    return []


def record(conn, document_id, hints_used, now):
    """Save the matches of an accepted proposal."""
    for h in hints_used:
        if h["kind"] == "support":
            conn.execute("INSERT INTO matches(kind, document_id, doc_ref, other_document_id, other_ref, amount, method,"
                         " confidence, created_at) VALUES ('support',?,?,?,?,?,?,?,?)",
                         (document_id, h["ref"], h["other_document_id"], h["other_ref"], h["amount"],
                          "amount+date", "high", now))
            continue
        for item in h["items"]:
            conn.execute("INSERT INTO matches(kind, document_id, doc_ref, other_document_id, other_ref, amount, method,"
                         " confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (h["kind"], document_id, h["ref"], h["other_document_id"], item, h["amount"],
                          "amount+date", "high", now))


def unmatched_report(conn, as_of=None, days=None):
    """Open items older than `days` (dashboard, plan §4.2)."""
    days = config.UNMATCHED_WARNING_DAYS if days is None else days
    today = _d(as_of) or date.today()
    return [i for i in open_items(conn) if (_d(i["date"]) and (today - _d(i["date"])).days > days)]

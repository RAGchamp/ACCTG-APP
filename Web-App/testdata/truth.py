"""Ledger truth first, documents second (plan §15.2).

A scenario (YAML) or the simulator gives the *real* business events. The
Builder turns them into:

- DOCUMENTS: every piece of paper each event really creates (the receipt, the
  card statement line, the bank line when the card is paid, the Square
  report, ...), each with its exact extraction (what Claude should read) and
  what is actually printed on it (with [?] where a digit is illegible);
- EXPECTED ENTRIES per document, following the posting rules of plan §4.2 in
  the order the owner would process the paperwork (a receipt before its card
  statement, so the statement line settles 2150 instead of booking a second
  expense);
- the OWNER SCRIPT per document (accept, confirm flags, give feedback,
  correct a figure, reject, reopen a month ...);
- the replayed Claude replies (extract / propose / revise) for mocked runs.

Reports and tax figures for the answer key are then computed by posting the
expected entries with the app's own ledger code (answer_key.py).
"""

import copy
import random
from datetime import date, timedelta

from books.money import parse_cents, pct_of, to_decimal_str

SHOP = "Hartford Slice Pizza LLC"
SHOP_ADDRESS = "412 Franklin Ave, Hartford, CT 06114"
CARD_LAST4 = "4417"
CARD_ISSUER = "Nutmeg Business Visa"
BANK_LAST4 = "0931"
BANK_NAME = "Charter Oak Community Bank"
PROFILE = {"card_last4": [CARD_LAST4], "bank_last4": [BANK_LAST4], "name": SHOP, "address": SHOP_ADDRESS,
           "ein": "00-0000000 (synthetic)", "owner_name": "Sam Rossi (synthetic)"}

PRIO = {"purchase_receipt": 10, "check": 20, "cash_log": 30, "pos_report": 40, "platform_statement": 50,
        "payroll_report": 60, "other": 70, "not_financial": 75, "credit_card_statement": 80, "bank_csv": 85,
        "bank_statement": 90}
CLEARING_BY_KIND = {"card": "2150", "debit": "2150", "check": "2150"}


def D(x):
    return date.fromisoformat(str(x)[:10])


def add_days(d, n):
    return (D(d) + timedelta(days=n)).isoformat()


def month_of(d):
    return str(d)[:7]


def month_end(m):
    y, mo = int(m[:4]), int(m[5:7])
    nxt = date(y + (mo == 12), mo % 12 + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def next_month(m):
    y, mo = int(m[:4]), int(m[5:7])
    return f"{y + (mo == 12)}-{mo % 12 + 1:02d}"


def c(x):
    """Scenario amounts ("84.98", 84.98, 85) -> cents."""
    if x is None:
        return 0
    if isinstance(x, int):
        return x * 100
    return parse_cents(str(x), allow_none=False)


s = to_decimal_str


def line(account, debit=0, credit=0, party="", memo=""):
    if account == "6900" and not memo:
        memo = party or "miscellaneous"
    if debit < 0:
        debit, credit = 0, credit - debit
    if credit < 0:
        credit, debit = 0, debit - credit
    return {"account": account, "debit": s(debit), "credit": s(credit), "memo": memo, "party": party}


def entry(date_, memo, lines, ref="document"):
    return {"ref": ref, "date": date_, "memo": memo, "lines": [l for l in lines if parse_cents(l["debit"]) or
                                                               parse_cents(l["credit"])]}


class ScenarioError(ValueError):
    pass


class Builder:
    def __init__(self, scenario, seed=1):
        self.sc = scenario
        self.rng = random.Random(seed)
        self.seed = seed
        self.docs = []            # document specs
        self.card_txns = []
        self.bank_txns = []
        self.manual = []          # owner's manual journal entries
        self.bad_files = []
        self.n = 0
        self.check_no = 1040
        self.opening = None
        self.difficulty = scenario.get("difficulty", "scanned")

    # ------------------------------------------------------------------ helpers
    def key(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n:03d}"

    def doc(self, doc_type, available, extraction, entries=None, **kw):
        spec = {"key": kw.pop("key", None) or self.key(doc_type.split("_")[0]), "doc_type": doc_type,
                "available": available, "prio": PRIO.get(kw.pop("prio_type", doc_type), 70),
                "extraction": extraction, "shown": kw.pop("shown", None), "entries": entries or [],
                "support": False, "expect": "post", "owner": [{"action": "accept"}], "revise": None,
                "first": None, "questions": [], "confidence": "high", "flags_expected": [],
                "render": {"difficulty": self.difficulty}, "file": None, "features": [], "ledger_ops": None}
        spec.update(kw)
        self.docs.append(spec)
        return spec

    def statement_name(self, vendor):
        return f"{vendor.upper()} #{self.rng.randint(100, 999)} HARTFORD CT"

    # ------------------------------------------------------------------ opening balances
    def opening_balances(self, o, start):
        lines = []
        amounts = {"1010": o.get("bank", "15000.00"), "1000": o.get("cash", 0), "1020": o.get("petty", 0),
                   "1500": o.get("equipment", 0), "1520": o.get("vehicle", 0), "1200": o.get("inventory", 0)}
        credits = {"2100": o.get("card", 0), "1590": o.get("accumulated_depreciation", 0), "2500": o.get("loan", 0),
                   "2200": o.get("sales_tax_payable", 0)}
        dr = cr = 0
        for a, v in amounts.items():
            if c(v):
                lines.append(line(a, debit=c(v), memo="opening balance"))
                dr += c(v)
        for a, v in credits.items():
            if c(v):
                lines.append(line(a, credit=c(v), memo="opening balance"))
                cr += c(v)
        lines.append(line("3200", credit=dr - cr, memo="opening balance equity"))
        self.opening = entry(start, "Opening balances", lines)
        self.card_opening = c(o.get("card", 0))
        self.bank_opening = c(o.get("bank", "15000.00"))

    # ------------------------------------------------------------------ events
    def purchase(self, ev):
        refund = bool(ev.get("refund"))
        sign = -1 if refund else 1
        rate = str(ev.get("tax_rate", "6.35"))
        items = []
        for it in ev["items"]:
            items.append({"description": it["desc"], "qty": it.get("qty", 1), "amount": c(it["amount"]),
                          "account": str(it["account"]), "taxable": bool(it.get("taxable", False)),
                          "first": str(it.get("first_account", it["account"]))})
        subtotal = sum(i["amount"] for i in items)
        taxable = sum(i["amount"] for i in items if i["taxable"])
        tax = c(ev["tax"]) if "tax" in ev else (pct_of(taxable, rate) if taxable and not ev.get("no_tax") else 0)
        tip = c(ev.get("tip", 0))
        total = subtotal + tax + tip
        pay = ev.get("payment", "card")
        check_no = None
        if pay == "check":
            check_no = str(ev.get("check_no") or self._next_check())
        rc = ev.get("receipt", {}) or {}
        extraction = {"doc_type": "purchase_receipt", "vendor": ev["vendor"], "date": ev["date"],
                      "invoice_no": str(ev.get("invoice_no") or self.rng.randint(100000, 999999)),
                      "lines": [{"description": i["description"], "qty": i["qty"], "amount": s(sign * i["amount"]),
                                 "taxable": i["taxable"]} for i in items],
                      "subtotal": s(sign * subtotal), "tax": s(sign * tax), "tip": s(tip) if tip else None,
                      "total": s(sign * total),
                      "payment": {"method": pay, "card_last4": CARD_LAST4 if pay == "card" else None,
                                  "check_no": check_no},
                      "is_refund": refund}
        credit_acct = {"cash": "1000", "petty_cash": "1020"}.get(pay, "2150")

        def lines_for(field):
            per = {}
            order = []
            for i in items:
                a = i[field]
                if a not in per:
                    order.append(a)
                    per[a] = 0
                per[a] += i["amount"]
            if tax:
                big = max((i for i in items if i["taxable"]), key=lambda i: i["amount"], default=items[0])
                per[big[field]] += tax
            if tip:
                big = max(items, key=lambda i: i["amount"])
                per[big[field]] += tip
            memo_of = {i[field]: i["description"] for i in items}
            out = [line(a, debit=sign * per[a], party=ev["vendor"],
                        memo=memo_of[a] if a in ("1500", "1510", "1520") else "") for a in order]
            out.append(line(credit_acct, credit=sign * total, party=ev["vendor"]))
            return [entry(ev["date"], ev["vendor"], out)]

        truth_entries, first_entries = lines_for("account"), lines_for("first")
        main_account = max(items, key=lambda i: i["amount"])["account"]
        spec = None
        available = rc.get("available", ev["date"])
        if rc.get("late"):
            # Found after the statement that paid it arrived (plan §15.4 "a receipt that arrives after its statement").
            paid = ev.get("post_date") or ev.get("clear_date") or add_days(ev["date"], 3)
            available = add_days(month_end(month_of(paid)), self.sc.get("statement_delay", 3) + 6)
        if not rc.get("missing"):
            spec = self.doc("purchase_receipt", available, extraction, truth_entries,
                            features=["receipt", f"pay:{pay}"] + (["refund"] if refund else []))
            spec["render"].update({k: v for k, v in rc.items() if k not in ("available", "missing")})
            spec["render"].setdefault("layout", "invoice" if rc.get("pages", 1) > 1 or ev.get("invoice") else "thermal")
            spec["late"] = bool(rc.get("late"))
            if first_entries != truth_entries:
                # Claude's first guess uses the item's "first_account"; the owner corrects it.
                spec["first"] = {"entries": first_entries, "confidence": ev.get("first_confidence", "medium"),
                                 "questions": ev.get("questions", [])}
                spec["owner"] = [{"action": "feedback", "text": ev.get("feedback", "Please fix the accounts."),
                                  "rule": ev.get("rule")}, {"action": "accept"}]
                spec["features"].append("feedback")
                if ev.get("rule"):
                    spec["features"].append("vendor_rule")
            if ev.get("owner"):
                spec["owner"] = ev["owner"]
            if ev.get("posted_correction"):
                # Accepted, then found wrong: the owner reverses the entry and posts the right one (plan P6).
                pc = ev["posted_correction"]
                fix = entry(pc.get("date", ev["date"]), pc.get("memo", "Correction"),
                            [line(str(a), debit=c(d), credit=c(cr)) for a, d, cr in pc["lines"]])
                spec["owner"] = [{"action": "accept"}, {"action": "reverse_and_repost", "entry": fix}]
                spec["replacement"] = fix
                spec["features"].append("reversal")
            if ev.get("expect"):
                spec["expect"] = ev["expect"]
            if ev.get("flags_expected"):
                spec["flags_expected"] = ev["flags_expected"]
            if ev.get("propose_errors_expected"):
                spec["propose_errors_expected"] = ev["propose_errors_expected"]
            if ev.get("illegible"):
                self._illegible(spec, ev["illegible"])
            if rc.get("duplicate_upload"):
                spec["duplicate_upload"] = True
            spec["check_no"] = check_no
        if pay == "card":
            post_date = ev.get("post_date") or add_days(ev["date"], ev.get("post_days", 1))
            self.card_txns.append({"date": post_date, "description": ev.get("statement_name")
                                   or self.statement_name(ev["vendor"]),
                                   "amount": sign * total, "kind": "refund" if refund else "purchase",
                                   "linked": spec["key"] if spec else None, "clear": "2150",
                                   "fallback_account": main_account, "party": ev["vendor"]})
        elif pay in ("debit", "check"):
            clear = ev.get("clear_date") or add_days(ev["date"], 2 if pay == "debit" else 4)
            desc = (f"CHECK {check_no}" if pay == "check" else
                    f"POS DEBIT {ev.get('statement_name') or self.statement_name(ev['vendor'])}")
            self.bank_txns.append({"date": clear, "description": desc, "amount": -sign * total,
                                   "check_no": check_no, "linked": spec["key"] if spec else None,
                                   "clear": "2150", "fallback": [line(main_account, debit=sign * total),
                                                                  line("1010", credit=sign * total)],
                                   "party": ev["vendor"]})
            if pay == "check" and ev.get("check_doc", False) and spec:
                self._check_doc_for(spec, ev, total, check_no)
        return spec

    VENDORS_BATCH = [("Restaurant Depot", "5010"), ("Sysco Connecticut", "5010"), ("Coca-Cola Beverages NE", "5020"),
                     ("WebstaurantStore", "5030"), ("Shell", "6400"), ("Staples", "6900"), ("Home Depot", "6300"),
                     ("Facebook Ads", "6410"), ("Mutual Uniform", "6310")]

    def purchases_batch(self, ev):
        """`count` card purchases with no receipts (a long card statement, bulk accept)."""
        start, days = ev["start"], int(ev.get("days", 28))
        for i in range(int(ev["count"])):
            vendor, acct = self.VENDORS_BATCH[self.rng.randrange(len(self.VENDORS_BATCH))]
            amount = self.rng.randint(1200, 42000)
            self.purchase({"date": add_days(start, self.rng.randrange(days)), "vendor": vendor, "payment": "card",
                           "items": [{"desc": "purchase", "amount": s(amount), "account": acct}],
                           "receipt": {"missing": True}})

    def _next_check(self):
        self.check_no += 1
        return str(self.check_no)

    def _illegible(self, spec, field):
        """Print [?] for one digit of `field` (e.g. "total" or "lines.0.amount"); the owner corrects it."""
        shown = copy.deepcopy(spec["extraction"])
        target = shown
        parts = field.split(".")
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        value = str(target[parts[-1]])
        pos = max(i for i, ch in enumerate(value) if ch.isdigit() and i < len(value) - 3) if len(value) > 4 else 0
        target[parts[-1]] = value[:pos] + "[?]" + value[pos + 1:]
        spec["shown"] = shown
        spec["render"]["illegible"] = {"field": field, "value": value, "pos": pos}
        spec["owner"] = [{"action": "correct"}] + [a for a in spec["owner"] if a["action"] != "correct"]
        spec["flags_expected"] = spec["flags_expected"] + ["unreadable"]
        spec["features"].append("illegible_digit")

    def _check_doc_for(self, receipt_spec, ev, total, check_no):
        ext = {"doc_type": "check", "direction": "outgoing", "check_no": check_no, "date": ev["date"],
               "payee": ev["vendor"], "payer": SHOP, "amount_numeric": s(total),
               "amount_words": amount_words(total), "memo": f"Inv {receipt_spec['extraction']['invoice_no']}",
               "void": False}
        spec = self.doc("check", ev["date"], ext, [], support=True, expect="support",
                        features=["check", "check_pays_invoice"])
        spec["render"].update({"layout": "check"})
        spec["support_of"] = receipt_spec["key"]

    def check_out(self, ev):
        """A check written without a receipt/invoice (rent to the landlord, a contractor)."""
        check_no = str(ev.get("check_no") or self._next_check())
        amount = c(ev["amount"])
        words_amount = c(ev["words_amount"]) if ev.get("words_amount") else amount
        ext = {"doc_type": "check", "direction": "outgoing", "check_no": check_no, "date": ev["date"],
               "payee": ev["payee"], "payer": SHOP, "amount_numeric": s(amount),
               "amount_words": amount_words(words_amount), "memo": ev.get("memo", ""), "void": bool(ev.get("void"))}
        if ev.get("void"):
            spec = self.doc("check", ev["date"], ext, [], expect="reject", features=["check", "void"],
                            owner=[])
            spec["render"]["layout"] = "check"
            return
        acct = str(ev["account"])
        ents = [entry(ev["date"], f"Check {check_no} {ev['payee']}",
                      [line(acct, debit=amount, party=ev["payee"], memo=ev.get("memo", "")),
                       line("2150", credit=amount, party=ev["payee"])])]
        spec = None
        if not ev.get("no_image"):
            spec = self.doc("check", ev["date"], ext, ents, features=["check"])
            spec["render"]["layout"] = "check"
            spec["check_no"] = check_no
            if words_amount != amount:
                spec["flags_expected"] = ["written amount"]
                spec["owner"] = [{"action": "accept", "confirm": True}]
                spec["expect"] = "flag"
                spec["features"].append("words_mismatch")
        self.bank_txns.append({"date": ev.get("clear_date") or add_days(ev["date"], 5),
                               "description": f"CHECK {check_no}", "amount": -amount, "check_no": check_no,
                               "linked": spec["key"] if spec else None, "clear": "2150",
                               "fallback": [line(acct, debit=amount), line("1010", credit=amount)],
                               "party": ev["payee"]})

    def check_in(self, ev):
        food, tax = c(ev["food"]), c(ev["tax"]) if "tax" in ev else pct_of(c(ev["food"]), "7.35")
        amount = food + tax
        ext = {"doc_type": "check", "direction": "incoming", "check_no": str(ev.get("check_no", "2211")),
               "date": ev["date"], "payee": SHOP, "payer": ev["payer"], "amount_numeric": s(amount),
               "amount_words": amount_words(amount),
               "memo": ev.get("memo") or f"Catering: food ${s(food)} + tax ${s(tax)}", "void": False}
        ents = [entry(ev["date"], f"Check from {ev['payer']}",
                      [line("1050", debit=amount, party=ev["payer"]), line("4000", credit=food, party=ev["payer"]),
                       line("2200", credit=tax, party=ev["payer"])])]
        spec = self.doc("check", ev["date"], ext, ents, features=["check", "check_incoming"])
        spec["render"]["layout"] = "check"
        dep = ev.get("deposit_date") or add_days(ev["date"], 1)
        self.bank_txns.append({"date": dep, "description": "DEPOSIT", "amount": amount, "check_no": None,
                               "linked": spec["key"], "clear": "1050", "fallback": None, "party": ev["payer"]})
        if ev.get("bounced"):
            b = ev["bounced"]
            self.bank_txns.append({"date": b["date"], "description": f"RETURNED DEPOSITED ITEM {ev['payer'].upper()}",
                                   "amount": -amount, "lines": [line("1150", debit=amount, party=ev["payer"]),
                                                                line("1010", credit=amount)]})
            if b.get("fee"):
                fee = c(b["fee"])
                self.bank_txns.append({"date": b["date"], "description": "RETURNED ITEM FEE", "amount": -fee,
                                       "lines": [line("6700", debit=fee), line("1010", credit=fee)]})

    def cash_log(self, ev):
        rows, lines = [], []
        sale = c(ev.get("cash_sales", 0))
        tax = pct_of(sale, "7.35") if sale else 0
        cash_in = 0
        if sale:
            rows += [{"description": "cash sales not rung", "amount": s(sale), "kind": "cash_sale"},
                     {"description": "tax", "amount": s(tax), "kind": "sales_tax"}]
            lines += [line("4000", credit=sale), line("2200", credit=tax)]
            cash_in += sale + tax
        petty_total = 0
        for p in ev.get("petty", []):
            a = c(p["amount"])
            rows.append({"description": p["desc"], "amount": s(a), "kind": "petty_expense"})
            lines.append(line(str(p["account"]), debit=a, memo=p["desc"]))
            petty_total += a
        if petty_total:
            lines.append(line("1020", credit=petty_total))
        if ev.get("tip_out"):
            a = c(ev["tip_out"])
            rows.append({"description": "tip out drivers", "amount": s(a), "kind": "tip_out"})
            lines += [line("2210", debit=a)]
            cash_in -= a
        drop = c(ev.get("cash_drop", 0))
        if drop:
            rows.append({"description": "drop to bank", "amount": s(drop), "kind": "cash_drop"})
            lines.append(line("1050", debit=drop))
            cash_in -= drop
        if cash_in:
            lines.append(line("1000", debit=cash_in))
        stated = sale + tax
        shown_total = stated + c(ev.get("sum_error", 0))
        ext = {"doc_type": "cash_log", "date": ev["date"], "entries": rows,
               "total_stated": s(shown_total) if (sale or ev.get("sum_error")) else None}
        spec = self.doc("cash_log", ev["date"], ext, [entry(ev["date"], "Cash sheet", lines)], features=["cash_log"])
        spec["render"]["layout"] = "cash_log"
        spec["confidence"] = "medium"
        if ev.get("sum_error"):
            spec["flags_expected"] = ["total written on the sheet"]
            spec["owner"] = [{"action": "accept", "confirm": True}]
            spec["expect"] = "flag"
            spec["features"].append("sum_error")
        if ev.get("crossed_out"):
            spec["render"]["crossed_out"] = True
            spec["features"].append("crossed_out")
        if ev.get("illegible"):
            self._illegible(spec, ev["illegible"])
        if drop:
            self.bank_txns.append({"date": ev.get("deposit_date") or add_days(ev["date"], 1),
                                   "description": "CASH DEPOSIT BRANCH 012", "amount": drop, "check_no": None,
                                   "linked": spec["key"], "clear": "1050", "fallback": None, "party": "cash drop"})

    def square_day(self, ev):
        inst, deliv, dfee = c(ev.get("instore", 0)), c(ev.get("delivery", 0)), c(ev.get("delivery_fees", 0))
        disc, comps, refunds = c(ev.get("discounts", 0)), c(ev.get("comps", 0)), c(ev.get("refunds", 0))
        tips, cash = c(ev.get("tips", 0)), c(ev.get("cash", 0))
        gross = inst + deliv + dfee
        net = gross - disc - comps - refunds
        tax = c(ev["tax"]) if "tax" in ev else pct_of(net, "7.35")
        total = net + tax + tips
        card = total - cash
        fees = c(ev["fees"]) if "fees" in ev else pct_of(card, "2.75")
        deposit = card - fees
        start = ev.get("date")
        ext = {"doc_type": "pos_report", "provider": "Square", "period_start": start, "period_end": start,
               "sales_instore": s(inst), "sales_delivery": s(deliv), "delivery_fees": s(dfee), "gross_sales": s(gross),
               "discounts": s(disc), "comps": s(comps), "refunds": s(refunds), "net_sales": s(net), "tax": s(tax),
               "tips": s(tips), "total_collected": s(total), "tender_card": s(card), "tender_cash": s(cash),
               "fees": s(fees), "net_card_deposit": s(deposit)}
        lines = [line("1000", debit=cash), line("1100", debit=deposit), line("6200", debit=fees),
                 line("4090", debit=disc + comps + refunds), line("4000", credit=inst), line("4010", credit=deliv),
                 line("4050", credit=dfee), line("2200", credit=tax), line("2210", credit=tips)]
        spec = self.doc("pos_report", ev.get("available") or start, ext,
                        [entry(start, "Square sales", lines)], features=["square"])
        spec["render"]["layout"] = "square"
        if ev.get("flags_expected"):
            spec["flags_expected"] = ev["flags_expected"]
            spec["expect"] = "flag"
            spec["features"].append("pos_tax_flag")
        spec["square_format"] = ev.get("format", self.sc.get("square_format", "pdf"))
        payout_date = ev.get("payout_date") or add_days(start, 1)
        group = ev.get("payout_group")
        self.bank_txns.append({"date": payout_date, "description": f"SQUARE INC DES:SQ{start[2:4]}{start[5:7]}"
                                                                   f"{start[8:10]} PAYOUT",
                               "amount": deposit, "check_no": None, "linked": spec["key"], "clear": "1100",
                               "fallback": None, "party": "Square", "group": group})
        return spec

    def platform_week(self, ev):
        platform = ev["platform"]
        sub = c(ev["subtotal"])
        rate = str(ev.get("commission_rate", {"DoorDash": "25", "Uber Eats": "30", "Grubhub": "20"}.get(platform, "25")))
        comm = c(ev["commission"]) if "commission" in ev else pct_of(sub, rate)
        mkt, promo, adj = c(ev.get("marketing_fees", 0)), c(ev.get("promotions", 0)), c(ev.get("adjustments", 0))
        payout = sub - comm - mkt - promo + adj
        end = ev["end"]
        pay_date = ev.get("payout_date") or add_days(end, 2)
        ext = {"doc_type": "platform_statement", "platform": platform, "period_start": ev["start"],
               "period_end": end, "payout_date": pay_date, "subtotal": s(sub),
               "tax_collected_by_platform": s(pct_of(sub, "7.35")), "commission": s(comm),
               "marketing_fees": s(mkt), "promotions": s(promo), "adjustments": s(adj), "payout": s(payout)}
        fees = comm + mkt + promo - adj
        lines = [line("1110", debit=payout, party=platform), line("6210", debit=fees, party=platform),
                 line("4020", credit=sub, party=platform)]
        spec = self.doc("platform_statement", add_days(end, 1), ext, [entry(end, f"{platform} payout statement", lines)],
                        features=["platform", platform.lower().replace(" ", "")])
        spec["render"]["layout"] = "platform"
        self.bank_txns.append({"date": pay_date, "description": {
            "DoorDash": "DOORDASH, INC. DES:PAYOUT", "Uber Eats": "UBER USA 6787 DES:EATS PAYOUT",
            "Grubhub": "GRUBHUB HOLDINGS DES:DEPOSIT"}.get(platform, platform.upper() + " PAYOUT"),
            "amount": payout, "check_no": None, "linked": spec["key"], "clear": "1110", "fallback": None,
            "party": platform})

    def payroll(self, ev):
        gross, tips = c(ev["gross"]), c(ev.get("tips_paid", 0))
        wh, er = c(ev["withholding"]), c(ev["employer_taxes"])
        net, total = gross - wh, gross + er
        pay_date = ev["pay_date"]
        ext = {"doc_type": "payroll_report", "provider": ev.get("provider", "Gusto"), "pay_date": pay_date,
               "period_start": ev["start"], "period_end": ev["end"], "gross_wages": s(gross), "tips_paid": s(tips),
               "employee_withholding": s(wh), "net_pay": s(net), "employer_taxes": s(er), "total_debit": s(total)}
        lines = [line("6000", debit=gross - tips), line("2210", debit=tips), line("6010", debit=er),
                 line("2300", credit=total)]
        spec = self.doc("payroll_report", pay_date, ext, [entry(pay_date, "Payroll", lines)], features=["payroll"])
        spec["render"]["layout"] = "payroll"
        self.bank_txns.append({"date": ev.get("debit_date") or add_days(pay_date, -1),
                               "description": f"{ext['provider'].upper()} DES:PAYROLL ID:88231", "amount": -total,
                               "check_no": None, "linked": spec["key"], "clear": "2300", "fallback": None,
                               "party": ext["provider"]})

    BANK_KINDS = {
        "rent": ("6100", "ACH DEBIT FARMINGTON AVE PROP RENT"), "utility": ("6110", "EVERSOURCE ENERGY DES:BILL PAY"),
        "gas_utility": ("6110", "CNG GAS DES:UTIL PMT"), "phone": ("6120", "COMCAST BUSINESS DES:PAYMENT"),
        "insurance": ("6500", "HARTFORD MUTUAL DES:INS PREM"), "bank_fee": ("6700", "MONTHLY SERVICE FEE"),
        "accountant": ("6520", "ONLINE TRANSFER TO MAIN ST CPA"), "license": ("6510", "CT SOTS DES:ANNUAL RPT"),
        "owner_draw": ("3100", "ONLINE TRANSFER TO CHK ...7788 OWNER DRAW"),
        "owner_contribution": ("3000", "ONLINE TRANSFER FROM CHK ...7788 OWNER CONTRIBUTION"),
        "sales_tax_payment": ("2200", "CT DRS DES:SALES TAX ID:OS114"),
        "petty_transfer": ("1020", "WITHDRAWAL PETTY CASH"),
        "card_payment": ("2100", "ONLINE PAYMENT NUTMEG VISA ...4417"),
    }

    def bank(self, ev):
        kind = ev["kind"]
        amount = c(ev["amount"])
        if kind == "loan_payment":
            principal, interest = c(ev["principal"]), c(ev["interest"])
            amount = principal + interest
            desc = ev.get("description") or f"CT COMMUNITY LENDER LOAN PMT PRIN {s(principal)} INT {s(interest)}"
            lines = [line("2500", debit=principal), line("6710", debit=interest), line("1010", credit=amount)]
            self.bank_txns.append({"date": ev["date"], "description": desc, "amount": -amount, "lines": lines})
            return
        acct, desc = self.BANK_KINDS[kind]
        acct = str(ev.get("account", acct))
        desc = ev.get("description", desc)
        inflow = kind in ("owner_contribution",) or ev.get("deposit")
        signed = amount if inflow else -amount
        lines = ([line("1010", debit=amount), line(acct, credit=amount)] if inflow else
                 [line(acct, debit=amount), line("1010", credit=amount)])
        self.bank_txns.append({"date": ev["date"], "description": desc, "amount": signed, "lines": lines})
        if kind == "card_payment":
            self.card_txns.append({"date": add_days(ev["date"], 1), "description": "PAYMENT - THANK YOU",
                                   "amount": -amount, "kind": "payment"})

    def card_charge(self, ev):
        amount = c(ev["amount"])
        kind = ev.get("kind", "interest")
        desc = ev.get("description") or {"interest": "INTEREST CHARGE ON PURCHASES", "fee": "LATE FEE",
                                         "annual_fee": "ANNUAL MEMBERSHIP FEE"}[kind]
        self.card_txns.append({"date": ev["date"], "description": desc, "amount": amount,
                               "kind": "interest" if kind == "interest" else "fee",
                               "lines": [line("6710" if kind == "interest" else "6700", debit=amount),
                                         line("2100", credit=amount)]})

    def manual_entry(self, ev):
        lines = [line(str(a), debit=c(d), credit=c(cr)) for a, d, cr in ev["lines"]]
        self.manual.append({"date": ev["date"], "entry": entry(ev["date"], ev.get("memo", "Manual entry"), lines),
                            "kind": ev.get("entry_kind", "adjustment")})

    def bad_file(self, ev):
        self.bad_files.append(dict(ev, key=self.key("bad")))

    # ------------------------------------------------------------------ statements
    def statements(self, start):
        skip = set(self.sc.get("skip_statements", []))
        bank_fmt = self.sc.get("bank_format", "pdf")
        last = max([t["date"] for t in self.card_txns + self.bank_txns] + [start])
        months = []
        m = month_of(start)
        while m <= month_of(last):
            months.append(m)
            m = next_month(m)
        bal = self.card_opening
        if self.card_txns or self.card_opening:
            for m in months:
                txns = sorted([t for t in self.card_txns if month_of(t["date"]) == m], key=lambda t: t["date"])
                opening = bal
                bal += sum(t["amount"] for t in txns)
                if f"card:{m}" in skip or (not txns and not opening):
                    continue
                ext = {"doc_type": "credit_card_statement", "issuer": CARD_ISSUER, "card_last4": CARD_LAST4,
                       "period_start": f"{m}-01", "period_end": month_end(m), "opening_balance": s(opening),
                       "closing_balance": s(bal),
                       "transactions": [{"date": t["date"], "description": t["description"], "amount": s(t["amount"]),
                                         "kind": t["kind"]} for t in txns]}
                spec = self.doc("credit_card_statement", add_days(month_end(m), self.sc.get("statement_delay", 3)), ext,
                                features=["card_statement"])
                spec["flags_expected"] = list(self.sc.get("statement_flags", {}).get(f"card:{m}", []))
                if spec["flags_expected"]:
                    spec["features"].append("statement_gap")
                spec["txns"] = txns
                spec["render"]["layout"] = "card_statement"
                if self.sc.get("card_bulk"):
                    spec["owner"] = [{"action": "bulk"}]
                    spec["features"].append("bulk_accept")
        bal = self.bank_opening
        if self.bank_txns or self.sc.get("bank_statements", False):
            for m in months:
                txns = sorted([t for t in self.bank_txns if month_of(t["date"]) == m], key=lambda t: t["date"])
                # Square payouts grouped ("payout_group") are one deposit.
                merged, seen = [], {}
                for t in txns:
                    g = t.get("group")
                    if g and g in seen:
                        seen[g]["amount"] += t["amount"]
                        seen[g]["linked_all"].append(t["linked"])
                        continue
                    t = dict(t, linked_all=[t.get("linked")])
                    if g:
                        seen[g] = t
                    merged.append(t)
                txns = merged
                opening = bal
                bal += sum(t["amount"] for t in txns)
                if f"bank:{m}" in skip or not txns:
                    continue
                ext = {"doc_type": "bank_statement", "bank": BANK_NAME, "account_last4": BANK_LAST4,
                       "period_start": f"{m}-01", "period_end": month_end(m), "opening_balance": s(opening),
                       "closing_balance": s(bal),
                       "transactions": [{"date": t["date"], "description": t["description"], "amount": s(t["amount"]),
                                         "check_no": t.get("check_no")} for t in txns]}
                avail = add_days(month_end(m), self.sc.get("statement_delay", 3) + 1)
                formats = {"pdf": ["pdf"], "csv": ["csv"], "both": ["csv", "pdf"]}[bank_fmt]
                for i, fmt in enumerate(formats):
                    spec = self.doc("bank_statement", avail, copy.deepcopy(ext),
                                    prio_type="bank_csv" if fmt == "csv" else "bank_statement",
                                    features=["bank_statement", f"bank_{fmt}"])
                    spec["txns"] = txns
                    spec["render"]["layout"] = "bank_statement"
                    spec["flags_expected"] = list(self.sc.get("statement_flags", {}).get(f"bank:{m}", []))
                    spec["bank_format"] = fmt
                    if i > 0:
                        spec["duplicate_of_statement"] = True

    # ------------------------------------------------------------------ expected statement entries
    def finalize(self):
        order = sorted(self.docs, key=lambda d: (d["available"], d["prio"], d["key"]))
        pos = {d["key"]: i for i, d in enumerate(order)}
        by_key = {d["key"]: d for d in self.docs}
        for spec in order:
            if spec["doc_type"] == "credit_card_statement":
                ents = []
                for n, t in enumerate(spec["txns"], 1):
                    ref = f"txn {n}"
                    if t["kind"] == "payment":
                        continue
                    if t.get("lines"):
                        ents.append(entry(t["date"], t["description"], t["lines"], ref))
                        continue
                    amt = t["amount"]
                    linked = t.get("linked")
                    if linked and pos[linked] < pos[spec["key"]]:
                        lines = [line("2150", debit=amt, party=t["party"]), line("2100", credit=amt, party=t["party"])]
                        spec.setdefault("matches", []).append({"ref": ref, "doc": linked})
                    else:
                        lines = [line(t["fallback_account"], debit=amt, party=t["party"]),
                                 line("2100", credit=amt, party=t["party"])]
                        if linked:
                            late = by_key[linked]
                            late.update(support=True, expect="support", entries=[], support_of=spec["key"],
                                        first=None, owner=[{"action": "accept"}])
                            late["features"].append("late_receipt")
                    ents.append(entry(t["date"], t["description"], lines, ref))
                spec["entries"] = ents
            elif spec["doc_type"] == "bank_statement":
                if spec.get("duplicate_of_statement"):
                    spec.update(support=True, expect="support", entries=[])
                    continue
                ents = []
                for n, t in enumerate(spec["txns"], 1):
                    ref = f"txn {n}"
                    if t.get("lines"):
                        ents.append(entry(t["date"], t["description"], t["lines"], ref))
                        continue
                    amt = t["amount"]
                    linked = [k for k in t.get("linked_all", [t.get("linked")]) if k]
                    if linked and all(pos[k] < pos[spec["key"]] for k in linked):
                        if amt > 0:
                            lines = [line("1010", debit=amt), line(t["clear"], credit=amt, party=t.get("party", ""))]
                        else:
                            lines = [line(t["clear"], debit=-amt, party=t.get("party", "")), line("1010", credit=-amt)]
                    elif t.get("fallback"):
                        lines = t["fallback"]
                        for k in linked:
                            late = by_key[k]
                            late.update(support=True, expect="support", entries=[], support_of=spec["key"],
                                        first=None, owner=[{"action": "accept"}])
                            late["features"].append("late_receipt")
                    else:
                        raise ScenarioError(f"bank line {t['description']} settles a document that arrives later")
                    ents.append(entry(t["date"], t["description"], lines, ref))
                spec["entries"] = ents
        return order


# ------------------------------------------------------------------ amounts in words (checks)

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
        "seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + (" " + _words(n % 100) if n % 100 else "")
    if n < 1_000_000:
        return _words(n // 1000) + " thousand" + (" " + _words(n % 1000) if n % 1000 else "")
    return _words(n // 1_000_000) + " million" + (" " + _words(n % 1_000_000) if n % 1_000_000 else "")


def amount_words(cents):
    return f"{_words(cents // 100).capitalize()} and {cents % 100:02d}/100"

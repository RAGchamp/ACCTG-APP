"""Business simulation (plan §15.5): a whole, consistent period for the pizza shop.

From a few parameters it writes the events of every day - Square sales, weekly
supplier purchases, weekly DoorDash / Uber Eats / Grubhub statements,
semi-monthly payroll (card tips paid through payroll), rent, utilities, the
loan, owner draws, the monthly sales tax payment, the card payment, cash sheets
with cash drops, month-end depreciation - as a scenario for the same Builder
that single scenarios use. Every receipt appears on the right statement and
every payout on the bank statement, so the books reconcile.
"""

import random
from datetime import date, timedelta

from books.money import pct_of, to_decimal_str as s
from testdata import truth

PRESETS = {
    "smoke-1-week": {"title": "One clean week (quick end-to-end check)", "start": "2026-01-01", "days": 7,
                     "difficulty": "clean", "avg_daily_sales": 1500, "employees": 3, "messiness": 0.0},
    "jan-2026-scanned": {"title": "January 2026, scanned paperwork", "start": "2026-01-01", "months": 1,
                         "difficulty": "scanned", "avg_daily_sales": 1800, "employees": 4, "messiness": 0.1,
                         "close_months": True},
    "q1-2026-messy": {"title": "Q1 2026, messy paperwork with a few adversarial documents", "start": "2026-01-01",
                      "months": 3, "difficulty": "messy", "avg_daily_sales": 1800, "employees": 4, "messiness": 0.3,
                      "adversarial": 0.03, "close_months": True},
    "year-2026": {"title": "The full year 2026, scanned (statements, Schedule C/SE, 1099 list)",
                  "start": "2026-01-01", "months": 12, "difficulty": "scanned", "avg_daily_sales": 1800,
                  "employees": 4, "messiness": 0.1, "close_months": True, "year_end": True,
                  "square_format": "csv"},
}

SUPPLIERS = [
    # vendor, payment, weekday (0=Mon), items [(desc, share of a week's sales, account, taxable)]
    # Food + drinks + packaging ~ 30% of sales, the usual pizza-shop food cost.
    ("Restaurant Depot", "card", 0, [("WHOLE MILK MOZZ 6/5LB CASES", 0.075, "5010", False),
                                     ("PEPPERONI SLICED 12.5LB", 0.030, "5010", False),
                                     ("PIZZA BOX 16IN 50CT", 0.012, "5030", True)]),
    ("Sysco Connecticut", "card", 3, [("FLOUR HI GLUTEN 50LB", 0.035, "5010", False),
                                      ("TOMATO SAUCE 6/#10", 0.030, "5010", False),
                                      ("SAUSAGE CRMBL 2/5LB", 0.030, "5010", False),
                                      ("PRODUCE AND DAIRY", 0.040, "5010", False),
                                      ("CUP PLASTIC 16OZ 1000CT", 0.010, "5030", True)]),
    ("Coca-Cola Beverages NE", "debit", 2, [("FOUNTAIN SYRUP BIB AND BOTTLES", 0.030, "5020", False)]),
]


def _c(dollars_float):
    return int(round(dollars_float * 100))


def daterange(start, days):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(days)]


def month_list(start, months):
    out, m = [], start[:7]
    for _ in range(months):
        out.append(m)
        m = truth.next_month(m)
    return out


def simulate(params, seed=1):
    """A scenario dict for the Builder."""
    p = dict(params)
    rng = random.Random(seed)
    start = p.get("start", "2026-01-01")
    if p.get("days"):
        days = daterange(start, p["days"])
    else:
        months = month_list(start, p.get("months", 1))
        days = daterange(start, (date.fromisoformat(truth.month_end(months[-1])) - date.fromisoformat(start)).days + 1)
    last_day = days[-1]
    avg = p.get("avg_daily_sales", 1800)
    mess = p.get("messiness", 0.0)
    adv = p.get("adversarial", 0.0)
    events = []
    card_by_month, tax_by_month, tips_since_payroll = {}, {}, 0
    register_cash = 30000            # cents in the drawer besides the $300 float... tracked for drops
    platform_week_start = start

    def card(date_, amount_cents):
        m = date_[:7]
        card_by_month[m] = card_by_month.get(m, 0) + amount_cents

    for n, d in enumerate(days):
        dt = date.fromisoformat(d)
        wd = dt.weekday()
        season = 1.25 if wd in (4, 5) else (0.85 if wd in (0, 1) else 1.0)
        sales = avg * season * rng.uniform(0.85, 1.15)
        instore = round(sales * 0.78, 2)
        own_delivery = round(sales * 0.12, 2)
        dfee = round(own_delivery / 25 * 3, 0)
        disc = round(sales * rng.uniform(0.01, 0.03), 2)
        comps = round(rng.choice([0, 0, 0, 8.5, 12.0, 18.0]), 2)
        refunds = round(rng.choice([0] * 8 + [14.25, 22.5]), 2)
        net = _c(instore) + _c(own_delivery) + _c(dfee) - _c(disc) - _c(comps) - _c(refunds)
        tax = pct_of(net, "7.35")
        tips = _c(sales * rng.uniform(0.05, 0.08))
        total = net + tax + tips
        cash = int(total * rng.uniform(0.15, 0.25) // 100 * 100)
        ev = {"type": "square_day", "date": d, "instore": s(_c(instore)), "delivery": s(_c(own_delivery)),
              "delivery_fees": s(_c(dfee)), "discounts": s(_c(disc)), "comps": s(_c(comps)),
              "refunds": s(_c(refunds)), "tips": s(tips), "cash": s(cash)}
        if wd == 5 and d != last_day:      # Saturday + Sunday paid out together on Monday
            ev.update(payout_group=f"wk{d}", payout_date=(dt + timedelta(days=2)).isoformat())
        elif wd == 6:
            prev = (dt - timedelta(days=1)).isoformat()
            if prev in days:
                ev.update(payout_group=f"wk{prev}", payout_date=(dt + timedelta(days=1)).isoformat())
        if p.get("square_format"):
            ev["format"] = p["square_format"]
        events.append(ev)
        tax_by_month[d[:7]] = tax_by_month.get(d[:7], 0) + tax
        tips_since_payroll += tips
        register_cash += cash

        # Suppliers
        for vendor, pay, day, items in SUPPLIERS:
            if wd == day:
                its = []
                for desc, share, acct, taxable in items:
                    its.append({"desc": desc, "amount": s(_c(avg * 7 * share * rng.uniform(0.85, 1.15))),
                                "account": acct, "taxable": taxable})
                pe = {"type": "purchase", "date": d, "vendor": vendor, "payment": pay, "items": its,
                      "invoice": vendor.startswith("Sysco"), "post_date": d if pay == "card" else None,
                      "clear_date": (dt + timedelta(days=2)).isoformat() if pay == "debit" else None}
                pe = {k: v for k, v in pe.items() if v is not None}
                r = rng.random()
                if r < mess * 0.3:
                    pe["receipt"] = {"missing": True}
                elif r < mess * 0.4 and d < truth.month_end(d[:7])[:8] + "20":
                    pe["receipt"] = {"late": True}
                elif rng.random() < mess:
                    pe["receipt"] = {"faded": True} if rng.random() < 0.5 else {"rotate": rng.choice([-5, 4, 7])}
                if adv and rng.random() < adv and "receipt" not in pe:
                    pe["illegible"] = "total"
                events.append(pe)
                if pay == "card":
                    sub = sum(_c(float(i["amount"])) for i in its)
                    taxable_sum = sum(_c(float(i["amount"])) for i in its if i["taxable"])
                    card(d, sub + pct_of(taxable_sum, "6.35"))
        if wd == 4:   # fuel for the delivery car, Fridays
            amt = _c(rng.uniform(38, 55))
            pe = {"type": "purchase", "date": d, "vendor": "Shell", "payment": "card", "post_date": d,
                  "items": [{"desc": "UNLEADED", "amount": s(amt), "account": "6400"}]}
            if rng.random() < 0.5:
                pe["receipt"] = {"missing": True}
            events.append(pe)
            card(d, amt)
        if dt.day == 5 and rng.random() < 0.8:   # petty cash run
            events.append({"type": "purchase", "date": d, "vendor": "CVS", "payment": "petty_cash",
                           "items": [{"desc": "PAPER TOWELS 12PK", "amount": s(_c(rng.uniform(12, 20))),
                                      "account": "6310", "taxable": True}]})

        # Weekly platform statements (Mon-Sun), and the cash sheet with the week's drop.
        if wd == 6 or d == last_day:
            wstart = max(platform_week_start, start)
            for platform, share in (("DoorDash", 0.10), ("Uber Eats", 0.06), ("Grubhub", 0.04)):
                sub = _c(avg * 7 * share * rng.uniform(0.8, 1.2) * (len(daterange(wstart, (dt - date.fromisoformat(wstart)).days + 1)) / 7))
                if sub <= 0:
                    continue
                pw = {"type": "platform_week", "platform": platform, "start": wstart, "end": d, "subtotal": s(sub)}
                if rng.random() < 0.3:
                    pw["promotions"] = s(_c(rng.uniform(10, 40)))
                if rng.random() < 0.15:
                    pw["adjustments"] = s(-_c(rng.uniform(5, 20)))
                if d == last_day and wd != 6:
                    pw["payout_date"] = d       # keep the payout inside the period
                events.append(pw)
            platform_week_start = (dt + timedelta(days=1)).isoformat()
            drop = max(0, register_cash - 30000) // 100 * 100
            cl = {"type": "cash_log", "date": d, "cash_sales": s(_c(rng.uniform(25, 90))),
                  "cash_drop": s(drop) if drop else "0", "deposit_date": d if d == last_day else
                  (dt + timedelta(days=1)).isoformat()}
            if rng.random() < mess * 0.5:
                cl["crossed_out"] = True
            if drop:
                register_cash -= drop
            events.append({k: v for k, v in cl.items() if v != "0"})

        # Payroll on the 15th and the last day of the month (tips paid through payroll).
        if dt.day == 15 or d == truth.month_end(d[:7]):
            gross = p.get("employees", 4) * _c(rng.uniform(1300, 1700)) + tips_since_payroll
            wh = gross * 14 // 100
            er = gross * 89 // 1000
            pstart = d[:8] + ("01" if dt.day == 15 else "16")
            events.append({"type": "payroll", "pay_date": d, "start": pstart, "end": d, "gross": s(gross),
                           "tips_paid": s(tips_since_payroll), "withholding": s(wh), "employer_taxes": s(er),
                           "debit_date": d})
            tips_since_payroll = 0

        # Monthly bank items
        if dt.day == 1:
            events.append({"type": "bank", "kind": "rent", "date": d, "amount": "4200.00"})
        if dt.day == 14:
            events.append({"type": "bank", "kind": "utility", "date": d, "amount": s(_c(rng.uniform(520, 690)))})
            events.append({"type": "bank", "kind": "gas_utility", "date": d, "amount": s(_c(rng.uniform(220, 340)))})
        if dt.day == 18:
            events.append({"type": "bank", "kind": "phone", "date": d, "amount": "149.99"})
            events.append({"type": "bank", "kind": "insurance", "date": d, "amount": "410.00"})
        if dt.day == 10:
            events.append({"type": "bank", "kind": "loan_payment", "date": d, "amount": 0,
                           "principal": "812.40", "interest": "187.60"})
        if dt.day == 25:
            events.append({"type": "bank", "kind": "owner_draw", "date": d, "amount": "3000.00"})
        if dt.day == 12:
            prev = (dt.replace(day=1) - timedelta(days=1)).isoformat()[:7]
            if card_by_month.get(prev):
                events.append({"type": "bank", "kind": "card_payment", "date": d, "amount": s(card_by_month[prev])})
        if dt.day == 20:
            prev = (dt.replace(day=1) - timedelta(days=1)).isoformat()[:7]
            if tax_by_month.get(prev):
                events.append({"type": "bank", "kind": "sales_tax_payment", "date": d, "amount": s(tax_by_month[prev])})
        if d == truth.month_end(d[:7]):
            events.append({"type": "manual", "date": d, "memo": f"Straight-line depreciation {d[:7]}",
                           "lines": [["6600", "535.71", 0], ["1590", 0, "535.71"]]})
            if p.get("year_end") and d.endswith("12-31"):
                events.append({"type": "manual", "date": d, "memo": "Year-end inventory count",
                               "lines": [["1200", "2650.00", 0], ["5010", 0, "2650.00"]]})

    scenario = {"id": p.get("id", "simulation"), "title": p.get("title", "Business simulation"), "area": "Simulation",
                "start": start, "difficulty": p.get("difficulty", "scanned"),
                "opening": {"bank": "25000.00", "cash": "300.00", "petty": "200.00", "equipment": "45000.00",
                            "accumulated_depreciation": "12000.00", "loan": "30000.00"},
                "events": events}
    if p.get("square_format"):
        scenario["square_format"] = p["square_format"]
    if p.get("close_months"):
        scenario["close_months"] = [{"month": m, "after": truth.add_days(truth.month_end(m), 4)}
                                    for m in sorted({d[:7] for d in days})[:-1]]
    return scenario


def preset(name, seed=1):
    if name not in PRESETS:
        raise ValueError(f"No preset {name!r}. Presets: {', '.join(PRESETS)}")
    return simulate(dict(PRESETS[name], id=name), seed)

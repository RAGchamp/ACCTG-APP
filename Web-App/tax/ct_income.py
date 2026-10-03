"""Connecticut income tax figures and the compliance calendar (plan §10.4).

A single-member LLC files no separate CT income tax return: its profit flows
through the owner's federal AGI into the CT-1040. The app shows the business
figures that flow in and the dates to remember; it does not compute the owner's
personal CT tax (that depends on their whole return).
"""

from datetime import date

from tax import rules_loader
from tax.sales_tax_ct import period_dates


def ct_figures(schedule_c, se):
    return {"lines": [
        {"label": "Schedule C net profit (flows into federal AGI via Schedule 1)", "amount": schedule_c["L"]["31"]},
        {"label": "Deductible part of self-employment tax (federal adjustment, reduces AGI)", "amount": -se["deduction"]},
        {"label": "Net effect of the business on federal AGI (the starting point of CT-1040)",
         "amount": schedule_c["L"]["31"] - se["deduction"]},
    ], "note": "Connecticut has no separate business income tax return for a single-member LLC owned by an individual."}


def calendar(year, frequency="monthly"):
    fed = rules_loader.load(year, "federal")
    ct = rules_loader.load(year, "ct")
    items = [{"what": d["what"], "date": d["date"], "who": "IRS", "form": d.get("form", "")}
             for d in fed.get("due_dates", [])]
    items += [{"what": d["what"], "date": d["date"], "who": "CT", "form": d.get("form", "")}
              for d in ct.get("calendar", []) if d["date"][:1].isdigit()]
    if frequency == "monthly":
        for m in range(1, 13):
            month = f"{year}-{m:02d}"
            nxt = f"{year + (m == 12)}-{m % 12 + 1:02d}"
            items.append({"what": f"OS-114 sales tax for {month}", "date": period_dates(nxt)[1], "who": "CT DRS",
                          "form": "OS-114"})
    today = date.today().isoformat()
    for i in items:
        i["past"] = i["date"] < today
    return sorted(items, key=lambda i: i["date"])

"""Coverage report (plan §15.4): which features each scenario exercises, so a
feature without a scenario is visible."""

from testdata import generate

# The features the app has; each should be exercised by at least one scenario.
FEATURES = {
    "receipt": "purchase receipt extraction and booking", "pay:card": "receipt paid by card (2150)",
    "pay:cash": "receipt paid in cash (1000)", "pay:petty_cash": "petty cash (1020)", "pay:debit": "debit card (2150 / bank)",
    "pay:check": "paid by check", "refund": "refund / return receipt", "late_receipt": "receipt after its statement (support)",
    "illegible_digit": "[?] unreadable figure + owner correction", "feedback": "owner feedback -> revised proposal",
    "vendor_rule": "feedback becomes a vendor rule", "card_statement": "card statement", "bulk_accept": "bulk accept",
    "statement_gap": "missing statement flagged", "bank_statement": "bank statement", "bank_csv": "bank CSV import",
    "bank_pdf": "bank PDF", "check": "check image", "check_incoming": "customer check", "check_pays_invoice":
    "check paying an invoice (support)", "void": "voided check", "words_mismatch": "written vs numeric amount",
    "cash_log": "handwritten cash sheet", "sum_error": "sheet total wrong", "crossed_out": "crossed-out figure",
    "square": "Square report", "platform": "delivery platform statement", "doordash": "DoorDash", "ubereats": "Uber Eats",
    "grubhub": "Grubhub", "payroll": "payroll provider report", "reversal": "reversing correction",
    "pos_tax_flag": "register tax rate flag",
}


def scenario_features():
    out = {}
    for sid, sc in generate.load_scenarios().items():
        _, order = generate.build(sc, seed=1)
        feats = sorted({f for d in order for f in d["features"]})
        if sc.get("close_months"):
            feats.append("month_close")
        if any(e.get("type") == "bad_file" for e in sc.get("events", [])):
            feats.append("bad_input")
        if any(e.get("type") == "manual" for e in sc.get("events", [])):
            feats.append("manual_entry")
        out[sid] = {"area": sc.get("area"), "title": sc.get("title"), "features": feats,
                    "documents": len(order)}
    return out


def report():
    sf = scenario_features()
    covered = {}
    for sid, v in sf.items():
        for f in v["features"]:
            covered.setdefault(f, []).append(sid)
    missing = [f for f in FEATURES if f not in covered]
    return {"scenarios": sf, "covered": covered, "missing": missing, "features": FEATURES}


def report_text():
    r = report()
    lines = ["Feature coverage (plan §15.4)", ""]
    for f, label in FEATURES.items():
        sids = r["covered"].get(f, [])
        lines.append(f"  {'✓' if sids else '✗'} {f:20s} {label:45s} {len(sids)} scenario(s)")
    extra = sorted(set(r["covered"]) - set(FEATURES))
    if extra:
        lines.append("\n  also covered: " + ", ".join(extra))
    lines.append(f"\n{len(r['scenarios'])} scenarios; {len(r['missing'])} feature(s) without a scenario"
                 + (": " + ", ".join(r["missing"]) if r["missing"] else ""))
    return "\n".join(lines)

"""Generate a test set into a sandbox (plan §15.1-15.3).

scenario (YAML) or simulation -> Builder (ledger truth) -> files (PDF / CSV)
-> answer_key.json -> a sandbox business with the opening balances posted.

The answer key holds, for each generated file (by SHA-256):
  extract_reply   what Claude should read from it (replayed in mocked runs)
  documents[n]    the expected entries, the replayed propose / revise replies,
                  the owner script and the expected outcome
and "expected": the reports, OS-114 worksheets, reconciliations and Schedule C
computed by posting the expected entries with the app's own ledger code.
"""

import copy
import hashlib
import json
import random
import tempfile
from pathlib import Path

import pymupdf
import yaml

import config
from books import business, db
from books.money import parse_cents, pct_of
from books.post import post_entry
from reports import statements
from tax import federal, sales_tax_ct
from testdata import degrade, render, truth

DIGITAL_TYPES = ("credit_card_statement", "bank_statement", "platform_statement", "payroll_report")
HANDLERS = {"purchase": "purchase", "check_out": "check_out", "check_in": "check_in", "cash_log": "cash_log",
            "square_day": "square_day", "platform_week": "platform_week", "payroll": "payroll", "bank": "bank",
            "card_charge": "card_charge", "manual": "manual_entry", "bad_file": "bad_file",
            "purchases_batch": "purchases_batch"}
GENERATOR_VERSION = "1"


# ------------------------------------------------------------------ scenarios

def plain(obj):
    """YAML turns 2026-01-14 into a date; the builder works with ISO strings."""
    import datetime as _dt
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain(v) for v in obj]
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()[:10]
    return obj


def load_scenarios():
    """{id: scenario} from testdata\\scenarios\\*.yaml (each file: a list of scenarios)."""
    out = {}
    for path in sorted(config.SCENARIOS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for sc in data:
            sc = plain(sc)
            sc["source_file"] = path.name
            if sc["id"] in out:
                raise ValueError(f"Duplicate scenario id {sc['id']} in {path.name}")
            out[sc["id"]] = sc
    return out


def build(scenario, seed=1):
    b = truth.Builder(scenario, seed)
    start = scenario.get("start", "2026-01-01")
    b.opening_balances(scenario.get("opening", {}), start)
    for ev in scenario.get("events", []):
        handler = HANDLERS.get(ev.get("type"))
        if not handler:
            raise truth.ScenarioError(f"{scenario['id']}: unknown event type {ev.get('type')!r}")
        getattr(b, handler)(ev)
    b.statements(start)
    order = b.finalize()
    for spec in order:
        spec["scenario"] = scenario["id"]
    return b, order


# ------------------------------------------------------------------ files

def slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in str(text)).strip("-")[:28].lower()


def plan_files(b, order):
    files, groups = [], {}
    for spec in order:
        t = spec["doc_type"]
        if t == "pos_report" and spec.get("square_format") == "csv":
            k = ("square_csv", spec["extraction"]["period_start"][:7])
        elif t == "bank_statement" and spec.get("bank_format") == "csv":
            k = ("bank_csv", spec["key"])
        elif spec["render"].get("group"):
            k = ("group", spec["render"]["group"])
        else:
            k = ("one", spec["key"])
        if k not in groups:
            groups[k] = {"kind": "csv" if k[0].endswith("csv") else "pdf", "group": k[0], "docs": []}
            files.append(groups[k])
        groups[k]["docs"].append(spec)
    for bad in b.bad_files:
        files.append({"kind": "pdf", "group": "bad", "docs": [], "bad": bad})
    for f in files:
        if f["docs"]:
            f["available"] = max(d["available"] for d in f["docs"])
            f["prio"] = min(d["prio"] for d in f["docs"])
            f["sort"] = f["docs"][0]["key"]
        else:
            f["available"] = f["bad"].get("available", f["bad"].get("date", "2026-01-15"))
            f["prio"] = 75
            f["sort"] = f["bad"]["key"]
    files.sort(key=lambda f: (f["available"], f["prio"], f["sort"]))
    for i, f in enumerate(files, 1):
        f["seq"] = i * 10
        first = f["docs"][0] if f["docs"] else None
        if first:
            party = (first["extraction"].get("vendor") or first["extraction"].get("platform")
                     or first["extraction"].get("payee") or first["extraction"].get("issuer")
                     or first["extraction"].get("bank") or first["extraction"].get("provider") or "")
            what = {"square_csv": "square-sales", "bank_csv": "bank-export", "group": "receipts-batch"}.get(
                f["group"], first["doc_type"].replace("_", "-"))
            f["name"] = f"{i:03d}-{f['available']}-{what}-{slug(party)}.{'csv' if f['kind'] == 'csv' else 'pdf'}"
        else:
            f["name"] = f"{i:03d}-{f['available']}-{f['bad']['kind']}.pdf"
    return files


def _difficulty(spec, scenario):
    level = spec["render"].get("difficulty") or scenario.get("difficulty", "scanned")
    if spec["doc_type"] in DIGITAL_TYPES and not spec["render"].get("scan"):
        level = scenario.get("digital_difficulty", "clean")
    if spec["render"].get("illegible") and level == "clean":
        level = "scanned"
    return level


def render_file(f, scenario, seed):
    rng = random.Random(seed)
    if f.get("bad"):
        return render_bad(f["bad"], seed)
    if f["kind"] == "csv":
        if f["group"] == "square_csv":
            return render.square_csv(f["docs"])
        return render.bank_csv(f["docs"][0]["extraction"])
    specs = f["docs"]
    if f["group"] == "group":
        # Several receipts scanned on one page.
        page = pymupdf.open()
        sheet = page.new_page(width=612, height=792)
        x, y, col_h = 20, 20, 0
        for spec in specs:
            src = pymupdf.open("pdf", render.text_pdf(spec))
            r = src[0].rect
            scale = min(1.0, 270 / r.width)
            w, h = r.width * scale, r.height * scale
            if x + w > 600:
                x, y, col_h = 20, y + col_h + 20, 0
            sheet.show_pdf_page(pymupdf.Rect(x, y, x + w, y + h), src, 0)
            x += w + 30
            col_h = max(col_h, h)
            src.close()
        data = page.tobytes()
        page.close()
        level = _difficulty(specs[0], scenario)
        return degrade.scan(render.finish(data, "receipts"), level if level != "clean" else "scanned", seed)
    spec = specs[0]
    level = _difficulty(spec, scenario)
    if spec["render"].get("layout") in ("check", "cash_log"):
        return degrade.handwritten_pdf(spec, level, seed)
    data = render.finish(render.text_pdf(spec), spec["doc_type"])
    ill = spec["render"].get("illegible")
    if ill:
        data = degrade.obscure_digit(data, ill["value"], ill["pos"], rng)
    if level == "clean":
        return data
    return degrade.scan(data, level, seed, faded=spec["render"].get("faded", False),
                        rotate=spec["render"].get("rotate", 0.0), upside_down=spec["render"].get("upside_down", False),
                        title=spec["doc_type"])


def render_bad(bad, seed):
    kind = bad["kind"]
    if kind == "blank":
        return degrade.scan(render.finish(render.blank_page({}), "blank"), "scanned", seed)
    if kind == "flyer":
        return degrade.scan(render.finish(render.flyer({}), "flyer"), "scanned", seed)
    if kind == "password":
        doc = pymupdf.open("pdf", render.finish(render.flyer({}), "locked"))
        data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-synthetic", user_pw="synthetic",
                           no_new_id=True)
        doc.close()
        return data
    if kind == "corrupt":
        return b"%PDF-1.7\n% SYNTHETIC TEST DATA - deliberately corrupt file\n1 0 obj << /Type /Catalog >>\n"
    raise truth.ScenarioError(f"unknown bad_file kind {kind!r}")


# ------------------------------------------------------------------ replies

def _pages(f, data):
    if f["kind"] == "csv":
        return [1]
    try:
        doc = pymupdf.open("pdf", data)
        n = doc.page_count
        doc.close()
    except Exception:
        return [1]
    return list(range(1, n + 1))


def extract_reply(f, data):
    if f.get("bad"):
        return {"documents": [{"pages": [1], "doc_type": "not_financial",
                               "description": f"{f['bad']['kind']} page"}]}
    pages = _pages(f, data)
    docs = []
    for spec in f["docs"]:
        shown = copy.deepcopy(spec["shown"] or spec["extraction"])
        docs.append(dict({"pages": [1] if f["group"] == "group" else pages, "confidence": spec["confidence"]}, **shown))
    return {"documents": docs}


def propose_reply(spec):
    if spec["support"]:
        return {"support_only": True, "entries": [], "confidence": "high",
                "reason": "Already booked from another document (see the match hint).", "questions": []}
    if spec.get("first"):
        f = spec["first"]
        return {"support_only": False, "entries": f["entries"], "confidence": f.get("confidence", "medium"),
                "reason": "Booked by the vendor's usual categories.", "questions": f.get("questions", [])}
    return {"support_only": False, "entries": spec["entries"], "confidence": spec["confidence"],
            "reason": f"Standard {spec['doc_type'].replace('_', ' ')} posting rules.",
            "questions": spec.get("questions", [])}


def revise_reply(spec):
    if not spec.get("first") and not any(a["action"] in ("feedback", "answer") for a in spec["owner"]):
        return None
    rule = next((a.get("rule") for a in spec["owner"] if a["action"] == "feedback" and a.get("rule")), None)
    return {"support_only": spec["support"], "entries": spec["entries"], "confidence": "high",
            "reason": "Revised following the owner's feedback.", "questions": [], "rule_suggestion": rule}


# ------------------------------------------------------------------ expected results

def expected_results(b, order, scenario, profile):
    """Post the expected entries with the app's own ledger code, then run the reports."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shadow.db"
        db.init_db(path, profile)
        with db.session(path) as conn:
            post_entry(conn, b.opening, kind="opening", allow_closed=True)
            manual = sorted(b.manual, key=lambda m: m["date"])
            dates = [b.opening["date"]]
            for spec in order:
                while manual and manual[0]["date"] < spec["available"]:
                    m = manual.pop(0)
                    post_entry(conn, m["entry"], kind="manual", allow_closed=True)
                    dates.append(m["date"])
                if spec["expect"] in ("post", "flag"):
                    for e in spec["entries"]:
                        post_entry(conn, e, kind="document", allow_closed=True)
                        dates.append(e["date"])
                    if spec.get("replacement"):
                        for e in spec["entries"]:       # the reversal nets the original out
                            post_entry(conn, {"date": e["date"], "memo": "reversal", "lines": [
                                dict(l, debit=l["credit"], credit=l["debit"]) for l in e["lines"]]},
                                kind="reversal", allow_closed=True)
                        post_entry(conn, spec["replacement"], kind="adjustment", allow_closed=True)
            for m in manual:
                post_entry(conn, m["entry"], kind="manual", allow_closed=True)
                dates.append(m["date"])
            start = scenario.get("start", "2026-01-01")
            end = truth.month_end(truth.month_of(max(dates)))
            summary = statements.summary(conn, start, end)
            months, m = [], truth.month_of(start)
            while m <= truth.month_of(end):
                months.append(m)
                m = truth.next_month(m)
            os114 = {m: sales_tax_ct.worksheet(conn, m) for m in months}
            # The shadow ledger has no documents table rows: add the receipts' use tax here.
            for spec in order:
                e = spec["extraction"]
                if (spec["doc_type"] == "purchase_receipt" and spec["expect"] in ("post", "flag")
                        and not spec["support"] and not parse_cents(e.get("tax"))):
                    taxable = sum(parse_cents(l["amount"]) for l in e["lines"] if l.get("taxable"))
                    m = e["date"][:7]
                    if taxable and m in os114:
                        use = pct_of(taxable, "6.35")
                        os114[m]["use_tax"] += use
                        os114[m]["total_due"] += use
            year = int(start[:4])
            sch_c = federal.schedule_c(conn, year)
            se = federal.schedule_se(sch_c["L"]["31"], year)
            gl_month_end = {}
            from books.post import balances
            for m in months:
                bal = balances(conn, end=truth.month_end(m))
                gl_month_end[m] = {"1010": bal.get("1010", 0), "2100": bal.get("2100", 0)}
    reconciled = {}
    for spec in order:
        if spec["doc_type"] in ("bank_statement", "credit_card_statement") and not spec.get("duplicate_of_statement"):
            e = spec["extraction"]
            m = e["period_end"][:7]
            acct = "1010" if spec["doc_type"] == "bank_statement" else "2100"
            gl = gl_month_end.get(m, {}).get(acct, 0) * (1 if acct == "1010" else -1)
            reconciled[f"{m} {acct}"] = parse_cents(e["closing_balance"]) == gl
    statement_closings = {}
    for spec in order:
        if spec["doc_type"] in ("bank_statement", "credit_card_statement") and not spec.get("duplicate_of_statement"):
            e = spec["extraction"]
            statement_closings[f"{spec['doc_type']}:{e['period_end'][:7]}"] = e["closing_balance"]
    return {"period": {"start": start, "end": end}, "reports": summary,
            "os114": {m: {k: w[k] for k in ("gross_receipts", "marketplace", "deductions", "taxable", "tax_due",
                                            "use_tax", "total_due", "collected_per_books", "difference")}
                      for m, w in os114.items()},
            "schedule_c": sch_c["L"], "schedule_c_part3": sch_c["part3"], "schedule_se": se,
            "gl_month_end": gl_month_end, "statement_closings": statement_closings, "reconciled": reconciled}


# ------------------------------------------------------------------ generate

def generate(scenario, sandbox_name=None, seed=1, difficulty=None, ai_mode="replay", out_dir=None, progress=None):
    """Generate `scenario` (a dict, or a scenario id) into a new sandbox. Returns a summary dict."""
    if isinstance(scenario, str):
        scenarios = load_scenarios()
        if scenario not in scenarios:
            raise ValueError(f"No scenario {scenario!r}. Try: python -m testdata list")
        scenario = scenarios[scenario]
    scenario = plain(copy.deepcopy(scenario))
    if difficulty:
        scenario["difficulty"] = difficulty
    b, order = build(scenario, seed)
    files = plan_files(b, order)
    sandbox_name = sandbox_name or scenario["id"]
    profile = dict(truth.PROFILE, **scenario.get("profile", {}))
    biz = business.create_sandbox(sandbox_name, ai_mode=ai_mode, replace=True, profile=profile)
    inputs = Path(out_dir) if out_dir else biz.root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    key = {"version": 1, "generator_version": GENERATOR_VERSION, "scenario": scenario["id"],
           "title": scenario.get("title", ""), "seed": seed, "difficulty": scenario.get("difficulty", "scanned"),
           "files": {}, "file_order": [], "close_months": scenario.get("close_months", []),
           "manual": b.manual, "opening": b.opening, "features": sorted({x for d in order for x in d["features"]})}
    for i, f in enumerate(files):
        if progress:
            progress(i, len(files), f"Rendering {f['name']}")
        data = render_file(f, scenario, seed * 1000 + i)
        path = inputs / f["name"]
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        entry = {"filename": f["name"], "seq": f["seq"], "available": f["available"], "kind": f["kind"],
                 "path": str(path), "expect": "refuse" if f.get("bad", {}).get("kind") in ("password", "corrupt")
                 else ("reject" if f.get("bad") else "process"),
                 "duplicate_upload": any(d.get("duplicate_upload") for d in f["docs"]),
                 "extract_reply": extract_reply(f, data), "documents": {}}
        for part, spec in enumerate(f["docs"], 1):
            entry["documents"][str(part)] = {
                "key": spec["key"], "doc_type": spec["doc_type"], "expect": spec["expect"],
                "entries": [] if spec.get("replacement") else spec["entries"], "support": spec["support"],
                "truth_extraction": spec["extraction"],
                "owner": spec["owner"], "flags_expected": spec["flags_expected"],
                "propose_errors_expected": spec.get("propose_errors_expected", []),
                "propose_reply": propose_reply(spec), "revise_reply": revise_reply(spec),
                "features": spec["features"], "available": spec["available"]}
        key["files"][sha] = entry
        key["file_order"].append(sha)
    with biz.session() as conn:
        prof = db.profile(conn)
    key["expected"] = expected_results(b, order, scenario, prof)
    biz.answer_key_path.write_text(json.dumps(key, indent=1, default=str), encoding="utf-8")
    business.reset_sandbox(sandbox_name)         # posts the opening balances from the key
    return {"sandbox": sandbox_name, "files": len(files), "documents": len(order), "inputs": str(inputs),
            "features": key["features"], "answer_key": str(biz.answer_key_path)}

"""The evaluation scorecard as one self-contained HTML page (plan §15.6)."""

import html


def esc(x):
    return html.escape("" if x is None else str(x))


def money(c):
    if not isinstance(c, int) or isinstance(c, bool):
        return esc(c)
    return f"{'-' if c < 0 else ''}${abs(c) // 100:,}.{abs(c) % 100:02d}"


def pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "–"


def badge(ok):
    return '<span class="ok">PASS</span>' if ok else '<span class="bad">FAIL</span>'


def _cmp_table(title, rows):
    if not rows:
        return ""
    body = "".join(f"<tr class='{'' if r['ok'] else 'row-bad'}'><td>{esc(r['field'])}</td><td class=r>{money(r['expected'])}"
                   f"</td><td class=r>{money(r['actual'])}</td><td>{badge(r['ok'])}</td></tr>" for r in rows)
    bad = sum(1 for r in rows if not r["ok"])
    return (f"<details {'open' if bad else ''}><summary><b>{esc(title)}</b> - {len(rows) - bad} of {len(rows)} identical"
            f"</summary><table><tr><th>Figure</th><th class=r>Expected</th><th class=r>Actual</th><th></th></tr>"
            f"{body}</table></details>")


def render(r):
    s = r["stages"]
    tiles = [
        ("Result", "PASS" if r["passed"] else "FAIL", "all stages identical to the answer key"),
        ("Extraction", pct(s["extract"]["fields_ok"], s["extract"]["fields"]),
         f"{s['extract']['silently_wrong']} silently wrong (must be 0)"),
        ("Entries", f"{s['propose']['entries_ok']} / {s['propose']['docs']}", f"{s['propose']['owner_edits']} owner edits"),
        ("Matching", f"{s['match']['support_ok']} / {s['match']['support']}",
         f"{s['match']['double_counted']} double-counted (must be 0)"),
        ("Reports", f"{s['reports']['ok']} / {s['reports']['total']}", "TB, IS, BS, CF, equity"),
        ("Tax", f"{s['tax']['ok']} / {s['tax']['total']}", "OS-114, Schedule C / SE"),
        ("Reconciled", f"{s['reconcile']['ok']} / {s['reconcile']['total']}", "statement vs GL"),
        ("Claude", f"{s['cost']['calls']} calls", f"{s['cost']['seconds']} s, ${s['cost']['usd']}"),
    ]
    tile_html = "".join(f"<div class=tile><div class=t>{esc(a)}</div><div class=v>{esc(b)}</div>"
                        f"<div class=n>{esc(c)}</div></div>" for a, b, c in tiles)
    types = "".join(f"<tr><td>{esc(t)}</td><td class=r>{v['docs']}</td><td class=r>{pct(v['fields_ok'], v['fields'])}</td>"
                    f"<td class=r>{v['entries_ok']} / {v['docs']}</td><td class=r>{v['ok']} / {v['docs']}</td></tr>"
                    for t, v in sorted(r["by_type"].items()))
    docs = "".join(
        f"<tr class='{'' if d['ok'] else 'row-bad'}'><td>{esc(d['file'])}#{d['part']}</td><td>{esc(d['doc_type'])}</td>"
        f"<td>{esc(d['expect'])}</td><td>{esc(d['status'])}</td><td class=r>{d['fields_ok']}/{d['fields_total']}</td>"
        f"<td>{badge(d['entries_ok'])}</td><td class=small>{esc(', '.join(d['features']))}</td>"
        f"<td class=small>{esc('; '.join(d['errors'] + ['wrong: ' + ', '.join(d['fields_wrong'])] if d['fields_wrong'] else d['errors']))}"
        f"{esc(' missing flags: ' + ', '.join(d['missing_flags'])) if d['missing_flags'] else ''}"
        f"{esc(' entries: ' + str(d.get('entries_diff'))) if d.get('entries_diff') else ''}</td></tr>"
        for d in r["documents"])
    files = "".join(f"<tr class='{'' if f.get('ok', True) else 'row-bad'}'><td>{esc(f['filename'])}</td>"
                    f"<td>{esc(f.get('expect', ''))}</td><td class=r>{f.get('documents', '')}</td>"
                    f"<td class=small>{esc('; '.join(f.get('notes', [])))}</td></tr>" for f in r["files"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Test scorecard</title>
<style>
:root {{ --bg:#fff; --fg:#1b1f24; --muted:#5b6470; --line:#dde1e6; --ok:#1a7f37; --bad:#c62828; --tile:#f5f7fa; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15181c; --fg:#e8eaed; --muted:#9aa3ad; --line:#30363d; --ok:#56d364; --bad:#ff7b72; --tile:#1f242a; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0 auto; max-width:1200px; padding:16px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .muted {{ color:var(--muted); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; margin:16px 0; }}
.tile {{ background:var(--tile); border-radius:8px; padding:10px; }} .t {{ color:var(--muted); font-size:12px; }}
.v {{ font-size:20px; font-weight:600; }} .n {{ color:var(--muted); font-size:12px; }}
table {{ border-collapse:collapse; width:100%; margin:8px 0 16px; }} td, th {{ border-bottom:1px solid var(--line); padding:4px 6px; text-align:left; vertical-align:top; }}
.r {{ text-align:right; font-variant-numeric:tabular-nums; }} .small {{ font-size:12px; color:var(--muted); }}
.ok {{ color:var(--ok); font-weight:600; }} .bad {{ color:var(--bad); font-weight:600; }} .row-bad td {{ background:rgba(198,40,40,.07); }}
details {{ margin:8px 0; }} summary {{ cursor:pointer; }} .wrap {{ overflow-x:auto; }}
</style></head><body>
<h1>Test scorecard - {esc(r['sandbox'])} {badge(r['passed'])}</h1>
<div class=muted>Scenario <b>{esc(r['scenario'])}</b> {esc(r.get('title') or '')} · mode <b>{esc(r['mode'])}</b>
 · difficulty {esc(r.get('difficulty'))} · seed {esc(r.get('seed'))} · {esc(r['at'])} · {r['seconds']} s · SYNTHETIC TEST DATA</div>
<div class=tiles>{tile_html}</div>
<h2>By document type</h2><div class=wrap><table><tr><th>Type</th><th class=r>Docs</th><th class=r>Fields right</th>
<th class=r>Entries right</th><th class=r>All right</th></tr>{types}</table></div>
<h2>Documents</h2><div class=wrap><table><tr><th>File</th><th>Type</th><th>Expected</th><th>Status</th><th class=r>Fields</th>
<th>Entries</th><th>Features</th><th>Notes</th></tr>{docs}</table></div>
<h2>Files</h2><div class=wrap><table><tr><th>File</th><th>Expected</th><th class=r>Documents</th><th>Notes</th></tr>{files}</table></div>
<h2>Reports, reconciliation and tax</h2>
{_cmp_table('Financial statements', r['reports'])}{_cmp_table('Reconciliation (statement vs GL)', r['reconciliation'])}
{_cmp_table('OS-114 sales tax', r['os114'])}{_cmp_table('Schedule C / SE', r['schedule_c'])}
<p class=small>Features covered: {esc(', '.join(r.get('features', [])))}</p>
</body></html>"""

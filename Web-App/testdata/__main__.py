"""python -m testdata ... (plan §15.7)

  python -m testdata list                                   the scenario catalog and its coverage
  python -m testdata generate receipt-cash-split --difficulty messy --seed 7
  python -m testdata simulate jan-2026-scanned --sandbox jan26
  python -m testdata evaluate jan26 [--live | --mocked]     --live makes real Claude calls (costs usage)
  python -m testdata all [--difficulty scanned]             generate + evaluate every scenario (mocked)
  python -m testdata coverage                               which features each scenario exercises
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testdata import coverage, evaluate, generate, simulate  # noqa: E402


def _progress(*args):
    print("  ", *args[-1:], flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m testdata", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("coverage")
    g = sub.add_parser("generate")
    g.add_argument("scenario")
    g.add_argument("--sandbox")
    g.add_argument("--difficulty", choices=["clean", "scanned", "messy", "adversarial", "photo"])
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--live", action="store_true", help="the sandbox calls Claude for real (default: replay)")
    s = sub.add_parser("simulate")
    s.add_argument("preset", choices=list(simulate.PRESETS))
    s.add_argument("--sandbox")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--difficulty", choices=["clean", "scanned", "messy", "adversarial", "photo"])
    s.add_argument("--live", action="store_true")
    e = sub.add_parser("evaluate")
    e.add_argument("sandbox")
    mode = e.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mocked", action="store_true")
    a = sub.add_parser("all")
    a.add_argument("--difficulty", choices=["clean", "scanned", "messy", "adversarial", "photo"])
    a.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        scen = generate.load_scenarios()
        area = None
        for sid, sc in scen.items():
            if sc.get("area") != area:
                area = sc.get("area")
                print(f"\n{area}")
            print(f"  {sid:34s} {sc.get('title', '')}")
        print("\nSimulation presets:")
        for name, p in simulate.PRESETS.items():
            print(f"  {name:34s} {p['title']}")
        return 0
    if args.cmd == "coverage":
        print(coverage.report_text())
        return 0
    if args.cmd == "generate":
        out = generate.generate(args.scenario, args.sandbox, seed=args.seed, difficulty=args.difficulty,
                                ai_mode="live" if args.live else "replay", progress=_progress)
        print(f"Sandbox {out['sandbox']}: {out['files']} files, {out['documents']} documents in {out['inputs']}")
        return 0
    if args.cmd == "simulate":
        sc = simulate.preset(args.preset, args.seed)
        out = generate.generate(sc, args.sandbox or args.preset, seed=args.seed, difficulty=args.difficulty,
                                ai_mode="live" if args.live else "replay", progress=_progress)
        print(f"Sandbox {out['sandbox']}: {out['files']} files, {out['documents']} documents in {out['inputs']}")
        return 0
    if args.cmd == "evaluate":
        mode = "live" if args.live else ("replay" if args.mocked else None)
        r = evaluate.run(args.sandbox, mode, progress=_progress)
        _print_result(r)
        return 0 if r["passed"] else 1
    if args.cmd == "all":
        failed = []
        for sid in generate.load_scenarios():
            generate.generate(sid, seed=args.seed, difficulty=args.difficulty)
            r = evaluate.run(sid)
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {sid}")
            if not r["passed"]:
                failed.append(sid)
        print(f"\n{len(failed)} failed" + (": " + ", ".join(failed) if failed else ""))
        return 1 if failed else 0


def _print_result(r):
    s = r["stages"]
    print(f"\n{'PASS' if r['passed'] else 'FAIL'}  {r['sandbox']} ({r['mode']}, {r['seconds']} s)")
    print(f"  extraction  {s['extract']['fields_ok']}/{s['extract']['fields']} fields, "
          f"{s['extract']['silently_wrong']} silently wrong, {s['extract']['missing_flags']} missing flags")
    print(f"  entries     {s['propose']['entries_ok']}/{s['propose']['docs']} documents, "
          f"{s['propose']['owner_edits']} owner edits")
    print(f"  matching    {s['match']['support_ok']}/{s['match']['support']} support-only, "
          f"{s['match']['double_counted']} double-counted")
    print(f"  reports     {s['reports']['ok']}/{s['reports']['total']}   reconcile {s['reconcile']['ok']}/"
          f"{s['reconcile']['total']}   tax {s['tax']['ok']}/{s['tax']['total']}")
    print(f"  Claude      {s['cost']['calls']} calls, {s['cost']['seconds']} s, ${s['cost']['usd']}")
    print(f"  scorecard   {r.get('files_saved', [''])[-1]}")
    for d in r["documents"]:
        if not d["ok"]:
            print(f"  ✗ {d['file']}#{d['part']} {d['doc_type']}: status {d['status']} (expected {d['expect']})"
                  f"{' entries differ' if not d['entries_ok'] else ''} {'; '.join(d['errors'])[:200]}")


if __name__ == "__main__":
    sys.exit(main())

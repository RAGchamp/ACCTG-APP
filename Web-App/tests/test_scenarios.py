"""Every synthetic scenario, end to end, in mocked (replay) mode (plan §14, §15.6).

Each scenario is generated into its own sandbox (PDFs / CSVs + answer key) and
the real pipeline runs on it: intake, extraction, proposal checks, the owner
script, posting, matching, reconciliation, reports and tax worksheets. Claude's
replies come from the answer key, so this costs nothing.
"""

import hashlib

import pytest

from testdata import evaluate, generate, simulate

SCENARIOS = list(generate.load_scenarios())


@pytest.mark.parametrize("sid", SCENARIOS)
def test_scenario(sid):
    generate.generate(sid, seed=3)
    r = evaluate.run(sid)
    problems = [d for d in r["documents"] if not d["ok"]] + [f for f in r["files"] if not f.get("ok", True)] + \
               [x for x in r["reports"] + r["os114"] + r["schedule_c"] + r["reconciliation"] if not x["ok"]]
    assert r["passed"], problems[:5]
    assert r["stages"]["extract"]["silently_wrong"] == 0
    assert r["stages"]["match"]["double_counted"] == 0


def test_generation_is_deterministic():
    """Same scenario + seed + generator version -> byte-identical files (plan §15.2)."""
    def hashes(name):
        out = generate.generate("receipt-multipage-invoice", name, seed=11, difficulty="messy")
        from books import business
        root = business.sandbox(name).root / "inputs"
        return sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()), out
    a, _ = hashes("det-a")
    b, _ = hashes("det-b")
    assert a == b


def test_every_feature_has_a_scenario():
    from testdata import coverage
    assert coverage.report()["missing"] == []


@pytest.mark.slow
@pytest.mark.parametrize("preset", ["smoke-1-week", "jan-2026-scanned"])
def test_simulation(preset):
    generate.generate(simulate.preset(preset, 5), preset, seed=5)
    r = evaluate.run(preset)
    assert r["passed"], [d for d in r["documents"] if not d["ok"]][:3]

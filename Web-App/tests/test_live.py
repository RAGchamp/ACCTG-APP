"""Real Claude calls on generated documents (plan §15.6 "live"). Costs usage on the
Claude Code plan, so it only runs on request:   python -m pytest -m live
"""

import pytest

from testdata import evaluate, generate

LIVE_SCENARIOS = ["receipt-card-matched", "cash-log-day", "square-day"]


@pytest.mark.live
@pytest.mark.parametrize("sid", LIVE_SCENARIOS)
def test_live_scenario(sid):
    generate.generate(sid, f"live-{sid}", seed=3, ai_mode="live")
    r = evaluate.run(f"live-{sid}", "live")
    s = r["stages"]
    assert s["extract"]["silently_wrong"] == 0, r["documents"]
    assert s["match"]["double_counted"] == 0
    assert r["passed"], [d for d in r["documents"] if not d["ok"]]

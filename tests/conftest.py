import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def stats():
    from jobscan.run import new_stats
    return defaultdict(new_stats)


@pytest.fixture
def no_ai():
    class NoGemini:
        def available(self):
            return False
    return NoGemini()


@pytest.fixture
def fixed_today(monkeypatch):
    """Pin 'today' so date logic is deterministic."""
    d = date(2026, 10, 4)
    import jobscan.core, jobscan.run, jobscan.sources.feeds
    for mod in (jobscan.core, jobscan.run, jobscan.sources.feeds):
        monkeypatch.setattr(mod, "today", lambda: d)
    return d

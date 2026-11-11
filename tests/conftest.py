import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def not_the_weekend(monkeypatch):
    """/approve asks first in the weekend quiet hours; tests run on any day, so that check is off unless a test turns it on."""
    from poster import timing
    monkeypatch.setattr(timing, "quiet", lambda cfg, at: False)

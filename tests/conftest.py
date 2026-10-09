import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GEMS_AUTH_ENABLED", "false")

import app as app_module  # noqa: E402


@pytest.fixture(autouse=True)
def reset_state():
    """Clear module-level caches and rate limiter around every test."""
    def clear():
        app_module._search_cache.clear()
        app_module._showcase_cache.update({'data': None, 'ts': 0})
        app_module._rate_limits.clear()
    clear()
    yield
    clear()


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


@pytest.fixture(scope="session")
def sample_nodes():
    with open(ROOT / "tests" / "fixtures" / "sample_nodes.json", encoding="utf-8") as f:
        return json.load(f)


class FakeResponse:
    def __init__(self, status_code=200, nodes=None):
        self.status_code = status_code
        self._nodes = nodes or []

    def json(self):
        return {'data': {'nodes': self._nodes}}

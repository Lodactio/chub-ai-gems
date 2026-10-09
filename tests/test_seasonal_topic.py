import time
from datetime import datetime

from conftest import FakeResponse

import app

NODES = [{'name': 'one', 'fullPath': 'a/one', 'n_favorites': 10, 'nChats': 50,
          'nMessages': 1000, 'starCount': 60, 'topics': ['x']}]


def freeze_month(monkeypatch, month, day=15):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, month, day, 12, 0, 0)
    monkeypatch.setattr(app, 'datetime', FakeDatetime)


def labels(topics):
    return [t['label'] for t in topics]


def test_october_is_halloween_and_december_is_christmas_in_one_process(monkeypatch):
    freeze_month(monkeypatch, 10)
    assert 'Halloween' in labels(app._showcase_topics())
    freeze_month(monkeypatch, 12)
    topics = labels(app._showcase_topics())
    assert 'Christmas' in topics
    assert 'Halloween' not in topics


def test_seasonal_topic_sits_after_isekai_and_static_list_unchanged(monkeypatch):
    static_before = list(app.SHOWCASE_TOPICS)
    freeze_month(monkeypatch, 10)
    topics = labels(app._showcase_topics())
    assert topics[topics.index('Isekai') + 1] == 'Halloween'
    assert len(topics) == len(static_before) + 1
    assert app.SHOWCASE_TOPICS == static_before
    assert 'Halloween' not in labels(app.SHOWCASE_TOPICS)


def test_showcase_labels_change_after_cache_expires_without_reimport(monkeypatch):
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(200, NODES))

    freeze_month(monkeypatch, 10)
    october = app.get_showcase_data()
    assert 'Halloween' in labels(october)
    assert 'Christmas' not in labels(october)

    # Month rolls over but the cache is still fresh: same data is served
    freeze_month(monkeypatch, 12)
    assert labels(app.get_showcase_data()) == labels(october)

    # Once the 24h TTL lapses the refresh picks up the new season
    app._showcase_cache['ts'] = time.time() - app.SHOWCASE_CACHE_TTL - 1
    december = app.get_showcase_data()
    assert 'Christmas' in labels(december)
    assert 'Halloween' not in labels(december)
    assert labels(december).index('Christmas') == labels(october).index('Halloween')

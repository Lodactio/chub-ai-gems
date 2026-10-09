import time

import requests
from conftest import FakeResponse

import app

NODES = [
    {'name': 'one', 'fullPath': 'a/one', 'n_favorites': 10, 'nChats': 50, 'nMessages': 1000,
     'starCount': 60, 'topics': ['x']},
    {'name': 'two', 'fullPath': 'b/two', 'n_favorites': 20, 'nChats': 40, 'nMessages': 800,
     'starCount': 70, 'topics': ['y']},
]
RETRY_DELAY = 300  # seconds, matches the backdating in get_showcase_data


def raiser(exc):
    def fake_get(*args, **kwargs):
        raise exc
    return fake_get


def ok_get(*args, **kwargs):
    return FakeResponse(200, NODES)


def query(client, **params):
    params.setdefault('query', 'q')
    return client.get('/api/query', query_string=params)


# ─── /api/query ───

def test_all_timeouts_return_504_and_cache_nothing(client, monkeypatch):
    monkeypatch.setattr(app.requests, 'get', raiser(requests.exceptions.Timeout()))
    r = query(client)
    assert r.status_code == 504
    assert 'error' in r.get_json()
    assert app._search_cache == {}


def test_all_connection_errors_return_502_and_cache_nothing(client, monkeypatch):
    monkeypatch.setattr(app.requests, 'get', raiser(requests.exceptions.ConnectionError()))
    r = query(client)
    assert r.status_code == 502
    assert app._search_cache == {}


def test_all_429_return_502_and_cache_nothing(client, monkeypatch):
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(429))
    r = query(client)
    assert r.status_code == 502
    assert app._search_cache == {}


def test_failed_fetch_is_retried_on_next_request(client, monkeypatch):
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(429))
    assert query(client).status_code == 502
    monkeypatch.setattr(app.requests, 'get', ok_get)
    r = query(client)
    assert r.status_code == 200
    assert r.get_json()['total'] == 2


def test_partial_failure_returns_results_but_does_not_cache(client, monkeypatch):
    def flaky(url, params=None, **kwargs):
        if params['page'] == '1':
            return FakeResponse(500)
        return FakeResponse(200, NODES)
    monkeypatch.setattr(app.requests, 'get', flaky)
    r = query(client)
    assert r.status_code == 200
    body = r.get_json()
    assert body['total'] == 2
    assert {c['name'] for c in body['results']} == {'one', 'two'}
    assert app._search_cache == {}


def test_full_success_is_cached_and_served_without_refetch(client, monkeypatch):
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return FakeResponse(200, NODES)
    monkeypatch.setattr(app.requests, 'get', counting)
    assert query(client).status_code == 200
    assert len(app._search_cache) == 1
    n = len(calls)
    assert n == len(app.SORT_STRATEGIES) * app.PAGES_PER_SORT
    assert query(client).status_code == 200
    assert len(calls) == n


def test_genuinely_empty_pages_are_cached(client, monkeypatch):
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(200, []))
    r = query(client)
    assert r.status_code == 200 and r.get_json()['total'] == 0
    assert len(app._search_cache) == 1


# ─── showcase ───

def showcase_nodes_get(*args, **kwargs):
    return FakeResponse(200, NODES)


def previous_data():
    return [{'query': '', 'emoji': 'P', 'label': 'Previous', 'cards': []}]


def expire_cache(data=None):
    app._showcase_cache.update({'data': data, 'ts': time.time() - app.SHOWCASE_CACHE_TTL - 10})


def test_showcase_all_failed_keeps_previous_data(monkeypatch):
    prev = previous_data()
    expire_cache(prev)
    monkeypatch.setattr(app.requests, 'get', raiser(requests.exceptions.ConnectionError()))
    assert app.get_showcase_data() == prev
    assert app._showcase_cache['data'] == prev


def test_showcase_failure_retries_in_about_five_minutes(monkeypatch):
    expire_cache(previous_data())
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(503))
    before = time.time()
    app.get_showcase_data()
    age = time.time() - app._showcase_cache['ts']
    remaining = app.SHOWCASE_CACHE_TTL - age
    # still fresh now, but expires in ~5 minutes rather than 24h
    assert 0 < remaining <= RETRY_DELAY + (time.time() - before) + 1
    assert remaining > RETRY_DELAY - 30


def test_showcase_failure_without_previous_data_is_cached_short(monkeypatch):
    monkeypatch.setattr(app.requests, 'get', lambda *a, **k: FakeResponse(503))
    result = app.get_showcase_data()
    assert result and all(t['cards'] == [] for t in result)
    remaining = app.SHOWCASE_CACHE_TTL - (time.time() - app._showcase_cache['ts'])
    assert RETRY_DELAY - 30 < remaining <= RETRY_DELAY + 1


def test_showcase_partial_failure_uses_short_ttl(monkeypatch):
    def flaky(url, params=None, **kwargs):
        if 'isekai' in (params.get('topics') or ''):
            return FakeResponse(500)
        return FakeResponse(200, NODES)
    monkeypatch.setattr(app.requests, 'get', flaky)
    result = app.get_showcase_data()
    assert any(t['cards'] for t in result)
    remaining = app.SHOWCASE_CACHE_TTL - (time.time() - app._showcase_cache['ts'])
    assert RETRY_DELAY - 30 < remaining <= RETRY_DELAY + 1


def test_showcase_success_caches_with_fresh_timestamp(monkeypatch):
    monkeypatch.setattr(app.requests, 'get', showcase_nodes_get)
    before = time.time()
    result = app.get_showcase_data()
    assert app._showcase_cache['data'] == result
    assert before <= app._showcase_cache['ts'] <= time.time()
    assert any(t['cards'] for t in result)


def test_showcase_cache_hit_skips_fetch(monkeypatch):
    monkeypatch.setattr(app.requests, 'get', showcase_nodes_get)
    first = app.get_showcase_data()
    monkeypatch.setattr(app.requests, 'get', raiser(AssertionError('should not fetch')))
    assert app.get_showcase_data() == first

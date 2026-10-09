import math
import statistics

import pytest

import app
from app import (
    C_CONV,
    C_DEPTH,
    DEPTH_CAP_GEM,
    MIN_MEDIAN_CONVERSION,
    MIN_MEDIAN_DEPTH,
    PRIOR_CONV,
    PRIOR_DEPTH,
    SORT_KEYS,
    _sorted_response,
    calculate_gem_scores,
    calculate_smoothed_conversion,
    calculate_smoothed_depth,
)


def make_card(messages, chats, favs, downloads=0):
    return {
        'favorites': favs, 'chats': chats, 'messages': messages, 'downloads': downloads,
        'smoothed_depth': calculate_smoothed_depth(messages, chats),
        'smoothed_conversion': calculate_smoothed_conversion(favs, chats, downloads),
    }


def test_zero_chat_card_equals_priors():
    assert calculate_smoothed_depth(0, 0) == pytest.approx(PRIOR_DEPTH)
    assert calculate_smoothed_conversion(0, 0, 0) == pytest.approx(PRIOR_CONV)


def test_none_inputs_treated_as_zero():
    assert calculate_smoothed_depth(None, None) == pytest.approx(PRIOR_DEPTH)
    assert calculate_smoothed_conversion(None, None, None) == pytest.approx(PRIOR_CONV)


def test_smoothed_depth_known_values():
    # (2010 + 20*12) / (10 + 20) = 75
    assert calculate_smoothed_depth(2010, 10) == pytest.approx(75.0)
    # Huge sample: converges to the raw ratio
    assert calculate_smoothed_depth(10_000_000, 1_000_000) == pytest.approx(10.0, abs=0.3)


def test_smoothed_depth_pulled_toward_prior_for_small_samples():
    # 1 chat of 100 messages: raw depth 100, but smoothing drags it near the prior
    d = calculate_smoothed_depth(100, 1)
    assert d == pytest.approx((100 + C_DEPTH * PRIOR_DEPTH) / (1 + C_DEPTH))
    assert PRIOR_DEPTH < d < 100


def test_smoothed_conversion_uses_max_of_chats_and_downloads():
    assert calculate_smoothed_conversion(40, 50, 50) == pytest.approx((40 + C_CONV * PRIOR_CONV) / (50 + C_CONV))
    # downloads dominate exposure: same result as chats=downloads
    assert calculate_smoothed_conversion(40, 10, 500) == pytest.approx((40 + C_CONV * PRIOR_CONV) / (500 + C_CONV))
    assert calculate_smoothed_conversion(40, 10, 500) < calculate_smoothed_conversion(40, 10, 10)


def test_empty_cards_returned_unchanged():
    assert calculate_gem_scores([]) == []


def depth_cards():
    # smoothed depths: 58, 75, 108 (10 chats each)
    return [make_card(1500, 10, 100), make_card(2010, 10, 100), make_card(3000, 10, 100)]


def test_median_uses_uncapped_depth():
    cards = calculate_gem_scores(depth_cards())
    # All three above the cap: a capped median would be 75, the uncapped one is higher.
    high = calculate_gem_scores([make_card(3000, 10, 10), make_card(4000, 10, 10), make_card(5000, 10, 10)])
    uncapped = [calculate_smoothed_depth(m, 10) for m in (3000, 4000, 5000)]
    assert high[0]['median_depth'] == pytest.approx(statistics.median(uncapped))
    assert high[0]['median_depth'] > DEPTH_CAP_GEM
    assert cards[0]['median_depth'] == pytest.approx(75.0)


def test_engagement_and_gem_score_use_capped_depth_but_norm_depth_does_not():
    cards = calculate_gem_scores(depth_cards())
    below, at, above = cards
    med_d, med_c = below['median_depth'], below['median_conv']

    # norm_depth stays uncapped
    assert above['norm_depth'] == pytest.approx(108.0 / med_d)
    assert above['norm_depth'] > at['norm_depth'] > below['norm_depth']

    # engagement uses min(depth, cap)
    exp_above = DEPTH_CAP_GEM / med_d + above['smoothed_conversion'] / med_c
    assert above['engagement'] == pytest.approx(exp_above)
    # At and above the cap share identical depth contribution and identical conversion inputs
    assert above['engagement'] == pytest.approx(at['engagement'])
    assert above['gem_score'] == pytest.approx(at['gem_score'])
    # Below the cap still scores lower
    assert below['engagement'] < at['engagement']
    assert above['gem_score'] == pytest.approx(above['engagement'] * math.log(100 + 1))


def test_gem_score_scales_with_log_favorites():
    a, b = calculate_gem_scores([make_card(500, 10, 0), make_card(500, 10, 1000)])
    assert a['gem_score'] == 0  # log(1) == 0
    assert b['gem_score'] == pytest.approx(b['engagement'] * math.log(1001))


def test_min_median_floors():
    cards = [{'favorites': 1, 'smoothed_depth': 0.0, 'smoothed_conversion': 0.0}]
    out = calculate_gem_scores(cards)[0]
    assert out['median_depth'] == MIN_MEDIAN_DEPTH
    assert out['median_conv'] == MIN_MEDIAN_CONVERSION


def test_median_floor_prevents_division_by_zero():
    # Median conversion is 0 for this pool; the floor keeps normalisation finite
    cards = [
        {'favorites': 5, 'smoothed_depth': 1.0, 'smoothed_conversion': 0.0},
        {'favorites': 5, 'smoothed_depth': 1.0, 'smoothed_conversion': 0.0},
        {'favorites': 5, 'smoothed_depth': 1.0, 'smoothed_conversion': 0.01},
    ]
    out = calculate_gem_scores(cards)
    assert out[0]['median_conv'] == MIN_MEDIAN_CONVERSION
    assert out[2]['norm_conv'] == pytest.approx(0.01 / MIN_MEDIAN_CONVERSION)


def test_fixture_cards_straddle_the_cap(sample_nodes):
    by_name = {n['name']: n for n in sample_nodes}
    def depth(name):
        n = by_name[name]
        return calculate_smoothed_depth(n['nMessages'], n['nChats'])
    assert depth('synthetic-below-cap') < DEPTH_CAP_GEM
    assert DEPTH_CAP_GEM - 2 < depth('synthetic-near-cap') < DEPTH_CAP_GEM
    assert depth('synthetic-at-cap') == pytest.approx(DEPTH_CAP_GEM)
    assert depth('synthetic-above-cap') > DEPTH_CAP_GEM
    hc = by_name['synthetic-high-conversion']
    conv = calculate_smoothed_conversion(hc['n_favorites'], hc['nChats'], hc['starCount'])
    assert conv > 0.5


def test_fixture_scoring_caps_synthetic_cards(sample_nodes):
    cards = []
    for n in sample_nodes:
        cards.append(make_card(n['nMessages'], n['nChats'], n['n_favorites'], n['starCount']) | {'name': n['name']})
    scored = {c['name']: c for c in calculate_gem_scores(cards)}
    assert scored['synthetic-above-cap']['norm_depth'] > scored['synthetic-at-cap']['norm_depth']
    assert scored['synthetic-above-cap']['gem_score'] == pytest.approx(scored['synthetic-at-cap']['gem_score'])


def test_sort_keys_cover_all_strategies():
    assert set(SORT_KEYS) == {'gem_score', 'depth', 'conversion', 'favorites', 'downloads', 'chats', 'messages'}


def entry():
    a = {'name': 'a', 'gem_score': 1, 'smoothed_depth': 30, 'smoothed_conversion': 0.1,
         'favorites': 5, 'downloads': 300, 'chats': 20, 'messages': 100}
    b = {'name': 'b', 'gem_score': 3, 'smoothed_depth': 10, 'smoothed_conversion': 0.3,
         'favorites': 50, 'downloads': 100, 'chats': 10, 'messages': 900}
    c = {'name': 'c', 'gem_score': 2, 'smoothed_depth': 20, 'smoothed_conversion': 0.2,
         'favorites': 20, 'downloads': 200, 'chats': 30, 'messages': 500}
    return {'processed': [a, b, c], 'total': 3, 'pool_size_raw': 9, 'pool_size_unique': 3}


@pytest.mark.parametrize('key,expected', [
    ('gem_score', 'bca'), ('depth', 'acb'), ('conversion', 'bca'), ('favorites', 'bca'),
    ('downloads', 'acb'), ('chats', 'cab'), ('messages', 'bca'),
])
def test_sorted_response_orders_descending(key, expected):
    out = _sorted_response(entry(), key)
    assert ''.join(r['name'] for r in out['results']) == expected
    assert out['total'] == 3 and out['pool_size_raw'] == 9 and out['pool_size_unique'] == 3


def test_sorted_response_unknown_key_falls_back_to_gem_score():
    out = _sorted_response(entry(), 'nonsense')
    assert [r['name'] for r in out['results']] == ['b', 'c', 'a']


def test_depth_cap_constant_is_imported_from_app():
    assert app.DEPTH_CAP_GEM == DEPTH_CAP_GEM == 75.0

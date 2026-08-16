# chub_search_tool.py

import os
import hmac
import math
import time
import logging
import statistics
import threading
import requests
from xml.sax.saxutils import escape as xml_escape
from functools import wraps
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template_string, request, jsonify, Response
from datetime import datetime, timezone

def get_seasonal_topic():
    """Return a showcase topic based on the current date."""
    today = datetime.now()
    m, d = today.month, today.day

    # Check date ranges (month, day_start, day_end)
    seasons = [
        ((10, 1),  (10, 31), {'query': 'Horror halloween',       'emoji': '🎃', 'label': 'Halloween',    'min_favs': 0, 'tags': 'halloween,horror,spooky,monster'}),
        ((12, 1),  (12, 31), {'query': 'Christmas winter',       'emoji': '🎄', 'label': 'Christmas',    'min_favs': 0, 'tags': 'christmas,winter,holiday,snow'}),
        ((1, 1),   (1, 7),   {'query': 'New Year party',         'emoji': '🎆', 'label': 'New Year',     'min_favs': 0, 'tags': 'new year,party,celebration'}),
        ((2, 7),   (2, 21),  {'query': 'Romance love valentine', 'emoji': '💘', 'label': "Valentine's",  'min_favs': 0, 'tags': 'valentine,romance,love,dating'}),
        ((3, 14),  (3, 20),  {'query': 'drinking lucky irish',   'emoji': '☘️', 'label': "St Patrick's", 'min_favs': 0, 'tags': 'irish,lucky,drinking'}),
        ((3, 30),  (4, 2),   {'query': 'Trickster prank',        'emoji': '🃏', 'label': 'April Fools',  'min_favs': 0, 'tags': 'trickster,prank,jester'}),
        ((3, 28),  (4, 15),  {'query': 'rabbit',                 'emoji': '🐣', 'label': 'Easter',       'min_favs': 0, 'tags': 'easter,rabbit,bunny,spring'}),
        ((5, 15),  (8, 31),  {'query': '',                 'emoji': '🏖️', 'label': 'Summer',       'min_favs': 0, 'tags': 'summer,vacation,camping,island,beach', 'exclude_tags': ['hyena', 'futanari', 'femboy']}),
        ((11, 20), (11, 30), {'query': 'thanksgiving',           'emoji': '🦃', 'label': 'Thanksgiving', 'min_favs': 0, 'tags': 'thanksgiving,harvest,feast'}),
    ]

    for (m1, d1), (m2, d2), topic in seasons:
        if (m == m1 and d >= d1) or (m == m2 and d <= d2) or (m1 < m < m2):
            return topic

    # Default fallback
    return {'query': 'Goth', 'emoji': '🧛', 'label': 'Goth', 'min_favs': 300, 'tags': 'goth,gothic,vampire,dark'}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ─── Basic Auth (toggleable) ───
# Enable by setting GEMS_AUTH_ENABLED=true and a username/password:
#   GEMS_AUTH_ENABLED=true GEMS_AUTH_USERNAME=me GEMS_AUTH_PASSWORD=secret python chub_search_tool.py
AUTH_ENABLED = os.environ.get('GEMS_AUTH_ENABLED', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
AUTH_USERNAME = os.environ.get('GEMS_AUTH_USERNAME', 'admin')
AUTH_PASSWORD = os.environ.get('GEMS_AUTH_PASSWORD', '')

if AUTH_ENABLED and not AUTH_PASSWORD:
    logging.warning("GEMS_AUTH_ENABLED is set but GEMS_AUTH_PASSWORD is empty — all requests will be rejected.")

@app.before_request
def require_basic_auth():
    if not AUTH_ENABLED:
        return None
    auth = request.authorization
    if (auth and auth.type == 'basic' and AUTH_PASSWORD
            and hmac.compare_digest(auth.username or '', AUTH_USERNAME)
            and hmac.compare_digest(auth.password or '', AUTH_PASSWORD)):
        return None
    return Response('Authentication required.', 401,
                    {'WWW-Authenticate': 'Basic realm="Chub AI Gems"'})

# ─── Rate Limiter ───
_rate_limits = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60
_rate_lock = threading.Lock()

def rate_limit(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr
        now = time.time()
        with _rate_lock:
            _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < RATE_LIMIT_WINDOW]
            if len(_rate_limits[ip]) >= RATE_LIMIT_MAX:
                limited = True
            else:
                _rate_limits[ip].append(now)
                limited = False
        if limited:
            return jsonify({'error': 'Rate limited. Try again shortly.'}), 429
        return f(*args, **kwargs)
    return decorated

# ─── Security Headers ───
@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

C_DEPTH = 20.0
PRIOR_DEPTH = 12.0
C_CONV = 20.0
PRIOR_CONV = 0.05

SORT_STRATEGIES = [
    'chat_count',
    'download_count',
    'default',
    'fav_count',
    'trending',
    'created_at',
]
PAGES_PER_SORT = 3
API_PER_PAGE = 200

SHOWCASE_TOPICS = [
    {'query': 'RPG',                'emoji': '🎲', 'label': 'RPG',              'min_favs': 0,  'tags': 'rpg'},
    {'query': '', 'emoji': '🎩', 'label': 'Gentlemen', 'min_favs': 0, 'tags': 'fempov,male,human', 'exclusive': True, 'exclude_tags': ['anypov', 'feet', 'scat', 'diaper', 'vore', 'furry', 'genderswap', 'malepov', 'feminization', 'bbc', 'pokemon', 'femdom', 'ntr', 'cuckold', 'femboy', 'horny', 'cum toilet', 'goblin', 'cumdump', 'female monster']},
    {'query': 'Fantasy',            'emoji': '⚔️', 'label': 'Fantasy',          'min_favs': 50,  'tags': 'fantasy,medieval,magic,elves'},
    {'query': '',            'emoji': '⚔️', 'label': 'Dark Fantasy',          'min_favs': 0,  'tags': 'dark fantasy,slave'},
    {'query': 'Romance',            'emoji': '💕', 'label': 'Romance',          'min_favs': 30,  'tags': 'romance,love,dating,relationship,slowburn'},
    {'query': 'sci-fi',    'emoji': '🚀', 'label': 'Science Fiction',  'min_favs': 0,   'tags': 'sci-fi,science fiction,cyberpunk,space'},
    {'query': '',             'emoji': '⚔️', 'label': 'Isekai',           'min_favs': 100, 'tags': 'isekai,reincarnation'},
    get_seasonal_topic(),
    {'query': '',              'emoji': '🌸', 'label': 'Anime',            'min_favs': 0,   'tags': 'anime,manga,waifu,anime game characters,webtoon,kemonomimi,mech pilot'},
    {'query': 'Roleplay',           'emoji': '🎭', 'label': 'Roleplay',         'min_favs': 0,   'tags': 'roleplay,rp'},
    {'query': '',  'emoji': '🧟', 'label': 'Apocalypse',       'min_favs': 3,   'tags': 'apocalypse,Post-apocalypse,zombies,zombie Apocalypse', 'exclude_tags': ['futanari', 'gentle femdom']},
    {'query': 'Wholesome',          'emoji': '💛', 'label': 'Wholesome',        'min_favs': 0,   'tags': 'wholesome,cute,comfort,slice of life,can be wholesome,can be sexy'},
    {'query': '',     'emoji': '☯', 'label': 'The Dao',        'min_favs': 0,   'tags': 'wuxia,xianxia,cultivation,dual cultivation,murim,ancient china,china'},
]
SHOWCASE_CARDS_PER_TOPIC = 10
SHOWCASE_CACHE_TTL = 86400  # 24 hours

# Simple in-memory cache for showcase data
_showcase_cache = {'data': None, 'ts': 0}
_showcase_lock = threading.Lock()
_search_cache = {}  # key: frozen params (sort-independent) → {'processed', 'total', 'pool_size_raw', 'pool_size_unique', 'ts'}
_search_lock = threading.Lock()
SEARCH_CACHE_TTL = 3600  # 60 minutes


def calculate_smoothed_depth(n_messages, n_chats):
    n_messages = float(n_messages or 0)
    n_chats = float(n_chats or 0)
    return (n_messages + C_DEPTH * PRIOR_DEPTH) / (n_chats + C_DEPTH)


def calculate_smoothed_conversion(favorites, n_chats, downloads):
    """Conversion = favorites / exposure, where exposure = max(chats, downloads).

    A card downloaded far more than it is chatted with should not look like a runaway hit.
    """
    favorites = float(favorites or 0)
    n_chats = float(n_chats or 0)
    downloads = float(downloads or 0)
    denominator = max(n_chats, downloads)
    return (favorites + C_CONV * PRIOR_CONV) / (denominator + C_CONV)


def calculate_gem_scores(cards):
    if not cards:
        return cards
    depths = [c['smoothed_depth'] for c in cards]
    convs = [c['smoothed_conversion'] for c in cards]
    median_depth = max(statistics.median(depths), 0.001) if depths else 1.0
    median_conv = max(statistics.median(convs), 0.0001) if convs else 1.0
    for c in cards:
        norm_depth = c['smoothed_depth'] / median_depth
        norm_conv = c['smoothed_conversion'] / median_conv
        engagement = norm_depth + norm_conv
        c['gem_score'] = engagement * math.log(c['favorites'] + 1)
        c['norm_depth'] = norm_depth
        c['norm_conv'] = norm_conv
        c['engagement'] = engagement
        c['median_depth'] = median_depth
        c['median_conv'] = median_conv
    return cards


def fetch_chub_page(query, api_page, sort_by, nsfw, headers, topics='', inclusive_or=True,
                    min_days_ago=None, max_days_ago=None):
    url = "https://api.chub.ai/search"
    params = {
        'search': query,
        'first': API_PER_PAGE,
        'page': str(api_page),
        'sort': sort_by,
        'venus': 'false',
        'asc': 'true' if sort_by == 'created_at' else 'false',
        'nsfw': 'true' if nsfw else 'false'
    }
    if topics.strip():
        params['topics'] = topics.strip()
        params['inclusive_or'] = 'true' if inclusive_or else 'false'
    # Chub API filters on card creation date: min/max days since creation
    if min_days_ago is not None:
        params['min_days_ago'] = str(min_days_ago)
    if max_days_ago is not None:
        params['max_days_ago'] = str(max_days_ago)
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            app.logger.warning(f"Chub fetch non-200 (sort={sort_by} page={api_page}): {r.status_code}")
            return []
        data = r.json()
        return data.get('data', {}).get('nodes', [])
    except Exception as e:
        app.logger.warning(f"Chub fetch failed (sort={sort_by} page={api_page}): {e}")
        return []


def fetch_showcase_topic(topic, headers):
    """Fetch a small set of cards for a showcase topic, score them, return top N."""
    url = "https://api.chub.ai/search"
    params = {
        'search': topic.get('query', ''),
        'first': 60,
        'page': '1',
        'sort': 'download_count',
        'venus': 'false',
        'asc': 'false',
        'nsfw': 'true',
        'include_forks': 'false'
    }
    # Use tags if provided for tighter showcase results
    if topic.get('tags'):
        params['topics'] = topic['tags']
        # Use exclusive (AND) mode if specified, otherwise default to OR
        if topic.get('exclusive'):
            params['inclusive_or'] = 'false'
        else:
            params['inclusive_or'] = 'true'
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            app.logger.warning(f"Showcase fetch non-200 (topic={topic.get('label')}): {r.status_code}")
            return []
        data = r.json()
        nodes = data.get('data', {}).get('nodes', [])

        cards = []
        for node in nodes:
            favs = int(node.get('n_favorites', 0) or 0)
            chats = int(node.get('nChats', 0) or 0)
            messages = int(node.get('nMessages', 0) or 0)
            downloads = int(node.get('starCount', 0) or 0)

            if chats < 5 or messages < 20:
                continue

            fp = node.get('fullPath', '')
            author = fp.split('/')[0] if '/' in fp else fp

            cards.append({
                'name': node.get('name', 'Untitled'),
                'author': author,
                'author_path': fp,
                'avatar_url': node.get('avatar_url', ''),
                'downloads': downloads,
                'favorites': favs,
                'chats': chats,
                'messages': messages,
                'topics': node.get('topics', []),  # needed for exclusion
                'smoothed_depth': calculate_smoothed_depth(messages, chats),
                'smoothed_conversion': calculate_smoothed_conversion(favs, chats, downloads),
            })

        # Exclude unwanted tags
        exclude = topic.get('exclude_tags', [])
        if exclude:
            exclude_lower = {t.lower().strip() for t in exclude}
            before = len(cards)
            cards = [
                c for c in cards
                if not any(
                    t.lower().strip() in exclude_lower
                    for t in c.get('topics', [])
                )
            ]
            app.logger.info(
                f"Showcase [{topic.get('label')}]: excluded {before - len(cards)} cards"
            )

        cards = calculate_gem_scores(cards)
        cards.sort(key=lambda x: x.get('gem_score', 0), reverse=True)
        return cards[:SHOWCASE_CARDS_PER_TOPIC]
    except Exception as e:
        app.logger.warning(f"Showcase topic '{topic.get('label')}' failed: {e}")
        return []


def get_showcase_data():
    """Return showcase data, using cache if fresh."""
    global _showcase_cache
    now = time.time()
    if _showcase_cache['data'] and (now - _showcase_cache['ts']) < SHOWCASE_CACHE_TTL:
        return _showcase_cache['data']

    with _showcase_lock:
        now = time.time()
        if _showcase_cache['data'] and (now - _showcase_cache['ts']) < SHOWCASE_CACHE_TTL:
            return _showcase_cache['data']

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/119.0.0.0 Safari/537.36'
        }

        result = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
            executor.submit(fetch_showcase_topic, t, headers): t
            for t in SHOWCASE_TOPICS
        }
            for future in as_completed(futures):
                topic = futures[future]
                cards = future.result()
                result.append({
                    'query': topic['query'],
                    'emoji': topic['emoji'],
                    'label': topic['label'],
                    'min_favs': topic.get('min_favs', 0),
                    'tags': topic.get('tags', ''),
                    'exclusive': topic.get('exclusive', False),  # ← add this
                    'exclude_tags': topic.get('exclude_tags', []),
                    'cards': cards
                })

        # Preserve the original topic order
        order = {t['label']: i for i, t in enumerate(SHOWCASE_TOPICS)}
        result.sort(key=lambda x: order.get(x['label'], 99))

        _showcase_cache = {'data': result, 'ts': now}
        return result


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chub AI Gems Search</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💎</text></svg>">
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * { box-sizing: border-box; }
        body { background-color: #0f111a; color: #e2e8f0; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; }
        .text-gradient { background: linear-gradient(135deg, #a5b4fc, #6366f1); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }

        /* ─── Showcase Banner ─── */
        .showcase-wrap {
            position: relative;
            background: linear-gradient(180deg, #131627 0%, #0f111a 100%);
            border: 1px solid #1e2235;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 16px;
            padding: 0;
        }
        .showcase-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 16px 0;
        }
        .showcase-title {
            font-size: 16px;
            font-weight: 700;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .showcase-title-emoji {
            font-size: 20px;
        }
        .showcase-title-label {
            cursor: pointer;
            transition: color 0.2s;
        }
        .showcase-title-label:hover {
            color: #a5b4fc;
        }
        .showcase-nav {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .showcase-nav-btn {
            background: rgba(255,255,255,0.06);
            border: 1px solid #252a3a;
            color: #94a3b8;
            width: 28px; height: 28px;
            border-radius: 6px;
            display: flex; align-items: center; justify-content: center;
            cursor: pointer;
            transition: all 0.15s;
            font-size: 12px;
        }
        .showcase-nav-btn:hover {
            background: rgba(99, 102, 241, 0.2);
            border-color: #6366f1;
            color: #e2e8f0;
        }
        .showcase-counter {
            font-size: 11px;
            color: #4b5563;
            min-width: 40px;
            text-align: center;
        }

        /* Carousel track */
        .showcase-viewport {
            overflow: hidden;
            position: relative;
        }
        .showcase-track {
            display: flex;
            transition: transform 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }
        .showcase-slide {
            min-width: 100%;
            padding: 10px 16px 14px;
            display: flex;
            gap: 10px;
            overflow-x: auto;
            scrollbar-width: none;
        }
        .showcase-slide::-webkit-scrollbar { display: none; }

        /* Individual showcase card thumbnail */
        .sc-thumb {
            flex-shrink: 0;
            width: 187px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .sc-thumb:hover {
            transform: translateY(-3px);
        }
        .sc-thumb-img {
            width: 187px;
            height: 230px;
            border-radius: 8px;
            overflow: hidden;
            background: #0d0f1a;
            border: 1px solid #252a3a;
            transition: border-color 0.2s;
        }
        .sc-thumb:hover .sc-thumb-img {
            border-color: #6366f1;
        }
        .sc-thumb-img img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .sc-thumb-name {
            font-size: 10px;
            font-weight: 600;
            color: #c7d2fe;
            margin-top: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .sc-thumb-author {
            font-size: 9px;
            color: #4b5563;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .sc-thumb-gem {
            font-size: 9px;
            color: #34d399;
            font-weight: 600;
            margin-top: 1px;
        }

        /* Dots */
        .showcase-dots {
            display: flex;
            justify-content: center;
            gap: 6px;
            padding: 0 16px 12px;
        }
        .showcase-dot {
            width: 7px; height: 7px;
            border-radius: 50%;
            background: #252a3a;
            cursor: pointer;
            transition: all 0.2s;
        }
        .showcase-dot.active {
            background: #6366f1;
            transform: scale(1.3);
        }
        .showcase-dot:hover {
            background: #4f46e5;
        }

        /* ─── Card Grid ─── */
        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr));
            gap: 10px;
        }

        /* ─── Horizontal Card ─── */
        .h-card {
            display: flex;
            background: #161926;
            border: 2px solid #1e2235;
            border-radius: 10px;
            overflow: hidden;
            cursor: pointer;
            transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
            height: 225px;
        }
        .h-card:hover {
            border-color: #6366f1;
            box-shadow: 0 0 16px rgba(99, 102, 241, 0.12);
            transform: translateY(-2px);
        }
        .h-card-img {
            width: 180px; min-width: 130px; height: 100%;
            position: relative; overflow: hidden; background: #0d0f1a; flex-shrink: 0;
        }
        .h-card-img img {
            width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s;
        }
        .h-card:hover .h-card-img img { transform: scale(1.06); }
        .h-card-rank {
            position: absolute; top: 5px; left: 5px;
            background: rgba(99, 102, 241, 0.88);
            color: #fff; font-size: 9px; font-weight: 700;
            padding: 1px 6px; border-radius: 5px; z-index: 2;
        }
        .h-card-signal {
            position: absolute; bottom: 5px; left: 5px;
            background: rgba(15, 17, 26, 0.82);
            color: #e2e8f0; font-size: 8px; font-weight: 600;
            padding: 2px 5px; border-radius: 4px; z-index: 2;
        }
        .h-card-body {
            flex: 1; display: flex; flex-direction: column;
            padding: 10px 12px; min-width: 0; overflow: hidden;
        }
        .h-card-header {
            display: flex; justify-content: space-between;
            align-items: flex-start; gap: 8px; margin-bottom: 2px;
        }
        .h-card-name {
            font-size: 14px; font-weight: 700; color: #e2e8f0;
            white-space: break-spaces; white-space-collapse: break-spaces; text-wrap-mode: wrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3;
        }
        .h-card-gem {
            flex-shrink: 0; background: rgba(16, 185, 129, 0.18);
            color: #34d399; font-size: 10px; font-weight: 700;
            padding: 2px 7px; border-radius: 5px;
            display: flex; align-items: center; gap: 3px; white-space: nowrap;
        }
        .h-card-author {
            font-size: 11px; color: #4b5563; margin-bottom: 4px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .h-card-desc {
            font-size: 12px; color: #7a839b; line-height: 1.45;
            overflow: hidden; display: -webkit-box;
            -webkit-line-clamp: 2; -webkit-box-orient: vertical;
            flex-shrink: 1; margin-bottom: auto;
        }
        .h-card-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px; }
        .h-card-tag {
            font-size: 9px; color: #64748b; background: #0f111a;
            padding: 1px 6px; border-radius: 4px; border: 1px solid #1e2235; white-space: nowrap;
        }
        .h-card-stats {
            display: flex; align-items: center; gap: 2px;
            margin-top: 6px; padding-top: 6px;
            border-top: 1px solid #1e2235; flex-wrap: wrap;
        }
        .h-stat { font-size: 10px; color: #4b5563; display: flex; align-items: center; gap: 3px; margin-right: 6px; }
        .h-stat i { font-size: 9px; }
        .h-stat strong { color: #94a3b8; font-weight: 600; }

        .h-bar-wrap { display: flex; align-items: center; gap: 3px; margin-left: auto; }
        .h-bar-label { font-size: 8px; font-weight: 700; width: 10px; text-align: center; }
        .h-micro-track { width: 36px; height: 3px; background: #0f111a; border-radius: 2px; overflow: hidden; }
        .h-micro-fill-d { height: 100%; border-radius: 2px; background: linear-gradient(90deg, #6366f1, #a5b4fc); }
        .h-micro-fill-c { height: 100%; border-radius: 2px; background: linear-gradient(90deg, #ec4899, #fb7185); }
        .h-bar-val { font-size: 8px; color: #64748b; min-width: 20px; }

        .h-card-sources { display: flex; gap: 2px; margin-left: 4px; }
        .h-card-sources span { font-size: 8px; line-height: 1; }

        #scroll-sentinel { height: 1px; width: 100%; }
        .load-ind { text-align: center; padding: 24px; color: #64748b; font-size: 13px; }
        .load-ind.done { color: #4b5563; }

        @keyframes cardIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .h-card-enter { animation: cardIn 0.25s ease forwards; }

        .pool-progress { height: 4px; background: #1e2235; border-radius: 2px; overflow: hidden; margin-top: 8px; }
        .pool-progress-bar { height: 100%; background: linear-gradient(90deg, #6366f1, #a5b4fc); border-radius: 2px; transition: width 0.3s; }

                /* ─── Tag Cloud Background ─── */
        .tag-bg {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            z-index: 0;
            pointer-events: none;
            overflow: hidden;
        }
        .tag-bg span {
            position: absolute;
            color: #6366f1;
            font-weight: 700;
            white-space: nowrap;
            pointer-events: auto;
            cursor: pointer;
            transition: opacity 0.3s, color 0.3s;
            user-select: none;
        }
        .tag-bg span:hover,
        .tag-bg span:focus-visible {
            opacity: 0.5 !important;
            color: #a5b4fc;
        }
        /* Visible keyboard-focus ring for all click-activated controls */
        [role="button"]:focus-visible {
            outline: 2px solid #818cf8;
            outline-offset: 2px;
            border-radius: 4px;
        }

        /* ─── Shiny Card Tiers ─── */
        /* Tier 1: Gem 50+ — Gold foil */
        .h-card.shiny-gold {
            border-color: rgba(251, 191, 36, 0.4);
            background: linear-gradient(135deg, #1a1714 0%, #1f1a10 50%, #1a1714 100%);
        }
        .h-card.shiny-gold:hover {
            border-color: rgba(251, 191, 36, 0.7);
            box-shadow: 0 0 200px rgba(251, 191, 36, 0.7), 0 0 270px rgba(251, 191, 36, 0.2);
        }
        .h-card.shiny-gold .h-card-body {
            background: linear-gradient(135deg,
                rgba(251, 191, 36, 0.01) 0%,
                rgba(251, 191, 36, 0.08) 25%,
                rgba(251, 191, 36, 0.01) 50%,
                rgba(251, 191, 36, 0.08) 75%,
                rgba(251, 191, 36, 0.01) 100%);
            background-size: 200% 200%;
            animation: foilShimmer 6s ease infinite;
        }
        .h-card.shiny-gold .h-card-name { color: #fbbf24; }
        .h-card.shiny-gold .h-card-gem { background: rgba(251, 191, 36, 0.2); color: #fbbf24; }
        .h-card.shiny-gold .h-card-rank { background: rgba(251, 191, 36, 0.9); }

        /* Tier 2: Depth 100+ — Blue holographic */
        .h-card.shiny-depth {
            border-color: rgba(99, 102, 241, 0.4);
            background: linear-gradient(135deg, #111428 0%, #161938 50%, #111428 100%);
        }
        .h-card.shiny-depth:hover {
            border-color: rgba(99, 102, 241, 0.7);
            box-shadow: 0 0 200px rgba(99, 102, 241, 0.2), 0 0 270px rgba(99, 102, 241, 0.08);
        }
        .h-card.shiny-depth .h-card-body {
            background: linear-gradient(135deg,
                rgba(99, 102, 241, 0.01) 0%,
                rgba(165, 180, 252, 0.08) 25%,
                rgba(99, 102, 241, 0.01) 50%,
                rgba(165, 180, 252, 0.08) 75%,
                rgba(99, 102, 241, 0.01) 100%);
            background-size: 200% 200%;
            animation: foilShimmer 6s ease infinite;
        }
        .h-card.shiny-depth .h-card-name { color: #a5b4fc; }
        .h-card.shiny-depth .h-card-signal { background: rgba(99, 102, 241, 0.6); }

        /* Tier 3: Conversion 100%+ — Pink prismatic */
        .h-card.shiny-conv {
            border-color: rgba(236, 72, 153, 0.4);
            background: linear-gradient(135deg, #1a1118 0%, #201220 50%, #1a1118 100%);
        }
        .h-card.shiny-conv:hover {
            border-color: rgba(236, 72, 153, 0.7);
            box-shadow: 0 0 200px rgba(236, 72, 153, 0.2), 0 0 270px rgba(236, 72, 153, 0.08);
        }
        .h-card.shiny-conv .h-card-body {
            background: linear-gradient(135deg,
                rgba(236, 72, 153, 0.01) 0%,
                rgba(251, 113, 133, 0.08) 25%,
                rgba(236, 72, 153, 0.01) 50%,
                rgba(251, 113, 133, 0.08) 75%,
                rgba(236, 72, 153, 0.01) 100%);
            background-size: 200% 200%;
            animation: foilShimmer 6s ease infinite;
        }
        .h-card.shiny-conv .h-card-name { color: #f9a8d4; }
        .h-card.shiny-conv .h-card-signal { background: rgba(236, 72, 153, 0.6); }

        /* Multi-shiny: card qualifies for 2+ tiers — rainbow foil */
        .h-card.shiny-multi {
            border-color: rgba(251, 191, 36, 0.5);
            background: linear-gradient(135deg, #1a1714 0%, #161938 33%, #201220 66%, #1a1714 100%);
        }
        .h-card.shiny-multi:hover {
            border-color: rgba(251, 191, 36, 0.8);
            box-shadow:
                0 0 24px rgba(251, 191, 36, 0.15),
                0 0 24px rgba(99, 102, 241, 0.15),
                0 0 24px rgba(236, 72, 153, 0.15),
                0 0 60px rgba(251, 191, 36, 0.06);
        }
        .h-card.shiny-multi .h-card-body {
            background: linear-gradient(135deg,
                rgba(251, 191, 36, 0.1) 0%,
                rgba(99, 102, 241, 0.1) 25%,
                rgba(236, 72, 153, 0.1) 50%,
                rgba(99, 102, 241, 0.1) 75%,
                rgba(251, 191, 36, 0.1) 100%);
            background-size: 300% 300%;
            animation: foilShimmer 4s ease infinite;
        }
        .h-card.shiny-multi .h-card-name {
            background: linear-gradient(90deg, #fbbf24, #a5b4fc, #f9a8d4, #fbbf24);
            background-size: 200% 100%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: nameShimmer 3s linear infinite;
        }
        .h-card.shiny-multi .h-card-rank {
            background: linear-gradient(135deg, rgba(251,191,36,0.9), rgba(236,72,153,0.9));
        }
        .h-card.shiny-multi .h-card-gem {
            background: linear-gradient(135deg, rgba(251,191,36,0.2), rgba(236,72,153,0.2));
            color: #fbbf24;
        }

        /* Shiny star indicator */
        .shiny-star {
            position: absolute;
            top: 5px;
            right: 5px;
            font-size: 14px;
            z-index: 3;
            filter: drop-shadow(0 0 4px rgba(251, 191, 36, 0.6));
            animation: starPulse 2s ease infinite;
        }

        @keyframes foilShimmer {
            0% { background-position: 0% 0%; }
            50% { background-position: 100% 100%; }
            100% { background-position: 0% 0%; }
        }
        @keyframes nameShimmer {
            0% { background-position: 0% 50%; }
            100% { background-position: 200% 50%; }
        }
        @keyframes starPulse {
            0%, 100% { opacity: 0.7; transform: scale(1); }
            50% { opacity: 1; transform: scale(1.15); }
        }

        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: #0f111a; }
        ::-webkit-scrollbar-thumb { background: #252a3a; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #6366f1; }
    </style>
</head>
<body class="min-h-screen py-6 px-4 sm:px-6 lg:px-8">
    <div class="tag-bg" id="tag-bg"></div>
    <div class="max-w-[1200px] mx-auto" style="position:relative;z-index:1;">
        <header class="text-center mb-5">
            <h1 class="text-2xl font-extrabold tracking-tight mb-1" style="cursor:pointer" role="button" tabindex="0" aria-label="Chub AI Gems — reset search to defaults" onclick="resetSearch()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();resetSearch();}">
                <span class="text-gradient"><i class="fa-solid fa-gem mr-1"></i>Chub AI Gems</span>
            </h1>
            <p class="text-gray-500 text-xs max-w-lg mx-auto">
                7 discovery pools · Dual-signal scoring · <strong class="text-gray-400">Depth × Conversion × Favorites</strong>
            </p>
        </header>

        <!-- Search bar -->
        <div class="bg-gray-900/60 border border-gray-800 rounded-xl p-3 mb-5 backdrop-blur-md shadow-xl">
            <form id="search-form" class="flex flex-wrap items-end gap-2.5">
                <div class="flex-1 min-w-[180px]">
                    <label for="query-input" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Search</label>
                    <div class="relative">
                        <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none text-gray-500">
                            <i class="fa-solid fa-magnifying-glass text-xs"></i>
                        </span>
                        <input type="text" id="query-input" placeholder="Blank = global top..."
                            class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 pl-8 pr-3 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                </div>
                <div class="flex-1 min-w-[160px]">
                    <label for="topics-input" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Tags <span style="color:#4b5563;font-weight:400">(comma separated)</span></label>
                    <div class="relative">
                        <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none text-gray-500">
                            <i class="fa-solid fa-tags text-xs"></i>
                        </span>
                        <input type="text" id="topics-input" placeholder="elf, fantasy, romance..."
                            class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 pl-8 pr-3 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500">
                    </div>
                </div>
                <div class="flex-1 min-w-[140px]">
                    <label for="exclude-input" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">
                        Exclude <span style="color:#4b5563;font-weight:400">(comma sep)</span>
                    </label>
                    <div class="relative">
                       <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none text-gray-500">
                            <i class="fa-solid fa-ban text-xs" style="color:#f43f5e"></i>
                        </span>
                        <input type="text" id="exclude-input" placeholder="anypov, bbc, pokemon..."
                            class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 pl-8 pr-3 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-red-500 border-red-900/30">
                    </div>
                </div>
                <div class="w-40">
                    <label for="sort-select" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Sort</label>
                    <select id="sort-select" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-indigo-500">
                        <option value="gem_score" selected>💎 Gem Score</option>
                        <option value="depth">🔵 Depth</option>
                        <option value="conversion">🩷 Conversion</option>
                        <option value="favorites">❤️ Favorites</option>
                        <option value="downloads">⬇️ Downloads</option>
                        <option value="chats">💬 Chats</option>
                        <option value="messages">📨 Messages</option>
                    </select>
                </div>
                <div class="w-[70px]">
                    <label for="min-favs" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Min Fav</label>
                    <input type="number" id="min-favs" value="1410" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-indigo-500">
                </div>
                <div class="w-[70px]">
                    <label for="min-chats" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Min Chat</label>
                    <input type="number" id="min-chats" value="10" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-indigo-500">
                </div>
                <div class="w-[70px]">
                    <label for="min-msgs" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Min Msg</label>
                    <input type="number" id="min-msgs" value="50" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-indigo-500">
                </div>
                <div class="w-[80px]" title="Only cards at least this many days old">
                    <label for="min-days" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Min Days</label>
                    <input type="number" id="min-days" min="0" placeholder="any" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500">
                </div>
                <div class="w-[80px]" title="Only cards at most this many days old">
                    <label for="max-days" class="block text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Max Days</label>
                    <input type="number" id="max-days" min="0" placeholder="any" class="w-full bg-gray-950/80 border border-gray-800 rounded-lg py-1.5 px-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500">
                </div>
                <div class="flex items-center gap-2.5">
                    <label class="flex items-center gap-1 text-xs text-gray-400 cursor-pointer">
                        <input type="checkbox" id="nsfw-checkbox" checked class="w-3.5 h-3.5 rounded border-gray-700 bg-gray-950 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0">
                        NSFW
                    </label>
                    <label class="flex items-center gap-1 text-xs text-gray-400 cursor-pointer">
                        <input type="checkbox" id="tag-or-checkbox" checked class="w-3.5 h-3.5 rounded border-gray-700 bg-gray-950 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0">
                        OR
                    </label>
                    <button type="submit" class="bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold py-1.5 px-4 rounded-lg transition inline-flex items-center gap-1.5 shadow-lg">
                        <i class="fa-solid fa-magnifying-glass text-xs"></i> Go
                    </button>
                </div>
            </form>
        </div>

        <!-- Showcase Banner -->
        <div class="showcase-wrap" id="showcase-wrap">
            <div class="showcase-header">
                <div class="showcase-title">
                    <span class="showcase-title-emoji" id="sc-emoji">🎲</span>
                    <span class="showcase-title-label" id="sc-label" role="button" tabindex="0" onclick="searchShowcaseTopic()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();searchShowcaseTopic();}">RPG</span>
                    <span style="font-size:11px;color:#4b5563;font-weight:400;margin-left:4px">— click to search</span>
                </div>
                <div class="showcase-nav">
                    <button type="button" class="showcase-nav-btn" id="sc-prev" aria-label="Previous category" onclick="scNav(-1)"><i class="fa-solid fa-chevron-left"></i></button>
                    <span class="showcase-counter" id="sc-counter">1 / 10</span>
                    <button type="button" class="showcase-nav-btn" id="sc-next" aria-label="Next category" onclick="scNav(1)"><i class="fa-solid fa-chevron-right"></i></button>
                </div>
            </div>
            <div class="showcase-viewport">
                <div class="showcase-track" id="sc-track"></div>
            </div>
            <div class="showcase-dots" id="sc-dots"></div>
        </div>

        <!-- Loading -->
        <div id="status-bar" class="hidden bg-indigo-950/40 border border-indigo-900 rounded-lg p-3 mb-4 text-center text-indigo-200 text-sm">
            <i class="fa-solid fa-spinner fa-spin mr-1"></i>
            <span id="status-text">Pulling from 7 pools...</span>
            <div class="pool-progress mt-2"><div class="pool-progress-bar" id="progress-bar" style="width:0%"></div></div>
        </div>

        <!-- Stats -->
        <div id="batch-stats" class="hidden bg-gray-900/40 border border-gray-800 rounded-lg px-4 py-2 mb-4">
            <div class="flex flex-wrap justify-center gap-x-5 gap-y-1 text-[11px] text-gray-500">
                <span>Raw: <strong class="text-gray-300" id="stat-pool">—</strong></span>
                <span>Unique: <strong class="text-gray-300" id="stat-unique">—</strong></span>
                <span>Filtered: <strong class="text-gray-300" id="stat-filtered">—</strong></span>
                <span>Med Depth: <strong class="text-gray-300" id="stat-med-depth">—</strong></span>
                <span>Med Conv: <strong class="text-gray-300" id="stat-med-conv">—</strong></span>
                <span>Top: <strong class="text-gray-300" id="stat-top-gem">—</strong></span>
            </div>
        </div>

        <!-- Card Grid -->
        <div id="results-grid" class="card-grid"></div>
        <div id="scroll-sentinel"></div>
        <div id="load-more" class="load-ind hidden"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Loading more...</div>
        <div id="end-marker" class="load-ind done hidden"><i class="fa-solid fa-check-circle mr-1"></i> All cards loaded</div>
    </div>

    <script>
        function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
        function fmt(n) { if(n>=1e6) return (n/1e6).toFixed(1)+'M'; if(n>=1e3) return (n/1e3).toFixed(1)+'K'; return String(n); }
        function barPct(v) { return Math.min(Math.max((v/3)*100,2),100); }
        // Make any element keyboard-operable as a button (role, focus, Enter/Space).
        function makeButton(el, label, handler) {
            el.setAttribute('role', 'button');
            el.tabIndex = 0;
            if (label) el.setAttribute('aria-label', label);
            el.addEventListener('click', handler);
            el.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handler(e); }
            });
        }
        function buildTagBackground(results) {
            const freq = {};
            results.forEach(card => {
                (card.topics || []).forEach(tag => {
                    const t = tag.toLowerCase().trim();
                    if (t) freq[t] = (freq[t] || 0) + 1;
                });
            });

            const sorted = Object.entries(freq)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 80);

            if (!sorted.length) return;

            const container = document.getElementById('tag-bg');
            container.innerHTML = '';

            const maxCount = sorted[0][1];
            const minCount = sorted[sorted.length - 1][1];
            const range = Math.max(maxCount - minCount, 1);

            const vw = window.innerWidth;
            const vh = window.innerHeight * 3;

            const cols = 6;
            const rows = Math.ceil(sorted.length / cols);
            const cellW = vw / cols;
            const cellH = vh / rows;

            sorted.forEach(([tag, count], i) => {
                const ratio = (count - minCount) / range;
                const size = 12 + ratio * 32;
                const opacity = 0.04 + ratio * 0.08;

                const col = i % cols;
                const row = Math.floor(i / cols);

                const x = col * cellW + (Math.random() * cellW * 0.7);
                const y = row * cellH + (Math.random() * cellH * 0.6);
                const rotate = (Math.random() - 0.5) * 30;

                const span = document.createElement('span');
                span.textContent = tag;
                span.title = tag + ' (' + count + ')';
                span.style.cssText = `
                    left: ${x}px;
                    top: ${y}px;
                    font-size: ${size.toFixed(1)}px;
                    opacity: ${opacity.toFixed(3)};
                    transform: rotate(${rotate.toFixed(1)}deg);
                `;
                makeButton(span, 'Search tag: ' + tag, () => {
                    document.getElementById('topics-input').value = tag;
                    document.getElementById('query-input').value = '';
                    document.getElementById('min-favs').value = '0';
                    document.getElementById('exclude-input').value = '';
                    document.getElementById('tag-or-checkbox').checked = true;
                    doSearch();
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                });
                container.appendChild(span);
            });
        }
        const FALLBACK='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" fill="none"><rect width="200" height="200" fill="#1e1b4b"/><text x="100" y="110" text-anchor="middle" font-size="64" fill="#6366f1">?</text></svg>');
        function safeImg(u){ u=String(u??''); const l=u.toLowerCase(); return (l.startsWith('http://')||l.startsWith('https://')) ? u : FALLBACK; }
        const SRC={'chat_count':'💬','download_count':'⬇️','default':'📨','fav_count':'❤️','trending':'🔥','created_at':'🆕','rating':'⭐'};

        // ─── Showcase ───
        let scData = [];
        let scIdx = 0;
        let scTimer = null;
        const SC_INTERVAL = 6000;

        async function loadShowcase() {
            try {
                const r = await fetch('/api/showcase');
                scData = await r.json();
                if (!scData.length) return;
                buildShowcase();
                scGo(0);
                startScTimer();
            } catch(e) { console.warn('Showcase load failed:', e); }
        }

        function buildShowcase() {
            const track = document.getElementById('sc-track');
            const dots = document.getElementById('sc-dots');
            track.innerHTML = '';
            dots.innerHTML = '';

            scData.forEach((topic, ti) => {
                // Slide
                const slide = document.createElement('div');
                slide.className = 'showcase-slide';
                (topic.cards || []).forEach(card => {
                    const img = safeImg(card.avatar_url);
                    const thumb = document.createElement('div');
                    thumb.className = 'sc-thumb';
                    makeButton(thumb, card.name || 'Open character', (e) => { e.stopPropagation(); window.open('https://chub.ai/characters/'+encodeURI(card.author_path),'_blank'); });
                    thumb.innerHTML = `
                        <div class="sc-thumb-img">
                            <img src="${esc(img)}" alt="${esc(card.name)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK}'">
                        </div>
                        <div class="sc-thumb-name" title="${esc(card.name)}">${esc(card.name)}</div>
                        <div class="sc-thumb-author">@${esc(card.author)}</div>
                        <div class="sc-thumb-gem">💎 ${fmt(Math.round(card.gem_score||0))}</div>
                    `;
                    slide.appendChild(thumb);
                });
                track.appendChild(slide);

                // Dot
                const dot = document.createElement('div');
                dot.className = 'showcase-dot';
                makeButton(dot, 'Go to category ' + (ti + 1), () => { scGo(ti); resetScTimer(); });
                dots.appendChild(dot);
            });
        }

        function scGo(idx) {
            scIdx = idx;
            const track = document.getElementById('sc-track');
            track.style.transform = `translateX(-${idx * 100}%)`;

            // Update header
            const topic = scData[idx];
            document.getElementById('sc-emoji').textContent = topic.emoji;
            document.getElementById('sc-label').textContent = topic.label;
            document.getElementById('sc-counter').textContent = `${idx+1} / ${scData.length}`;

            // Update dots
            document.querySelectorAll('.showcase-dot').forEach((d,i) => {
                d.classList.toggle('active', i === idx);
            });
        }

        let scPaused = false;

        function scNav(dir) {
            let next = scIdx + dir;
            if (next < 0) next = scData.length - 1;
            if (next >= scData.length) next = 0;
            scGo(next);
            if (!scPaused) resetScTimer();
        }

        function startScTimer() {
            clearInterval(scTimer);
            scTimer = setInterval(() => { scNav(1); }, SC_INTERVAL);
        }
        function resetScTimer() {
            clearInterval(scTimer);
            startScTimer();
        }

        // Pause on hover — full stop, fresh timer on leave
        document.getElementById('showcase-wrap').addEventListener('mouseenter', () => {
            scPaused = true;
            clearInterval(scTimer);
        });
        document.getElementById('showcase-wrap').addEventListener('mouseleave', () => {
            scPaused = false;
            if (scData.length) startScTimer();
        });

        function searchShowcaseTopic() {
            const topic = scData[scIdx];
            if (!topic) return;
            document.getElementById('query-input').value = topic.query;
            document.getElementById('topics-input').value = topic.tags || '';
            document.getElementById('min-favs').value = String(topic.min_favs ?? 0);
            // Set OR checkbox based on exclusive flag
            document.getElementById('tag-or-checkbox').checked = !topic.exclusive;
            document.getElementById('exclude-input').value = (topic.exclude_tags || []).join(', ');
            doSearch();
        }

        // ─── Main Search ───
        const CHUNK=60;
        const SCROLL_MARGIN='600px';
        let allR=[], rendered=0, isLoading=false;
        let searchController=null;  // aborts an in-flight search when a newer one starts

        function makeCard(item, rank) {
            const el=document.createElement('div');

            // Determine shiny tiers
            const isGem50 = item.gem_score >= 60;
            const isDepth100 = item.smoothed_depth >= 30;
            const isConv100 = item.smoothed_conversion >= 0.20;
            const shinyCount = (isGem50?1:0) + (isDepth100?1:0) + (isConv100?1:0);

            let shinyClass = '';
            let shinyStar = '';
            if (shinyCount >= 2) {
                shinyClass = ' shiny-multi';
                shinyStar = '<div class="shiny-star">🌟</div>';
            } else if (isGem50) {
                shinyClass = ' shiny-gold';
                shinyStar = '<div class="shiny-star">⭐</div>';
            } else if (isDepth100) {
                shinyClass = ' shiny-depth';
                shinyStar = '<div class="shiny-star">💠</div>';
            } else if (isConv100) {
                shinyClass = ' shiny-conv';
                shinyStar = '<div class="shiny-star">💗</div>';
            }

            el.className='h-card h-card-enter' + shinyClass;
            makeButton(el, (item.name || 'Open character') + ' — open on Chub', ()=>window.open('https://chub.ai/characters/'+encodeURI(item.author_path),'_blank'));
            const img=safeImg(item.avatar_url);
            const deep = isConv100 ? false : isDepth100 ? true : item.norm_depth > item.norm_conv;
            const ageStat = (item.days_old==null) ? '' :
                `<span class="h-stat" title="Created ${esc((item.created_at||'').slice(0,10))}"><i class="fa-regular fa-calendar" style="color:#f59e0b"></i><strong>${item.days_old===0?'today':fmt(item.days_old)}</strong></span>`;
            const tags=(item.topics||[]).slice(0,4).map(t=>`<span class="h-card-tag">${esc(t)}</span>`).join('');
            const srcs=(item.found_in||[]).map(s=>`<span title="${esc(s)}">${SRC[s]||s}</span>`).join('');
            el.innerHTML=`
                <div class="h-card-img">
                    <img src="${esc(img)}" alt="${esc(item.name)}" loading="lazy" onerror="this.onerror=null;this.src='${FALLBACK}'">
                    <div class="h-card-rank">#${rank}</div>
                    <div class="h-card-signal">${deep?'🔵 Deep':'🩷 Conv'}</div>
                    ${shinyStar}
                </div>
                <div class="h-card-body">
                    <div class="h-card-header">
                        <div class="h-card-name" title="${esc(item.name)}">${esc(item.name)}</div>
                        <div class="h-card-gem"><i class="fa-solid fa-gem" style="font-size:8px;color:#fbbf24"></i>${fmt(Math.round(item.gem_score))}</div>
                    </div>
                    <div class="h-card-author">@${esc(item.author)}</div>
                    <div class="h-card-desc">${esc(item.tagline)||'No description.'}</div>
                    <div class="h-card-tags">${tags}</div>
                    <div class="h-card-stats">
                        <span class="h-stat"><i class="fa-solid fa-heart" style="color:#f43f5e"></i><strong>${fmt(item.favorites)}</strong></span>
                        <span class="h-stat"><i class="fa-solid fa-download" style="color:#22c55e"></i><strong>${fmt(item.downloads)}</strong></span>
                        <span class="h-stat"><i class="fa-solid fa-comments" style="color:#06b6d4"></i><strong>${fmt(item.chats)}</strong></span>
                        <span class="h-stat"><i class="fa-solid fa-message" style="color:#8b5cf6"></i><strong>${fmt(item.messages)}</strong></span>
                        ${ageStat}
                        <div class="h-bar-wrap">
                            <span class="h-bar-label" style="color:#818cf8">D</span>
                            <div class="h-micro-track"><div class="h-micro-fill-d" style="width:${barPct(item.norm_depth)}%"></div></div>
                            <span class="h-bar-val">${item.smoothed_depth.toFixed(0)}</span>
                            <span class="h-bar-label" style="color:#f472b6">C</span>
                            <div class="h-micro-track"><div class="h-micro-fill-c" style="width:${barPct(item.norm_conv)}%"></div></div>
                            <span class="h-bar-val">${(item.smoothed_conversion*100).toFixed(1)}%</span>
                        </div>
                        <div class="h-card-sources">${srcs}</div>
                    </div>
                </div>`;
            return el;
        }

        function renderChunk() {
            if(rendered>=allR.length) return false;
            const grid=document.getElementById('results-grid');
            const end=Math.min(rendered+CHUNK,allR.length);
            const frag=document.createDocumentFragment();
            for(let i=rendered;i<end;i++) frag.appendChild(makeCard(allR[i],i+1));
            grid.appendChild(frag);
            rendered=end;
            document.getElementById('stat-filtered').textContent=rendered+' / '+allR.length;
            if(rendered>=allR.length){document.getElementById('end-marker').classList.remove('hidden');document.getElementById('load-more').classList.add('hidden');return false;}
            return true;
        }

        const obs=new IntersectionObserver(entries=>{
            for(const e of entries){
                if(e.isIntersecting&&!isLoading&&allR.length>0&&rendered<allR.length){
                    document.getElementById('load-more').classList.remove('hidden');
                    requestAnimationFrame(()=>{renderChunk();if(rendered>=allR.length) document.getElementById('load-more').classList.add('hidden');});
                }
            }
        },{rootMargin:SCROLL_MARGIN});
        obs.observe(document.getElementById('scroll-sentinel'));

        function resetSearch() {
            document.getElementById('query-input').value = '';
            document.getElementById('topics-input').value = '';
            document.getElementById('tag-or-checkbox').checked = true;
            document.getElementById('sort-select').value = 'gem_score';
            document.getElementById('min-favs').value = '1410';
            document.getElementById('min-chats').value = '10';
            document.getElementById('min-msgs').value = '50';
            document.getElementById('min-days').value = '';
            document.getElementById('max-days').value = '';
            document.getElementById('nsfw-checkbox').checked = true;
            doSearch();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        async function doSearch() {
            if(searchController) searchController.abort();  // cancel any previous in-flight search
            searchController=new AbortController();
            const signal=searchController.signal;
            const grid=document.getElementById('results-grid');
            const status=document.getElementById('status-bar');
            const stats=document.getElementById('batch-stats');
            grid.innerHTML='';allR=[];rendered=0;
            document.getElementById('load-more').classList.add('hidden');
            document.getElementById('end-marker').classList.add('hidden');
            status.classList.remove('hidden');stats.classList.add('hidden');
            document.getElementById('progress-bar').style.width='0%';
            document.getElementById('status-text').textContent='Querying 7 pools...';
            isLoading=true;

            try{
                const params=new URLSearchParams({
                    query:document.getElementById('query-input').value,
                    topics:document.getElementById('topics-input').value,
                    inclusive_or:document.getElementById('tag-or-checkbox').checked,
                    sort:document.getElementById('sort-select').value,
                    min_favs:document.getElementById('min-favs').value,
                    min_chats:document.getElementById('min-chats').value,
                    min_msgs:document.getElementById('min-msgs').value,
                    nsfw:document.getElementById('nsfw-checkbox').checked,
                    exclude_tags: document.getElementById('exclude-input').value,
                    min_days_ago: document.getElementById('min-days').value,
                    max_days_ago: document.getElementById('max-days').value,
                });
                const resp=await fetch(`/api/query?${params}`, {signal});
                let data;
                try { data=await resp.json(); }
                catch(_) { throw new Error('Server error ('+resp.status+'). Please try again.'); }
                if(signal.aborted) return;  // a newer search started while this one was in flight
                document.getElementById('progress-bar').style.width='100%';
                status.classList.add('hidden');isLoading=false;

                if(data.error){grid.innerHTML=`<div class="col-span-full text-center py-12 text-red-400 border border-red-900/50 bg-red-950/20 rounded-xl"><p class="font-semibold">Error</p><p class="text-sm mt-1 text-gray-500">${esc(data.error)}</p></div>`;return;}
                allR=data.results;
                if(!allR.length){grid.innerHTML=`<div class="col-span-full text-center py-12 text-gray-400 border border-gray-800 bg-gray-900/20 rounded-xl"><p class="font-semibold">No results</p></div>`;return;}

                const f=allR[0];
                document.getElementById('stat-pool').textContent=fmt(data.pool_size_raw);
                document.getElementById('stat-unique').textContent=fmt(data.pool_size_unique);
                document.getElementById('stat-med-depth').textContent=(f.median_depth||0).toFixed(1);
                document.getElementById('stat-med-conv').textContent=((f.median_conv||0)*100).toFixed(2)+'%';
                document.getElementById('stat-filtered').textContent='0 / '+allR.length;
                document.getElementById('stat-top-gem').textContent=fmt(Math.round(f.gem_score));
                stats.classList.remove('hidden');
                buildTagBackground(allR);  // ← add this line
                renderChunk();
            }catch(err){
                if(err.name==='AbortError') return;  // superseded by a newer search; leave its state intact
                console.error(err);status.classList.add('hidden');isLoading=false;
                grid.innerHTML=`<div class="col-span-full text-center py-12 text-red-400"><p>${esc(err.message)}</p></div>`;
            }
        }

        document.getElementById('search-form').addEventListener('submit',e=>{e.preventDefault();doSearch();});
        window.addEventListener('DOMContentLoaded',()=>{
            document.getElementById('query-input').value='';
            doSearch();
            loadShowcase();
        });
    </script>
</body>
</html>
"""


@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/showcase')
@rate_limit
def showcase_api():
    """Return pre-scored top cards for each showcase topic. Cached for 24h."""
    try:
        data = get_showcase_data()
        return jsonify(data)
    except Exception as e:
        app.logger.warning(f"Showcase API failed: {e}")
        return jsonify([])

@app.route('/rss')
@app.route('/rss/<category>')
@rate_limit
def rss_feed(category=None):
    """RSS feed of top gems, optionally filtered by category."""
    min_gem = 0

    # Use showcase data as the source — already cached and scored
    showcase = get_showcase_data()

    # Validate category
    if category:
        valid = {topic['label'].lower() for topic in showcase}
        if category.lower() not in valid:
            labels = ', '.join(sorted(t['label'] for t in showcase))
            return jsonify({'error': f'Unknown category. Available: {labels}'}), 404

    items = []
    for topic in showcase:
        if category and topic['label'].lower() != category.lower():
            continue
        for card in topic.get('cards', []):
            if card.get('gem_score', 0) >= min_gem:
                items.append({
                    'topic': topic['label'],
                    'emoji': topic['emoji'],
                    **card
                })

    items.sort(key=lambda x: x.get('gem_score', 0), reverse=True)
    items = items[:50]

    now = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

    rss_items = ''
    for item in items:
        name = xml_escape(item.get('name', 'Untitled'))
        author = xml_escape(item.get('author', 'unknown'))
        topic = xml_escape(item.get('topic', ''))
        link = "https://chub.ai/characters/" + xml_escape(item.get('author_path', ''))
        score = round(item.get('gem_score', 0))
        depth = round(item.get('smoothed_depth', 0))
        conv = round(item.get('smoothed_conversion', 0) * 100, 1)

        rss_items += f"""
        <item>
            <title>💎 {score} — {name}</title>
            <link>{link}</link>
            <guid>{link}</guid>
            <description>{topic} | Depth: {depth} | Conv: {conv}% | by @{author}</description>
            <pubDate>{now}</pubDate>
            <category>{topic}</category>
        </item>"""

    title = f"Chub AI Gems — {xml_escape(category)}" if category else "Chub AI Gems — Top Discoveries"

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>{title}</title>
        <link>http://localhost:5123</link>
        <description>High-engagement character card discoveries from Chub.ai</description>
        <lastBuildDate>{now}</lastBuildDate>
        <ttl>60</ttl>
        {rss_items}
    </channel>
</rss>"""

    response = app.response_class(rss, mimetype='application/rss+xml')
    return response

SEARCH_CACHE_MAX = 200

SORT_KEYS = {
    'gem_score': lambda x: x.get('gem_score', 0),
    'depth': lambda x: x['smoothed_depth'],
    'conversion': lambda x: x['smoothed_conversion'],
    'favorites': lambda x: x['favorites'],
    'downloads': lambda x: x['downloads'],
    'chats': lambda x: x['chats'],
    'messages': lambda x: x['messages'],
}


def _sorted_response(entry, sort_strategy):
    key_fn = SORT_KEYS.get(sort_strategy, SORT_KEYS['gem_score'])
    ordered = sorted(entry['processed'], key=key_fn, reverse=True)
    return {'results': ordered, 'total': entry['total'],
            'pool_size_raw': entry['pool_size_raw'], 'pool_size_unique': entry['pool_size_unique']}


@app.route('/api/query')
@rate_limit
def query_api():
    query = request.args.get('query', '')[:200]
    topics = request.args.get('topics', '')[:500]
    exclude_raw = request.args.get('exclude_tags', '')[:500]
    exclude_set = {t.lower().strip() for t in exclude_raw.split(',') if t.strip()}
    inclusive_or = request.args.get('inclusive_or', 'true') == 'true'
    sort_strategy = request.args.get('sort', 'gem_score')
    if sort_strategy not in ('gem_score', 'depth', 'conversion', 'favorites', 'downloads', 'chats', 'messages'):
        sort_strategy = 'gem_score'
    try: min_favs = max(0, min(int(request.args.get('min_favs', '0') or 0), 999999))
    except (ValueError, TypeError): min_favs = 0
    try: min_chats = max(0, min(int(request.args.get('min_chats', '0') or 0), 999999))
    except (ValueError, TypeError): min_chats = 0
    try: min_msgs = max(0, min(int(request.args.get('min_msgs', '0') or 0), 999999))
    except (ValueError, TypeError): min_msgs = 0
    nsfw = request.args.get('nsfw', 'true') == 'true'

    def parse_days(name):
        raw = request.args.get(name, '').strip()
        if not raw:
            return None
        try:
            return max(0, min(int(raw), 36500))
        except (ValueError, TypeError):
            return None
    min_days_ago = parse_days('min_days_ago')
    max_days_ago = parse_days('max_days_ago')

    cache_key = (query.lower().strip(), topics.lower().strip(), inclusive_or, min_favs, min_chats, min_msgs, nsfw, frozenset(exclude_set), min_days_ago, max_days_ago)
    now = time.time()

    with _search_lock:
        cached = _search_cache.get(cache_key)
        hit = cached is not None and (now - cached['ts']) < SEARCH_CACHE_TTL
    if hit:
        return jsonify(_sorted_response(cached, sort_strategy))

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/119.0.0.0 Safari/537.36'
        }

        card_map = {}
        total_raw = 0

        jobs = []
        for sort_by in SORT_STRATEGIES:
            for pg in range(1, PAGES_PER_SORT + 1):
                jobs.append((sort_by, pg))

        with ThreadPoolExecutor(max_workers=18) as executor:
            futures = {
                executor.submit(fetch_chub_page, query, pg, sort_by, nsfw, headers, topics, inclusive_or, min_days_ago, max_days_ago): (sort_by, pg)
                for sort_by, pg in jobs
            }
            for future in as_completed(futures):
                sort_by, pg = futures[future]
                nodes = future.result()
                total_raw += len(nodes)
                for node in nodes:
                    fp = node.get('fullPath', '')
                    if not fp:
                        continue
                    if fp in card_map:
                        card_map[fp]['found_in'].add(sort_by)
                        # Union topics from all sources
                        existing = set(card_map[fp]['node'].get('topics', []))
                        incoming = set(node.get('topics', []))
                        if incoming - existing:
                            card_map[fp]['node']['topics'] = list(existing | incoming)
                    else:
                        card_map[fp] = {'node': node, 'found_in': {sort_by}}

        pool_unique = len(card_map)

        processed = []
        for fp, entry in card_map.items():
            node = entry['node']
            found_in = entry['found_in']
            favs = int(node.get('n_favorites', 0) or 0)
            chats = int(node.get('nChats', 0) or 0)
            messages = int(node.get('nMessages', 0) or 0)
            downloads = int(node.get('starCount', 0) or 0)

            if favs < min_favs or chats < min_chats or messages < min_msgs:
                continue
            # Tag exclusion
            if exclude_set:
                card_topics = {t.lower().strip() for t in node.get('topics', [])}
                if card_topics & exclude_set:
                    continue

            raw_depth = messages / chats if chats > 0 else 0.0
            raw_conversion = favs / chats if chats > 0 else 0.0
            smoothed_depth = calculate_smoothed_depth(messages, chats)
            smoothed_conversion = calculate_smoothed_conversion(favs, chats, downloads)
            author = fp.split('/')[0] if '/' in fp else fp

            created_at = node.get('createdAt') or ''
            days_old = None
            if created_at:
                try:
                    created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    days_old = max(0, (datetime.now(timezone.utc) - created_dt).days)
                except ValueError:
                    pass

            processed.append({
                'name': node.get('name', 'Untitled'),
                'tagline': node.get('tagline', ''),
                'author': author,
                'author_path': fp,
                'avatar_url': node.get('avatar_url', ''),
                'downloads': downloads,
                'favorites': favs,
                'chats': chats,
                'messages': messages,
                'raw_depth': raw_depth,
                'raw_conversion': raw_conversion,
                'smoothed_depth': smoothed_depth,
                'smoothed_conversion': smoothed_conversion,
                'created_at': created_at,
                'days_old': days_old,
                'topics': node.get('topics', []),
                'found_in': sorted(list(found_in))
            })

        processed = calculate_gem_scores(processed)

        new_entry = {'processed': processed, 'total': len(processed),
                     'pool_size_raw': total_raw, 'pool_size_unique': pool_unique, 'ts': now}

        with _search_lock:
            _search_cache[cache_key] = new_entry
            stale = [k for k, v in _search_cache.items() if (now - v['ts']) > SEARCH_CACHE_TTL * 2]
            for k in stale:
                _search_cache.pop(k, None)
            while len(_search_cache) > SEARCH_CACHE_MAX:
                oldest = min(_search_cache, key=lambda k: _search_cache[k]['ts'])
                _search_cache.pop(oldest, None)

        return jsonify(_sorted_response(new_entry, sort_strategy))

    except requests.exceptions.Timeout:
        return jsonify({'error': 'Chub API timed out.'}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({'error': 'Could not connect to Chub API.'}), 502
    except Exception as e:
        app.logger.error(f"Query error: {e}")
        return jsonify({'error': 'Internal server error.'}), 500


if __name__ == '__main__':
    total_calls = len(SORT_STRATEGIES) * PAGES_PER_SORT
    print("=" * 60)
    print("  💎 Chub AI Gems — Showcase Banner + Horizontal Cards")
    print("=" * 60)
    print(f"  Search: {len(SORT_STRATEGIES)} pools × {PAGES_PER_SORT} pages = {total_calls} calls")
    print(f"  Showcase: {len(SHOWCASE_TOPICS)} topics × top {SHOWCASE_CARDS_PER_TOPIC} each (cached {SHOWCASE_CACHE_TTL}s)")
    print(f"  Gem = (depth/med + conv/med) × log(favs + 1)")
    if AUTH_ENABLED:
        print(f"  Basic auth: ENABLED (user: {AUTH_USERNAME})")
    else:
        print("  Basic auth: disabled (set GEMS_AUTH_ENABLED=true to enable)")
    print("=" * 60)
    print("  http://localhost:5123")
    print("=" * 60)

    try:
        from gunicorn.app.wsgiapp import run
        import sys
        # Single worker + threads: in-memory caches & rate limiter are per-process,
        # so one shared process keeps them coherent (app is I/O-bound on the Chub API).
        sys.argv = [
            'gunicorn',
            '-w', '1',
            '-b', '0.0.0.0:5123',
            '--max-requests', '1000',        # Recycle after 1000 requests
            '--max-requests-jitter', '50',    # Stagger so they don't all die at once
            '--timeout', '30',                # Kill stuck workers
            '--graceful-timeout', '10',       # Give them 10s to finish up
            '--worker-class', 'gthread',      # Threaded workers for your I/O-heavy API calls
            '--threads', '8',                 # 8 threads per worker
            'chub_search_tool:app'
        ]
        run()
    except ImportError:
        from waitress import serve
        serve(app, host='0.0.0.0', port=5123, threads=8)
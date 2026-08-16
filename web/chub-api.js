// web/chub-api.js
//
// Browser-side port of the Chub API fetch/orchestration/caching layer from
// chub_search_tool.py. Talks directly to api.chub.ai from the client instead
// of proxying through Flask.
//
// IMPORTANT: api.chub.ai's CORS policy (access-control-allow-origin: *) only
// covers "simple" requests. Any custom request header (including a custom
// User-Agent, which browsers block anyway) forces a CORS preflight (OPTIONS),
// and that preflight is rejected with 403. Every fetch() call below must stay
// a plain, headerless GET.

import {
    calculateSmoothedDepth,
    calculateSmoothedConversion,
    calculateGemScores,
    sortedResults,
} from './scoring.js';

const CHUB_SEARCH_URL = 'https://api.chub.ai/search';

export const SORT_STRATEGIES = [
    'chat_count',
    'download_count',
    'default',
    'fav_count',
    'trending',
    'created_at',
];
export const PAGES_PER_SORT = 3;
export const API_PER_PAGE = 200;

const VALID_QUERY_SORTS = new Set([
    'gem_score', 'depth', 'conversion', 'favorites', 'downloads', 'chats', 'messages',
]);

/** Return a showcase topic based on the current (local) date. */
export function getSeasonalTopic() {
    const today = new Date();
    const m = today.getMonth() + 1; // JS months are 0-indexed; Python's are 1-indexed
    const d = today.getDate();

    // Check date ranges (month, day_start, day_end)
    const seasons = [
        [[10, 1], [10, 31], { query: 'Horror halloween', emoji: '🎃', label: 'Halloween', min_favs: 0, tags: 'halloween,horror,spooky,monster' }],
        [[12, 1], [12, 31], { query: 'Christmas winter', emoji: '🎄', label: 'Christmas', min_favs: 0, tags: 'christmas,winter,holiday,snow' }],
        [[1, 1], [1, 7], { query: 'New Year party', emoji: '🎆', label: 'New Year', min_favs: 0, tags: 'new year,party,celebration' }],
        [[2, 7], [2, 21], { query: 'Romance love valentine', emoji: '💘', label: "Valentine's", min_favs: 0, tags: 'valentine,romance,love,dating' }],
        [[3, 14], [3, 20], { query: 'drinking lucky irish', emoji: '☘️', label: "St Patrick's", min_favs: 0, tags: 'irish,lucky,drinking' }],
        [[3, 30], [4, 2], { query: 'Trickster prank', emoji: '🃏', label: 'April Fools', min_favs: 0, tags: 'trickster,prank,jester' }],
        [[3, 28], [4, 15], { query: 'rabbit', emoji: '🐣', label: 'Easter', min_favs: 0, tags: 'easter,rabbit,bunny,spring' }],
        [[5, 15], [8, 31], { query: '', emoji: '🏖️', label: 'Summer', min_favs: 0, tags: 'summer,vacation,camping,island,beach', exclude_tags: ['hyena', 'futanari', 'femboy'] }],
        [[11, 20], [11, 30], { query: 'thanksgiving', emoji: '🦃', label: 'Thanksgiving', min_favs: 0, tags: 'thanksgiving,harvest,feast' }],
    ];

    for (const [[m1, d1], [m2, d2], topic] of seasons) {
        if ((m === m1 && d >= d1) || (m === m2 && d <= d2) || (m1 < m && m < m2)) {
            return topic;
        }
    }

    // Default fallback
    return { query: 'Goth', emoji: '🧛', label: 'Goth', min_favs: 300, tags: 'goth,gothic,vampire,dark' };
}

export const SHOWCASE_TOPICS = [
    { query: 'RPG', emoji: '🎲', label: 'RPG', min_favs: 0, tags: 'rpg' },
    { query: '', emoji: '🎩', label: 'Gentlemen', min_favs: 0, tags: 'fempov,male,human', exclusive: true, exclude_tags: ['anypov', 'feet', 'scat', 'diaper', 'vore', 'furry', 'genderswap', 'malepov', 'feminization', 'bbc', 'pokemon', 'femdom', 'ntr', 'cuckold', 'femboy', 'horny', 'cum toilet', 'goblin', 'cumdump', 'female monster'] },
    { query: 'Fantasy', emoji: '⚔️', label: 'Fantasy', min_favs: 50, tags: 'fantasy,medieval,magic,elves' },
    { query: '', emoji: '⚔️', label: 'Dark Fantasy', min_favs: 0, tags: 'dark fantasy,slave' },
    { query: 'Romance', emoji: '💕', label: 'Romance', min_favs: 30, tags: 'romance,love,dating,relationship,slowburn' },
    { query: 'sci-fi', emoji: '🚀', label: 'Science Fiction', min_favs: 0, tags: 'sci-fi,science fiction,cyberpunk,space' },
    { query: '', emoji: '⚔️', label: 'Isekai', min_favs: 100, tags: 'isekai,reincarnation' },
    getSeasonalTopic(),
    { query: '', emoji: '🌸', label: 'Anime', min_favs: 0, tags: 'anime,manga,waifu,anime game characters,webtoon,kemonomimi,mech pilot' },
    { query: 'Roleplay', emoji: '🎭', label: 'Roleplay', min_favs: 0, tags: 'roleplay,rp' },
    { query: '', emoji: '🧟', label: 'Apocalypse', min_favs: 3, tags: 'apocalypse,Post-apocalypse,zombies,zombie Apocalypse', exclude_tags: ['futanari', 'gentle femdom'] },
    { query: 'Wholesome', emoji: '💛', label: 'Wholesome', min_favs: 0, tags: 'wholesome,cute,comfort,slice of life,can be wholesome,can be sexy' },
    { query: '', emoji: '☯', label: 'The Dao', min_favs: 0, tags: 'wuxia,xianxia,cultivation,dual cultivation,murim,ancient china,china' },
];
export const SHOWCASE_CARDS_PER_TOPIC = 10;
export const SHOWCASE_CACHE_TTL = 86400; // 24 hours, in seconds
export const SEARCH_CACHE_TTL = 3600; // 60 minutes, in seconds
export const SEARCH_CACHE_MAX = 20;

const SHOWCASE_CACHE_KEY = 'gems_showcase_v1';
const SEARCH_CACHE_PREFIX = 'gems_search_v1:';

// ─── localStorage TTL cache, with an in-memory fallback ───
//
// Wraps every localStorage access in try/catch: private-browsing modes,
// disabled storage, and QuotaExceededError all fall back to an in-memory
// Map so the app keeps working, just without persistence across reloads.
const memoryFallback = new Map();

function storageAvailable() {
    try {
        return typeof localStorage !== 'undefined' && localStorage !== null;
    } catch (_e) {
        return false;
    }
}

function readEntry(key) {
    if (storageAvailable()) {
        try {
            const raw = localStorage.getItem(key);
            if (raw != null) {
                return JSON.parse(raw);
            }
        } catch (_e) {
            // Corrupt entry or storage error — fall through to memory fallback.
        }
    }
    return memoryFallback.has(key) ? memoryFallback.get(key) : null;
}

function writeEntry(key, entry) {
    if (storageAvailable()) {
        try {
            localStorage.setItem(key, JSON.stringify(entry));
            memoryFallback.delete(key);
            return;
        } catch (_e) {
            // QuotaExceededError (or any other storage failure) — degrade to memory.
        }
    }
    memoryFallback.set(key, entry);
}

function removeEntry(key) {
    if (storageAvailable()) {
        try {
            localStorage.removeItem(key);
        } catch (_e) {
            // ignore
        }
    }
    memoryFallback.delete(key);
}

function listKeysWithPrefix(prefix) {
    const keys = new Set();
    if (storageAvailable()) {
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (k && k.startsWith(prefix)) keys.add(k);
            }
        } catch (_e) {
            // ignore
        }
    }
    for (const k of memoryFallback.keys()) {
        if (k.startsWith(prefix)) keys.add(k);
    }
    return [...keys];
}

/** Read a {ts, data} JSON wrapper, honoring ttlMs. Returns null on miss/expiry. */
export function cacheGet(key, ttlMs) {
    const entry = readEntry(key);
    if (!entry || typeof entry.ts !== 'number') return null;
    if (Date.now() - entry.ts >= ttlMs) return null;
    return entry.data;
}

/** Write a {ts, data} JSON wrapper, stamped with the current time. */
export function cacheSet(key, data) {
    writeEntry(key, { ts: Date.now(), data });
}

/** Cap the search cache at SEARCH_CACHE_MAX entries, evicting oldest-by-ts first. */
function pruneSearchCache() {
    const keys = listKeysWithPrefix(SEARCH_CACHE_PREFIX);
    if (keys.length <= SEARCH_CACHE_MAX) return;
    const entries = keys
        .map((key) => ({ key, entry: readEntry(key) }))
        .filter((e) => e.entry && typeof e.entry.ts === 'number');
    entries.sort((a, b) => a.entry.ts - b.entry.ts);
    const excess = entries.length - SEARCH_CACHE_MAX;
    for (let i = 0; i < excess; i++) {
        removeEntry(entries[i].key);
    }
}

/**
 * Stable cache key matching the server's cache_key tuple: query/topics
 * lowercased+trimmed, inclusive_or, min_favs, min_chats, min_msgs, nsfw,
 * sorted exclude_tags, min_days_ago, max_days_ago.
 */
function buildSearchCacheKey({ query, topics, inclusiveOr, minFavs, minChats, minMsgs, nsfw, excludeTags, minDaysAgo, maxDaysAgo }) {
    const tuple = [
        query.toLowerCase().trim(),
        topics.toLowerCase().trim(),
        !!inclusiveOr,
        minFavs,
        minChats,
        minMsgs,
        !!nsfw,
        [...excludeTags].sort(),
        minDaysAgo,
        maxDaysAgo,
    ];
    return SEARCH_CACHE_PREFIX + JSON.stringify(tuple);
}

// Mirrors Python's `int(x or 0)` — None/0/''/NaN all collapse to 0.
function toInt(value) {
    const n = Math.trunc(Number(value || 0));
    return Number.isNaN(n) ? 0 : n;
}

/**
 * Fetch one page of the Chub search API for a given sort strategy.
 * Non-200 responses and network errors are swallowed to [] so a single
 * failed pool never breaks the wider fan-out.
 */
export async function fetchChubPage(query, apiPage, sortBy, nsfw, options = {}) {
    const { topics = '', inclusiveOr = true, minDaysAgo = null, maxDaysAgo = null, signal } = options;
    const params = new URLSearchParams({
        search: query,
        first: String(API_PER_PAGE),
        page: String(apiPage),
        sort: sortBy,
        venus: 'false',
        asc: sortBy === 'created_at' ? 'true' : 'false',
        nsfw: nsfw ? 'true' : 'false',
    });
    if (topics && topics.trim()) {
        params.set('topics', topics.trim());
        params.set('inclusive_or', inclusiveOr ? 'true' : 'false');
    }
    // Chub API filters on card creation date: min/max days since creation
    if (minDaysAgo !== null && minDaysAgo !== undefined) {
        params.set('min_days_ago', String(minDaysAgo));
    }
    if (maxDaysAgo !== null && maxDaysAgo !== undefined) {
        params.set('max_days_ago', String(maxDaysAgo));
    }
    try {
        const resp = await fetch(`${CHUB_SEARCH_URL}?${params.toString()}`, { signal });
        if (!resp.ok) {
            return [];
        }
        const data = await resp.json();
        return data?.data?.nodes || [];
    } catch (_e) {
        return [];
    }
}

/** Fetch a small set of cards for a showcase topic, score them, return top N. */
export async function fetchShowcaseTopic(topic, options = {}) {
    const { signal } = options;
    const params = new URLSearchParams({
        search: topic.query || '',
        first: '60',
        page: '1',
        sort: 'download_count',
        venus: 'false',
        asc: 'false',
        nsfw: 'true',
        include_forks: 'false',
    });
    // Use tags if provided for tighter showcase results
    if (topic.tags) {
        params.set('topics', topic.tags);
        // Use exclusive (AND) mode if specified, otherwise default to OR
        params.set('inclusive_or', topic.exclusive ? 'false' : 'true');
    }
    try {
        const resp = await fetch(`${CHUB_SEARCH_URL}?${params.toString()}`, { signal });
        if (!resp.ok) {
            return [];
        }
        const data = await resp.json();
        const nodes = data?.data?.nodes || [];

        let cards = [];
        for (const node of nodes) {
            const favs = toInt(node.n_favorites);
            const chats = toInt(node.nChats);
            const messages = toInt(node.nMessages);
            const downloads = toInt(node.starCount);

            if (chats < 5 || messages < 20) continue;

            const fp = node.fullPath || '';
            const author = fp.includes('/') ? fp.split('/')[0] : fp;

            cards.push({
                name: node.name ?? 'Untitled',
                author,
                author_path: fp,
                avatar_url: node.avatar_url ?? '',
                downloads,
                favorites: favs,
                chats,
                messages,
                topics: node.topics || [], // needed for exclusion
                smoothed_depth: calculateSmoothedDepth(messages, chats),
                smoothed_conversion: calculateSmoothedConversion(favs, chats, downloads),
            });
        }

        // Exclude unwanted tags
        const exclude = topic.exclude_tags || [];
        if (exclude.length) {
            const excludeLower = new Set(exclude.map((t) => t.toLowerCase().trim()));
            cards = cards.filter(
                (c) => !(c.topics || []).some((t) => excludeLower.has(t.toLowerCase().trim()))
            );
        }

        cards = calculateGemScores(cards);
        cards.sort((a, b) => (b.gem_score || 0) - (a.gem_score || 0));
        return cards.slice(0, SHOWCASE_CARDS_PER_TOPIC);
    } catch (_e) {
        return [];
    }
}

/**
 * Return showcase data for all topics, using the localStorage cache if
 * fresh. Fan-out is a Promise.all (vs. Python's ThreadPoolExecutor), which
 * naturally preserves the original SHOWCASE_TOPICS order in the result.
 */
export async function getShowcase(options = {}) {
    const { signal, forceRefresh = false } = options;

    if (!forceRefresh) {
        const cached = cacheGet(SHOWCASE_CACHE_KEY, SHOWCASE_CACHE_TTL * 1000);
        if (cached) return cached;
    }

    const results = await Promise.all(
        SHOWCASE_TOPICS.map(async (topic) => {
            const cards = await fetchShowcaseTopic(topic, { signal });
            return {
                query: topic.query,
                emoji: topic.emoji,
                label: topic.label,
                min_favs: topic.min_favs || 0,
                tags: topic.tags || '',
                exclusive: topic.exclusive || false,
                exclude_tags: topic.exclude_tags || [],
                cards,
            };
        })
    );

    cacheSet(SHOWCASE_CACHE_KEY, results);
    return results;
}

/**
 * Run the 18-request fan-out (6 SORT_STRATEGIES x 3 pages) against the Chub
 * API, dedupe/filter/score the pool, and return the same shape the Flask
 * /api/query handler returns: {results, total, pool_size_raw, pool_size_unique}.
 *
 * The unsorted, scored/filtered pool is what gets cached — sorting is
 * applied per call via SORT_KEYS so changing `sort` never triggers a refetch.
 */
export async function query(options = {}) {
    const {
        query: rawQuery = '',
        topics: rawTopics = '',
        excludeTags: rawExcludeTags = '',
        inclusiveOr = true,
        sort = 'gem_score',
        minFavs: rawMinFavs = 0,
        minChats: rawMinChats = 0,
        minMsgs: rawMinMsgs = 0,
        nsfw = true,
        minDaysAgo: rawMinDaysAgo = null,
        maxDaysAgo: rawMaxDaysAgo = null,
        signal,
        forceRefresh = false,
    } = options;

    const q = String(rawQuery ?? '').slice(0, 200);
    const topics = String(rawTopics ?? '').slice(0, 500);
    const excludeRaw = String(rawExcludeTags ?? '').slice(0, 500);
    const excludeSet = new Set(
        excludeRaw.split(',').map((t) => t.toLowerCase().trim()).filter(Boolean)
    );
    const sortStrategy = VALID_QUERY_SORTS.has(sort) ? sort : 'gem_score';

    const clampInt = (value, lo, hi, fallback) => {
        const n = parseInt(value, 10);
        if (Number.isNaN(n)) return fallback;
        return Math.max(lo, Math.min(n, hi));
    };
    const minFavs = clampInt(rawMinFavs, 0, 999999, 0);
    const minChats = clampInt(rawMinChats, 0, 999999, 0);
    const minMsgs = clampInt(rawMinMsgs, 0, 999999, 0);

    const parseDays = (value) => {
        if (value === null || value === undefined || value === '') return null;
        const n = parseInt(value, 10);
        if (Number.isNaN(n)) return null;
        return Math.max(0, Math.min(n, 36500));
    };
    const minDaysAgo = parseDays(rawMinDaysAgo);
    const maxDaysAgo = parseDays(rawMaxDaysAgo);

    const cacheKey = buildSearchCacheKey({
        query: q, topics, inclusiveOr, minFavs, minChats, minMsgs, nsfw,
        excludeTags: excludeSet, minDaysAgo, maxDaysAgo,
    });

    if (!forceRefresh) {
        const cached = cacheGet(cacheKey, SEARCH_CACHE_TTL * 1000);
        if (cached) {
            return {
                results: sortedResults(cached.processed, sortStrategy),
                total: cached.total,
                pool_size_raw: cached.pool_size_raw,
                pool_size_unique: cached.pool_size_unique,
            };
        }
    }

    const jobs = [];
    for (const sortBy of SORT_STRATEGIES) {
        for (let pg = 1; pg <= PAGES_PER_SORT; pg++) {
            jobs.push({ sortBy, pg });
        }
    }

    const pageResults = await Promise.all(
        jobs.map(({ sortBy, pg }) =>
            fetchChubPage(q, pg, sortBy, nsfw, { topics, inclusiveOr, minDaysAgo, maxDaysAgo, signal })
                .then((nodes) => ({ sortBy, nodes }))
        )
    );

    const cardMap = new Map();
    let totalRaw = 0;
    for (const { sortBy, nodes } of pageResults) {
        totalRaw += nodes.length;
        for (const node of nodes) {
            const fp = node.fullPath || '';
            if (!fp) continue;
            if (cardMap.has(fp)) {
                const entry = cardMap.get(fp);
                entry.foundIn.add(sortBy);
                // Union topics from all sources
                const existing = new Set(entry.node.topics || []);
                let changed = false;
                for (const t of node.topics || []) {
                    if (!existing.has(t)) {
                        existing.add(t);
                        changed = true;
                    }
                }
                if (changed) entry.node.topics = [...existing];
            } else {
                cardMap.set(fp, { node, foundIn: new Set([sortBy]) });
            }
        }
    }

    const poolUnique = cardMap.size;
    const processed = [];
    for (const [fp, entry] of cardMap) {
        const node = entry.node;
        const favs = toInt(node.n_favorites);
        const chats = toInt(node.nChats);
        const messages = toInt(node.nMessages);
        const downloads = toInt(node.starCount);

        if (favs < minFavs || chats < minChats || messages < minMsgs) continue;
        // Tag exclusion
        if (excludeSet.size) {
            const cardTopics = new Set((node.topics || []).map((t) => t.toLowerCase().trim()));
            let excluded = false;
            for (const t of cardTopics) {
                if (excludeSet.has(t)) {
                    excluded = true;
                    break;
                }
            }
            if (excluded) continue;
        }

        const rawDepth = chats > 0 ? messages / chats : 0.0;
        const rawConversion = chats > 0 ? favs / chats : 0.0;
        const smoothedDepth = calculateSmoothedDepth(messages, chats);
        const smoothedConversion = calculateSmoothedConversion(favs, chats, downloads);
        const author = fp.includes('/') ? fp.split('/')[0] : fp;

        const createdAt = node.createdAt || '';
        let daysOld = null;
        if (createdAt) {
            const createdDt = new Date(createdAt);
            if (!Number.isNaN(createdDt.getTime())) {
                daysOld = Math.max(0, Math.floor((Date.now() - createdDt.getTime()) / 86400000));
            }
        }

        processed.push({
            name: node.name ?? 'Untitled',
            tagline: node.tagline ?? '',
            author,
            author_path: fp,
            avatar_url: node.avatar_url ?? '',
            downloads,
            favorites: favs,
            chats,
            messages,
            raw_depth: rawDepth,
            raw_conversion: rawConversion,
            smoothed_depth: smoothedDepth,
            smoothed_conversion: smoothedConversion,
            created_at: createdAt,
            days_old: daysOld,
            topics: node.topics || [],
            found_in: [...entry.foundIn].sort(),
        });
    }

    calculateGemScores(processed);

    const cacheEntry = {
        processed,
        total: processed.length,
        pool_size_raw: totalRaw,
        pool_size_unique: poolUnique,
    };
    cacheSet(cacheKey, cacheEntry);
    pruneSearchCache();

    return {
        results: sortedResults(processed, sortStrategy),
        total: cacheEntry.total,
        pool_size_raw: totalRaw,
        pool_size_unique: poolUnique,
    };
}

// web/scoring.js
//
// Pure scoring math ported from chub_search_tool.py. No DOM, no fetch —
// this module must run unmodified under plain `node` (see tests/parity_check.mjs)
// as well as in the browser.

// Bayesian-smoothing constants (see chub_search_tool.py for rationale):
// each card's raw signal is pulled toward a prior until it accumulates
// enough exposure (chats) to be trusted on its own.
export const C_DEPTH = 20.0;
export const PRIOR_DEPTH = 12.0;
export const C_CONV = 20.0;
export const PRIOR_CONV = 0.05;

/**
 * Mirrors Python's `x or 0` truthiness check for numeric fields (null,
 * undefined, NaN, 0, and '' are all "falsy" in both languages) before
 * coercing to a float.
 */
function numOr0(value) {
    return Number(value || 0);
}

export function calculateSmoothedDepth(nMessages, nChats) {
    const messages = numOr0(nMessages);
    const chats = numOr0(nChats);
    return (messages + C_DEPTH * PRIOR_DEPTH) / (chats + C_DEPTH);
}

/**
 * Conversion = favorites / exposure, where exposure = max(chats, downloads).
 *
 * A card downloaded far more than it is chatted with should not look like
 * a runaway hit.
 */
export function calculateSmoothedConversion(favorites, nChats, downloads) {
    const favs = numOr0(favorites);
    const chats = numOr0(nChats);
    const dls = numOr0(downloads);
    const denominator = Math.max(chats, dls);
    return (favs + C_CONV * PRIOR_CONV) / (denominator + C_CONV);
}

/**
 * Replicates Python's statistics.median: sort ascending numerically,
 * odd length -> middle element, even length -> mean of the two middle
 * elements.
 */
function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    const n = sorted.length;
    const mid = Math.floor(n / 2);
    if (n % 2 === 1) {
        return sorted[mid];
    }
    return (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Scores a pool of cards in place (matching the Python function's
 * mutate-and-return behavior) using engagement normalized against the
 * pool's own median depth/conversion.
 */
export function calculateGemScores(cards) {
    if (!cards || !cards.length) {
        return cards;
    }
    const depths = cards.map((c) => c.smoothed_depth);
    const convs = cards.map((c) => c.smoothed_conversion);
    const medianDepth = depths.length ? Math.max(median(depths), 0.001) : 1.0;
    const medianConv = convs.length ? Math.max(median(convs), 0.0001) : 1.0;
    for (const c of cards) {
        const normDepth = c.smoothed_depth / medianDepth;
        const normConv = c.smoothed_conversion / medianConv;
        const engagement = normDepth + normConv;
        c.gem_score = engagement * Math.log(c.favorites + 1);
        c.norm_depth = normDepth;
        c.norm_conv = normConv;
        c.engagement = engagement;
        c.median_depth = medianDepth;
        c.median_conv = medianConv;
    }
    return cards;
}

// Map of sort name -> key function, all sorted descending (see
// chub_search_tool.py SORT_KEYS / _sorted_response).
export const SORT_KEYS = {
    gem_score: (c) => c.gem_score || 0,
    depth: (c) => c.smoothed_depth,
    conversion: (c) => c.smoothed_conversion,
    favorites: (c) => c.favorites,
    downloads: (c) => c.downloads,
    chats: (c) => c.chats,
    messages: (c) => c.messages,
};

/**
 * Returns a new array sorted descending by the given strategy, falling
 * back to gem_score for unknown strategies (mirrors _sorted_response).
 * Array.prototype.sort is stable in modern JS engines, matching Python's
 * stable sorted().
 */
export function sortedResults(cards, sortStrategy) {
    const keyFn = SORT_KEYS[sortStrategy] || SORT_KEYS.gem_score;
    return [...cards].sort((a, b) => keyFn(b) - keyFn(a));
}

#!/usr/bin/env node
// Score parity check (JS side).
//
// Loads the same fixture cards as tests/parity_check.py, runs them through
// web/scoring.js, and prints sorted JSON to stdout so it can be diffed
// against the Python output.
//
// Run with: node tests/parity_check.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { calculateSmoothedDepth, calculateSmoothedConversion, calculateGemScores } from '../web/scoring.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.join(__dirname, 'fixtures', 'sample_nodes.json');

function main() {
    const nodes = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8'));

    let cards = nodes.map((node) => {
        const favs = Number(node.n_favorites || 0);
        const chats = Number(node.nChats || 0);
        const messages = Number(node.nMessages || 0);
        const downloads = Number(node.starCount || 0);

        return {
            name: node.name ?? 'Untitled',
            fullPath: node.fullPath ?? '',
            favorites: favs,
            chats,
            messages,
            downloads,
            smoothed_depth: calculateSmoothedDepth(messages, chats),
            smoothed_conversion: calculateSmoothedConversion(favs, chats, downloads),
        };
    });

    cards = calculateGemScores(cards);

    const out = cards
        .map((c) => ({
            fullPath: c.fullPath,
            smoothed_depth: c.smoothed_depth,
            smoothed_conversion: c.smoothed_conversion,
            gem_score: c.gem_score,
            norm_depth: c.norm_depth,
            norm_conv: c.norm_conv,
            engagement: c.engagement,
            median_depth: c.median_depth,
            median_conv: c.median_conv,
        }))
        .sort((a, b) => (a.fullPath < b.fullPath ? -1 : a.fullPath > b.fullPath ? 1 : 0));

    console.log(JSON.stringify(out, null, 2));
}

main();

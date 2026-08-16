#!/usr/bin/env python3
"""Score parity check (Python side).

Loads the fixture cards, runs them through chub_search_tool's scoring
functions exactly as the Flask app would, and prints sorted JSON to stdout
so it can be diffed against tests/parity_check.mjs's output.

Run with a Python that has flask+requests importable, e.g.:
    uv run --with flask --with requests python tests/parity_check.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chub_search_tool import (  # noqa: E402
    calculate_smoothed_depth,
    calculate_smoothed_conversion,
    calculate_gem_scores,
)

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'sample_nodes.json')


def main():
    with open(FIXTURE_PATH, 'r', encoding='utf-8') as f:
        nodes = json.load(f)

    cards = []
    for node in nodes:
        favs = int(node.get('n_favorites', 0) or 0)
        chats = int(node.get('nChats', 0) or 0)
        messages = int(node.get('nMessages', 0) or 0)
        downloads = int(node.get('starCount', 0) or 0)

        cards.append({
            'name': node.get('name', 'Untitled'),
            'fullPath': node.get('fullPath', ''),
            'favorites': favs,
            'chats': chats,
            'messages': messages,
            'downloads': downloads,
            'smoothed_depth': calculate_smoothed_depth(messages, chats),
            'smoothed_conversion': calculate_smoothed_conversion(favs, chats, downloads),
        })

    cards = calculate_gem_scores(cards)

    out = [
        {
            'fullPath': c['fullPath'],
            'smoothed_depth': c['smoothed_depth'],
            'smoothed_conversion': c['smoothed_conversion'],
            'gem_score': c['gem_score'],
            'norm_depth': c['norm_depth'],
            'norm_conv': c['norm_conv'],
            'engagement': c['engagement'],
            'median_depth': c['median_depth'],
            'median_conv': c['median_conv'],
        }
        for c in cards
    ]
    out.sort(key=lambda c: c['fullPath'])
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()

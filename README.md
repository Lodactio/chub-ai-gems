# 💎 Chub AI Gems

A discovery engine for [Chub.ai](https://chub.ai) character cards that surfaces hidden gems using engagement-quality scoring instead of raw popularity.

![Screenshot](Screenshot.png)

## The Problem

Chub's default search ranks by popularity — favorites, downloads, clicks. This creates a rich-get-richer effect where viral cards with shallow engagement dominate, while deeply engaging cards with smaller audiences get buried.

## The Solution

Gems scores every card using two behavioral signals:

- **🔵 Depth** — average messages per chat (do people actually talk to this character?)
- **🩷 Conversion** — favorite-to-exposure ratio (do people who try it love it enough to come back?)

These are combined into a single **Gem Score**:

$$\text{Gem} = \left(\frac{\text{depth}}{\text{median depth}} + \frac{\text{conversion}}{\text{median conversion}}\right) \times \ln(\text{favorites} + 1)$$

The log-scaled favorites act as a confidence weight — a card needs *some* audience to rank, but doubling from 5,000 to 10,000 favorites matters far less than the quality signals.

Both signals use Bayesian smoothing to prevent small-sample cards from gaming the rankings:

$$\text{smoothed depth} = \frac{\text{messages} + C \times \text{prior}}{\text{chats} + C}$$

## Features

- **7 Discovery Pools** — queries Chub's API across 6 different sort strategies (chat count, downloads, default, favorites, trending, newest) to build a diverse candidate pool
- **Shiny Cards** — exceptional cards get visual indicators:
  - ⭐ Gold — high gem score
  - 💠 Blue — unusually deep conversations
  - 💗 Pink — exceptional conversion rate
  - 🌟 Rainbow — multiple signals firing at once
- **Showcase Carousel** — rotating featured categories (RPG, Fantasy, Romance, etc.) with seasonal holiday themes
- **Tag Cloud Background** — clickable tag cloud built from search results
- **Smart Caching** — search results cached 60 min, showcase cached 24h
- **Production Ready** — rate limiting, input validation, security headers, WSGI server

## Quick Start

### Requirements
- Python 3.8+

### Run

**Linux / Mac:**
```bash
bash run.sh
```

**Windows:**
```
Double-click run.bat
```

This will:
1. Create a virtual environment
2. Install dependencies
3. Start the server at `http://localhost:5123`

### Manual Setup

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python chub_search_tool.py
```

## Usage

| Field | Default | What it does |
|---|---|---|
| **Search** | blank (global top) | Keyword search against Chub's API |
| **Sort** | 💎 Gem Score | How results are ranked |
| **Min Fav** | 1410 | Minimum favorites threshold |
| **Min Chat** | 10 | Minimum chat count |
| **Min Msg** | 50 | Minimum message count |
| **Min Days** | any | Only cards at least N days old |
| **Max Days** | any | Only cards at most N days old (e.g. `7` = last week's releases) |
| **NSFW** | ✅ | Include NSFW cards |

Min/Max Days filter on card **creation date** and are applied server-side by the Chub API, so the whole discovery pool respects the range. Combine both for a window (e.g. Min 30 + Max 90 = cards created 1–3 months ago). Each card shows its age (🗓️) in the stats row.

- Click the **💎 Chub AI Gems** title to reset to defaults
- Click a **showcase category label** to search that topic
- Click **background tags** to search that tag
- Set all minimums to **0** for hidden gems discovery mode

## How Scoring Works

### Depth Signal
A card with 100 chats and 5,000 messages has a depth of 50 messages/chat. That means people are having real conversations, not just sending "hi" and leaving.

### Conversion Signal
Conversion = favorites / exposure, where **exposure = max(chats, downloads)**. A card downloaded far more than it is chatted with should not look like a runaway hit. For example, a card with 1,000 chats, 200 downloads, and 200 favorites has a 20% conversion rate (200 / max(1000, 200) = 200 / 1000 = 20%). One in five people who try it love it enough to favorite — that's a strong signal regardless of total popularity.

### Why Not Just Favorites?
A card with 10,000 favorites but 200,000 chats and 400,000 messages has:
- Depth: 2 msgs/chat (people bail immediately)
- Conversion: 5% (95% of people don't come back)

That's thumbnail bait. Gems ranks it lower than a card with 500 favorites, 2,000 chats, and 100,000 messages (depth 50, conversion 25%).

## Project Structure

```
chub_search_tool/
├── chub_search_tool.py   # Everything — server, API, frontend
├── requirements.txt      # flask, requests, gunicorn, waitress
├── run.sh                # Linux/Mac launcher
├── run.bat               # Windows launcher
└── README.md
```

## Configuration

All tunable constants are at the top of `chub_search_tool.py`:

```python
C_DEPTH = 20.0          # Bayesian smoothing strength for depth
PRIOR_DEPTH = 12.0      # Prior assumption for depth
C_CONV = 20.0           # Bayesian smoothing strength for conversion
PRIOR_CONV = 0.05       # Prior assumption for conversion (5%)
```

### Basic Auth (optional)

Protect the whole app (UI, API, RSS) behind HTTP Basic Auth via environment variables:

```bash
GEMS_AUTH_ENABLED=true \
GEMS_AUTH_USERNAME=me \
GEMS_AUTH_PASSWORD=secret \
bash run.sh
```

| Variable | Default | Description |
|---|---|---|
| `GEMS_AUTH_ENABLED` | `false` | Set to `true`/`1`/`yes`/`on` to require login |
| `GEMS_AUTH_USERNAME` | `admin` | Login username |
| `GEMS_AUTH_PASSWORD` | *(empty)* | Login password — must be set when auth is enabled, otherwise all requests are rejected |

When disabled (the default), the app behaves exactly as before.

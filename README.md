# FINN: a financial investment agent for a daily investor

FINN is a mobile-first triage tool for a 15-stock NSE portfolio. It tells you what changed and why you should care (push), and lets you dig into any company from its annual reports and concall transcripts (pull).

**The product idea:** an investor’s scarce skill is filtering. The home experience is a ranked feed that buries routine paperwork, not a chat window. Chat, voice and research are one tap away from any card.

> `finn.html` runs standalone with clearly labelled **sample data** so it opens anywhere. `pipeline.py` and `research.py` produce the live data it is designed to read. Wiring the page to `data/*.json` is the main step not yet done (see Limitations).

## Coverage of the brief

| Brief asks for | Where it lives |
|---|---|
| Portfolio of 15–20 NSE stocks, end-of-day price and volume | `pipeline.py: eod()` (yfinance, `.NS`); Watchlist tab |
| Corporate announcements from NSE/BSE, classified | `pipeline.py: fetch_filings(), classify()` (16 categories from the reference doc) |
| Fundamentals: annual reports and concalls from screener.in | `research.py: screener_docs(), pages()` |
| Push: windowed digest, user-chosen period, prioritised, routine collapsed | Feed tab: 24h/3d/7d chips plus a 6h–7d slider; “N routine filings” fold |
| Push: triggered alerts, configurable (volume ×, ±% move, announcement types) | Alerts tab sliders and chips, bell badge, alert inbox, live toast; `pipeline.py: alerts()` |
| Management change with a short bio of the incoming person | Feed card; `pipeline.py: bio()` |
| Results: Revenue, EBITDA, PBT, PAT, QoQ and YoY | Feed card; `extract_results(), deltas(), sane()` |
| Other types: judgment on what earns attention | `WEIGHT` table plus a one-line “why it matters” per card |
| Pull: business snapshot, financial snapshot, trajectory, balance sheet, cash quality, narrative vs numbers, guidance, bull vs bear | Research tab, nine sections (the eight plus Ask) |
| Worked example: last four quarters, promised vs delivered | Research → Guidance; `research.py: pair()` |
| Primary sources only on the pull side | Retrieval only over AR and concall pages; uncited claims dropped by `validate()`; Ask refuses when documents are silent |
| Works on any NSE ticker | `python research.py TICKER`; three demo companies, one outside the portfolio (DMART) |
| Interaction experiment | Feed-first triage, bottom-sheet stock view, voice ask, shareable morning brief |
| Efficiency | Model routing, rules before models, hash caching, table-page-only extraction |

## Features beyond the brief

- **Market-aware ranking.** A filing outranks others when its stock also moved unusually, so the feed answers “why did this move?”
- **Stock sheet.** Tap any stock for a chart (1W/1M/3M), volume vs average, your holding’s P&L and its recent filings.
- **Triage actions.** Pin, mark done (dims the card), jump from a filing to the stock or its research.
- **Brief.** One tap builds a five-line summary of what matters in your window, with copy.
- **Alert inbox and live toast.** Dragging a threshold tells you immediately how many alerts now trigger.
- **Holdings.** Value, overall and daily P&L, sector allocation bar.
- **Voice.** Speak a question in the Ask tab (browsers with speech recognition).
- **Cost panel (ⓘ).** Shows the model-routing design in the app itself.
- **Dark mode**, reduced-motion support, keyboard focus styles, safe-area padding for phones.

## Architecture

```
NSE announcements ──► classify (rules → cheap LLM only if unsure) ──┐
yfinance EOD ───────► volume multiple, % move ───────────────────────┤──► priority() ──► digest / alerts ──► finn.html
results PDF ────────► table pages only → cheap LLM → sane() check ───┤
management change ──► cheap LLM (+ web search for background) ───────┘

screener.in ─► AR + last 4 concalls ─► page text (cached) ─► BM25 retrieval per section
            ─► cheap model: business, financials, trajectory, balance sheet, cash
            ─► strong model: guidance, delivered, narrative vs numbers, bull/bear
            ─► validate(): drop uncited ─► data/research/TICKER.json   (Ask reuses the index)
```

## Priority score

`score = weight[category] × (0.5 + 0.5·e^(−age_h/48)) + 4 if unusual move + 3 if a type you flagged + 3 if a stock you watch`

Weights run from 10 (results) down to 0 (routine paperwork). Anything with weight ≤ 2 is collapsed. Pinned cards rank first in the page.

## Model routing and cost

| Step | Model tier | Why |
|---|---|---|
| Classification | Rules; small model only for leftovers | Most subjects are unambiguous |
| Results numbers | Small model on 1–3 table pages | Never send the whole PDF |
| Business, financials, trajectory, balance sheet, cash | Small model | Extraction, not judgment |
| Guidance, delivered, narrative vs numbers, bull/bear | Strong model | Needs comparison and judgment |
| Ask (chat) | Small model over five retrieved pages | Grounded and cheap |

Everything is cached by SHA-1 of the document URL or prompt text: filings, extractions, page text and research are never recomputed. Default models: `claude-haiku-5-5` and `claude-sonnet-5-5` (override with env vars; `llm.py` is the only file that touches a provider).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env           # add ANTHROPIC_API_KEY
python pipeline.py fetch       # prices + announcements -> data/
python pipeline.py digest --hours 72
python pipeline.py alerts --vol 2 --move 3
python research.py INFY        # any NSE ticker
python research.py INFY --ask "why did attrition rise?"
python -m pytest tests         # unit tests for rules, deltas, scoring, retrieval, validation
```

Open `finn.html` on a phone, or host it as a static page.

## Design decisions

- **Feed before chat.** Asking requires knowing what to ask. The feed does that work; chat handles follow-ups.
- **Rules before models.** Cheaper, faster, auditable; the model only handles what rules cannot.
- **Cite or drop.** An uncited claim is removed rather than shown. The Ask box says “not covered” rather than guessing.
- **Sanity checks on numbers.** Extracted results must satisfy PAT ≤ PBT ≤ EBITDA or they are rejected instead of displayed.
- **Cached demo.** The live data is fetched ahead of time so a demo never depends on an exchange endpoint being up.

## Limitations (honest list)

- `finn.html` is **not yet wired** to `data/*.json`; it ships with illustrative sample data, labelled as such.
- The pipeline scripts were unit-tested for logic but **not run against the live NSE or screener.in endpoints** in the build environment. Expect small fixes (NSE cookie handling, screener link labels).
- Results extraction assumes the filing’s PDF has text; scanned filings need OCR.
- Bio enrichment depends on what is publicly verifiable; it returns “unknown” rather than guessing.
- No real push notifications, authentication or per-user storage.

## Roadmap

Wire the page to the JSON outputs; web-push for alerts; extraction accuracy evals against hand-labelled results; XBRL parsing for results instead of PDFs; a watchlist-driven agent that builds research automatically when a material filing lands; portfolio-level risk views (shared sector or promoter-pledging exposure).

## Layout

`finn.html` UI · `pipeline.py` push side · `research.py` pull side · `llm.py` model routing · `tests/` unit tests · `.env.example` · `requirements.txt`

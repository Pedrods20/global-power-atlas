# PROJECT REFACTORING STATE

Last updated: 2026-09-13 · Repository: `global-power-atlas` · Replaces: `power-pulse-global`

## 1. Summary of Actions Completed

- [x] Audited the previous project (`power-pulse-global`) and concluded the architecture, not the bugs, was the problem: 13 live API calls inside a Supabase Edge Function with a 1-hour cache, no accumulated history, no analysis possible.
- [x] Started a clean repository. Supabase, the 1803-line Deno edge function and the React frontend are discarded entirely.
- [x] Built the Python package `gpa` with a `src/` layout, installed editable, running on Python 3.13.
- [x] `zones.py`: canonical zone registry with market timezone, civil timezone, currency and a real peak-block definition per market.
- [x] `calendar.py`: NERC holidays, daylight-saving-aware local days, block assignment. 23 tests.
- [x] `schema.py`: pandera contracts for the three fact tables, enforced at the boundary.
- [x] `store.py`: Parquet partitioned by zone and month, upsert on natural key, DuckDB views.
- [x] Four source adapters, all verified against live providers: Energy-Charts (DE-LU), ONS (BR-SIN), AEMO (AU-NSW1 price and load), OpenElectricity (AU-NSW1 generation). EIA (ERCOT) is written and tested but inert until a key is set.
- [x] `metrics/`: load profiles and duration curves, load factor, block prices, price duration curves, negative-price statistics, tail statistics, realised volatility, capture rates, generation mix, renewable share, dual-basis carbon intensity, spark/dark/clean spreads.
- [x] `pipeline.py` and `cli.py`: `gpa zones | ingest | backfill | validate | stats | export | query`.
- [x] Backfilled two years: **1,603,591 rows, 8.0 MB** across DE-LU, BR-SIN and AU-NSW1.
- [x] `export.py`: reduces the store to **172 KB** of site-ready aggregates.
- [x] Observable Framework site: five pages, builds clean, all links validated.
- [x] **103 tests passing**, ruff lint and format clean.
- [x] Three GitHub Actions workflows: CI, daily ingest with commit, Pages deploy.

## 2. Current Architecture Snapshot

```
GitHub Actions (daily cron)
   → Python ETL (gpa ingest)
   → schema validation (pandera)
   → Parquet, partitioned by zone and month, committed to the repo
   → gpa export → 172 KB of aggregates
   → Observable Framework build
   → GitHub Pages
```

```
global-power-atlas/
├── src/gpa/
│   ├── zones.py          canonical registry; everything derives from here
│   ├── calendar.py       NERC holidays, DST, peak/off-peak blocks
│   ├── schema.py         pandera contracts + canonical fuel taxonomy
│   ├── store.py          partitioned Parquet + DuckDB
│   ├── pipeline.py       ingestion orchestration, per-target outcomes
│   ├── export.py         store → site aggregates
│   ├── cli.py            gpa command
│   ├── sources/          base, energy_charts, ons, aemo, openelectricity, eia
│   └── metrics/          load, price, mix, spreads
├── data/curated/         committed Parquet, 8.0 MB, 2 years
├── site/                 Observable Framework (root configured to "site")
│   ├── index.md  prices.md  demand.md  supply.md  methodology.md
│   └── data/             exported aggregates, 172 KB
├── tests/                103 tests
└── .github/workflows/    ci.yml, ingest.yml, deploy.yml
```

**Coverage today.** DE-LU: price, load, generation. BR-SIN: load, generation. AU-NSW1: price, load, generation. ERCOT: pending an EIA key.

## 3. Pending Tasks & Roadmap

**Immediate, blocking first publication**
- [ ] Create the GitHub repository and push. `gh` CLI is not installed on this machine; create via the web UI or `winget install GitHub.cli`.
- [ ] Enable GitHub Pages with source set to "GitHub Actions".
- [ ] Register for a free EIA key at https://www.eia.gov/opendata/register.php, add it as the repository secret `EIA_API_KEY`, then run `gpa backfill --zone ERCOT --years 2`. This lights up North America.
- [ ] Verify the site renders in a browser. `npm run dev`, then open http://localhost:3000. The build passes and the exported column names were checked programmatically, but the pages have **not** been rendered in a browser yet.

**Next increments, each independently publishable**
- [ ] Request an ENTSO-E Transparency token by email, then write `sources/entsoe.py` and move DE-LU onto it. This unlocks every European bidding zone at once.
- [ ] Add zones: PJM and CAISO via the existing EIA adapter; FR, ES, IT, GB, NORD via ENTSO-E; the remaining four NEM regions via the existing AEMO and OpenElectricity adapters. Each is a registry entry, not new code.
- [ ] CCEE PLD for Brazil, which is the marquee Brazilian price and is currently absent.
- [ ] A gas, coal and EUA price feed, which is the only thing standing between the tested spread functions and a spread chart on the site.
- [ ] Asia: Japan via JEPX, or India via Grid-India.
- [ ] Screenshot in the README once the site is live.

## 4. Technical Constraints & Decisions

**Architecture**
- Supabase, Deno and React discarded. Static site with git-versioned data. No backend, no database server, no API key reachable from the browser.
- Parquet partitioned by zone and UTC month, queried with DuckDB. The UTC month is a file-layout choice only and never an analytical grouping.
- Writes are upserts on the natural key, because operators revise published figures.
- The site reads pre-computed aggregates from `gpa export`, not Observable data loaders. Loaders invoke an interpreter the framework chooses, which differs between Windows and Linux CI; an explicit step behaves identically on both.

**Domain rules, each enforced by a named test**
- Every instant is stored UTC-aware and interpreted through the market's timezone. Nothing is ever grouped by UTC calendar day.
- A local day has 23, 24 or 25 hours. Daily energy integrates power over each interval's real duration.
- Market time is not always civil time. AEMO settles on AEST year-round, so AU-NSW1 carries `Australia/Brisbane` as market time and `Australia/Sydney` as civil time.
- Peak and off-peak are market blocks. NERC on-peak is HE0700–HE2200, Mon–Sat, ex-holidays, and **includes Saturday**. NERC does **not** shift a Saturday holiday to the Friday. European peakload is 08:00–20:00 CET Mon–Fri, holidays included.
- Negative prices are preserved and counted. No log returns, because price goes negative. Volatility annualises on 365 days, not 252.
- Settlement resolution is measured from timestamp spacing, never assumed. AEMO stamps interval-**ending**, so the resolution is subtracted to get interval start.
- No currency conversion. Each market stays in its own currency; only normalised quantities are compared across markets.
- Carbon intensity is published on two clearly separated bases, and is **withheld** when under 80% of generation has a known emission factor. This is why Brazil has no carbon line: ONS publishes one unresolved aggregate thermal column.
- Missing values are null, never zero.

**Environment**
- Python 3.13.9, Node 24.14, git 2.53. No `uv`, no `gh` CLI.
- `tzdata` is a hard dependency: Windows ships no system timezone database, so `zoneinfo` fails without it.

**Known provider limits, encoded as `max_window_days` per adapter**
- OpenElectricity rejects windows over 32 days at hourly resolution with a 400.
- Energy-Charts has no documented cap but times out past roughly 60 days, and rate-limits a long backfill with 429s. The HTTP layer honours `Retry-After`.
- AEMO and ONS publish whole files per month and per year, so a longer window is strictly cheaper.

## 5. Next Prompt Instructions for the Next AI

> The project is `global-power-atlas` at `C:\Users\Pedro\Desktop\Python\global-power-atlas`. It is a Python ETL plus Observable Framework static site that publishes wholesale electricity market data for four continents, already working end to end with two years of history for Germany, Brazil and Australia.
>
> Read `STATE.md` and `site/methodology.md` first. The domain rules in section 4 of `STATE.md` are non-negotiable and each is enforced by a named test; do not relax one to make something pass.
>
> Set up the environment with `python -m venv .venv`, `.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Verify with `pytest -q` (expect 103 passing), `.venv\Scripts\gpa.exe stats` and `npm run build`.
>
> Your next task is: **[state the task]**.
>
> If the task is adding a market, add a `Zone` to `src/gpa/zones.py` and, only if the provider is new, an adapter in `src/gpa/sources/` following the `Source` protocol in `sources/base.py`. Adapters fetch, normalise timestamps to UTC interval-start, and map fuels onto `gpa.schema.FUELS`. They never analyse and never write to disk. Add a parsing test against a small recorded fixture in `tests/test_sources.py`.
>
> After any change to ingestion or metrics, run `gpa export` and commit `site/data`, or CI will fail its staleness check.

# Global Power Atlas

Supply, demand and price across four wholesale electricity markets, rebuilt from primary sources that need no credential and are independently verifiable, published as a static site.

[![CI](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ci.yml)
[![Deploy](https://github.com/Pedrods20/global-power-atlas/actions/workflows/deploy.yml/badge.svg)](https://github.com/Pedrods20/global-power-atlas/actions/workflows/deploy.yml)
[![Ingest](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ingest.yml/badge.svg)](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ingest.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Live site:** [Open dashboard](https://pedrods20.github.io/global-power-atlas/) · **Methodology:** [`site/methodology.md`](site/methodology.md) · **Forecast backtest:** [`site/forecast.md`](site/forecast.md)

| | |
|---|---|
| Zones | 4: Germany-Luxembourg, France, Spain, Brazil (SIN) |
| Sources | 2: Energy-Charts, ONS — neither needs a credential |
| History | Two years, extended daily by a scheduled job |
| Stack | Python, Polars, DuckDB, Parquet, Observable Framework |
| Infrastructure | None. No backend, no database server, no browser-visible credential |

---

## What this is

A reproducible pipeline that collects hourly load, generation by fuel, and clearing prices from the system operators themselves, normalises them into one comparable model, and publishes the result. There is no backend, no database server and no API key in the browser. A scheduled job writes Parquet into this repository, and a static site builds from those files.

The point of the project is not that it draws charts. It is that the numbers underneath the charts are defensible. Wholesale power data is full of traps that generic time-series tooling walks straight into, and the [methodology page](site/methodology.md) documents the conventions and limitations.

**Scope reduced, 2026-09-13.** A [repository-wide audit](docs/REVIEW-2026-09-13.md)
found a resolution-transition defect in the European price history, among other
issues. Rather than patch each source in place, every market whose only source
needed a credential, blocked automated clients, or depended on a hand-imported
CSV was removed from the registry: ERCOT, PJM, CAISO, AU-NSW1, JP-TOKYO and the
two CCEE Brazilian PLD submarkets. What remains — Germany-Luxembourg, France,
Spain and Brazil's national system — comes from two keyless sources, was rebuilt
from cached provider responses, and was checked against a second, independent
publisher for each series (SMARD, OMIE and the ONS hourly balance). Zero
mismatches were found in the rebuilt price history. See "Why four zones" below
and [`TODO.md`](TODO.md) for what was dropped and what a return to those markets
would require.

![Dashboard preview](docs/screenshots/dashboard-1440.png)

## Coverage

| Zone | Market | Operator | Source | Key required |
|---|---|---|---|---|
| `DE-LU` | Germany-Luxembourg | 50Hertz, Amprion, TenneT, TransnetBW | Energy-Charts | no |
| `FR` | France | RTE | Energy-Charts | no |
| `ES` | Spain | Red Eléctrica | Energy-Charts | no |
| `BR-SIN` | Brazil National Interconnected System | ONS | ONS open data | no |

Coverage is dataset-specific. DE-LU, FR and ES each carry price, load and generation. BR-SIN carries load and generation only: Brazilian PLD is set per submarket by CCEE, which blocks automated clients, and is not currently registered.

**Providers do not publish at the same speed.** Most zones here sit within a few hours of real time. Brazilian generation is the exception: the ONS hourly balance trails real time by about two days, and no faster ONS source for generation by technology exists. Brazilian generation, fuel mix and renewable share therefore end about two days before every other market on the site. Brazilian load does not share that lag, because it comes from the ONS verified-load API instead and stays within about an hour. The [methodology page](site/methodology.md) sets out both, along with two ways that API fails silently if read carelessly.

## Why four zones

Every dropped market failed one of two tests: it needed a credential this
project will not ask a reader to obtain (EIA, PJM Data Miner), or it could not
be checked against a source independent of the one already stored (CCEE
blocked automated download entirely; AEMO, OpenElectricity and JEPX had no
second publisher on hand to compare against). Keeping them would have meant
either an inconsistent reproduction story — some zones need a free signup,
most don't — or trusting a single provider's own self-consistency, which is
exactly what let the resolution-transition defect stand undetected. Four zones
that are both keyless and cross-checked is a stronger claim than eleven zones
of uneven provenance. `scripts/audit_external.py` is the check that backs the
claim, and it is designed to be re-run, not taken on faith.

## Analytical questions

- How do on-peak and off-peak prices differ under each zone's block definition?
- What fraction of observed time clears below zero, and how long do negative-price runs last?
- How do generation-weighted solar and wind revenues compare with baseload prices?
- How do load shape, generation mix and renewable share vary by market?
- What do historical German clean spark and dark screening spreads show after benchmark fuel and EUA costs?
- Can a fitted model beat "the same hour last week" at forecasting tomorrow's day-ahead price, and in which hours does it fail to?

The site reports observed data, not causal attribution. A negative-price episode does not by itself prove curtailment, and a falling block spread does not isolate the effect of solar. These hypotheses need additional dispatch, outage and constraint data.

The initial store covers approximately September 2024 to September 2026. The first and last calendar years are partial; provider gaps and different settlement resolutions also matter. Compare matched date ranges before making annual trend claims. Carbon intensity is an estimate from technology factors, withheld below 95% known-factor coverage; Brazil's unresolved thermal category currently prevents publication.

## What the data shows

Computed by this repository from the sources above, reproducible with the
commands further down.

**Germany's on-peak/off-peak spread has collapsed and gone negative as solar
was added.** European peakload runs 08:00-20:00 CET on weekdays, which is
where the solar midday sits.

| Year | On-peak | Off-peak | Spread |
|---|---|---|---|
| 2024 | 122.83 | 82.16 | +40.67 |
| 2025 | 92.35 | 87.64 | +4.71 |
| 2026 (through September) | 95.98 | 109.76 | −13.78 |

EUR/MWh. Over the full two years, 6.2 percent of DE-LU day-ahead hours cleared
below zero (5.9 for France, 7.0 for Spain). Generation-weighted solar captured
0.53 of the time-weighted average price in Germany against 0.88 for wind
(France: 0.58 solar / 0.89 wind; Spain: 0.55 solar / 0.93 wind), which is the
mechanism in a single number: solar produces when its own output has made the
market cheap, and wind largely does not. The
[prices](https://pedrods20.github.io/global-power-atlas/prices) and
[supply](https://pedrods20.github.io/global-power-atlas/supply) pages put the
three side by side.

**Ridge beats the naive baselines in the local retrospective benchmark;
LightGBM loses to them in the tails.** Over 374 calendar days, from 2025-09-04
to 2026-09-12, all five models are scored on the same 8,971 clock-hour cells of
DE-LU day-ahead price, using the store as rebuilt and independently checked
after the resolution-transition fix (see "Scope reduced" above — this figure
changed from the last published run precisely because the inputs did).
Daily-refitted ridge reaches a mean absolute error of 21.04 EUR/MWh against
28.80 for the best naive baseline, a skill of 27.0 percent. The fixed LightGBM
challenger reaches 22.48 EUR/MWh and loses to the best naive baseline by 8.5
percent on negative hours and 11.8 percent on scarce hours. Ridge's breakdown:

| Bucket | MAE | Skill over the best baseline | Bias |
|---|---|---|---|
| Off-peak | 16.74 | +33.7% | −0.7 |
| On-peak | 28.77 | +16.7% | −2.8 |
| Negative hours | 37.58 | +6.5% | −30.6 |
| Scarce hours (top 5%) | 58.46 | +12.7% | +53.3 |

EUR/MWh; bias is realised price minus forecast. The model shrinks towards the
middle: it forecasts negative hours too high and scarce hours too low. The
[local forecasting page](site/forecast.md) reports these failures together with
pinball loss, interval coverage and interval width calculated before rounding.
This is previously inspected development history using the providers' latest
revisions, not an untouched holdout or a record of forecasts issued in advance.
Only complete observed hours enter; the repeated autumn clock hour is averaged
into one cell. Publication of this local increment remains pending.

## Why the numbers are trustworthy

These are the decisions that separate this from a dashboard that merely renders:

- **Market time, not UTC days.** Every instant is stored UTC-aware and interpreted through the zone's market timezone. Nothing is ever grouped by UTC calendar day, which would smear a European or Brazilian trading day across two UTC dates.
- **A local day has 23, 24 or 25 hours.** Daily means divide by the hours that existed, not by 24. Germany, France and Spain all observe European daylight saving; Brazil has not since 2019.
- **Peak and off-peak are market blocks.** European peakload is 08:00 to 20:00 CET, Monday through Friday, holidays not adjusted for. It is not the daily maximum and minimum.
- **Negative prices are preserved and counted.** They are economically meaningful observations, so nothing filters them out. Because price can be zero or negative, log returns are undefined, and this project uses arithmetic differences instead. Volatility annualises at 365 days, not the 252 trading days of a financial exchange, because spot power settles every day of the year.
- **MW and MWh are different units.** Power is integrated over the interval's real duration. Nothing assumes a 60-minute interval.
- **Every table is validated at the boundary.** Schema contracts in [`src/gpa/schema.py`](src/gpa/schema.py) reject a source that changed shape, naming the offending column, before anything reaches storage.

Brazil is a deliberate caveat rather than a silent gap. It is included for supply and demand, and its peak block is labelled as a distribution-tariff construct, not a traded product, because Brazil has settled a genuinely hourly PLD since 2021.

## Architecture

```
GitHub Actions (daily cron)
        │
        ▼
   Python ETL  ──fetch──►  Energy-Charts · ONS
        │
        │  validate against schema contracts
        ▼
   Parquet, partitioned by zone and month, committed to this repo
        │
        ▼
   Observable Framework (static build)
        │
        ▼
   GitHub Pages
```

CI runs lint, types, tests, `gpa export --check`, the site build and the
browser smoke test on every push; only a fully green run on `main` triggers
deploy, and it publishes the exact commit that passed, not whatever `main`
later moves to. The daily ingest job commits new observations first, checks
freshness second so a quiet provider cannot discard a good day from every
other market, and then triggers deploy itself with the commit it just made —
independent of whether the freshness check passed.

History accumulates in git rather than expiring from a cache, so any past state of the dataset is recoverable with `git checkout`, and backtesting is the natural use case rather than an impossible one.

## Repository layout

```
src/gpa/           Python package
  zones.py         canonical zone registry; everything derives from here
  calendar.py      market calendars, DST, peak/off-peak blocks
  schema.py        schema contracts for the three fact tables
  store.py         partitioned Parquet store and DuckDB queries
  sources/         one adapter per upstream provider
  metrics/         load, price, mix and spread analytics
  forecast/        day-ahead price forecasting and its walk-forward harness
    panel.py       the target, and only what was knowable before it
    models.py      naive baselines and a per-hour ridge regression
    boosting.py    fixed pooled LightGBM challenger
    backtest.py    expanding-origin backtest, refitted at every step
    scoring.py     MAE, RMSE and pinball, split by block and by regime
    linalg.py      Cholesky ridge solver, tested against a NumPy oracle
    tracking.py    optional local MLflow metrics, inputs and source snapshots
  freshness.py     per-series staleness rules and the alerting check
  export.py        builds the aggregated tables the static site reads
  benchmarks.py    official fuel, carbon and FX references
  cli.py           gpa backfill | ingest | validate | stats | freshness |
                   export | backtest | query
data/curated/      committed interval Parquet, partitioned by zone and month
data/reference/    committed monthly fuel, EUA and FX references
site/              Observable Framework site
tests/             contract, calendar and metric tests
```

## Reproduce it

Requires Python 3.11 or newer and Node 18 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use source .venv/bin/activate elsewhere
pip install -e ".[dev]"

gpa zones                      # list the registry
gpa backfill --days 30         # fill data/curated from keyless sources
gpa validate                   # check every Parquet file against its contract
gpa freshness                  # measure each series against its staleness rule
gpa backtest --scope regime    # walk-forward price forecast and its scoreboard
pytest

npm ci
gpa export
npm run build
npm run dev
```

With the preview running, use `npm run test:browser` in another terminal.
It checks the home page and every configured navigation page at 1440 px and
390 px for HTTP failures, runtime errors, horizontal overflow and visible
charts (except the text-only methodology page). `GPA_TEST_URL` overrides the
preview URL; `GPA_SCREENSHOT_DIR` overrides `docs/screenshots` for dashboard
captures.

No credential and no manual import step is needed for any registered zone.
`scripts/audit_external.py` additionally cross-checks the store against SMARD,
OMIE and the ONS hourly balance; see "Rebuilding the record" in
[`site/methodology.md`](site/methodology.md).

## Data sources and licensing

| Source | Provider | Terms |
|---|---|---|
| [Energy-Charts](https://api.energy-charts.info/) | Fraunhofer ISE | CC BY 4.0 |
| [ONS Open Data](https://dados.ons.org.br/) | Operador Nacional do Sistema Eletrico | CC BY 4.0 |
| [World Bank Pink Sheet](https://www.worldbank.org/en/research/commodity-markets) | World Bank | CC BY 4.0 |
| [EEX EU ETS auctions](https://www.eex.com/en/markets/environmentals/eu-ets1-eu-ets2-auctions/eu-ets1-auctions) | European Energy Exchange | provider terms |
| [ECB exchange rates](https://data.ecb.europa.eu/data/datasets/EXR) | European Central Bank | ECB data terms |

Attribution for each series appears on the methodology page alongside the retrieval date. [SMARD](https://www.smard.de/) and [OMIE](https://www.omie.es/) are used only by the independent audit script above, not by the published site.

## Project status

The data platform is built and running. What follows is analytical depth on top
of it.

| Front | State |
|---|---|
| Ingestion, validation, storage | Done. Two adapters, four zones, daily scheduled refresh, deploy gated on CI. |
| Market metrics and site | Seven pages in the local build, including forecasting; blocks, duration curves, negative prices, capture rates, carbon intensity and thermal spreads. |
| Engineering quality bar | Done. mypy strict across all source files and a 75 percent coverage floor, both enforced by CI. |
| Operations and alerting | Done. Per-series freshness rules; a stalled feed fails the scheduled run and opens an issue; deploy is called by CI and by a successful ingest commit, not by an independent push trigger. |
| Data provenance | Done for the four registered zones. Rebuilt from cached provider responses and checked against SMARD, OMIE and the ONS hourly balance; zero price mismatches found. |
| Short-term price forecasting | Locally validated retrospective benchmark on DE-LU: three naive baselines, ridge, LightGBM, daily refits, block/regime errors and empirical intervals. Optional local MLflow tracking. Publication and prospective validation remain pending. |
| Regulatory retrieval | Planned after Front C, so its contribution can be measured against the harness. |

Known gaps are stated rather than hidden. Seven zones (ERCOT, PJM, CAISO,
AU-NSW1, JP-TOKYO and the two CCEE Brazilian submarkets) were removed from the
registry rather than kept half-verified; returning any of them needs either a
credential this project chooses not to require, or a second independent source
to check against. Brazilian PLD is not registered for the same reason. The
local forecast runs on one market and without the operators' own day-ahead
wind, solar and load forecasts, which are the strongest inputs for the day
being predicted and which this project does not store; substituting the
realised values would be leakage, so the model works without them and says so.
Brazilian generation trails real time by about two days, which is the
provider's lag. Everything here is a bidding zone or a national system, so
nothing measures nodal congestion. The full list lives in [`TODO.md`](TODO.md)
and the conventions behind every figure in
[`site/methodology.md`](site/methodology.md).

Orchestration deliberately stays on GitHub Actions rather than Airflow. A
scheduler, metadata database and webserver would buy nothing this pipeline needs
and would cost the property that anyone can clone this repository and reproduce
the whole thing with no infrastructure.

Publication and a separately recorded prospective evaluation remain open for
the forecasting work; the retrospective scores do not establish future
performance, and they already changed once when the underlying price history
was corrected, which is the point of not treating a retrospective score as
final.

## License

MIT for the code. The underlying data remains under the terms of its provider.

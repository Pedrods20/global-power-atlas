# Roadmap

Steps 1-6 of the original delivery are complete and recorded in
[`STATE.md`](STATE.md). The site is live at
<https://pedrods20.github.io/global-power-atlas/> with 13 zones, five
continents and 2,380,416 stored observations.

This file now tracks **what is still missing**. Items are ordered by how much
they affect the credibility of the published work, not by effort. Nothing here
is started.

---

## P1 - Coverage gaps visible on the published site today

These are the gaps a reader notices first, because they make comparison charts
show truncated series next to complete ones.

- [ ] **Backfill France and Spain to two years.** Both hold 4 monthly
  partitions from 2026-06-14, against 25 for every other Energy-Charts zone.
  Every cross-market chart currently shows two stub series.
  `gpa backfill -z FR -z ES --years 2 --chunk-days 60`. Energy-Charts
  rate-limits long backfills, so expect 429s; the HTTP layer honours
  `Retry-After` but the run is slow. **Done when** both zones report 25
  partitions in `gpa stats` and the price/demand charts show full history.

- [ ] **Backfill CCEE PLD for 2024 and 2025.** All four submarkets hold only
  2026 (9 partitions from 2026-01-01). PLD is the headline Brazilian price and
  it is the shortest series on the site. Requires the official
  `pld_horario_2024.csv` and `pld_horario_2025.csv` placed under
  `data/raw/ccee/`, then re-run the import with `GPA_CCEE_IMPORT_DIR` set.
  **Done when** each of BR-SECO, BR-S, BR-NE and BR-N covers the same window as
  DE-LU, with no gaps, duplicates or nulls.

- [ ] **Diagnose the BR-SIN freshness lag.** ONS load and generation sit at
  roughly 46 hours old while every other zone is between 5 and 21 hours. Decide
  whether this is genuine ONS publication lag or a silent adapter failure, and
  record the answer in the methodology. **Done when** the cause is stated in
  writing and, if it is an adapter problem, fixed with a regression test.

## P2 - Analytical asymmetries

The three largest US markets carry no price, which removes the metrics that
matter most in exactly the systems where gas sets the margin.

- [ ] **Add a wholesale price source for ERCOT, PJM and CAISO.** EIA-930
  publishes balancing-authority load and generation but no hub or nodal price,
  so these three zones have no price duration curve, no block spread, no
  capture rate and no spark spread. Evaluate GridStatus (free key), each ISO's
  own public settlement-point or day-ahead LMP reports, or CAISO OASIS.
  **Done when** at least ERCOT hub price is stored and its duration curve and
  block spread render alongside DE-LU.

- [ ] **Extend thermal spreads beyond Europe.** `europe_spreads.parquet` covers
  24 months for Germany only. Once US price exists, add spark and dark spreads
  for ERCOT and PJM against a Henry Hub gas reference, keeping the efficiency
  and emissions assumptions as visible as the European ones already are.

- [ ] **Add load and generation for JP-TOKYO.** The zone holds price only, so
  Asia contributes nothing to the demand, supply or carbon pages. OCCTO
  publishes area demand; TEPCO publishes its own area records. **Done when**
  Tokyo appears on the demand and supply pages.

## P3 - Operations

- [ ] **Resolve or formalise the CCEE refresh.** CCEE returns HTTP 403 to
  automated clients, so its four zones are excluded from the daily cron and
  drift silently between manual imports. Either find a supported access path,
  or add an explicit staleness banner on the site so a reader can see when PLD
  was last refreshed.

- [ ] **Alert on ingestion failure.** The daily workflow can fail, or a single
  zone can go stale, with nothing surfacing it. Add a failure notification, or
  a freshness check that fails the run when any scheduled zone exceeds an
  agreed age.

- [ ] **Add CI and deploy badges to the README.** The workflows pass but a
  reader cannot see that without opening the Actions tab.

## P4 - Depth a senior reader would look for next

None of these exist yet. Each is a self-contained increment on data already
stored.

- [ ] **Residual (net) load and its duration curve.** Demand minus wind and
  solar is the series that actually sizes flexibility and drives the duck
  curve. Every input is already in the store; no new source is needed.

- [ ] **Ramp analysis.** Hourly and sub-hourly ramp rates, and the annual
  worst-case ramp, which is what dimensions flexible capacity. Australia's
  five-minute data makes this genuinely interesting.

- [ ] **Storage arbitrage value.** Perfect-foresight daily spread capture for a
  one-hour and four-hour battery, per market. Directly answers what storage
  would have earned on the stored history.

- [ ] **Nodal or locational coverage.** Everything is currently hub or zonal,
  so congestion and basis are invisible. Documented as a limitation today;
  CAISO OASIS LMP would be the natural first step.

- [ ] **Forecasting and backtesting.** Two years of validated history now
  exists, which is the substrate the previous architecture could never provide.
  A day-ahead price or load baseline with an honest walk-forward backtest and
  error metrics would use it.

---

## Constraints that must survive any future work

- Missing observations stay missing. Never synthesize provider data.
- External credential and provider restrictions stay documented as pending.
- After any change to ingestion or metrics, regenerate and commit `site/data`,
  or CI fails its freshness check.
- The domain rules in `STATE.md` are enforced by named tests. Do not relax a
  test to make a change pass.

## Commands

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\gpa.exe validate
.\.venv\Scripts\gpa.exe stats
.\.venv\Scripts\gpa.exe export
.\.venv\Scripts\gpa.exe export --check
npm run build
node scripts/browser-smoke.mjs
```

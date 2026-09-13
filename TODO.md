# Roadmap

Steps 1-6 of the original delivery are complete and recorded in
[`STATE.md`](STATE.md). The site is live at
<https://pedrods20.github.io/global-power-atlas/> with 11 zones across five
continents.

This file tracks **what is still missing**. Items are ordered by how much they
affect the credibility of the published work, not by effort.

The P1 block was cleared in the session of 2026-09-12; see
"Recently completed" at the foot of this file for what changed and what it
revealed.

---

## P1 - Coverage gaps visible on the published site today

All three are done. They are kept here with their outcomes because the third
one changed where Brazilian load comes from.

- [x] **Backfill France and Spain to two years.** Both now hold 25 monthly
  partitions instead of 4, matching every other Energy-Charts zone. France
  carries 42,473 price, 35,844 load and 365,899 generation observations.

- [x] **Backfill CCEE PLD for 2024 and 2025.** Southeast/Central-West and
  Northeast now hold 23,661 contiguous hourly observations each, spanning
  2024-01-01 to date, with no gaps, duplicates or nulls. The official CSVs live
  under `data/raw/ccee/` and are read through `GPA_CCEE_IMPORT_DIR`; the
  provider still refuses automated download.

- [x] **Diagnose the BR-SIN freshness lag.** It was both a provider lag and a
  correctable sourcing choice, so the answer is split.

  *Generation* is genuine ONS lag. The hourly subsystem balance is republished
  several times a day, but its contents trail real time by about two days, and
  no faster ONS source for generation by technology exists. Documented under
  "Publication lag" in the methodology rather than treated as a defect.

  *Load* was correctable and is now fixed. It comes from the ONS verified-load
  API, which carries the same two years at half-hourly resolution and stays
  within about an hour of real time. The lag fell from 46 hours to 0.4 hours,
  and the series was rebuilt from scratch because the resolution changed from
  60 to 30 minutes: 35,040 observations, exactly 730 days at 48 per day.

  Two traps in that API are now covered by named tests. Its own `SIN` aggregate
  answers with every value zeroed, so national load is summed from the four
  submarket areas and a timestamp is only kept when all four reported. And the
  Southeast area code is `SECO`; the older `SE` returns an empty list rather
  than an error, which would silently drop about a third of national demand.

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

## Scope decisions

- **Brazilian South and North submarkets are not registered.** CCEE publishes
  PLD for all four, but only Southeast/Central-West and Northeast are carried
  here. They are the two that dominate Brazilian price formation, and the other
  two added two more series to every chart without changing the reading. This
  was the user's call on 2026-09-12; their curated partitions were deleted.

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

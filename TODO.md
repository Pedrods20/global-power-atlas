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

## Sprint 1 - Enforce the quality bar before the codebase triples

Worked and completed 2026-09-13.

Outcome in one line: mypy is clean across all 23 source files and enforced by
CI, and coverage went from 53 to 76 percent with the orchestration at 100.

### S1.1 - Type the two expansion adapters  [x]

- [x] `jepx.py` and `ccee.py` fully annotated, matching the `Source` protocol.
- [x] Parse helpers documented and typed, following `ons.py` as the reference.
- [x] All seven adapters now annotate `name`, `datasets` and `max_window_days`
      explicitly. This was the real cause of the protocol failure: without an
      annotation mypy infers the literal's type, `None` or `int` or
      `tuple[str, str]`, and Protocol attributes are invariant, so none of them
      satisfied the declared `int | None`. Clearing it fixed 8 of 17 errors.

### S1.2 - Clear the remaining type errors  [x]

- [x] `types-openpyxl` added to the dev extra, so `benchmarks.py` is checkable
      without weakening the global mypy settings.
- [x] `export.py` now declares an `Overview` TypedDict for the shape of
      `zones.json`, so indexing it no longer widens to `object`.
- [x] `data_as_of` is narrowed with `isinstance` rather than cast. polars types
      `.max()` as a broad union; an unexpected dtype should read as unknown
      instead of crashing on `.isoformat()` during the site build.

### S1.3 - Test the orchestration  [x]

- [x] `tests/test_pipeline.py`, 19 tests. `pipeline.py` went from 0 to **100
      percent**. Covers every outcome path, the guarantee that one failing zone
      never stops the others, source window caps overriding the caller's chunk,
      chunks tiling the window without gaps or overlap, dry run, and rejection
      of naive or inverted windows.
- [x] `tests/test_cli.py`, 16 tests. `cli.py` went from 0 to **95 percent**.
      Exit codes are asserted because the workflows read them: `validate` must
      exit non-zero on a corrupt partition, and `export --check` on stale site
      tables.
- [x] `tests/test_http.py`, 22 tests. `sources/base.py` went from 49 to **88
      percent**. Covers retry, the much longer 429 schedule, `Retry-After`,
      the backoff cap, and credential handling, all against an
      `httpx.MockTransport` with sleep patched out.

**Coverage floor is global, not per-module, and that is deliberate.** The
original criterion said no module under 60 percent. Six adapters still sit
below it, and the honest reason is that what remains uncovered in them is the
sequence of HTTP calls inside `fetch`. Their testable logic, parsing, fuel
mapping and timezone conversion, is extracted into pure functions that fixtures
already cover, and the retry behaviour they all share is now tested once in
`base.py`. Mocking seven providers to exercise the call sequence would buy very
little. The gate is therefore 75 percent overall. Raise it when it becomes easy
to; never lower it to make a change pass.

### S1.4 - Make CI enforce what pyproject declares  [x]

- [x] `mypy` added to the CI python job.
- [x] `pytest -q --cov=gpa --cov-fail-under=75` added.
- [x] Both proven to fail: a deliberate type error exits 1, an unreachable
      coverage floor exits 1, and both exit 0 once restored.

### S1.5 - Publish  [x]

- [x] Pushed as `d884bb5`. CI and Deploy both completed successfully, which is
      the first time mypy and the coverage gate ran on the Linux runner rather
      than only on the Windows workstation.

**Sprint 1 is complete.** Agree the next scope with the user before starting
anything: P2 is the missing US wholesale price, Front C is forecasting, Front B
is retrieval after it.

## P2 - Analytical asymmetries

California is done. ERCOT and PJM are blocked on access, not on code.

- [x] **Californian day-ahead price via CAISO OASIS.** 17,520 contiguous hourly
  observations over two years at the SP15 trading hub, no gaps. CAISO is the
  one large US market whose price is reachable without a credential.

  Three OASIS behaviours are now handled and each has a named test, because all
  three fail quietly: an LMP is five components and only the total is a price;
  an empty window returns XML with error code 1000 inside a 200 response, which
  a CSV parser reads as a table whose only column is the XML declaration; and
  rate limiting also returns 200, carrying HTML rather than a 429, so the
  shared retry layer cannot see it. The window cap is 30 rather than 31,
  because OASIS counts calendar days touched, so a 31-day window starting
  mid-afternoon spans 32 and is rejected with error 1004.

  What it shows: the NERC on-peak block has cleared below off-peak at SP15 for
  three consecutive years, 12.04 percent of day-ahead hours are negative, and
  solar captures 0.603 of the average price against 0.971 for wind.

- [ ] **ERCOT price.** Blocked, not unimplemented. Every ERCOT host returns
  HTTP 403 to automated clients: `api.ercot.com`, `www.ercot.com` and
  `data.ercot.com` all sit behind the same bot protection, and `mis.ercot.com`
  fails the TLS handshake. The legitimate route is to register for an ERCOT API
  account and use the issued credential; do not attempt to defeat the block.
  Once a credential exists this is an adapter following `caiso.py`.

- [ ] **PJM price.** Needs a free Data Miner 2 subscription key, registered at
  `dataminer2.pjm.com`. `api.pjm.com` answers 401 without one. Same shape of
  work as ERCOT once the key exists.

- [ ] **Extend thermal spreads beyond Europe.** `europe_spreads.parquet` covers
  24 months for Germany only. CAISO price now exists, so a Californian spark
  spread is possible against a gas reference, though Henry Hub is a poor basis
  for California and SoCal Border would be the honest choice.

- [ ] **Add load and generation for JP-TOKYO.** The zone holds price only, so
  Asia contributes nothing to the demand, supply or carbon pages. OCCTO
  publishes area demand; TEPCO publishes its own area records. **Done when**
  Tokyo appears on the demand and supply pages.

## P3 - Operations

- [x] **Alert on ingestion failure.** A run that fetched nothing used to exit
  zero as long as no adapter raised, so a provider could go dark for a week
  while the site served stale numbers as current. `gpa freshness` now measures
  every declared series against a per-series rule and the scheduled workflow
  fails on a breach, opening an issue labelled `ingest-failure` with the log
  link. Repeated failures comment on the open issue instead of filing a new one
  each night.

  The check runs *after* the commit, deliberately: whatever was fetched
  successfully should be persisted even when one feed is quiet, rather than a
  single stale provider discarding a whole day from every other market.

  Rules are per zone and dataset because the providers differ legitimately.
  Brazilian generation is allowed 96 hours because ONS trails by two days; US
  generation 48 because EIA restates on a day's delay; everything else 36. Each
  rule carries its reason, and a test asserts none is left unexplained.

- [x] **Formalise the CCEE refresh.** The two PLD zones are declared manual and
  allowed 30 days. They report staleness but never fail the run, since nobody
  can fix them from a cron job and nightly failures would train people to
  ignore the alarm. Their age is now visible on the front page, alongside every
  other series measured against its own limit, so a reader sees the drift
  rather than trusting a stale number.

- [x] **Add CI and deploy badges to the README.**

## Front C - Short-term price forecasting (next after Sprint 1)

Two years of validated hourly history exist, which is the substrate the
previous architecture could never provide. This is the front that turns a
well-built data platform into evidence of analytical capability.

Sequenced before the retrieval layer on purpose: the evaluation harness built
here is what will later decide whether regulatory signals actually improve
anything, rather than being assumed to.

- [ ] **Naive baselines first, and publish them.** Previous day, previous week
  same hour, and a seasonal-naive variant. Every later model is judged against
  these. A forecast that cannot beat "same hour last week" is not a forecast.
- [ ] **Walk-forward backtest, never a random split.** Time-series data leaks
  through a shuffled split. Expanding or rolling origin, refit at each step,
  and no feature that would not have been known at prediction time.
- [ ] **Report the error metrics that suit power prices.** MAE and RMSE plus a
  pinball loss if any quantile output is produced. Report them by block and by
  regime separately: aggregate error hides the fact that the interesting hours
  are the scarce and the negative ones.
- [ ] **State the target precisely.** Day-ahead hourly price for one zone to
  start, most likely DE-LU given its depth and clean history. Say which
  information set is available at forecast time.
- [ ] **Publish failure honestly.** If the model loses to a naive baseline in
  some regime, the site says so. A backtest that only shows wins is not a
  backtest.

## Front B - Regulatory retrieval (after Front C)

Indexing normative and market documents from ANEEL, ONS and CCEE to extract
signals no price series carries. Genuinely differentiated; almost no portfolio
has it.

Deliberately sequenced after forecasting so its value can be measured rather
than asserted.

- [ ] **Define the question it answers before building it.** A retrieval layer
  that produces plausible prose but no measurable feature is decoration.
- [ ] **Cite sources with document and date.** An answer without provenance is
  unusable in this domain.
- [ ] **Measure it against the Front C baseline.** Ship it only if the
  regulatory features move a backtested error metric.

## P4 - Other depth a senior reader would look for

None of these exist yet. Each is a self-contained increment on data already
stored.

- [ ] **Residual (net) load and its duration curve.** Demand minus wind and
  solar is the series that actually sizes flexibility and drives the duck
  curve. Every input is already in the store; no new source is needed. This is
  also a strong feature for Front C.

- [ ] **Ramp analysis.** Hourly and sub-hourly ramp rates, and the annual
  worst-case ramp, which is what dimensions flexible capacity. Australia's
  five-minute data makes this genuinely interesting.

- [ ] **Storage arbitrage value.** Perfect-foresight daily spread capture for a
  one-hour and four-hour battery, per market. Directly answers what storage
  would have earned on the stored history.

- [ ] **Nodal or locational coverage.** Everything is currently hub or zonal,
  so congestion and basis are invisible. Documented as a limitation today;
  CAISO OASIS LMP would be the natural first step.

---

## Scope decisions

- **No Airflow.** Orchestration stays on GitHub Actions cron. Airflow needs a
  scheduler, a metadata database and a webserver, none of which fit the free
  Actions runner, and adopting it would cost the property that anyone can clone
  this repository and reproduce the whole pipeline with no infrastructure. That
  reproducibility is the strongest thing the project has. Decided 2026-09-13.
- **Machine learning before retrieval.** Two years of validated hourly history
  already exist, so a day-ahead baseline with an honest walk-forward backtest
  can be built now and will establish the evaluation harness. A regulatory
  retrieval layer comes afterwards and is judged by whether it measurably
  improves that baseline. Decided 2026-09-13.

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

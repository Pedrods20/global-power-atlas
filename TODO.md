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

## Sprint 1 (active) - Enforce the quality bar before the codebase triples

Audited on 2026-09-13. The domain logic is well tested; the orchestration and
the modules added during the expansion are not. Machine learning and a
retrieval layer are next, and both will add a lot of code. Locking the bar now
means each new front is born under the rule instead of inheriting the debt.

Acceptance for the sprint as a whole: `mypy` passes with zero errors, `pytest`
reports no module under 60 percent, CI enforces both, and `origin/main` is up
to date.

### S1.1 - Type the two expansion adapters

`jepx.py` and `ccee.py` were written without type annotations while every
other adapter has them. Their untyped `fetch` is also why
`sources/__init__.py:39` fails the `Source` protocol check, so this one task
clears 8 of the 17 errors.

- [ ] Annotate every function in `src/gpa/sources/jepx.py` (lines 21, 35, 46).
- [ ] Annotate every function in `src/gpa/sources/ccee.py` (lines 23, 31, 52, 65).
- [ ] `fetch` must match the `Source` protocol in `sources/base.py` exactly:
      `(self, zone: Zone, dataset: str, start: dt.datetime, end: dt.datetime) -> pl.DataFrame`.
- [ ] Module-level parse helpers return `pl.DataFrame` and take `str` input,
      matching `_parse_balance` in `ons.py` as the reference style.

**Done when** `mypy` reports no error in `jepx.py`, `ccee.py` or
`sources/__init__.py`.

### S1.2 - Clear the remaining type errors

- [ ] `benchmarks.py:18` - `openpyxl` ships no stubs. Add `types-openpyxl` to
      the dev extra, or a targeted `# type: ignore[import-untyped]` with a
      comment saying why. Do not weaken the global mypy settings.
- [ ] `export.py:87` - `len()` on a value typed `object`. Narrow the type at
      the source rather than casting at the call site.
- [ ] `export.py:353` - `.isoformat()` on a polars union type. Guard the branch
      so only date-like values reach it.

**Done when** `mypy` exits clean across all 23 source files.

### S1.3 - Test the orchestration

`pipeline.py` and `cli.py` sit at 0 percent. `pipeline.py` is the entire
resilience story of the daily cron: it decides what becomes `skipped`,
`failed`, `written` or `empty`, and nothing has ever exercised it.

- [ ] `tests/test_pipeline.py`, using a fake `Source` and a `tmp_path` store
      via the `GPA_DATA_ROOT` environment variable, as `tests/test_store.py`
      already does. Cover at minimum:
  - a missing credential becomes `SKIPPED`, never `FAILED`, and never stops
    the zones that follow it
  - an adapter raising `UpstreamError` becomes `FAILED` while the other
    targets still run to completion
  - a schema violation becomes `FAILED` with the offending detail retained
  - an adapter returning an empty frame becomes `EMPTY`, not `FAILED`
  - `max_window_days` on a source caps the caller's `chunk_days`
  - a naive `start` or `end` raises `ValueError`
- [ ] `tests/test_cli.py` using `typer.testing.CliRunner`. Cover `zones`,
      `stats`, `validate` and `export --check`, asserting exit code 0 on a
      populated temporary store and a non-zero exit when validation fails.

**Done when** `pipeline.py` and `cli.py` each report at least 60 percent, and
no module in the coverage table is below 60 percent.

### S1.4 - Make CI enforce what pyproject declares

`[tool.mypy]` sets `strict = true` and the CI never runs it. A standard that is
not enforced is a standard that drifts, which is exactly what happened.

- [ ] Add `mypy` to the `python` job in `.github/workflows/ci.yml`, after the
      format check.
- [ ] Add `pytest --cov=gpa --cov-fail-under=60`.
- [ ] Keep both non-negotiable: do not add `continue-on-error`.

**Done when** CI fails on a deliberately introduced type error and on a
deliberately removed test.

### S1.5 - Publish

- [ ] `git push`. Two commits are unpushed, so the live site does not yet show
      the France and Spain backfills, the CCEE history or the Brazilian load
      fix.
- [ ] Confirm the CI and Pages runs both succeed, and that the hosted site
      reflects the new coverage.

**Done when** `git status -sb` shows no divergence from `origin/main` and the
public site shows France and Spain with full history.

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

- [x] **Add CI and deploy badges to the README.** The workflows pass but a
  reader cannot see that without opening the Actions tab.

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

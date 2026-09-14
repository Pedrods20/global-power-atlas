# Energy-market portfolio roadmap

Review date: 14 September 2026. Baseline: `074ad42` on `main`.
The checkpoint below separates implemented work from the remaining plan.

## Execution checkpoint — start here when resuming

Updated: 14 September 2026. User authorized starting P0/P1 and requested a
written plan before code changes. No GitHub publication is authorized by this
request. This section is the single implementation log; do not create parallel
STATE/TODO/handoff documents.

**Current state: A/B/C implemented and validated locally; D substantially
implemented but not yet green; E/F/G pending.**
P0 and P1 are not complete as whole priorities. Work remains uncommitted on
`main`; no push, public export, data ingestion or prospective issuance occurred.
Resume by finishing the open items in "D handoff evidence" below (two
pre-existing tests need updating to the new ledger contract, one ruff finding,
three mypy findings, `reconcile` CLI and the workflow failure path still
unwired) — not by reimplementing the battery engine or the ledger/provenance/
attempts core, which are done and covered by new passing tests.

### D execution plan — recorded before implementation

The user resumed D. Preserve A/B/C, their uncommitted files and ignored study
artifacts. Scope: local provenance/attempt recording, reconciliation selection,
CLI and workflow definitions with offline tests. No publication, real issuance,
provider refresh or pilot activation is part of this increment.

1. **Regression baseline:** rerun the existing suite; add tests for target-day
   fingerprint sensitivity, different model parameters, masked future targets,
   late/non-finite predictions, preserved abstentions and duplicate attempts.
2. **Evidence:** archive a sanitized training/target panel, feature schema,
   deterministic model configuration, calculation-source snapshots, dependency
   versions, source-frame checksums and actual local observation timestamps.
   Do not invent provider publication times. Capture completion time after model
   computation and recording time after writing inputs. Diagnostic or late
   persistence is explicitly ineligible. Add an offline replay check.
3. **Ledger contract:** retain the full expected clock-hour grid, including
   abstentions. Protect original forecasts and identifiers from replacement
   during reconciliation. Exclude legacy records without new evidence rather
   than retroactively certifying them. Select the earliest complete, verified,
   pre-gate issue per model/day as a whole; never combine hours from retries.
   Keep the hourly benchmark distinction on DST days when passing it to battery.
4. **Attempts:** persist start and completion events, including failure before
   inputs load and all-abstention runs. Register workflow attempts before refresh;
   finalize and preserve evidence even if refresh/issuance fails. A bounded-date
   availability report must show missing scheduled days with no attempt at all.
   Setup/runner failures cannot create local logs; report them as missing slots,
   not successful coverage.
5. **Frozen policy:** centralize the initial Ridge alpha 0.1 configuration to
   match the documented historical selection, plus its training requirement.
   Version model changes and support the existing naive comparators without
   selecting parameters from prospective outcomes. This is a development policy,
   not approval to operate a live pilot.
6. **Integration/verification:** update issue/reconcile/battery CLI consumers and
   the workflow failure path. Test only temporary stores and mocked clocks/IO;
   run lint, strict typing, the full coverage suite and snapshot replay. Preserve
   historical site data. Record achieved checks and residual E/F/G work here.

Planned code: `forecast/ledger.py`, focused `forecast/provenance.py` and
`forecast/attempts.py`, `forecast/models.py` only for feature-only naive issuance,
`cli.py`, `.github/workflows/forecast.yml`, and focused tests. A source snapshot
records local availability evidence; provider publication/vintage correctness
and history catch-up remain E and are not implicitly certified by a hash.

### D handoff evidence — partial, uncommitted (this session)

The user resumed D and asked to stop before it was finished. This is not a
completed increment: two tests fail, lint and mypy each have open findings,
and two of the six D plan items (reconcile CLI, workflow) were not started.

Changed files: `src/gpa/forecast/ledger.py` (rewritten), `src/gpa/cli.py`
(`issue` and `battery` commands, new `forecast-attempt` command),
`src/gpa/forecast/models.py` (+4 lines: feature-only `predict_day` on `Naive`).
New files: `src/gpa/forecast/provenance.py`, `src/gpa/forecast/attempts.py`,
`tests/test_ledger.py`, `tests/test_forecast_attempts.py`. `src/gpa/battery.py`
and `src/gpa/battery_study.py` carry only the already-recorded A/B/C changes.

Implemented and covered by new, passing tests:

- **Fingerprint and identity.** `provenance.sanitized` masks delivery-day
  target outcomes and rejects the target as a feature before hashing or
  fitting; `provenance.input_hash` covers the full sanitized panel, so a
  changed target-day feature now changes the hash. `provenance.describe`/
  `digest` fold the full model class and parameters into `model_version`, so
  two Ridge configurations no longer share an identity.
- **Late/failed eligibility.** `ledger.timing_reason` classifies every issue as
  `pre_gate`, `late_prediction`, `late_recording`, `outside_issue_day` or
  `inputs_after_prediction`; only `pre_gate` sets `eligible=True`. Diagnostic
  and legacy rows are excluded from `ledger.canonical`, which re-verifies each
  candidate against its saved snapshot before selecting one canonical issue
  per model/day, without using outcomes.
- **Full grid and duplicates.** `ledger.delivery_grid` retains every expected
  clock hour, including abstentions and the doubled autumn DST hour; `issue`
  rejects duplicate or unexpected delivery keys; `append` refuses to overwrite
  immutable fields on retry while still letting a null-settlement retry fill
  in a later observation.
- **Snapshots.** `provenance.save_snapshot`/`read_snapshot` persist the input
  panel, any supplied source frames, a full copy of the `forecast/` source
  tree, dependency versions and a checksum manifest, and re-verify all of it
  (plus the recomputed input hash and source hash) on read. This is local
  evidence only: it records `provider_publication_times: "unknown"` and does
  not certify upstream provider vintage; that remains E.
- **Attempts.** `forecast/attempts.py` persists a `started.json`/
  `completed.json` pair per attempt id, rejects a second completion without
  `if_open`, and `attempts.report` produces one row per expected delivery day
  over a bounded window — including days with no attempt at all — cross-checked
  against `ledger.canonical` so a logged "issued" status is not accepted
  without a matching canonical, verified issuance.
- **CLI.** `gpa issue` now registers a start attempt before reading inputs,
  always records a completion (including on ingestion failure or a late
  issue), and never reports success without a persisted, eligible issue.
  `gpa battery` reads its scored sample through `ledger.canonical` instead of
  raw `status == "scored"` rows. A new `gpa forecast-attempt` subcommand
  exposes `start`/`finish`/`report` directly.

Known gaps — do not report D as done:

- `tests/test_forecast_extensions.py::test_issue_ledger_retains_abstentions_and_round_trips`
  and `::test_reconcile_scores_observed_hours_without_changing_issue_identity`
  fail against the new code: they assert the old, narrower contract (a 2-row
  result instead of the full 24-row grid; no `abstain_missing_inputs` status on
  unscored hours). The new behaviour matches the plan above; rewrite these two
  pre-existing tests to the new contract rather than reverting the fix.
- `ruff check src tests` reports one open finding: `src/gpa/cli.py:545` should
  use `contextlib.suppress(FileNotFoundError)` instead of `try`/`except`/`pass`.
- `mypy` (strict) reports three open findings: `provenance.py:66` and
  `attempts.py:34` return `Any` from a typed function; `ledger.py:152` has a
  redundant cast.
- `gpa reconcile` and `.github/workflows/forecast.yml` were not touched this
  session. Plan item 6 ("update issue/reconcile/battery CLI consumers and the
  workflow failure path") is two-thirds done: `issue` and `battery` call the
  new ledger/attempts code; `reconcile` and the workflow's failure path still
  use the old interfaces.

Test evidence (Windows, Python 3.13.9, this session):

| Check | Result |
|---|---|
| Full suite | 307 collected, **305 passed, 2 failed** (both pre-existing, contract-mismatch — listed above) |
| Ruff lint | 1 open finding, `src/gpa/cli.py:545` |
| Mypy strict | 3 open findings, `provenance.py:66`, `ledger.py:152`, `attempts.py:34` |
| Ruff format / full coverage run | Not run to completion this session |

### Session close — 14 September 2026 (second checkpoint, item D)

The user asked to stop again after this partial D implementation. This update
changes documentation only; it does not start the next increment.

To resume safely in a new session:

1. Open this roadmap in `C:\Users\Pedro\Desktop\Python\global-power-atlas` and
   run `git status --short`. Expect four modified files (`src/gpa/battery.py`,
   `src/gpa/cli.py`, `src/gpa/forecast/ledger.py`, `src/gpa/forecast/models.py`)
   and untracked new files including this roadmap, `src/gpa/battery_study.py`,
   `src/gpa/forecast/provenance.py`, `src/gpa/forecast/attempts.py` and four
   test files. Preserve all of them.
2. Read "D handoff evidence" above, then finish D in this order: (a) rewrite
   the two failing tests in `tests/test_forecast_extensions.py` for the new
   ledger contract; (b) fix the one ruff finding and three mypy findings;
   (c) wire `gpa reconcile` and the `.github/workflows/forecast.yml` failure
   path to the attempts/ledger changes already made to `issue`/`battery`;
   (d) run the full validation command list below and record a clean result
   here before calling D done.
3. Keep the existing studies under `.gpa/battery-studies/`; this session did
   not touch them. Do not clean this directory or overwrite a saved study.
4. After D is genuinely green, continue with E (input availability/history),
   then F (frozen release/presentation) and G (remaining sensitivities). Do
   not treat P0/P1 as complete or start a prospective pilot yet. No commit,
   push or dashboard publication was performed in this session.

Suggested resume request:

> Leia `docs/portfolio-roadmap.md` e finalize o item D (testes falhando, lint,
> mypy, CLI do reconcile, workflow); depois siga para o item E. Preserve os
> avanços A/B/C e os estudos locais; atualize este mesmo arquivo com os
> resultados; não publique no GitHub sem eu pedir.

**First implementation increment:** make the battery accounting trustworthy and
build the P1 economic-comparison layer on that corrected engine. This increment
does not declare all of P0 complete or begin the prospective pilot. Corrected
research outputs must be frozen and reviewed before replacing public headlines.

| Work item | Planned files / scope | Acceptance | Status |
|---|---|---|---|
| A — Dispatch correctness (P0) | `src/gpa/battery.py`, `tests/test_battery.py` | Fractional cycle budget, finite inputs, initial/terminal SOC, unique chronological intervals, 23/25-hour UTC days, duration propagation, rejection of ambiguous DST clock-hour input | Done locally; regression and exhaustive small-schedule tests passed |
| B — Comparable strategies (P1) | Battery backtest adapter and tests | All five existing forecast models plus no trade and perfect foresight; 1/2/4 MWh; exactly the same complete settled days for every strategy; consistent actuals; no-trade keeps initial SOC | Done locally; common-sample and coverage tests passed |
| C — Economic evidence (P1) | `src/gpa/battery_study.py`, tests and a local `battery-study` CLI | Daily margin, cost accounting, incremental value versus each fixed naive, downside/concentration, deterministic paired calendar-block bootstrap; costs explicitly labelled assumptions | Done locally; three historical studies saved and zero-cost study replayed |
| D — Issuance provenance (P0) | `forecast/ledger.py`, `forecast/provenance.py`, `forecast/attempts.py`, `cli.py`, workflow/tests | Target-day feature hash, model parameters/version, input snapshot, late/failure/abstention policy, canonical issuance | Core done locally (2 tests, ruff, mypy still open); `reconcile` CLI and workflow not started |
| E — Input availability/history (P0) | Pipeline, sources, ingest/forecast workflows and tests | Full already-published curve, no unavailable targets, checkpoint catch-up and isolated persistent history | Pending after first increment |
| F — Frozen release and presentation (P0/P1) | Snapshot/export, existing site pages, README/tests | Reproducible corrected release; honest date/coverage/cost labels; separate units; concise commercial summary | Pending after engine and provenance checks |
| G — Remaining P1 sensitivities | Analysis/configuration/tests | Sourced/calibrated cost assumptions, availability/error stresses, model-selection/evaluation separation and a qualified duration recommendation | Pending after comparison layer |

Execution rules agreed before editing:

1. Read the affected source and tests; capture the existing test baseline.
2. Add regression tests for each confirmed defect, then implement the correction.
3. Keep the public hourly forecast explicitly hourly. A clock-hour average on
   a DST day cannot recover the original repeated delivery intervals: reject
   that ambiguous battery day, while supporting complete timestamped 23/25-hour
   inputs. Quarter-hour support is accounting support, not a new price forecast.
4. Enforce cycle limits using battery-side throughput and retain the documented
   one charge-then-discharge episode assumption. Do not silently claim general
   multi-cycle optimization or intraday trading.
5. Choose one full-day pre-auction schedule from the supplied forecast and settle
   on actual prices; actuals must never select actions. Cost sensitivities must
   rerun optimization because costs can change the schedule.
6. Compare models on shared complete settlement days. Explicitly report excluded
   days and any comparator chosen using the evaluation sample; do not present a
   hindsight-selected winner as an out-of-sample trading policy.
7. Run focused tests after each increment and lint/types/full tests before the
   handoff where feasible. Build the site if exports or presentation change.
8. Update this checkpoint with exact changes, commands/results, limitations and
   the next executable step before stopping. Do not commit/push, modify secrets,
   issue historical forecasts as prospective, or overwrite the published data
   snapshot during this initial engine increment.

Validation commands (PowerShell, repository root):

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m ruff format --check src tests
.venv\Scripts\python.exe -m mypy
# Only when site/export files change:
npm run build
```

**Next action — finish D, then move to issuance provenance's remaining scope:**
items 1–4 below are implemented and covered by new passing tests (see "D
handoff evidence" above); items 5 and 6 are the open work.

1. ~~Changing a target-day feature must change the issuance fingerprint...~~
   Done: `provenance.input_hash`/`provenance.sanitized`.
2. ~~Late diagnostic runs must be explicitly ineligible...~~ Done:
   `ledger.timing_reason` plus `forecast/attempts.py`.
3. ~~Select a canonical eligible pre-gate issue per model/delivery...~~ Done:
   `ledger.canonical`, snapshot-verified.
4. ~~Add parameter/code/environment manifests and archived inputs...~~ Done:
   `provenance.save_snapshot`/`read_snapshot`.
5. Rewrite the two failing tests in `tests/test_forecast_extensions.py` for the
   new contract, fix the one ruff finding (`cli.py:545`) and three mypy
   findings (`provenance.py:66`, `ledger.py:152`, `attempts.py:34`), then wire
   `gpa reconcile` and `.github/workflows/forecast.yml`'s failure path to the
   attempts/ledger changes already made to `issue`/`battery`. Run the full
   validation command list and record a clean pass before calling D done.
6. After D, implement E's availability-aware price retrieval and checkpoint
   catch-up. Do not start a real pilot before those end-to-end tests pass.

Then finish F's full-precision frozen research release and public presentation,
and G's calibrated costs/availability/error stresses. The current C snapshots
freeze supplied predictions and the economic calculation, not upstream model
training or raw-data vintages. Do not declare complete forecasting provenance.

Local evaluation protocol, recorded before the cost runs:

- Input: the existing `site/data/forecast_predictions.parquet`, including its
  presentation rounding. This is a development prediction sample, not a newly
  trained or prospectively validated model.
- Common sample, all five models; 1 MW and 1/2/4 MWh, 90% round-trip efficiency,
  0.25 MWh SOC grid, identical initial/terminal SOC of zero, at most one
  charge-then-discharge episode and one equivalent cycle per day.
- Three independent reoptimized runs: variable/degradation rates of 0/0,
  2/3 and 5/10 EUR per absolute grid MWh. The latter two are deliberately
  illustrative sensitivities, not externally calibrated battery costs.
- Paired calendar blocks of seven days, 2,000 resamples, seed 20260914. Intervals
  are exploratory, conditional on eligible observed days and not adjusted for
  multiple comparisons.
- Outputs: `.gpa/battery-studies/<content-id>/`, ignored by Git. Each completed
  run includes predictions, dispatch, coverage, daily margins, summary, risk,
  naive comparisons, calculation-source snapshots and a checksum manifest.
- The preliminary zero-cost run `8ce3ca0d5d7a958e4218` is superseded by the final
  studies below. It remains an ignored local artifact, not a public release.
- The full test suite passed the existing 75% coverage threshold. Public
  exports are intentionally not regenerated in this increment;
  `gpa export --check` is expected to require the new frozen release in F.

```powershell
.venv\Scripts\gpa.exe battery-study --predictions site/data/forecast_predictions.parquet
.venv\Scripts\gpa.exe battery-study --predictions site/data/forecast_predictions.parquet --variable-cost 2 --degradation-cost 3
.venv\Scripts\gpa.exe battery-study --predictions site/data/forecast_predictions.parquet --variable-cost 5 --degradation-cost 10
.venv\Scripts\python.exe -m pytest --cov=gpa --cov-fail-under=75
```

### Handoff evidence — completed first increment

Changed files: `src/gpa/battery.py`, `src/gpa/cli.py`, the new
`src/gpa/battery_study.py`, `tests/test_battery.py`,
`tests/test_battery_study.py` and this roadmap. No dependencies were added.
The code/test files and roadmap must all be included when a future commit is
authorized; `git diff` alone omits newly created, untracked files.

Implementation decisions:

- Backward dynamic programming chooses a whole-day schedule once. With equal
  initial/terminal SOC and one charge-then-discharge episode, battery throughput
  equals twice the increase from initial to peak SOC. A bounded peak therefore
  enforces the fractional cycle budget; this proof does not extend to multiple
  episodes or a free terminal SOC. Budget rounding is conservative on the SOC grid.
- Dispatch carries UTC interval identity and duration throughout. Missing or
  ambiguous days are excluded; duplicate/overlapping intervals and inconsistent
  actuals are errors. Missing settlement is not reported as zero or partial profit.
- All requested models must exist. Model comparisons use the intersection of
  complete, settled physical days with the same intervals. `coverage` records
  candidate, complete, common and excluded-day counts.
- `horizon_steps` now defaults to a full delivery day; an explicit shorter
  value raises. The existing ledger CLI uses its actually present models, three
  capacities and the market timezone, and writes coverage. Its issuance-selection
  defects are still pending in D; this change does not validate the live workflow.
- `gpa battery-study` reads only a supplied Parquet file and never ingests data,
  trains a model, reads the live store or exports the site. Snapshot calculation
  sources are captured at module load so later workspace edits do not silently
  change which source bytes are attached to a completed run.
- Daily downside includes a zero initial equity reference. Concentration is the
  top five positive-margin days divided by all positive daily margin. Monthly
  totals expose their observed-day count. Bootstrap requires at least eight
  calendar blocks and 56 observed days at the default seven-day block length.

Final checks (Windows, Python 3.13.9):

| Check | Result |
|---|---|
| Existing test baseline before editing | 238 passed |
| New engine regressions before fixing | 30 failed / 3 passed, reproducing defects and missing contracts |
| Full suite after implementation | **281 passed**, 18.54 s |
| Coverage | **85.74%** overall; battery 90%, economic study 92%; existing 75% floor unchanged |
| Ruff lint / format | Passed; 49 source/test files formatted |
| Mypy strict | Passed; 32 source files |
| `git diff --check` | Passed |
| Historical snapshot verification | All three final manifests and artifact checksums passed |
| Historical replay | All six result tables reproduced after loading the zero-cost snapshot; absolute tolerance 1e-8, relative tolerance 1e-10 |
| Public export check / site build | Not run; public files unchanged and the new economic export is deliberately pending F |

Final local studies (368 shared days, 8,832 hourly intervals per strategy/asset):

| Variable / degradation EUR per grid MWh | Study ID | Ridge 4h sample margin after stated costs | Increment over best naive in this sample |
|---|---|---:|---:|
| 0 / 0 | `519eb2e34940db17889e` | EUR 136,672.56 | EUR 5,577.55 |
| 2 / 3 — illustrative | `acf92cf4414826f42a13` | EUR 121,927.30 | EUR 5,819.59 |
| 5 / 10 — illustrative | `04f2f1c03b8968d3d724` | EUR 93,536.80 | EUR 5,594.97 |

The best naive for 4h is `naive_similar_day`; for 1h/2h it is
`naive_previous_day`. In the zero-cost 1h case, LightGBM trails that previous-day
baseline by EUR 59.68. The zero-cost 4h Ridge versus similar-day paired
exploratory interval is approximately EUR 3,810–7,571 over the observed sample.
These figures are not annualized, investable returns or a new untouched test.

The historical day count falls from the published 369 to 368 because the
collapsed clock-hour input for 26 October 2025 cannot identify both physical
instances of the repeated hour. Timestamped 23/25-hour and 92/100-quarter-hour
fixtures are supported; the missing physical detail is not invented.

All three final runs use calculation-source fingerprint
`d0e8c7131795c1fb1836bbe05f698fc0666699e3f31289da4a0ec23a5a0f24fe`.
To inspect a saved study without regenerating it:

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; from gpa.battery_study import read_study; m,t=read_study(Path('.gpa/battery-studies/519eb2e34940db17889e')); print(m['assumptions']); print(t['risk'].to_dicts())"
```

To reproduce results in memory, call `evaluate` with the saved `predictions`
table and manifest fields `model_names`, `durations_mwh`, `spec_kwargs`,
`block_days`, `resamples` and `seed`. Running the same CLI command with unchanged
code/environment and assumptions intentionally refuses to overwrite its existing
study; use `read_study`, or a separate `--output` directory for an independent run.

**Publication gate:** do not push this intermediate tree expecting the site CI
to pass its export freshness check. The corrected engine changes the battery
schema/sample; regenerate a reviewed, frozen release in F and verify
`gpa export --check`, the site build and browser smoke before publication.
The deployed dashboard still contains the earlier development results.

## Positioning

Target energy-market analyst, power quantitative analyst and storage-analytics
roles. Keep Germany-Luxembourg (DE-LU) as the research market and retain four
pages: executive overview, forecast, battery and methodology. Historical prices,
load and generation remain monthly context, not an operational monitoring product.

The central case should answer: **When does a better day-ahead price forecast
create additional battery value, and how robust is that value to costs and
market change?** A complementary study should test how renewable and battery
additions could change that opportunity. It must distinguish scenarios from
validated forecasts.

This positioning combines market interpretation, statistical modelling and
commercial communication. Those capabilities appear together in Vattenfall's
[archived trading-analyst role](https://careers.vattenfall.com/de/de/job/intraday-quantitative-analyst-trading-analyst-in-hamburg-jid-50031)
and Aurora's [modelling-programme profile](https://auroraer.com/careers/early-careers/sao-paulo-graduate-modelling-programme).
These are illustrative role descriptions, not a survey of all recruiters or a
claim that the positions are currently open.

## What the project already demonstrates

- Reproducible Python data pipelines, partitioned Parquet, validation, tests,
  CI and a published static dashboard.
- Time-aware price modelling with naive baselines, Ridge and LightGBM,
  walk-forward evaluation, regime analysis and interval forecasts.
- A constrained battery-dispatch simulation, retrospective experiment-snapshot
  support and initial prospective-ledger infrastructure.
- An appropriately compact interface and explicit limitations.

The published retrospective comparison contains 8,971 scored clock-hour cells.
Ridge's MAE is approximately EUR 21.04/MWh, versus EUR 28.80/MWh for the best
price-error naive baseline, the previous day: a 27.0% reduction.

On the battery comparison's common 369 complete days, the 1 MW / 4 MWh case
produces simulated margin of EUR 136,777 for Ridge and EUR 129,753 for the
previous-week strategy. The incremental margin is approximately EUR 7,024,
or 5.4%. Ridge captures 94.5% of the constrained perfect-foresight upper bound.
These are sample-period results, not annual returns. Operating and degradation
costs are zero in this published case, and the battery comparison omits the
previous-day baseline. It therefore does not yet establish incremental value
over the strongest simple dispatch alternative.

## Baseline findings before implementation

| Finding | Evidence in the current project | Consequence |
|---|---|---|
| Prospective inputs are incomplete before issuance | `pipeline.py` defaults the ingestion end to now; `energy_charts.py` clips delivery timestamps at that end; `panel.py` requires complete previous-day price aggregates | Already-cleared prices for later delivery on the issue day are dropped. Waiting for the next pre-auction run does not resolve this structural gap. |
| Monthly refresh can leave missing history | `ingest.yml` runs monthly but fetches seven days; the isolated forecast store also refreshes only seven days from a monthly seed | Refresh from the last complete observation with overlap, including missed runs; daily public-site updates are unnecessary. |
| Issuance is not yet independently reproducible | `ledger.py` hashes only rows before the delivery date and identifies the model without its parameters; live Ridge uses alpha 1.0 versus 0.1 in the historical selection | Changing target-day inputs can change predictions without changing the stored hash. A versioned manifest and input snapshot are required. |
| Prospective eligibility is underspecified | Late diagnostic issues can retain `issued` status; all-abstention attempts are not persisted; multiple issue times are possible | A scheduled-attempt denominator, explicit eligibility and one canonical pre-gate issue are needed before performance claims. |
| Battery constraints and time handling need correction | `max_cycles_per_day` is validated but not enforced as a fractional throughput budget; completion uses 24 local-hour labels; the prediction adapter drops interval duration | Correct physical accounting and test UTC intervals, DST and quarter-hour data before extending the economic claims. |
| Fundamental forecasts are not connected end to end | `cli.issue` does not supply them; the SMARD parser loses offshore wind and lacks a configured load series | A LightGBM upgrade alone does not add an auditable forward information set. |
| A fixed evaluation end is not a frozen experiment | Snapshot support exists, but the current export can recompute from revised observations without a saved release snapshot | Preserve the exact research inputs and configuration used for each public result. |
| The first screen understates the work | The preview leads with historical charts; personal positioning and a decision-oriented conclusion are missing | Recruiters must infer the candidate's contribution and the commercial question. |
| Some presentation choices can mislead | Latest daily load has only 14.5 hours of coverage; battery sample mixes price, action and SOC units; GitHub About remains generic with no topics | Hide or mark partial periods, separate chart units, and align public metadata with the actual research focus. |

Read-only diagnostic checks confirmed that changing target-day features leaves
the issuance hash unchanged, different Ridge alphas share a model-version label,
late diagnostic forecasts can be marked issued, and a 0.5-cycle limit can permit
one full equivalent cycle. These checks do not replace a complete model audit.
Successful CI or acceptance of a workflow by GitHub is not prospective-model
acceptance; the reviewed checkout contains no issued-and-reconciled pilot record.

## Implementation sequence

### P0 — Establish a defensible research baseline

**Deliverable:** corrected input timing, dispatch accounting and reproducible
historical/prospective records. This is a prerequisite for stronger public claims.

- Separate delivery time, source publication time and retrieval time. Ingest the
  full already-published price curve without allowing delivery-day outcomes into
  the predictor. Use information actually available at issuance.
- Replace fixed seven-day history windows with checkpoint-based catch-up and
  revision overlap. Persist the isolated forecasting history and input snapshots.
- Record every scheduled attempt, including failure and abstention. Label late
  runs diagnostic and exclude them from prospective performance. Define the
  canonical eligible issuance before evaluating results.
- Hash both training and target-day inputs. Store source provenance, observation
  vintage, code revision, feature schema, fitted-model/configuration identifiers
  and dependency versions. Align live parameters with an explicitly approved,
  frozen model; do not silently equate different Ridge versions.
- Fix cycle-budget enforcement, interval identity, DST and duration propagation.
  Define the current strategy as a simplified day-ahead price-taking schedule;
  do not imply intraday execution without intraday data and execution rules.
- Freeze one retrospective release and correct partial-period and chart-unit
  presentation. Keep the current hourly study explicitly labelled as a benchmark
  until finer-resolution evaluation is available.

**Acceptance:** integration tests cover a pre-gate issue with complete available
inputs, exclusion of unavailable information, a failed attempt, a late diagnostic,
duplicate attempts, a missed monthly refresh, fractional cycle limits and 23/25-hour
days. An independently loaded snapshot reproduces predictions and dispatch within
declared numerical tolerances. Any corrected historical results replace the
figures above with an explanation of the change.

### P1 — Measure the commercial contribution of forecasting

**Deliverable:** a compact comparison of incremental battery margin and downside,
with an interim executive summary on the existing pages.

- Compare all existing naive strategies, Ridge, LightGBM, no trade and the
  constrained perfect-foresight upper bound on the same eligible intervals.
  The best price-error baseline need not be the best dispatch baseline.
- Evaluate 1 MW batteries with 1, 2 and 4 MWh. State how initial/terminal SOC,
  efficiency and available energy affect comparability.
- Introduce documented variable operating, trading and degradation-cost
  sensitivities. Report gross simulated margin and margin after those costs
  separately; neither is investment return.
- Report incremental EUR/MW over the strongest fixed simple comparator, cycle
  count, worst month, daily losses, cumulative drawdown and concentration in the
  best days. Show sensitivity to forecast errors and asset availability.
- Use paired daily comparisons and a time-block bootstrap for uncertainty;
  publish negative or inconclusive results. Keep development, validation and
  untouched evaluation roles explicit.
- Explain when forecast improvements do not improve dispatch. A credible
  conclusion may favour Ridge or a naive model over LightGBM.

**Acceptance:** every headline can be reproduced from a frozen release; the
summary states sample dates, costs, baseline, uncertainty and limitations. It
answers which strategy and duration deserve further investigation, and why.

### P2 — Improve the information set and test the market mechanism

**Deliverable:** an ablation study explaining which market signals help price
prediction and whether they also improve battery outcomes.

- Integrate archived pre-auction load, onshore/offshore wind and solar forecasts.
  Correct component aggregation and interval coverage. Preserve each observed
  vintage rather than assigning a historical availability time retrospectively.
- Start collecting snapshots as soon as P0's provenance rules are implemented.
  Where vintage history is unavailable, keep the existing model as the baseline
  while new training data accumulates; do not substitute realised fundamentals.
- Test forecast residual load, ramps and renewable penetration first. Add outages
  and cross-border availability only after checking access, licensing and reliable
  historical publication times. Add fuel/carbon inputs only with comparable provenance.
- Compare feature groups and models under the same walk-forward protocol. Tune
  inside training/validation periods, not against the final test or the pilot.
- Reassess interval calibration by regime; test risk-aware dispatch only after
  those uncertainty estimates are sufficiently calibrated.
- Evaluate real delivery-interval products, including quarter-hours where
  applicable, without presenting repeated hourly predictions as a new fine-scale
  forecasting model. Confirm market rules against official documentation first.

**Acceptance:** incremental features have an auditable availability timestamp,
coverage report and leakage tests. Publish changes in MAE, calibration and
incremental cost-adjusted battery margin. No minimum improvement is promised.

### P3 — Add the differentiating study: market change and storage value

**Deliverable:** a DE-LU 2027–2030 scenario brief on renewable build-out, battery
competition and the durability of arbitrage opportunities, within the battery page.

- Build a monthly capacity ledger using the official
  [Marktstammdatenregister](https://www.bundesnetzagentur.de/EN/Areas/Energy/CoreEnergyMarketDataRegister/start.html)
  and renewable auction results for
  [solar](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/Ausschreibungen/Solaranlagen1/BeendeteAusschreibungen/start.html)
  and [onshore wind](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/Ausschreibungen/Wind_Onshore/BeendeteAusschreibungen/start.html).
  Distinguish operating assets from future projects and unsupported announcements;
  retain source dates, commissioning assumptions and uncertainty. Deduplicate
  registry and award records. An auction award is not commissioned capacity and
  a support-auction price is not a wholesale-price forecast.
- Define a reference path, faster renewable deployment and faster battery
  deployment, with explicit ranges and commissioning delays. Test demand, weather
  and fuel-price sensitivities separately to make the drivers interpretable.
- Explain the competing mechanisms: renewable output changes the hourly price
  shape; storage charging/discharging can compress spreads; outages and
  interconnection limits can change the outcome. Estimate magnitudes with a
  documented, calibrated scenario model, not an arbitrary revenue haircut or an
  out-of-distribution extrapolation of the short-term ML model.
- Track spreads, negative-price exposure, capture prices and battery margin by
  duration. Add a transparent asset-screening sheet with sourced CAPEX/OPEX,
  degradation, availability and discount-rate sensitivities only at this stage.
- Discuss intraday and ancillary revenues as extensions. Do not add unsupported
  revenue streams or simultaneously sell incompatible services. RWE's
  [battery-business presentation](https://www.rwe.com/-/media/RWE/documents/05-investor-relations/finanzkalendar-und-veroeffentlichungen/veroeffentlichungen-und-praesentationen/investor-presentation-on-battery-business.pdf)
  illustrates the commercial relevance of multiple markets, cycling costs and
  competitive market conditions; its assumptions are not this project's inputs.

**Acceptance:** each scenario is reproducible from dated capacity and assumption
tables, reconciles its base case to history and discloses omitted market effects.
The conclusion states what could make the preferred battery case unattractive.
Scenario outputs remain separate from statistically validated price forecasts.

### P4 — Package the evidence for recruiting

**Deliverable:** one coherent public case, with a short executive brief and an
optional technical deep dive rather than additional dashboard pages.

- Home: clear research question, author's role and verified contact/profile links,
  three qualified findings, one commercial comparison and short historical context.
- Forecast: baseline skill, fundamentals ablation, evaluation protocol and a
  compact prospective-status summary. Put secondary diagnostics in a disclosure.
- Battery: incremental margin, costs/downside, duration trade-offs and the
  structural scenario brief. Separate EUR/MWh, MW and MWh in figures.
- Methodology: information timing, market mechanics, assumptions, data rights,
  limitations and a reproducible release command.
- README/GitHub: lead with market, question, finding and contribution; update
  screenshot, About and topics to reflect forecasting and storage. Add an English
  two-page research brief and verified author links. State what was built,
  what was learned and what remains unproven; do not invent employment impact.

**Acceptance:** a reader can identify the market, candidate contribution,
commercial finding and principal caveat in under a minute, then locate the
reproduction path without navigating a collection of planning documents.

## Prospective pilot — runs alongside research, after P0

Freeze the eligible models, naive comparators, issuance policy, scoring and
battery assumptions before starting. Record daily before the applicable market
gate and reconcile after delivery. Daily research collection does not require
daily dashboard publication; a monthly public summary is sufficient.

Use an initial six-week operational pilot. Proposed readiness criteria are at
least 95% of scheduled deliveries issued on time with complete inputs, a visible
failure/abstention denominator, and a reproducible record for every scored result.
These are proposed project targets, not already achieved service levels. Treat
delayed scheduling and provider outages as recorded failures, never backdated
successes. Pilot status must distinguish running, insufficient evidence and
operational acceptance; losses or no improvement do not invalidate honest research.

Six weeks can test reliability and prospective discipline, not annual profitability
or seasonal robustness. Continue across seasons before broader performance claims.

## Scope and delivery discipline

Start with P0, then P1. Begin collecting fundamental vintages early; implement P2
once sufficient valid history exists. Start P3 after the economic baseline is
defensible. Apply P4 incrementally as verified results become available. Each
priority is a separate reviewable increment; re-estimate effort after P0 rather
than assigning a delivery date before resolving data availability.

Do not add more markets, live-news panels, chatbots, broad maps, unrelated models
or live trading to this plan. Existing cross-market data may remain supporting
context without new UI. Remove files only after dependency checks establish that
they are unused; working tests and reusable analytical code are not clutter.
Maintain this one roadmap and retire it once delivered rather than accumulating
overlapping status and planning files.

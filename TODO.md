# Roadmap

Steps 1-6 of the original delivery are complete and recorded in
[`STATE.md`](STATE.md). The site is live at
<https://pedrods20.github.io/global-power-atlas/>, serving the four-zone
rebuild described below since it deployed successfully on 2026-09-13.
The repository now targets **4 zones** (Germany-Luxembourg, France, Spain,
Brazil's national system) from **2 credential-free sources** (Energy-Charts,
ONS) — see "Scope reduction, 2026-09-13" below for why.

This file tracks **what is still missing**. Items are ordered by how much they
affect the credibility of the published work, not by effort.

The P1 block was cleared in the session of 2026-09-12; see
the completed P1 items below for what changed and what it revealed. Some P1
and P2 items below describe zones that were later removed entirely (2026-09-13);
they are kept as historical record with a note rather than deleted, since they
explain decisions (e.g. why Brazilian load moved sourcing) that still hold for
the zones that remain.

## Scope reduction and rebuild, 2026-09-13

The [full review](docs/REVIEW-2026-09-13.md) after commit `083063a` found a
window-wide resolution-inference defect mislabelling part of the European
price history, among other gaps. Rather than patch adapters in place, every
zone whose only source needed a credential, blocked automated clients, or
depended on a hand-imported CSV was removed from the registry, and the four
that remained were rebuilt from cached provider responses and checked against
an independent publisher. Full account in `STATE.md`'s "Scope reduction and
rebuild" section. Summary:

- **Closed:** review priorities 1 (resolution transitions), 2 (duration-aware
  metrics and capture alignment), 3 (deploy gated on CI) and 4 (source
  semantics and structural coverage). Each is marked `[x]` below with what
  changed.
- **Removed for lack of independent verification:** ERCOT, PJM, CAISO,
  AU-NSW1, JP-TOKYO, BR-SECO, BR-NE. See the dedicated section below for what
  each would need to come back.
- **Still open:** priority 5 (experiment-input versioning ahead of a
  prospective evaluation) and one new open question the rebuild's own audit
  raised (Spain's interval-labelling clock check).

### Review priorities — before further model expansion

- [x] **Repair Energy-Charts resolution transitions.** ~~One inferred
  duration per request mislabels hourly prices in mixed windows. Local
  September 2025 counts: 557 suspicious rows for DE-LU, 550 each for FR and
  ES.~~ Fixed 2026-09-13: the adapter now infers resolution per request window;
  the full store was rebuilt from cached provider responses and checked
  against SMARD (DE-LU, FR) and OMIE (ES) with zero price mismatches over the
  full two-year history. See `scripts/rebuild_verified.py` and
  `scripts/audit_external.py`.
- [x] **Finish duration-aware metrics and capture alignment.** ~~Load factor
  and duration curves count rows; volatility averages rows and bridges missing
  days; capture rates combine repeated autumn clock hours.~~ Fixed 2026-09-13:
  `load_factor`/`daily_profile` now duration-weight the average instead of a
  row mean; `duration_curve` ranks by cumulative duration, not row count;
  `capture_rate` was rewritten to integrate the actual overlapping UTC
  intervals of price and generation rather than grouping both onto a
  market-local hour (which is what merged repeated autumn hours);
  `realised_volatility` now upsamples to an explicit calendar-day index so a
  rolling window cannot silently bridge a missing day. Named counterexamples
  in `tests/test_data_review.py` (`test_mixed_duration_statistics_and_curve_endpoints`,
  `test_capture_preserves_both_autumn_delivery_hours`,
  `test_capture_integrates_overlaps_without_filling_a_gap`,
  `test_volatility_does_not_bridge_missing_calendar_days`) exercise the real
  `metrics/load.py` and `metrics/price.py` functions, not a reimplementation.
- [x] **Persist valid partial ingestion and deploy a validated revision.**
  ~~A failed ingest/export step currently skips commit; a post-commit
  freshness failure blocks deployment. Push deploy is independent of CI.~~
  Fixed 2026-09-13: `deploy.yml` is now `workflow_call`-only and publishes one
  exact commit SHA; `ci.yml` calls it after lint/types/tests/export-check/build
  and a new browser-smoke step all pass on `main`; `ingest.yml` calls it with
  the commit it just made, independent of whether the subsequent freshness
  check passes (a quiet provider should not hold back a good day from every
  other market). Proven on the Linux runner: pushing this increment took three
  attempts to go green (`gpa export --check` failed twice first — see "Two
  bugs the first deploy attempt found" below — before CI run `34785003344`
  passed both jobs and successfully called `Deploy`, which built and published
  the site). The reusable-workflow wiring itself worked on the first try; the
  failures were pre-existing correctness gaps it exposed, not new problems.
- [x] **Check source semantics and time coverage.** ~~Flag unknown generation
  categories, select one load definition instead of summing alternatives, and
  report gaps/resolution inconsistencies beyond dataset-level freshness.~~
  Fixed 2026-09-13: the Energy-Charts adapter now selects exactly one load
  category (`Load`, not `Load (incl. self-consumption)`) and raises on an
  unrecognised generation category instead of silently dropping or double
  counting it (`test_energy_charts_selects_one_load_definition_and_rejects_unknown_units`,
  `test_energy_charts_battery_output_and_charging_net_but_never_double_count`).
  `src/gpa/quality.py` (new) reports per-fuel structural coverage, gap hours
  and invalid/overlapping rows per zone and dataset, exported as
  `data_quality.parquet` and gated with a new `gpa audit` CLI command wired
  into both `ci.yml` (after `gpa validate`) and `ingest.yml` (before commit,
  so a structural problem blocks persistence rather than only staleness).
  Done 2026-09-13: `site/methodology.md`'s new "Structural coverage" section
  renders `data_quality.parquet` as a table sorted worst-coverage-first, with
  a fix along the way — the paragraph above it used a backtick-quoted
  `${...}` expression, which Markdown renders as a literal code span rather
  than evaluating; Observable Framework expects `${...}` directly in prose.
  It had been silently broken since it was written, undetected because it
  produced text, not a runtime error, and the browser smoke check only
  catches the latter. Verified against both the local preview and the
  hosted build.
- [ ] **Version experiment inputs and separate future prediction rows.**
  Half done, deliberately not the other half without asking first.

  **Done 2026-09-13:** `gpa backtest --save-snapshot` now calls
  `forecast.snapshot.save()`, freezing the run's inputs, predictions, scores
  and source under a content-addressed `data/experiments/<hash>/`, checksum
  every artifact, and refuses to silently overwrite an existing experiment.
  `export.py` already preferred a saved snapshot over a live recompute when
  one exists (`snapshot.read()`, wired earlier); it just had nothing to read.
  `snapshot.py` went from 25% to 100% test coverage
  (`tests/test_forecast_snapshot.py`): round-trip, a tampered artifact
  rejected by its checksum, a tampered fingerprint rejected separately, and a
  malformed pointer file rejected rather than followed.

  **Deliberately not done yet: no snapshot has actually been saved into the
  repository.** Doing so is not just plumbing — the moment
  `data/experiments/.../current.json` exists and is committed, `gpa export`
  (called by every scheduled `Ingest` run) switches from recomputing the
  forecast tables against the latest data to serving that one frozen
  experiment, indefinitely, until someone runs `--save-snapshot` again. That
  is exactly what a prospective evaluation needs, but it silently stops the
  forecast page from reflecting new data on the daily job, which is a bigger
  behavioural change than "wire the CLI flag" and needs the user's go-ahead
  first, the same way the scope reduction did.

  **Still fully open regardless:** the panel only ever builds rows that
  already have a target price (`Panel.frame` is documented as "one row per
  market-local hour that has a target price" — see `load_panel` in
  `forecast/panel.py`), so there is still no way to construct a genuine
  future prediction row before its price is known. A real prospective
  evaluation needs that issuance path, plus a later reconciliation step once
  the target becomes known, neither of which exists. This is materially more
  work than the snapshot plumbing above.
- [ ] **Confirmed 2026-09-13: ES generation timestamps are interval-end,
  opposite to DE-LU/FR/BR-SIN, and nothing corrects for it.** The original
  crude solar-clock check (broad "near equinox" months, no equation-of-time
  correction) only found a small, dismissable-looking margin. Tightened
  twice — first by correcting each observation's expected solar noon for the
  equation of time (a ±16-minute-per-year wobble the crude check ignored
  entirely), then by narrowing to ±10 days of the actual equinoxes — the
  signal went from "maybe noise" to unambiguous:

  | Zone | Corrected offset from expected solar noon | Half the interval | Reading |
  |---|---|---|---|
  | DE-LU | −9.6 min | 7.5 min | interval-start |
  | FR | −9.5 min | 7.5 min | interval-start |
  | BR-SIN | −30.1 min | 30 min | interval-start |
  | ES | **+7.3 min** | 7.5 min | **interval-end** |

  ES sits within 0.2 minutes of the theoretical exact interval-end value
  (n=8,206 solar observations in the tight window), while the other three
  cleanly read interval-start with 2-3 minutes of residual noise each
  (plausibly asymmetric morning/afternoon solar output, e.g. from cloud
  patterns, not a labelling artefact). The equation-of-time approximation
  used was checked against known reference dates (Feb 11, Mar 21, Nov 3,
  ...) to within about a minute before trusting it.

  This means Spain's generation timestamps, and only Spain's, appear to be
  labelled by the underlying provider (Red Eléctrica via Energy-Charts)
  one interval later than Germany's and France's, even though the same
  adapter and the same "unix_seconds" field handling is used for all three
  and applies no per-country shift. ES **price** is independently confirmed
  correct (zero mismatches against OMIE, which publishes unambiguous
  period-of-delivery data), so this is isolated to generation — but that
  still means every ES metric that joins price against generation
  (`capture_rate` for ES solar/wind, and by extension the supply page's
  capture-rate chart) is combining two series offset by up to one interval
  for that one zone. ES load was not checked (no independent reference
  and no natural periodicity to test against, unlike solar).

  **Not yet fixed, deliberately** — this needs the user's sign-off before
  touching the adapter and re-running the rebuild-and-audit cycle again,
  the same way the original scope decision did. If confirmed further and
  accepted, the fix is a per-country (or per-provider-quirk) timestamp
  correction in `energy_charts.py` for ES generation specifically, followed
  by another `rebuild_verified.py` + `audit_external.py` pass limited to ES
  generation, then re-promoting just that data and re-exporting.

### Two bugs the first deploy attempt found

Pushing the rebuild (commit `bdbfb9d`) was the first time `gpa export --check`
ever ran comparing a Windows-generated commit against a Linux recompute; every
previous commit's `site/data` had been generated and checked on the same
platform (a scheduled Linux Actions run). That exposed two real bugs in
`check_exports`/`gpa export --check` itself, both now fixed and covered by
named tests:

- [x] **JSON export comparison was exact, not tolerant.** `forecast.json`
  differed from Linux's recompute in `best_mae`'s last few significant digits
  — a fitted model's floating-point output is not bit-reproducible across
  platforms even with a fixed seed. Parquet tables already tolerated this
  through `assert_frame_equal`'s default tolerance; JSON used `!=`. Fixed by
  `_json_values_close` in `src/gpa/export.py`
  (`test_export_check_tolerates_cross_platform_float_noise_not_real_change`).
- [x] **`input_sha256` hashed raw floats, defeating that same tolerance.** A
  hash is designed to amplify any difference, so once the JSON comparison
  became tolerant, the *hash field itself* still failed: `forecast.json`'s
  `input_sha256` is a SHA-256 of the duration-weighted panel, and a
  duration-weighted mean is a parallel reduction whose summation order (and
  so its last bit) can differ by machine core count. Fixed by
  `gpa.forecast.backtest.stable_hash`, which rounds float columns to 6
  decimal places before hashing, used both where `input_sha256` is first
  computed and where `forecast.snapshot.read()` re-verifies a saved
  experiment's fingerprint
  (`test_stable_hash_absorbs_last_bit_noise_but_catches_a_real_change`).

Both bugs were latent since `gpa export --check` and the forecasting module
were built; the scope-reduction rebuild's cross-platform deploy is what
finally exercised the path that could reveal them. Worth remembering: **any**
future commit that regenerates `site/data` on Windows and pushes it should
expect `gpa export --check` to be the first real cross-platform test of that
data — run it against a Linux runner (or a WSL/container Python) before
trusting a clean local `gpa export --check` as sufficient.

## Removed for lack of independent verification, 2026-09-13

Every zone below was previously registered and delivering data; each is now
absent from `src/gpa/zones.py`, its adapter deleted, and its old
`data/curated` partitions removed from git. This is a deliberate scope
decision (see `README.md`'s "Why four zones" and `STATE.md`'s "Scope reduction
and rebuild"), not an oversight, and re-adding any of these needs the user's
sign-off since it reopens a decision the user already made once. What each
would need to come back cleanly:

- **ERCOT price/load/generation.** Blocked on access: every ERCOT host
  returns HTTP 403 to automated clients and `mis.ercot.com` fails the TLS
  handshake. Needs a registered ERCOT API account.
- **PJM price/load/generation.** Needs a free Data Miner 2 subscription key.
- **CAISO price/load/generation.** Load/generation came from EIA (needs
  `EIA_API_KEY`); price came from the credential-free CAISO OASIS interface,
  which had no independent second publisher on hand to check it against.
  Reviving just the price series would need finding one (a Californian
  utility's or the CPUC's own published SP15 reference, if one exists) before
  it meets the current bar.
- **AU-NSW1 price/load/generation.** AEMO's own archive (price, load) needs no
  credential, but had no second publisher to check against; OpenElectricity
  (generation) is a redistributor of the same AEMO data, so checking one
  against the other proves nothing about accuracy, only self-consistency.
- **JP-TOKYO price.** JEPX needs no credential but likewise had no
  independent second publisher identified.
- **BR-SECO, BR-NE (CCEE PLD by submarket).** CCEE returns HTTP 403 to
  automated clients; the only path is a hand-imported official CSV
  (`GPA_CCEE_IMPORT_DIR`), which fails the "no manual import" bar even though
  the data itself is likely trustworthy. The raw CSVs used before removal are
  still under `data/raw/ccee/` if this is revisited.

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
  provider still refuses automated download. **Superseded 2026-09-13:** both
  CCEE submarkets were later removed from the registry because a hand-imported
  CSV fails the "no manual import" bar adopted in the scope reduction — see
  "Removed for lack of independent verification" above. The raw CSVs are still
  under `data/raw/ccee/` if this is revisited.

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

**Sprint 1 is complete.** Do not repeat it. P2 contains the remaining data gaps;
Front C now has local retrospective validation; publication remains pending
and Front B follows it.

## P2 - Analytical asymmetries (superseded 2026-09-13)

Every item below concerned a zone (CAISO, ERCOT, PJM, JP-TOKYO) removed from
the registry in the 2026-09-13 scope reduction; see "Removed for lack of
independent verification" above. Kept as historical record of what was built
and why it was later dropped, not as active work.

- [x] **Californian day-ahead price via CAISO OASIS.** ~~17,520 contiguous
  hourly observations over two years at the SP15 trading hub, no gaps. CAISO
  is the one large US market whose price is reachable without a credential.~~
  Three OASIS behaviours were handled, each with a named test, because all
  three fail quietly: an LMP is five components and only the total is a
  price; an empty window returns XML with error code 1000 inside a 200
  response, which a CSV parser reads as a table whose only column is the XML
  declaration; and rate limiting also returns 200, carrying HTML rather than a
  429, so the shared retry layer cannot see it. What it showed: the NERC
  on-peak block cleared below off-peak at SP15 for three consecutive years,
  12.04 percent of day-ahead hours were negative, and solar captured 0.603 of
  the average price against 0.971 for wind. Removed 2026-09-13: no independent
  publisher was found to check the price series against, and the load/
  generation series depended on `EIA_API_KEY`.

- [ ] ~~**ERCOT price.**~~ Removed with the zone. Blocked on access if ever
  revisited: every ERCOT host returns HTTP 403 to automated clients, and
  `mis.ercot.com` fails the TLS handshake.

- [ ] ~~**PJM price.**~~ Removed with the zone. Needs a free Data Miner 2
  subscription key if ever revisited.

- [ ] **Extend thermal spreads beyond Europe.** `europe_spreads.parquet`
  covers 24 months for Germany only. With CAISO gone, this is deferred until
  a registered zone has both a price series and a matching fuel-cost
  reference; France and Spain are the nearer candidates now.

- [ ] ~~**Add load and generation for JP-TOKYO.**~~ Removed with the zone.

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
  Brazilian generation is allowed 96 hours because ONS trails by two days;
  everything else defaults to 36. ~~US generation 48 because EIA restates on a
  day's delay~~ no longer applies: the US zones that rule covered were removed
  2026-09-13. Each rule carries its reason, and a test asserts none is left
  unexplained.

- [x] ~~**Formalise the CCEE refresh.**~~ Superseded 2026-09-13: both CCEE
  zones were removed from the registry rather than kept on a manual-refresh
  rule, so there is no longer a "manual" freshness category at all — see
  "Removed for lack of independent verification" above. The mechanism
  (`FreshnessRule.manual`) still exists in `src/gpa/freshness.py` for a future
  zone that genuinely needs it, but nothing currently uses it.

- [x] **Add CI and deploy badges to the README.**

- [x] **Keep browser smoke aligned with site navigation.** Read routes from
  `observablehq.config.js`, covering all seven current pages at desktop and
  mobile widths. Fail on HTTP errors and missing visible Plot charts, excluding
  the text-only methodology page. Dashboard captures support a separate output
  directory through `GPA_SCREENSHOT_DIR`.

## Front C - Short-term price forecasting (retrospective validation complete locally)

`src/gpa/forecast/`, CLI/export integration, the page and exported tables are
tested locally and committed as `083063a`. Existing work includes three naive
baselines, per-hour ridge, a fixed pooled LightGBM challenger and optional local
MLflow tracking. Local acceptance and publication are tracked separately below.

Two years of validated hourly history exist, which is the substrate the
previous architecture could never provide. This is the front that turns a
well-built data platform into evidence of analytical capability.

Sequenced before the retrieval layer on purpose: the evaluation harness built
here is what will later decide whether regulatory signals actually improve
anything, rather than being assumed to.

- [x] **Naive baselines first, with a common scoreboard.** Previous day, previous week
  same hour, and a seasonal-naive variant. Every later model is judged against
  these. A forecast that cannot beat "same hour last week" is not a forecast.
- [x] **Walk-forward backtest, never a random split.** Time-series data leaks
  through a shuffled split. Expanding or rolling origin, refit at each step,
  and no feature that would not have been known at prediction time.
- [x] **Report the error metrics that suit power prices.** MAE and RMSE plus a
  pinball loss if any quantile output is produced. Report them by block and by
  regime separately: aggregate error hides the fact that the interesting hours
  are the scarce and the negative ones.
- [x] **State the target precisely.** DE-LU duration-weighted hourly day-ahead
  price, complete observed hours only. Repeated autumn clock hours are averaged
  into one cell. Price lags start at D-1, actuals-derived inputs at D-2; source
  revisions are not historical publication-time vintages.
- [x] **Show failure honestly in the local page.** Numbers updated 2026-09-13
  after the price-history rebuild (previous figures below are stale — this is
  the "a capped benchmark still changes when revised inputs arrive" review
  finding, confirmed in practice): LightGBM loses to the best naive baseline
  on negative hours (skill -8.5%) and scarce hours (-11.8%). Every model uses
  the same 8,971 cells over 374 calendar days, ending 2026-09-12. Ridge MAE is
  21.04 EUR/MWh; LightGBM is 22.48. This is a retrospective development
  benchmark, not an untouched holdout. Do not average these figures with the
  pre-rebuild ones (7,982 cells, 333 days, ridge 21.06, LightGBM 22.18) —
  they describe different, non-comparable input data.
- [x] **Validate predictive intervals numerically.** Pinball, coverage and
  mean width have hand-calculated test cases; coverage is computed before
  chart values are rounded. Incomplete intervals do not enter coverage/width.
  Local MLflow runs record these diagnostics alongside inputs and source.
- [x] **Commit the local increment.** Forecasting implementation, tests and
  regenerated `site/data` are in `083063a`.
- [x] **Publish after resolving review findings.** Done 2026-09-13: pushed
  as three commits (`bdbfb9d` rebuild, `987b075`/`e9a12d0`/`0c4d625` fixing
  the two export-check bugs the first deploy attempt found — see above), CI
  run `34785003344` passed both jobs and its called `Deploy` workflow built
  and published successfully. The live site now serves the four-zone,
  independently-audited build, including forecasting. Confirmed 2026-09-13:
  `GPA_TEST_URL=https://pedrods20.github.io/global-power-atlas/ node
  scripts/browser-smoke.mjs` passed all 14 route/viewport combinations
  against the hosted build itself, not just the local preview — this front
  is fully closed.
- [ ] **Record a prospective evaluation.** Issue and retain forecasts before
  prices become known, preserve input vintages and evaluate the separate period.
  The historical benchmark is capped at 2026-09-12; extending that cap is not
  prospective validation.

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

These remain unpublished analytical extensions. Residual load already has a
forecasting helper; the other items still need implementation.

- [ ] **Residual (net) load and its duration curve.** Demand minus wind and
  solar is the series that actually sizes flexibility and drives the duck
  curve. Every input is already in the store; no new source is needed. This is
  already used as a lagged feature for Front C; publication as a market metric
  and duration curve is still pending.

- [ ] **Ramp analysis.** Hourly ramp rates, and the annual worst-case ramp,
  which is what dimensions flexible capacity. ~~Australia's five-minute data
  makes this genuinely interesting~~ — AU-NSW1 was removed 2026-09-13; this is
  now hourly-resolution analysis on DE-LU/FR/ES/BR-SIN only, still useful but
  less distinctive without sub-hourly data.

- [ ] **Storage arbitrage value.** Perfect-foresight daily spread capture for a
  one-hour and four-hour battery, per market. Directly answers what storage
  would have earned on the stored history.

- [ ] **Nodal or locational coverage.** Everything is currently a bidding zone
  or a national system, so congestion and basis are invisible. Documented as a
  limitation today. ~~CAISO OASIS LMP would be the natural first step~~ — CAISO
  was removed 2026-09-13; no currently-registered zone publishes nodal prices,
  so this would need either reviving CAISO under a found independent check or
  registering a new nodal-price zone from scratch.

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
  **Superseded 2026-09-13:** moot now that both remaining CCEE submarkets were
  also removed — see the next decision.

- **Four zones, two keyless and independently-checkable sources.** Decided
  2026-09-13, at the user's confirmation after this session found the
  resolution-transition review finding and proposed the trade-off. ERCOT, PJM,
  CAISO, AU-NSW1, JP-TOKYO and both CCEE submarkets were removed because each
  either needed a credential this project chooses not to require, or had no
  publisher independent of the one already stored to check it against — see
  "Removed for lack of independent verification" above. Do not re-add any of
  them without either resolving that gap or getting the user's sign-off to
  accept the weaker guarantee.

## Constraints that must survive any future work

- Missing observations stay missing. Never synthesize provider data.
- External credential and provider restrictions stay documented as pending.
- After any change to ingestion or metrics, regenerate and commit `site/data`,
  or CI fails its freshness check.
- The domain rules in `STATE.md` are enforced by named tests. Do not relax a
  test to make a change pass.
- Every registered zone's source needs no credential, no manual import step,
  and must be checkable against a publisher independent of itself. Re-run
  `scripts/audit_external.py` after touching a source adapter or the zone
  registry.

## Commands

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\gpa.exe validate
.\.venv\Scripts\gpa.exe audit
.\.venv\Scripts\gpa.exe stats
.\.venv\Scripts\gpa.exe export
.\.venv\Scripts\gpa.exe export --check
npm run build
node scripts/browser-smoke.mjs
.\.venv\Scripts\python.exe scripts\audit_external.py
```

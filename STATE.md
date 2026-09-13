# PROJECT STATE

Last recorded delivery: 2026-09-13 (scope reduction and independently-audited
rebuild) · Repository: `global-power-atlas` · Replaces: `power-pulse-global`

## Scope reduction and rebuild, 2026-09-13 (supersedes the review below)

The [full review](docs/REVIEW-2026-09-13.md) below found a window-wide
resolution-inference defect mislabelling part of the European price history,
plus other data, metric and workflow gaps. Rather than patch each source
adapter in place, the registry was cut back to the two sources that need no
credential, block no automated client, and can be checked against a publisher
independent of themselves: Energy-Charts (DE-LU, FR, ES) and ONS (BR-SIN).
ERCOT, PJM, CAISO, AU-NSW1, JP-TOKYO and the two CCEE submarkets (and their
adapters: `aemo.py`, `caiso.py`, `ccee.py`, `eia.py`, `jepx.py`,
`openelectricity.py`) were removed rather than kept half-verified. This work
was interrupted mid-rebuild and resumed and finished in this session; see
`git log` for the commit this produced.

What was done, in order:

1. **Registry and adapters trimmed** to `{DE-LU, FR, ES, BR-SIN}` /
   `{energy_charts, ons}`. A named test,
   `test_active_markets_need_no_market_credentials_or_manual_imports` in
   `tests/test_data_review.py`, asserts this so the claim cannot silently
   drift again.
2. **`scripts/rebuild_verified.py`** re-fetched every partition for the four
   remaining zones from provider responses cached under
   `.gpa/provider-audit/`, writing to a side directory
   (`.gpa/audited-data/`) rather than the live store, so an interrupted run
   could never leave `data/curated` half-written. Re-running it resumes from
   the cached responses already retrieved.
3. **`scripts/audit_external.py`** checked the rebuild against a publisher
   independent of the one already stored: SMARD for DE-LU/FR price, OMIE for
   ES price, and the ONS hourly subsystem balance for BR-SIN load. Energy-Charts
   redistributes ENTSO-E, so checking it against itself would have proven
   nothing. Result: **zero price mismatches** over the full two-year history
   against both SMARD and OMIE. BR-SIN load matched best at zero timestamp
   offset (median absolute difference 1.3%, consistent with the two ONS
   publications using slightly different load definitions, not a labelling
   error). One open question, not a confirmed defect: the solar generation
   "clock" check (peak output should sit near local solar noon) reads Spain's
   generation timestamps as marginally closer to interval-*end* than
   interval-*start* (about 7 minutes at quarter-hour resolution, against a
   heuristic coarse enough that this may just be noise) — see the review
   priorities in `TODO.md`.
4. **The audited data was promoted into `data/curated`**, replacing the old
   partitions for these four zones. `gpa validate`, the full test suite, ruff,
   mypy, `gpa export`/`--check`, `npm run build` and the 14-route/viewport
   browser smoke all pass against the rebuilt store.
5. **Deploy was made conditional on CI**, closing the review's "deploy is not
   gated on CI" finding: `deploy.yml` is now a `workflow_call`-only workflow
   that publishes one exact commit SHA. `ci.yml` calls it (with a new
   browser-smoke step added to the site job) only after a push to `main` where
   both jobs pass; `ingest.yml` calls it with the commit it just made,
   independent of whether the freshness check that runs after that commit
   passes — a quiet provider must not hold back observations every other
   market published successfully.
6. **Docs, workflows and the site's own prose were brought in line**:
   `README.md`, this file, `TODO.md`, `site/methodology.md` and the other site
   pages (color legends and narrative text) no longer describe the removed
   zones as live; `pyproject.toml`, `scripts/github_setup.py` and
   `ingest.yml` no longer reference `EIA_API_KEY`/`ENTSOE_API_KEY` or a CCEE
   secret step, since no remaining source needs one.

The forecasting benchmark numbers changed once the underlying DE-LU price
history was corrected — exactly the review's warning that "a capped benchmark
still changes when revised inputs arrive." Current numbers are in `README.md`
and reproduced by `gpa backtest`; do not average this run's figures with any
figure quoted before this rebuild.

### Review findings not yet addressed

Only the resolution-transition/provenance finding (review priority 1) was
closed by this rebuild. Still open, and now scoped to just four zones:
duration-aware metrics and capture alignment for repeated autumn hours (review
priority 2, though `test_data_review.py` now covers some of this — see
`TODO.md`), source-semantics/gap reporting beyond dataset-level freshness
(priority 4), and experiment-input versioning ahead of a prospective forecast
evaluation (priority 5). Priority 3, the deploy/CI gating gap, was closed in
step 5 above.

## Original review, 2026-09-13 (superseded above for scope; data findings below are why the rebuild happened)

All implementation changes were committed as `083063a` at the user's request.
The subsequent [full review](docs/REVIEW-2026-09-13.md) found unresolved data,
metric and workflow defects despite passing existing checks. Treat its findings
and the new review priorities in `TODO.md` as the current handoff, ahead of older
claims that domain rules are completely enforced.

The remote scheduled ingestion advanced `main` to `92a4d83` and deployed
successfully, with 3,604,404 observations. The local implementation snapshot
still contained 3,602,004. That divergence is moot now: the store for all four
remaining zones was rebuilt from scratch in the increment above, superseding
both figures.

Main findings: window-wide resolution inference mislabels part of September
2025 European price history (fixed above); some duration metrics still count
rows; capture rates merge repeated autumn delivery hours; explicit upstream
failures can prevent persistence of other successful targets; deploy was not
gated on CI (fixed above). The forecast API requires target values to choose
prediction rows, and a capped benchmark still changes when revised inputs
arrive (confirmed above — it did). Details and reproductions are in the review.

## Current result

Global Power Atlas is a Python ETL and Observable Framework static site. It
stores validated interval data as Parquet, computes market-aware aggregates,
and publishes them without a backend or browser-visible credentials.

- 4 registered zones: Germany-Luxembourg, France, Spain and Brazil (SIN).
  Every zone's source needs no credential and no manual import.
- 2,546,845 interval/fuel observations in 275 validated monthly partitions,
  rebuilt and independently checked against SMARD, OMIE and the ONS hourly
  balance (see "Scope reduction and rebuild" above).
- Price: DE-LU, FR, ES. Brazilian PLD by submarket (CCEE) is not registered.
- Load and generation: DE-LU, FR, ES, BR-SIN, subject to each provider's
  reported categories.
- 24 aligned months of World Bank fuel, EEX EUA and ECB FX references.
- Historical German clean spark and clean dark screening spreads with explicit
  efficiency, emissions and coal-energy assumptions.

Steps 1-6 of the original delivery are complete. [`TODO.md`](TODO.md) now
holds the forward roadmap, ordered by how much each item affects
the credibility of the published work. Treat it as the authoritative list of
what is still missing.

## Current local increment

- Browser smoke follows the navigation configuration, including the existing
  forecasting page, at 1440 px and 390 px. HTTP failures and missing visible
  Plot charts now fail the check; methodology is explicitly text-only.
- `GPA_SCREENSHOT_DIR` allows local checks to keep captures outside the
  versioned screenshots directory.
- Validation: `npm run build` passed for seven pages; `npm run test:browser`
  passed all 14 page/viewport combinations against the local preview, with no
  runtime errors or horizontal overflow. A temporary HTTP 503 page containing
  only a legend SVG correctly failed with exit code 1 for both the HTTP error
  and missing chart. This is UI validation, not an audit
  of the forecasting methodology or a hosted deployment check.
- Forecasting code, CLI/export integration, page and exported tables were
  already present as uncommitted work, including a fixed LightGBM challenger
  and optional local MLflow tracking. The continuation requested on 2026-09-13
  exercised the existing acceptance tests; it was subsequently committed as
  `083063a`. Publication and the new review findings remain pending.
- Interval coverage and mean width now come from full-precision Python scoring,
  including MLflow metrics. The browser previously calculated coverage from
  rounded chart values, which could change inclusion at an interval boundary.
- Acceptance tests cover unavailable future prices/actuals, daily refits,
  validation-only ridge selection, common samples, incomplete hours and days,
  both DST transitions, negative skill, and hand-calculated pinball/coverage.
- The local benchmark scores all five models on 7,982 of 7,986 target cells
  across 333 calendar days (2025-10-15 through 2026-09-12). Ridge MAE is
  21.06 EUR/MWh, LightGBM 22.18, best naive 28.43. LightGBM loses to the best
  naive on negative hours (-3.5% skill) and scarce hours (-9.5%).
- Historical inputs are the latest provider revisions. This is a retrospective
  development benchmark, not an untouched holdout or proof of publication-time
  data availability. Prospective evaluation remains a separate open TODO.
- Validation on 2026-09-13: 259 tests passed, 81.47% overall coverage against
  the 75% floor; ruff lint/format and strict mypy passed (33 source files).
  All 616 partitions passed validation. The 18 site exports were regenerated,
  `gpa export --check` passed and `npm run build` built all seven pages.
  Browser smoke passed all 14 route/viewport combinations (1440 px and 390 px),
  with no runtime errors, missing charts or horizontal overflow. Captures are
  under ignored `.gpa/screenshots/`. No commit, push or hosted verification was
  performed in this continuation.

## Completed in the current delivery

- Export freshness checks compare table values instead of Parquet binary
  metadata; build-clock fields were removed from committed metadata.
- Scheduled ingestion now triggers deployment even when the commit is created
  by `GITHUB_TOKEN`.
- Duration-weighted load/price metrics and gap-aware negative-price runs are
  covered by regression tests.
- German missing price months were backfilled.
- Browser smoke covers every page at 1440 px and 390 px, checking JavaScript
  errors, chart presence and horizontal overflow. Screenshots are under
  `docs/screenshots/`.
- EIA parsing was fixed and two years of ERCOT, PJM and CAISO load/generation
  were backfilled.
- France and Spain were added through Energy-Charts.
- Tokyo day-ahead price was added through official JEPX fiscal-year CSVs.
- Official CCEE `pld_horario_2026.csv` was imported for BR-SECO, BR-S, BR-NE
  and BR-N: 6,117 consecutive hourly rows per submarket, without gaps,
  duplicates or null prices.
- Official monthly fuel/carbon/FX references and a dedicated spread page were
  added.
- Public repository created at
  <https://github.com/Pedrods20/global-power-atlas>; `EIA_API_KEY` is stored as
  an encrypted Actions secret. The local `.env` is ignored.
- Commit `aa1ea71` was pushed to `main`; CI run `34725613826` and Pages deploy
  run `34725613889` completed successfully.
- The public site at <https://pedrods20.github.io/global-power-atlas/> passed the
  six-route desktop/mobile browser smoke against the hosted build.

## Completed on 2026-09-13

- France and Spain were backfilled from 4 to 25 monthly partitions each, so
  every Energy-Charts zone now carries the same two years.
- CCEE 2024 and 2025 were imported from the official CSVs: 23,661 contiguous
  hourly observations per submarket from 2024-01-01, no gaps or duplicates.
- The Brazilian South and North submarkets were removed from the registry and
  their partitions deleted, at the user's request.
- Brazilian load was moved off the balance file and onto the ONS verified-load
  API. The lag fell from 46 hours to under one hour. The series was rebuilt at
  half-hourly resolution: 35,040 observations over exactly 730 days.
- Brazilian generation keeps its roughly two-day lag, which is the provider's
  and has no faster ONS source. It is now documented under "Publication lag" in
  the methodology instead of looking like a collection failure.
- Six routes pass the browser smoke at 1440 px and 390 px with no JavaScript
  errors and no horizontal overflow.

## Quality audit, 2026-09-13

Verdict: **approved with reservations.** Measured, not asserted. All three
reservations were then closed in Sprint 1 on the same day; see "Sprint 1
outcome" below.

What holds up. The domain rigour is real and enforced by named tests: 23-hour
and 25-hour local days, NEM market time separate from civil time, NERC blocks
that include Saturday and do not shift a Saturday holiday, negative prices kept
with arithmetic differences instead of log returns, annualisation on 365, power
integrated over each interval's real duration. The 95 percent carbon-coverage
guard that refuses to publish a Brazilian intensity is the most mature decision
in the repository: it prefers silence to a biased number.

Three reservations, each with evidence.

1. **A declared standard that nothing enforces.** `pyproject.toml` sets mypy to
   `strict`; CI never runs it. `mypy` reports 17 errors across 5 files. The
   original modules are typed and the expansion modules are not, so the standard
   drifted silently. `jepx.py` and `ccee.py` have unannotated functions, which
   is also why `sources/__init__.py:39` fails the `Source` protocol check.

2. **The orchestration has never been exercised.** Coverage is 53 percent, and
   the distribution is the point: `calendar.py` 98, `store.py` 96, `metrics/`
   84-97, but `export.py` 28, and `pipeline.py` and `cli.py` at 0.
   `pipeline.py` decides what becomes skipped, failed, written or empty, which
   is the entire resilience story of the daily cron, and no test has ever run
   it.

3. **The public site trails the repository.** Two commits are unpushed, so the
   France and Spain backfills, the CCEE history and the Brazilian load fix are
   not live.

None of the three breaks anything today. All three were scheduled into Sprint 1
rather than deferred, because machine learning and a retrieval layer are next
and both will add a great deal of code.

## Sprint 1 outcome, 2026-09-13

All three audit reservations are closed.

| | Before | After |
|---|---|---|
| mypy errors | 17 | 0, and enforced by CI |
| `pipeline.py` coverage | 0% | 100% |
| `cli.py` coverage | 0% | 95% |
| `sources/base.py` coverage | 49% | 88% |
| Total coverage | 53% | 76%, gated at 75% |
| Tests | 119 | 176 |

Pushed as `d884bb5`; CI and Deploy both succeeded, so the gates are proven on
the Linux runner and not only on the Windows workstation.

The protocol failure had a cause worth recording. Adapters wrote
`max_window_days = None` with no annotation, so mypy inferred `None`, and
Protocol attributes are invariant, so it did not satisfy the declared
`int | None`. All seven adapters now annotate the three protocol attributes
explicitly.

The coverage floor is global rather than per-module, deliberately. What remains
uncovered is the HTTP call sequence inside each adapter's `fetch`. Their
testable logic is already extracted into pure functions with fixture coverage,
and the retry and throttle behaviour they share is now tested once in
`sources/base.py`. The reasoning is recorded in `TODO.md` so it is not mistaken
for an oversight.

## P2 progress, 2026-09-13

California now has a price. CAISO OASIS is the only one of the three large US
markets reachable without a credential, and it carries 17,520 contiguous hourly
day-ahead observations at the SP15 trading hub over two years.

ERCOT and PJM remain demand and generation only, and the reason is access
rather than effort. Every ERCOT host answers 403 to automated clients and
`mis.ercot.com` fails the TLS handshake; PJM needs a free Data Miner
subscription key. Both are recorded in `TODO.md` with the legitimate route to
unblock them. No attempt was made to defeat ERCOT's bot protection.

Three OASIS behaviours are handled, each with a named test, because each fails
without raising:

- An LMP is five components, and only the total is a price. The others are
  energy, congestion, loss, and a greenhouse-gas term that exists because
  California prices carbon into dispatch.
- An empty window returns XML inside a 200 response, carrying error code 1000.
  A CSV parser reads it as a table whose only column is the XML declaration,
  which is how it first surfaced.
- Rate limiting also returns 200, carrying HTML rather than a 429, so the
  shared retry layer cannot detect it. Requests are paced at six seconds.

The window cap is 30 days rather than 31: OASIS counts calendar days touched,
so a 31-day window starting mid-afternoon spans 32 and is rejected with error
1004.

What the series shows: the NERC on-peak block has cleared below off-peak at
SP15 for three consecutive years, 12.04 percent of day-ahead hours are
negative, and solar captures 0.603 of the time-weighted average price against
0.971 for wind.

## P3 outcome, 2026-09-13

Operations closed. The pipeline can no longer stall in silence.

`gpa freshness` measures every declared series against a rule declared per zone
and dataset, and the scheduled workflow fails on a breach and opens an issue
labelled `ingest-failure` carrying the log link. Repeated failures comment on
the open issue rather than filing one nightly.

Two ordering decisions matter and are easy to get backwards:

- The check runs **after** the commit. Whatever was fetched successfully should
  be persisted even when one feed is quiet; running it first would let a single
  stale provider discard a day of good observations from every other market.
- The issue step runs **last**, so a failure in any earlier step reaches it,
  including the commit and push themselves.

Rules are per series because the providers differ legitimately: 96 hours for
Brazilian generation, since ONS trails by two days; 48 for US generation, since
EIA restates on a day's delay; 36 by default. Every rule carries its reason and
a test asserts none is left unexplained.

The two CCEE zones are declared manual: they report staleness but never fail
the run, because nobody can refresh them from a cron job and a nightly failure
nobody can act on trains people to ignore the alarm. Their age is published on
the front page instead, next to every other series measured against its own
limit.

## Architecture

```
GitHub Actions daily cron
  → gpa ingest / gpa benchmarks
  → schema validation
  → partitioned Parquet committed to git
  → gpa export
  → Observable Framework static build
  → GitHub Pages
```

Important paths:

- `src/gpa/zones.py`: market registry, timezone, currency and block rules.
- `src/gpa/sources/`: adapters; fetch and normalize, but do not analyse or
  write.
- `src/gpa/schema.py`: canonical interval contracts and fuel taxonomy.
- `src/gpa/store.py`: monthly Parquet upserts and DuckDB views.
- `src/gpa/metrics/`: price, load, mix and spread calculations.
- `src/gpa/benchmarks.py`: World Bank, EEX and ECB reference ingestion.
- `src/gpa/export.py`: compact site tables and deterministic freshness check.
- `site/`: Observable pages; `site/data/` contains committed exports.

## Domain rules

- Store UTC-aware interval-start timestamps; group through market-local time.
- Integrate MW over each observation's duration; never assume an hourly row.
- Apply each market's declared peak block. Do not substitute daily max/min.
- Preserve negative and zero prices. Use arithmetic price changes.
- Keep currencies separate except where historical ECB FX is explicitly part
  of a European spread calculation.
- Missing observations stay missing. Do not synthesize provider data.
- Withhold carbon intensity below 95% known-factor generation coverage.
- Label carbon intensity and thermal spreads as estimates with assumptions.
- Every registered zone's source must need no credential and no manual import
  step, and must be checkable against a publisher independent of itself
  (`scripts/audit_external.py`). This is what the 2026-09-13 scope reduction
  enforces and `test_data_review.py` asserts; do not register a new zone that
  fails either test without discussing it with the user first.

## Provider constraints

- The ONS verified-load API exposes a `SIN` aggregate that answers with every
  value zeroed, so national load is summed from its four submarket areas and a
  timestamp is only kept when all four reported. Its Southeast code is `SECO`;
  the older `SE` returns an empty list rather than an error.
- The ONS hourly balance trails real time by about two days. No faster ONS
  source for generation by technology exists.
- Energy-Charts can return HTTP 429 during long backfills; the HTTP layer
  retries and honours `Retry-After`. It moved the European day-ahead auction
  from hourly to quarter-hourly products on 2025-10-01; the adapter now infers
  resolution per request window rather than once per backfill (this was the
  2026-09-13 rebuild's fix).
- Fuel and carbon inputs are monthly reference benchmarks and carry basis risk.
- EIA, PJM Data Miner, AEMO/OpenElectricity, JEPX and CCEE were the sources
  behind the seven zones removed on 2026-09-13. Their constraints (credential
  requirements, HTTP 403 to automated clients, manual CSV import) are recorded
  in `TODO.md` under "Removed for lack of independent verification" as the
  reason each was dropped, not as an active integration note.

## Resume instructions

Work from `C:\Users\Pedro\Desktop\Python\global-power-atlas`. Read
`TODO.md` first and inspect `git status` for existing work. Do not use the
old `power-pulse-global` directory.

Sprint 1 and P3 are complete. Front C is retrospectively validated locally on
the rebuilt four-zone store; preserve its existing implementation and
distinguish local validation from publication and prospective acceptance.
Remaining Front C TODOs track both. Read `docs/REVIEW-2026-09-13.md` for the
full original review, and the "Scope reduction and rebuild" section above for
what it triggered and what remains open from it.

As of this session's end, the scope-reduction and rebuild work described above
is complete in the working tree (`git status` will show the deleted adapters
and zone data, the promoted partitions, the workflow changes and the doc
updates) but **not yet committed**; check whether a later session or the user
committed it before assuming it is still pending.

Three scope decisions are settled and should not be reopened without the user:

- **No Airflow.** Orchestration stays on GitHub Actions. Airflow would need a
  scheduler, a metadata database and a webserver, none of which fit the free
  runner, and it would cost the property that anyone can clone this repository
  and reproduce the pipeline with no infrastructure.
- **Forecasting before retrieval.** The evaluation harness built for Front C is
  what will later decide whether regulatory signals improve anything.
- **Four zones, two keyless and independently-checkable sources.** Decided
  2026-09-13. Do not re-add ERCOT, PJM, CAISO, AU-NSW1, JP-TOKYO or the CCEE
  submarkets without either a credential-free path or a second independent
  publisher to check the data against — see "Why four zones" in `README.md`.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\gpa.exe validate
.\.venv\Scripts\gpa.exe export
.\.venv\Scripts\gpa.exe export --check
npm run build
.\.venv\Scripts\python.exe scripts\audit_external.py
```

### Prompt for the next AI

> Work in `C:\Users\Pedro\Desktop\Python\global-power-atlas`. This is a Python
> ETL and Observable Framework static site publishing wholesale electricity
> market data for 4 zones (Germany-Luxembourg, France, Spain, Brazil's national
> system), already live at
> <https://pedrods20.github.io/global-power-atlas/> (the live site may still
> show the older 11-zone build until this increment is committed and deployed
> — check before assuming the two match).
>
> Read `STATE.md` and `TODO.md` before touching anything, and read
> `site/methodology.md` before touching any metric. The domain rules in
> `STATE.md` are each enforced by a named test; never relax a test to make a
> change pass.
>
> Set up with `python -m venv .venv` and
> `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Baseline as of this
> session: 259+ tests passing, ruff clean, mypy clean, coverage at 85 percent
> against a 75 percent gate, `gpa validate` reporting 275 valid partitions, and
> `gpa export --check` clean. Recheck these before publishing.
>
> Sprint 1, P3 and the 2026-09-13 scope-reduction rebuild are complete. Follow
> `TODO.md` for remaining acceptance: the still-open review priorities (2, 4
> and 5 — duration-aware metrics, source-semantics/gap reporting, and
> experiment-input versioning), Front C publication and prospective
> evaluation, then Front B regulatory retrieval after Front C.
>
> Do not introduce Airflow, do not start Front B before Front C, and do not
> re-add a removed zone without either a credential-free source or an
> independent publisher to check it against. All three are recorded under
> "Scope decisions" in `TODO.md`.
>
> After any change to ingestion or metrics, run `gpa export` and commit
> `site/data`, or CI fails its freshness check. If you touch a source adapter
> or the zone registry, re-run `scripts/audit_external.py` before trusting the
> result.

After changes to ingestion or metrics, regenerate and commit `site/data`.
Keep `TODO.md` current as the handoff record and preserve the scope decisions
above. Do not equate the local retrospective scores with prospective results,
and do not average this rebuild's forecast numbers with any figure quoted
before it — the underlying price history changed.

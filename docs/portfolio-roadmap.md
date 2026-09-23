# Energy-market portfolio roadmap

Review date: 14 September 2026. Baseline: `074ad42` on `main`.
The checkpoint below separates implemented work from the remaining plan.

## Execution checkpoint — start here when resuming

Updated: 14 September 2026. User authorized starting P0/P1 and requested a
written plan before code changes. No GitHub publication is authorized by this
request. This section is the single implementation log; do not create parallel
STATE/TODO/handoff documents.

**Current state (15 September 2026): A-F committed; portfolio steps 1-2 / the
bounded G sensitivity study are implemented, verified and committed.** Baseline
before this increment: `7b4aefe`, 334 tests passing, lint and strict typing
clean. The review found that F's export omitted two available naive
comparators, its README promised cost tables not displayed by the site, and
its cumulative battery chart was blank despite smoke tests; all three are
fixed and verified below. The battery engine, forecast snapshot, ledger and
ingestion core were not reopened. The English executive case (site/index.md)
is also implemented and verified below. No push, public deploy, real
issuance or live pilot is authorized. **The DE-LU history extension is now
re-frozen and reconciled** in release `b0e69bf47f6e230be9b5`; the prior
`5e3c9f1a256ec73c62e2` release remains for provenance. The current headline
pages and README now use the 2019-onward benchmark. The old session-close
instructions below are retained as historical handoff evidence. **Historical
market regimes (A) is done** (`edb79d8`, forecast page narrative). **Pre-auction
fundamentals (B) is done as an ablation, not adopted**: Ridge MAE 22.07 →
~19.17 EUR/MWh, LightGBM 25.56 → ~20.55 EUR/MWh on the identical frozen test
sample, driven mostly by a reversal in the negative-price regime; the
scarcity regime gets worse for LightGBM. Full evidence below. The published
release, README and site are unchanged; adopting this as a new frozen release
is an explicitly separate, undone decision. User chose (15 September 2026) to
proceed straight to the capacity/renewables/storage scenario study (P3), full
scope, and to leave the fundamentals-adoption decision open for later. That
plan was then rethought before coding: reframed from a data-engineering-heavy
scenario/asset-screening study around a new Marktstammdatenregister ingestion
to a market-mechanism study (cannibalisation via the already-built,
never-wired `capture_rate`; capacity from Energy-Charts' `/installed_power`,
which already carries the government's own EEG2023/WindSeeG targets) once
research showed the heavier approach added engineering risk without adding
market-analytical signal. **P3 is now done, all 8 steps**, committed and
verified in a real browser: solar's capture rate fell 0.93 -> 0.51 (2019-2026)
as capacity grew, correlated with the price-shape metrics (r=-0.89 to -0.91);
every 2030 policy target already exceeds anything realised; battery-fleet
competition is not yet visibly compressing this project's own arbitrage
margin (a positive, not negative, correlation so far); two real sourced
citations (BNEF battery pricing, a BNetzA auction result) and a new
"Is this margin durable" section on `site/battery.md`. Full evidence below.
**A review pass against that P3 work then found and fixed two real bugs and
two real documentation/contract gaps** (16 September 2026), detailed further
below: a clock-year-dependent (not data-dependent) correlation cutoff that
could have changed results on 1 January with no new data; a wind fundamentals
fetch that silently summed onshore-only when offshore data was missing rather
than dropping the interval; forecast-snapshot metadata that did not identify
a fundamentals-ablation run; and `forecast_predictions.parquet` being
documented as the battery study's reproducible input while actually holding
only the 12-week browser preview. All fixed, tested, verified in a real
browser again, and a live spot-check across five years confirmed the wind bug
never actually corrupted the already-published fundamentals-ablation numbers.
**Next:** user chose P4 (package the evidence for recruiting) over the
fundamentals-adoption decision. Plan recorded immediately below, before any
edit. **P4 is now done except one deferred item** (15 September 2026): site
disclosures, methodology accuracy fixes and README's personal-contribution
line are all in; the fundamentals ablation is now shown on the forecast page
as a labelled diagnostic (Ridge 22.07 -> 19.17, LightGBM 25.56 -> 20.56
EUR/MWh, on the published test window) via a new, separate
`data/reference/fundamentals_ablation/` artifact that never touches
`data/experiments/current.json` (verified by hash, not assumed) -- adopting
it as a new frozen release remains a separate, undone decision. The home-page
commercial comparison was explicitly deferred by the user after four sources
proved unverifiable in this environment (paywalled, 403, or unparseable).
GitHub About/topics text was drafted for the user to apply; no `gh` access
exists here. Full evidence below.

**UI review pass (15 September 2026, user-requested): three broken interactive
charts on the forecast page found and fixed, plus one cosmetic Plot warning.**
Reviewed every page (index, forecast, battery, methodology) at four viewports
with Playwright, checking for horizontal overflow, chart-vs-container fit,
`NaN`/`undefined` leakage and console errors -- none of those were present.
But three of the forecast page's five interactive selectors silently never
updated their chart after the first render: "Explore the full breakdown"
(`scope`/`split`), "Where the models fail"'s daily-error chart
(`selectedModel`/`modelDaily`), and "Inspect a week" (`weekModel`/`week`/
`weekRows`). Root cause, confirmed with an in-page debug probe: each paired a
`const x = view(Inputs.select(...))` declaration with a derived `const y = ...x...`
in the *same* fenced JS cell. The dropdown's own value (`x`) updated correctly
and was picked up by other cells that referenced it directly (e.g. a chart
title), but `y` -- computed from `x` inside that same cell -- froze at its
first, usually-empty computation and never recomputed on later interaction,
even though downstream cells reading `y` did re-render (with the stale value).
battery.md's one `view()`-driven selector was already correctly isolated in
its own cell (confirmed working, matches its existing passing smoke-test
assertions), which is what exposed the pattern. Fixed by splitting each of the
three pairs into separate cells in `site/forecast.md`, matching battery.md's
convention; re-verified interactively (circle counts on the breakdown chart
go 15/120/10/15 across the four options where they were 0 before; the
daily-error line path now differs by model; the week chart's path count
changes with the week selector). Separately, the index page's generation-mix
bar chart triggered a Plot console warning ("data ... appear to be dates ...")
because its `x` scale used a `"MM.YYYY"`-style field without an explicit type;
fixed with `x: {type: "band", ...}` in `site/index.md`, confirmed the warning
is gone. `npm run build` and `npm run test:browser` both pass after the fix.
No data, export or CLI code was touched -- this was a `site/*.md` reactivity
bug, not a pipeline bug. Committed as `c0ae798`.

**P5, the editorial pass, is done** (16 September 2026): the README, all four
site pages and one module docstring were rewritten or extended for executive
framing, consolidated premises, full source provenance and a references
section, with no data, model or reactive-cell change and no published number
altered.

**P6 is code-complete and proven locally** (16 September 2026): the prospective
issue path produced its first real pre-gate forecast, two defects that only
appear when the system runs forward were found and fixed, and the
fundamentals-adoption question became a running experiment instead of a stalled
decision. **Next:** the single open item blocking a daily prospective record is
a decision, not work — GitHub Actions needs this branch pushed (41 commits
ahead of `origin/main`), which has never been authorized here, and a local
scheduler is the alternative. The deferred commercial comparison and the GitHub
About/topics metadata remain open too.

**P7 — the P6 review pass — is recorded below and in progress** (16 September
2026): a line-by-line review of P6 reproduced a defect that makes the
fundamentals arm unable to issue at all, found that the two arms are not
separable in the ledger, and found three published statements that promise what
the code does not do. Plan immediately below, evidence after it.

**P8 — full product review and repositioning — recorded below, in progress**
(22 September 2026): the user asked for a complete review of everything already
built — texts, titles, findings and insights — against one question: is this
useful to someone hiring for European power-market analysis? The review found
the project technically healthy and commercially mispositioned, and found that
the repositioning work of the last five increments was never published at all.
Plan immediately below, evidence after it.

### P8 — reposition as a market research note, publish it, and start the ledger — recorded before code

**Review findings (22 September 2026), verified rather than recalled.** The
working tree passes 415 tests, `ruff check`, `ruff format --check`, `mypy` and
`gpa export --check`. Three failures, in order of severity:

1. **The public product does not exist.** `main` here is 42 commits ahead of
   `origin/main`, which still points at `074ad42`. Fetching the live site
   confirms it: the home page's H1 is "Historical dashboard" with no headline
   numbers, and `/battery` carries only "Economic scoreboard" and "Dispatch
   example" — no durability section, no cannibalisation, no capacity, no BNEF
   citation. Every increment from P3 through P7 is invisible to any reader who
   opens the README's badge link.
2. **The project is organised as a model-validation report**, not as market
   research. The central question — does a validated forecast create battery
   value — is applied data science. None of the home page's three findings is a
   statement about the German market.
3. **The strongest market analysis in the project is buried and written
   defensively.** It sits in section 5 of page 3, and the hedging markers in the
   running prose count 6 on `index.md`, 30 on `forecast.md` and 26 on
   `battery.md`. `site/battery.md:309` carries a double negative over a
   correlation.

Supporting detail found in the review, all read from the committed site tables:

- The market story is already computed. Solar capture rate 0.93 (2019) to 0.51
  (2026 YTD); on/off-peak spread +10.6 to **-13.7 EUR/MWh**, so peak hours are
  now on average cheaper than off-peak; negative hours 2.4% to 7.2%; solar 46 to
  118 GW; the battery fleet 1.6 to 21.1 GW and 2.3 to 32.9 GWh. Against all of
  that, the value a 4h battery can extract rose from 253 EUR/MW/day (2023) to
  **451 EUR/MW/day (2026 YTD)**, the highest since the gas crisis. The daily
  shape moved from peak-versus-offpeak to midday-trough-versus-evening-peak; it
  did not flatten. The fleet's ~1.6h average duration says it is mostly domestic
  storage, not grid-scale competing for this spread — an argument the site has
  the data for and does not make.
- Scale is never stated the way a desk thinks. EUR 29,014/MW spans 2,404 days,
  about 4,400 EUR/MW/year or 12 EUR/MW/day. The exported
  `mean_daily_incremental_eur_mw` is 12.07 and is not shown anywhere.
- The one-cycle-per-day cap binds on 2,403 of 2,404 days
  (`equivalent_cycles = 2403.69`). A real German BESS cycles 1.5-2.5 times daily
  across day-ahead and intraday.
- `site/index.md`'s front matter still reads `title: Historical dashboard`,
  contradicting its own executive H1 and supplying the live site's title.
- This document's tail ("Positioning", "Baseline findings before
  implementation") still quotes MAE 21.04, 8,971 cells, 369 days and EUR 7,024 —
  superseded by 22.07, 58,645, 2,404 and 29,014 in the checkpoint above. P3's
  original text (Marktstammdatenregister, 2027-2030 scenarios, a CAPEX screening
  sheet) was correctly redirected during planning but still reads as the standing
  plan.

What the review found worth protecting, unchanged: the 12:00 gate and the
vintage discipline; Ridge beating LightGBM in the tails (scarcity skill +16.0%
against -56.7%; negative-price -3.9% against -35.1%); `naive_similar_day` being
the best dispatch comparator despite being the second-worst price forecaster;
publishing the 827 of 2,404 days on which the model loses; the frozen
content-addressed release with `gpa export --check`; and the two-armed
prospective design with recorded abstentions.

**User decisions taken before this plan was written** (22 September 2026), each
chosen from three options: publish now and again at the end; reposition the
whole product around the market thesis rather than patching it; answer the
asset-realism gap with a price-shape decomposition plus a two-cycle sensitivity
instead of re-freezing the release; and start the prospective ledger now,
presenting it as a running counter rather than a promise.

**The thesis the product will argue.** Solar killed the German peak premium and
doubled the spread a battery monetises. The forecast and battery studies stop
being the product and become the two pieces of evidence supporting it.

**Steps.**

1. Publish what already exists. Commit P7, push, let CI gate the deploy, verify
   the live site, and let `forecast.yml` begin accumulating the ledger.
2. New analysis: mean hourly price profile by year (2019 against 2026), the
   annual intraday max-minus-min spread shown beside the on/off-peak spread, and
   a two-cycles-per-day battery scenario added as a sensitivity without
   re-freezing the release.
3. Rewrite the home page as a research note: market-first H1 and findings, and an
   explicit "what would make me wrong" block naming the gas-premium unwind, a
   lengthening fleet duration and market reform.
4. Editorial pass on the evidence pages: fix the front-matter title, retitle
   around market facts, cut the hedging to one disciplined block per page, remove
   the double negative, report every incremental figure in EUR/MW/year and
   EUR/MW/day beside the cumulative, add a sourced paragraph on FCR/aFRR and
   intraday positioning this study as a lower bound, and show an honest
   issued/abstained counter.
5. Documentation hygiene: README led by the thesis; this document's stale tail
   marked superseded; P3's original text marked as not executed as written.
6. Publish again and verify: tests, lint, types, `gpa export --check`, build,
   browser smoke, push, live check.

**Acceptance.** A power-market analyst opening the live URL can state the market
claim, the evidence behind it and the main risk to it within a minute, without
reading a methodology page first. Every number on the site is reproducible from
the committed release, and no figure appears only as a multi-year cumulative.

### P8 handoff evidence — published, repositioned, and the shape thesis measured

**Step 1, publish, is done and verified against the live site.** `main` was
pushed to `origin/main` as `1cce26c`, CI gated the deploy, and fetching
`https://pedrods20.github.io/global-power-atlas/battery` afterwards returned the
durability section, the solar-cannibalisation sentence and the BloombergNEF
citation — none of which existed on the live site before. The 42-commit backlog
is no longer invisible, and `forecast.yml`'s two daily slots are now on the
default branch, so the prospective ledger begins accumulating without further
action.

**Step 2, the analysis the thesis needed, is done.** Two metrics were added to
`gpa.metrics.price` and wired into the export:

- `intraday_spread` — the mean within-day high-minus-low range over complete
  local days, deliberately distinct from `block_prices`' on-peak-minus-off-peak
  figure, whose own docstring already warned they are not the same number.
  Complete means at least 23 observed hours, which is what the spring clock
  change leaves; partial days are dropped rather than scaled, because a
  half-reported day has a genuinely smaller range.
- `hourly_shape` — the duration-weighted mean price for each local clock hour,
  exported beside each year's baseload so the profile can be indexed to it.

Read against the committed store, the two spreads move in opposite directions,
which is the finding the repositioning is built on:

    year  block spread  within-day range  range as % of baseload
    2019    +10.6           30.1                80%
    2022    +49.6          187.0                79%
    2024    +14.6          111.2               142%
    2025     +4.7          130.4               146%
    2026    -13.7          178.4               169%   (partial)

The 2022 row is what makes the rest readable. The gas crisis was by far the most
expensive year in the sample and its daily range, relative to its own price
level, was no wider than 2019's: a price-level shock, not a change in shape. The
widening since is the shape change, and shape is what a storage asset is paid
for. Solar drove it — its capture rate fell 0.93 to 0.51 while wind's moved 0.87
to 0.90 — and the hourly profile shows where: the midday hours now sit below the
night, and the evening ramp is a sharper peak than before.

**The asset definition was the other half of the honesty problem.** The
one-episode cap bound on 2,403 of 2,404 days, so the published benchmark was
measuring an asset that does not exist in this market. `BatterySpec` gained
`max_episodes_per_day` and `_schedule`'s two-phase DP was generalised to `2 * E`
phases: charging from an odd phase opens the next episode and is refused once
the allowance is spent. At `E = 1` this is the previous optimizer exactly, which
is the property that mattered — `battery_summary.parquet` and all 147
pre-existing sensitivity rows came back **value-identical** after the change,
checked with `assert_frame_equal` against the committed files rather than eyed.
A synthetic two-peak day confirms the generalisation does what it claims:
revenue 264.58 at one episode, 343.11 at two, 385.01 at three, with cycles
1.00 / 1.50 / 2.06.

The two-episode result is worth more than the margin it adds, because it cuts
both ways. Gross margin rises 16.4% (EUR 639,353 to 744,455 over the sample, at
1.64 equivalent cycles a day rather than a forced 2.00 — the optimizer takes the
second episode only when it pays), while Ridge's advantage over the best naive
*falls* 14.9%, from EUR 29,014 to 24,692/MW. The second episode is the midday
solar trough, which is the most predictable feature of the German day, so a
naive strategy captures it almost as well as a fitted model does. Widening the
asset adds margin that needs no forecast, which is the same conclusion the page
reaches from the other direction.

**Steps 3 to 5, the repositioning, are done.** The home page is now a market
research note whose H1 states the claim, with three market findings, the two
charts that carry them, an explicit "what would make this wrong" block naming
the gas-premium unwind, a lengthening fleet duration and market-design change,
and the forecast/battery results demoted to the evidence they are. Every
incremental figure is now given in EUR/MW/year and EUR/MW/day beside the
cumulative — the headline EUR 29,014/MW is about EUR 4,400/MW/year and EUR
12/MW/day, and not saying so was the single most misleading thing on the old
site. Titles across the site state findings rather than ask questions, the site
title and navigation were renamed, `site/index.md`'s `title: Historical
dashboard` front matter is gone, and `site/battery.md`'s double negative over the
competition correlation was replaced with a statement of what the data does and
does not support. A sourced paragraph on continuous intraday and the balancing
markets (FCR/aFRR/mFRR, via the German TSOs' joint platform) now positions the
whole study as a lower bound rather than leaving the omission implicit. The
README leads with the thesis and a table of the structural change; this
document's tail is marked superseded, with its stale figures named against the
current ones and P3's unexecuted original scope flagged.

**Two defects were found and fixed while verifying, neither introduced by this
work.** `site/forecast.md` rendered a literal `${run.input_sha256}` on the live
page, because Observable does not interpolate inside an inline code span; it now
interpolates an `html` fragment instead. And every page 404'd on a missing
favicon, now supplied inline from the config. The README screenshots were
regenerated, with the capture retried until the page rendered four charts and no
error element, after a first attempt caught a transient module-load failure.

**A real reproducibility defect surfaced while verifying, and it was not
cosmetic.** `gpa export --check` failed reporting a value mismatch in
`battery_sensitivities.parquet`'s `scenario` column, with both sides holding the
same 189 rows and the same visible head and tail. The column named in the error
was not where the difference was.

The cause is that `export_all` canonicalised row order by sorting on *every*
column, computed floats included. Regenerating the battery tables from the same
frozen snapshot and diffing them against the committed file showed the values
agree within any tolerance a comparison would accept, with one exception at the
last bits: `worst_observed_month_eur` differed by at most **1.137e-13**, which is
summation-order noise from a parallel group-by, not a change in the analysis.
That noise was enough to swap two rows whose economics are otherwise identical —
`no_trade` under `base` and under `two_episodes`, which are the same all-zero row
under a different label — and once the rows were misaligned, the float columns
still matched within tolerance while the exact string column did not. The new
two-episode scenarios did not cause the fragility; they exposed it by creating
the first pairs of near-identical rows.

The fix is `_canonical_sort`, used by both the writer and the check: order by the
exactly-comparable columns first and let floats break only the remaining ties, so
row order is a function of the data rather than of summation order. Two tests pin
it, one reproducing the swap an all-column sort makes under 1e-13 of noise. The
committed tables were rewritten in the new order without recomputing any value.

**Verification.** 422 tests pass, coverage 87.8% against a 75% floor, `ruff
check`, `ruff format --check` and `mypy` clean across 37 source files,
`npm run build` renders four pages with 11 links validated, and a Playwright pass
over all four pages at 1440px and 390px reports zero console errors, zero
horizontal overflow and no `NaN`, `undefined` or uninterpolated tokens. New tests
cover both metrics against hand-checkable values, including the partial-day drop
and duration weighting, and pin that a second episode can only add margin while
leaving the one-episode result untouched; the sensitivity count assertion now
derives from the scenario registry instead of a hardcoded number.

**Deliberately not done, and why.** The site does not yet render an
issued/abstained counter from the prospective ledger. The workflow only reached
the default branch in this session, so the counter would read zero today, and
shipping a zero counter is a weaker statement than the sentence already on the
home page saying the ledger is running and not yet long enough to score. That
counter is the first item when the ledger has real rows. The GitHub About/topics
metadata still needs applying by hand, and the deferred home-page commercial
comparison remains deferred.

### P7 — fix the prospective fundamentals arm and make it an experiment — recorded before code

Review finding (16 September 2026), reproduced rather than argued. P6 wired
`from_store_observed` into `gpa issue`, which stamps **every** archived row with
the single instant the run read the store. :func:`attach` then keeps a snapshot
only when `published_at <= gate` for that row's own delivery day. Every
historical row's gate is in the past and the stamp is now, so the whole training
history loses its fundamental features:

    observed vintage (prospective path):  trainable rows = 0
    policy vintage  (retrospective path): trainable rows = 67,399

`Ridge.predict_day` fits on `panel.complete()`, which requires every feature to
be present, so with zero training rows every delivery hour falls through and the
run abstains. Reproduced end-to-end against an isolated store with the clock
moved into a real pre-gate window, on a delivery day the archive does cover:

    DE-LU 2026-09-15: abstained, 0 forecasts, 24 abstentions
    status = abstain_missing_inputs (24/24), eligible = true, pre_gate

`gpa issue` exits 1 on any status other than `issued`, so this is a red workflow
on every day the provider publishes the delivery day in time — the opposite of
the intended behaviour. P6's live proof did not catch it because Energy-Charts
had not published 2026-09-17 at that moment, so only the fallback arm ran. The
six tests added with P6 use a single-delivery-day panel and therefore cannot
observe a training history at all.

Two further findings from the same pass:

- *The two arms are not separable.* Both scheduled runs issue
  `FORECAST_MODEL: ridge`, `POLICY_ID` is a constant that does not name the
  information set, and `ledger.canonical` de-duplicates on
  `(zone, model, delivery_date)`. Two issues carrying different information sets
  therefore collide on one key, and only the manifest distinguishes them. The
  set is also chosen implicitly, by whatever the provider happened to have
  published that minute, which contradicts this roadmap's own pilot rule:
  "Freeze the eligible models, naive comparators, issuance policy, scoring and
  battery assumptions before starting."
- *Three published statements over-promise.* `README.md`, `site/forecast.md` and
  `site/methodology.md` all state that the prospective run uses these features
  and that the ledger will answer the adoption question. It cannot yet. Separately,
  README and methodology both say SMARD "supplies the prospective path's forecast
  snapshots"; it does not — DE-LU maps every dataset including `fundamentals` to
  `energy_charts`, and `SmardSource`, `from_smard_series` and
  `combine_smard_series` are reachable only from tests. methodology also still
  describes `/public_power_forecast` as "used only in the labelled ablation" ten
  lines above the paragraph that says otherwise.

**Plan.**

1. *Mixed vintage, so the arm can train.* Add
   `fundamentals.from_store_prospective(zone, delivery_date=, retrieved_at=)`:
   historical rows keep the research-policy gate that `from_store` assigns, and
   only the delivery day carries the observed retrieval instant. The vintage
   assumption then affects the fit alone, never the information set of the
   forecast actually issued, and a post-gate retrieval is still refused for the
   delivery day. `from_store_observed` stays as the honest primitive it is; it is
   simply not the right thing to hand a panel that needs a training history.
2. *`da_forecast_age_hours` becomes metadata.* It is constant zero under the
   policy vintage and constant zero across training under the mixed one, so it
   carries no information in either; `ridge_from_moments` already drops constant
   columns to a zero coefficient, which is why this was silent rather than
   harmful. Split `FUNDAMENTAL_FEATURES` (modelled) from `FUNDAMENTAL_METADATA`
   (attached as evidence, never fitted). Verify — do not assume — that the
   published ablation numbers are unchanged by re-running
   `gpa fundamentals-ablation` and comparing against the committed artifact.
3. *Two frozen arms, not one variable one.* Register `ridge_da` as a distinct
   model identity and make `policy_id` name the information set. `gpa issue
   --model ridge` never attaches fundamentals; `--model ridge_da` always attaches
   them, and abstains honestly when the provider has not published the delivery
   day. That deletes the availability-driven branch entirely: each identity has
   one frozen information set, `canonical` keys them apart, and scoring compares
   instead of pooling. The workflow issues both daily, with the fundamentals arm
   allowed to fail without failing the job, so an honest abstention stays visible
   evidence rather than a red run.
4. *Align the published text* with what the code does, and correct the SMARD
   attribution, the `/public_power_forecast` contradiction, two "17 September
   2026" dates for work committed on the 15th, and a "Not yet committed" line for
   the UI fix committed as `c0ae798`.

Acceptance: a multi-delivery-day test asserting the fundamentals arm issues 24
forecasts; `ridge` and `ridge_da` distinguishable in the ledger without opening a
manifest; the published ablation numbers byte-identical; `ruff`, `mypy`, the full
suite, `gpa export --check`, the site build and the browser smoke all clean; the
frozen retrospective release and every published site number unchanged.

### P7 handoff evidence — both arms issue, and the ledger can tell them apart

**The defect is fixed and the fix is demonstrated, not asserted.** Replaying the
same scenario that produced twenty-four abstentions, against an isolated store
seeded from `data/curated` with the clock inside a genuine pre-gate window and a
delivery day the archive fully covers (2026-09-10, retrieved 2026-09-09 02:17Z):

    DE-LU 2026-09-10 ridge     issued, 24 forecasts, 0 abstentions
    DE-LU 2026-09-10 ridge_da  issued, 24 forecasts, 0 abstentions
    canonical arms: ['ridge', 'ridge_da'], 48 rows
    ridge     policy_id = de-lu-development-v1-ridge-train270-published-set
    ridge_da  policy_id = de-lu-development-v1-ridge_da-train270-da-fundamentals
    mean |ridge_da - ridge| = 10.487 EUR/MWh

The last line matters as much as the first: the two arms are the same estimator
at the same alpha, so a non-zero difference is the information set doing work
rather than a relabelled copy of one forecast. `canonical` now returns both
instead of discarding one as a duplicate key.

A second replay on 2026-09-15 returned `partial, 16 forecasts, 8 abstentions`
for `ridge_da`, and the eight were local hours 16-23 — exactly the tail beyond
the local archive's last fundamentals row at 2026-09-15 14:00Z. That is stale
local data, not the code: a scheduled run ingests the delivery day first. It is
recorded here because it is also what a genuine late-provider day will look
like, and the arm degrades into a recorded abstention rather than a wrong
number.

**Regression cover for the thing that was missed.** The P6 tests could not see
this defect because a single-delivery-day panel has no training history to lose.
Three of the eight new tests work on a five-day panel:
`test_a_single_observed_stamp_erases_the_whole_training_history` pins the old
behaviour so the reason for the mixed vintage cannot be optimised away,
`test_the_prospective_vintage_keeps_the_history_the_model_has_to_fit` pins the
new one, and
`test_the_prospective_vintage_still_refuses_a_delivery_day_read_after_the_gate`
confirms a late retrieval still costs the delivery day and only the delivery
day. Two CLI tests run the arms the way the workflow does, asserting
`issued, 24 forecasts, 0 abstentions` for both, distinct policy identifiers, and
that the published arm's manifest carries no `da_*` feature even with
fundamentals sitting in the store.

**Scheduling.** Each slot now issues both arms. The published arm gates the job;
the fundamentals arm carries `continue-on-error: true`, because `gpa issue`
exits non-zero on any status but `issued` and this arm abstains by design when
the provider is late. Failing the run on that would make an honest abstention
indistinguishable from a broken job, and the pilot's own readiness criteria ask
for a visible abstention denominator. Both arms get their own tracked attempt,
so one never closes the other's record. The workflow YAML parses and every
`run:` block passes `bash -n`.

**Published text.** The three over-promising passages in `README.md`,
`site/forecast.md` and `site/methodology.md` now describe two frozen arms and
disclose the limitation that remains: only the delivery day's snapshot carries
an observed vintage, so the assigned vintage reaches how `ridge_da` is fitted
and never what its issued forecast was allowed to know. The SMARD attribution is
corrected everywhere — it is a registered, tested adapter that no zone is routed
to, and DE-LU takes fundamentals from Energy-Charts like everything else — and
methodology no longer describes `/public_power_forecast` as ablation-only ten
lines above the paragraph that says otherwise. `fundamentals.py`'s module
docstring, which still claimed SMARD as the source and the store as
realised-only, now names the three vintage builders and what separates them.
Two "17 September 2026" dates were corrected to the 15th, and the UI review's
"Not yet committed" now names `c0ae798`.

### P6 — start the prospective ledger — diagnosis and plan, recorded before code

User decision (16 September 2026): start the prospective arm now, because it is
the only open item bounded by calendar time, and resolve the long-open
fundamentals question by *not* adopting them retrospectively while including
them in the prospective arm, where a real retrieval instant is recorded.

**Diagnosis, before planning any change.** The prospective ledger has never
recorded a single issue: `data/forecast_issues/` is absent locally and holds no
tracked file on `origin/main`. That is not because the job was never switched
on. The Forecast workflow is `active` on GitHub and has run four times — twice
on push and twice on schedule — and **all four runs failed**. Reading the job
steps of the most recent run (34981200106): checkout, install, seed, `gpa
ingest`, `gpa validate`, `gpa reconcile` and `gpa battery` all succeeded, and
the run failed at exactly one step, `Issue before the market gate`.

The cause is not a bug in `gpa issue`. The two scheduled runs were created at
14:22Z and 15:56Z against a `30 9 * * *` cron. DE-LU's gate is 12:00 market
time on D-1, which is 10:00Z under CEST, so both runs reached the issue step
four to six hours *after* the auction had closed. `gpa issue` refused, raising
`LateIssueError`, which is the behaviour the protocol requires: issuing after
the gate would produce exactly the retrospective-dressed-as-prospective record
this project exists to avoid. The system was right and the schedule was wrong.

The underlying design flaw is the margin. A 09:30Z cron leaves thirty minutes
before a 10:00Z summer gate, and GitHub's scheduled workflows are explicitly
best-effort; the delay observed in this repository was five to six hours. Any
gate-bound job scheduled that tightly will fail most days, and a prospective
record that is missing on most days is not evidence.

A second fact matters for the decision that follows: `origin/main` is at
`074ad42` and this branch is 41 commits ahead of it. GitHub is therefore running
a workflow that predates all of P0-P5, and no amount of local work reaches the
scheduler until something is pushed.

**Plan.**

1. *Fundamentals in the prospective arm.* `gpa.forecast.fundamentals.from_store`
   assigns each backfilled row the market gate as a research-policy vintage, and
   its own docstring already records the consequence: `da_forecast_age_hours` is
   a constant zero, and "a live, prospective use of these features (not
   implemented here) must instead record its actual retrieval instant, which
   would vary". Implement that sibling path: keep `from_store` exactly as it is
   for the retrospective release, add a variant that stamps a caller-supplied
   observed instant, and wire `gpa issue` to pass the issue run's own input
   `as_of` so the panel's fundamentals carry an observed, varying vintage.
   `build_panel` already accepts `fundamentals=` and already keeps only the
   latest snapshot published before the gate, so no panel logic changes. The
   frozen retrospective release, the published scores and the site are untouched:
   this changes only what a future prospective issue records.
2. *Scheduling margin.* Move the issue cron earlier and add a second backstop
   run, both far enough ahead of the gate to absorb a multi-hour delay. D-1's
   own prices cleared on D-2, so nothing needed is unavailable early in the
   morning. Issuance is already idempotent — `ledger` merges on a key of zone,
   delivery date, hour and model, refuses to alter immutable issuance fields and
   preserves observed settlements on retry — so a backstop run after a
   successful one cannot double-write or overwrite.
3. *Prove it locally before trusting any scheduler.* Run `gpa issue` inside a
   real pre-gate window and confirm an actual eligible ledger row, since the
   path has never once produced one.
4. *Decide how it runs daily.* GitHub Actions requires pushing this branch,
   which no request in this project has authorized; the alternative is a local
   scheduler on the workstation. Raise as a decision, do not assume it.

Acceptance: a real pre-gate ledger row exists locally with an observed, non-zero
fundamentals age; `ruff`, `mypy` and `pytest` clean; the published retrospective
release, `data/experiments/current.json` and every site number unchanged.

### P6 handoff evidence — the issue path works; the daily run is a decision, not a task

**The path now produces a real issue.** Replicating the workflow locally against
an isolated store seeded from `data/curated`, inside a genuine pre-gate window
(03:45Z on 16 September 2026, 6h15m before the 10:00Z summer gate):
`DE-LU 2026-09-17: issued, 24 forecasts, 0 abstentions`, exit 0. All 24 ledger
rows carry `eligible = true`, `eligibility_reason = pre_gate` and
`status = issued`, with forecasts from 155 to 175 EUR/MWh. This is the first
successful issue this project has ever produced.

**Two real defects were found on the way, both invisible until something ran
forward.**

*First, the ingest window never reached tomorrow.* `PUBLISHED_AHEAD_DAYS`
contained only `{"price": 2}`. Fundamentals are the operators' forecasts for a
delivery day, so they exist before that day does, but the pipeline treated them
like a measurement and capped the request at now. The omission was harmless
while those features were only replayed over history for the ablation, and
fatal the moment a prospective issue needed tomorrow's snapshot. Adding
`"fundamentals": 2` changed the observed coverage for the current day from six
hours to a full twenty-four, and is now locked by a test.

*Second, an unavailable provider series would have cost the whole day.* Passing
a snapshot that does not cover the delivery day adds the fundamental features to
the panel with nothing behind them, and every delivery hour then abstains for
missing inputs. The first version of this change did exactly that, and two CLI
tests caught it: `DE-LU 2026-07-01: abstained, 0 forecasts, 24 abstentions`.
Fundamentals are now supplied only when they actually cover the target day;
otherwise the run issues from the published information set. Emptiness alone is
not the test, since the archive always holds older days.

**The fundamentals decision is now an experiment, and its first real data point
is already in.** Checked live at 05:45 CEST on 16 September 2026, Energy-Charts
had published no day-ahead forecast for 2026-09-17 at all, so the proof issue
fell back — and said so: its manifest records the nineteen published features
and no `da_*` feature, alongside an `observed_at` entry for fundamentals and an
archived `source_fundamentals` frame. The fallback is therefore auditable rather
than silent. It also means the provider publishes the delivery day later than
04:17 CEST, which the schedule has to respect; the exact hour is unknown and
will be read off the ledger over the first week rather than guessed.

**Scheduling.** The cron moved from `30 9 * * *` to `17 2 * * *` and
`47 6 * * *`, leaving roughly eight and four hours of margin against the summer
gate instead of thirty minutes, and running off the top of the hour where
GitHub's scheduler is least contended. Two runs are safe: `ledger.canonical`
keys on `issued_at` and selects the earliest complete, verified pre-gate issue
per model and delivery day, and every other issue stays recorded. That also
settles which arm is canonical: the conservative published information set,
exactly as in the retrospective release, with any fundamentals-bearing issue
recorded beside it as evidence rather than promoted over it.

Verified: `ruff`, `ruff format --check`, `mypy` and the full `pytest` suite all
clean, including five new tests for the observed vintage (age 0 under the policy
vintage against a real 6 under an observed one, and a post-gate snapshot refused
rather than backdated) and one for the ingest window. The retrospective release,
`data/experiments/current.json` and every published site number are untouched.

**Open decision, not assumed.** The workflow is `active` on GitHub and its four
runs to date all failed at the issue step for the late-gate reason above. Fixing
that reaches the scheduler only if this branch is pushed: `origin/main` is at
`074ad42`, 41 commits behind, so GitHub is still running a workflow that
predates P0-P5. Pushing to a public repository has never been authorized in this
project, so it is left to the user, with a local scheduler as the alternative
that avoids publication entirely.

### P5 — editorial pass: executive framing, premises, sources and references — recorded before editing

User request (16 September 2026): make the prose more executive, explain the
study, its premises, the sources and the references more fully; the portfolio
"feels incomplete". Explicit constraint: **text only** -- no data, model, export
or reactive-cell changes.

Diagnosis of what is actually missing. The five texts are technically careful
and well hedged, but they document *what* was done far better than *why it is
the right market convention*, and the provenance layer is thin:

1. **Sources are under-documented relative to what the code actually does.**
   `site/methodology.md` lists four zones against two bare provider links.
   The repository in fact uses three upstream providers plus one documented
   lineage: Energy-Charts (published by Fraunhofer ISE, CC BY 4.0,
   credential-free, `api.energy-charts.info`, endpoints `/price`,
   `/public_power`, `/public_power_forecast`, `/installed_power`, and itself a
   republisher of ENTSO-E and SMARD figures); SMARD (operated by the
   Bundesnetzagentur, `smard.de/app/chart_data`, used for the German backfill
   and as the prospective path's forecast-snapshot source); and ONS for Brazil
   through two endpoints with different publication lags (hourly energy-balance
   CSV on public S3, about two days behind; verified-load API, about one hour
   behind). None of that reaches the reader today.
2. **No consolidated premises.** Assumptions are correct but scattered across
   four pages, so no reader can see the whole assumption set at once.
3. **The market rationale for the D-1 noon gate is never stated in prose**,
   although the run metadata itself already records it ("12:00 market time on
   the day before delivery, when the day-ahead auction closes").
4. **No references section anywhere.** NREL ATB, BloombergNEF and the
   Bundesnetzagentur auction statistics are cited inline on the battery page
   only; nothing is collected where a reviewer would look for it.
5. **Coverage is never quantified.** The committed store holds DE-LU from
   2018-12-31 (94 monthly partitions) against FR/ES/BR-SIN from 2024-09-01
   (25 partitions) -- a materially different depth that the text never admits.

Facts verified for this pass before writing (nothing below is asserted from
memory): store coverage and row counts read from `data/curated/`; provider,
licence and endpoint details read from the adapter docstrings; SMARD's operator
confirmed against smard.de itself; the SDAC move from hourly to 15-minute
market time units (trading day 30 September 2025, delivery 1 October 2025)
confirmed against the NEMO Committee's own SDAC page. EPEX SPOT blocks
automated retrieval, so the 12:00 CET day-ahead order-book closure is cited to
EPEX SPOT as market convention and matches this project's own recorded gate
definition; no figure sourced only from a search snippet is presented as a
computed result.

Plan, page by page. Every existing number stays as it is, and site pages keep
using their interpolated values rather than hardcoded figures, so nothing here
can drift away from the data:

- `README.md`: sharpen the opening into an executive statement; add what the
  project demonstrates; replace the thin scope table with a full provenance
  table (provider, institution, licence, coverage, role); add a consolidated
  premises section and a references section.
- `site/index.md`: add why this market and this question (market context), and
  a compact premises block naming the five assumptions that drive every number.
- `site/forecast.md`: state why the D-1 noon gate is the auction gate, why the
  information set is restricted the way it is, and why ridge is fitted per hour
  while LightGBM is pooled; add how to read MAE commercially.
- `site/battery.md`: put the asset specification and the commercial logic of the
  naive comparison in prose before the tables.
- `site/methodology.md`: full provenance table, a data-lineage and revisions
  note, a consolidated assumptions register, and a references section.
- `src/gpa/battery_sensitivity.py`: fix a broken reference URL in the module
  docstring (`atb.nlr.gov` -> `atb.nrel.gov`); text-only, no behaviour change.

Acceptance: `npm run build` and `npm run test:browser` still pass (markdown
interpolation errors would surface there), `gpa export --check` still clean,
and no committed number changes.

### P5 handoff evidence — done, verified

All six files edited as planned, text only; no reactive cell, data file, export
path or model parameter was touched, and every pre-existing number is unchanged
and still interpolated from the data rather than hardcoded.

What was added. `README.md` gained an executive opening ("why this market and
this question"), a "what this project is meant to demonstrate" section, a
premises table with the rationale for each assumption, a provenance table
carrying publisher and committed coverage per zone, prose on each of the three
adapters, an explicit revisions caveat, and a references section.
`site/index.md` gained a market-context paragraph, a consolidated premises
block and a sources block, placed after the implication so the "under a minute"
path (question, findings, implication) stays intact. `site/forecast.md` now
explains why the D-1 noon gate is the auction's own deadline, why residual load
rather than raw demand, why per-hour ridge is contrasted with pooled LightGBM,
why the three naive baselines are the standard to clear, and how to read MAE,
skill and bias commercially. `site/battery.md` states the asset specification
and the commercial logic of the naive comparison in prose before the tables.
`site/methodology.md` gained a "sources, lineage and revisions" section, a
coverage column on the scope table, an eleven-row assumptions register with
rationale and consequence-if-wrong for each entry, and a references section.

Accuracy corrections made during the pass rather than after review: a first
draft of the README described SMARD as backing the committed German history.
The store disagrees -- every committed row records `energy_charts` or `ons` as
its source -- so the text now describes SMARD as a second, independent adapter
for backfill and for the prospective path's forecast snapshots, and says
explicitly what the committed store records. One real defect was fixed:
`src/gpa/battery_sensitivity.py` cited the NREL ATB as `atb.nlr.gov`, a
transposition of `atb.nrel.gov`, so the module docstring's only external
reference was a dead link.

Verification. `npm run build` passes and now validates 9 internal links (was 7);
`npm run test:browser` passes on all four pages at 1440px and 390px with no
console errors and no horizontal overflow; `ruff check`, `ruff format --check`
and `mypy` are clean; the full pytest suite passes; `gpa export --check`
reports the exported tables still match the committed observations. The new
four-column assumptions register was inspected in a real browser to confirm it
renders without overflow. External claims were verified before being written:
SMARD's operator against smard.de itself, and the SDAC move to 15-minute market
time units (trading day 30 September 2025, delivery 1 October 2025) against the
NEMO Committee's own page. EPEX SPOT blocks automated retrieval, so the midday
day-ahead gate closure is cited to EPEX SPOT as market convention and matches
the run metadata this project already records; it is not presented as a
computed or quoted figure.

### P4 — package the evidence for recruiting — recorded before implementation

A gap review against the roadmap's own P4 acceptance criterion ("a reader can
identify the market, candidate contribution, commercial finding and principal
caveat in under a minute, then locate the reproduction path") against the
current `site/index.md`, `site/forecast.md`, `site/battery.md`,
`site/methodology.md` and `README.md`, before writing anything.

**What already satisfies P4, left alone:** the home page already has the
question, three qualified findings, limitations and a personal-contribution
line with a verified GitHub link (confirmed with the user earlier: GitHub
only). The battery page already has incremental margin, costs/downside,
duration trade-offs and the P3 structural/durability narrative -- P4's
battery bullet is essentially done already.

**Real gaps found, each traced to the file and line, not asserted from
memory:**

1. **Forecast page has zero fundamentals-ablation content.** P4's own text
   explicitly asks for "baseline skill, fundamentals ablation, evaluation
   protocol." `site/forecast.md` currently only compares the five published
   models; the ablation (Ridge 22.07 -> ~19.17, LightGBM 25.56 -> ~20.55
   EUR/MWh) exists only as roadmap prose, never as a site table. This is the
   highest-risk item in this plan -- see step 3.
2. **No disclosure structure.** P4 says "put secondary diagnostics in a
   disclosure." `site/forecast.md` is 221 lines of uniformly-weighted
   sections (interval calibration, hour/regime breakdowns, the week
   inspector) with nothing collapsed; a time-boxed reader has no fast path
   through it today.
3. **`site/methodology.md` is stale, not just incomplete.** Line 134-137
   still reads "Operator forecast vintages are not yet stored for the
   retrospective history" -- false since the fundamentals backfill earlier
   this session; the page also never mentions the P3 capacity/cannibalisation
   data sources (Energy-Charts `/installed_power`, BNEF, BNetzA) or their
   small-sample-correlation caveat at all.
4. **No commercial comparison on the home page.** P4's home bullet asks for
   "one commercial comparison"; none exists today.
5. **README has no personal-contribution framing.** The homepage has it;
   README (the GitHub landing surface, read by anyone who never clicks
   through to the live site) does not.
6. **The screenshot predates all of P3.** `docs/screenshots/dashboard-1440.png`
   was captured before the battery page's new section existed.
7. **GitHub "About" and topics are outside this session's reach.** No `gh`
   CLI or API access is available in this environment (confirmed: `gh` is
   not installed, checked both Bash and PowerShell). These are live
   repository settings, not files to edit and commit, and changing them is a
   public-facing action this session cannot take and should not attempt via
   any other channel without the user's explicit action. **This step
   produces a recommended description and topic list as a deliverable for
   the user to apply, not a commit.**

**Sequencing and one real risk, resolved before coding, not during it:**

Items 2, 3, 4, 5 and 6 are straightforward content/structure edits, no new
computation, done first. Item 1 is not: `gpa backtest --include-fundamentals
--save-snapshot` calls `snapshot.save()`, which **unconditionally overwrites
`data/experiments/current.json`** (confirmed by reading
`src/gpa/forecast/snapshot.py:41` directly, not assumed) -- there is no
"save but do not adopt" mode. Running that command naively would silently
repoint the published release at the fundamentals-ablation run, exactly the
mistake the roadmap has repeatedly flagged as "an explicitly separate, undone
decision." This plan does **not** touch `snapshot.save()`/`current.json` at
all. Instead, mirroring the P3 precedent (`gpa capacity` ->
`data/reference/capacity/`, never touching the interval store or the
frozen-release contract): a small, separate, explicitly-named artifact under
`data/reference/fundamentals_ablation/` holding just the `scores` comparison
(with vs. without fundamentals, both scoped to the identical frozen test
window), produced by a dedicated command run once, not by routine `gpa
export` (a full walk-forward re-run on every export would make it minutes
slower for a diagnostic nobody asked to re-verify daily). `gpa export` reads
that reference file if present and ships it as a new, clearly-separate site
table; the page states plainly that this is a diagnostic comparison, not the
published model. Live output re-verified against the roadmap's already-cited
figures before writing a sentence about it -- if the wind-onshore/offshore
fix or the metadata fix from the last review pass changed the numbers even
slightly, the site must show the real re-run number, not the old prose.

**Acceptance:** every number added is either read live from a committed
table (the site's own existing pattern) or a citation already verified
against its primary source (matching the P3 citation discipline); nothing
in `data/experiments/current.json` or the published frozen release changes;
`gpa export --check`, full `pytest`/`ruff`/`mypy`, and a real-browser
`npm run build` + `npm run test:browser` pass before any commit.

### P4 handoff evidence — done except the commercial comparison (deferred), committed and run

Items 2, 3, 5 done first (docs/structure, no new computation), then item 1
(the one real risk), and item 6 turned out to need no change. Item 4 (a
home-page commercial comparison) was explicitly deferred by the user after
a real, disclosed verification wall -- recorded below, not silently dropped.
Item 7 (GitHub About/topics) produced a recommendation, not a commit, since
this environment has no `gh` CLI or API access.

**Item 2 — disclosures.** `site/forecast.md`'s three secondary-diagnostic
sections (the full block/regime/year/hour breakdown, predictive intervals,
the week inspector) moved into collapsed `<details>` blocks. Caught a real
bug on the first run: the week inspector reused a `selectedModel` variable
defined inside the now-collapsed breakdown section, and the smoke test's
generic "exercise every select" check hung on a `<select>` that was invisible
inside a closed disclosure. Fixed by giving the week inspector its own
`weekModel` selector (removing the cross-section reactive coupling) and by
having `scripts/browser-smoke.mjs` open every `<details>` right after
navigation, before any assertion -- collapsed-by-default content must not be
exempt from the regression check that exists specifically to catch this.

**Item 3 — methodology accuracy.** Fixed two claims in `site/methodology.md`
that were true when written and false now: "operator forecast vintages are
not yet stored" (they have been, since this session's fundamentals backfill)
appeared twice. Added a new "Capacity and market structure" section
documenting the P3 correlation methodology, the country-vs-DE-LU-zone
distinction, and the small-sample/non-causal caveat -- previously not
mentioned on the methodology page at all despite being a major addition to
the battery page.

**Item 5 — README.** Added the personal-contribution line already on the
homepage, matching its exact existing wording (GitHub-only contact, per the
user's earlier confirmed choice) rather than inventing new framing.

**Item 6 — screenshot.** Checked, not changed: `docs/screenshots/dashboard-1440.png`
came back byte-identical after a fresh capture, because none of this
session's P3/P4 work touches `site/index.md`'s own computed content. The
concern in the plan was reasonable to check and turned out not to apply.

**Item 4 — commercial comparison, deferred by the user.** Checked four
sources for a real, dated German battery-arbitrage revenue figure to compare
against this project's own margin: the Aurora Energy Research/LCP Delta BESS
revenue indexes (paywalled, only available secondhand via Montel/
energy-storage.news, which returned HTTP 403 on direct fetch); RWTH Aachen
ISEA's Battery Revenue Index (free, real, but renders its numbers only in an
interactive chart no fetch tool here can read); and RWE's battery-business
investor presentation (downloaded successfully but is an unparseable
image-based PDF in this environment). Presented the situation to the user
with three options (skip, user supplies a source, or cite a comparable
open-source project found during the search instead of a commercial one);
user chose to skip it for this round. **Open in the roadmap, not resolved.**

**Item 1 — the fundamentals ablation, the one real risk in this plan.** New
`gpa.fundamentals_ablation` module (`path`/`write`/`read`, mirroring the P3
`gpa.capacity` precedent exactly: its own `data/reference/` artifact, never
`data/experiments/` or `current.json`) and a new `gpa fundamentals-ablation`
CLI command that runs `gpa.forecast.backtest.run()` twice -- once at every
default parameter (confirmed by reading the published run's own
`run.json`: min_train_days=270, validation_days=90, alpha selected not
fixed, expanding window, LightGBM tuned, refit daily -- all of which are
already this function's defaults, so no parameter needed overriding to match
the published protocol exactly) with `include_fundamentals=False`, once with
`True` -- and writes only the overall-scope scorecard, never a full
snapshot. Verified `data/experiments/current.json`'s SHA-256 unchanged
before and after running it, not just assumed from reading the code.

Run live: Ridge MAE 22.07 -> **19.17** EUR/MWh, LightGBM 25.56 -> **20.56**
EUR/MWh, on the identical 58,645-hour, 2020-01-03 to 2026-09-12 window the
published release uses -- matching the roadmap's own earlier "~19.17" /
"~20.55" algebraically-derived approximations to within rounding, confirming
both that derivation and that the review pass's wind-fetch and metadata
fixes did not materially change these numbers. Wired into a new "Does more
information help?" section on `site/forecast.md`, positioned in the primary
reading path (not a disclosure, since P4 groups this with "baseline skill"
as primary content) but labelled plainly as a diagnostic throughout, with an
explicit sentence that adopting it into a new frozen release remains a
separate, undone decision.

**Verification:** full `pytest` (400 tests), `ruff check`, `mypy` clean.
`npm run build` and `npm run test:browser` (both viewports) clean, no
console errors, no leaked `NaN`/`undefined`; the new section's rendered DOM
text read directly and confirmed to show the exact real figures above.
`gpa export --check` passes. `data/experiments/current.json` byte-identical
before and after every step in this item, confirmed by hash, not assumed.

**Open items carried forward, not silently dropped:** the home-page
commercial comparison (item 4, deferred); GitHub About/topics (item 7,
recommendation given, application is the user's to do); the still-undecided
fundamentals-adoption question from part B of the earlier plan (unaffected
by this item -- showing the ablation as a diagnostic is not adopting it).

### DE-LU capacity/renewables/storage scenario study (P3) — recorded before implementation, rethought before coding

This replaces the first version of this plan (committed `995d08d`, never
implemented). That version reached for a new external data source (the
Marktstammdatenregister full registry dump) and centred the deliverable on a
CAPEX/OPEX/discount-rate asset-screening sheet — closer to a project-finance
feasibility study than to what a trading desk or generator's analyst actually
does. User asked (15 September 2026) to rethink the plan itself around its
real purpose: demonstrating market, analytical and technical judgement to
European trading/generation employers, not maximum data-engineering scope.
Two findings from re-checking what already exists changed the plan, not just
its implementation:

- **`gpa.metrics.price.capture_rate`** (`src/gpa/metrics/price.py:348`)
  already computes generation-weighted capture price and capture rate per
  technology and period — exactly the cannibalisation metric a trading desk
  uses to talk about renewable revenue erosion. It is tested
  (`tests/test_metrics.py`) but wired into nothing: the same "built, never
  connected" pattern as `fundamentals.py` before this session's earlier work.
  This is the core metric this study should foreground, not a table item.
- **Energy-Charts' `/installed_power` endpoint** (same provider as every
  other dataset in this project) returns installed capacity by technology for
  Germany — yearly back through history, and monthly (Germany only) — in GW
  (GWh for battery storage), plus net installation/decommission deltas
  directly, and, confirmed live on 15 September 2026, **the German
  government's own legislated build-out targets as named series**: "Solar
  planned (EEG 2023)", "Wind onshore planned (EEG 2023)", "Wind offshore
  planned (WindSeeG)", sparse points out to 2030. This makes the
  Marktstammdatenregister unnecessary: no new dependency, no custom XML
  parser, no new architecture, reuses the existing `EnergyChartsSource`
  pattern exactly, and the forward path is a cited government target instead
  of an invented growth-rate assumption. Like every other Energy-Charts
  dataset here, it covers Germany, not Luxembourg; DE-LU's Luxembourgish
  share stays excluded and disclosed, not estimated.

**Deliverable, reframed:** a market-mechanism study — how DE-LU's renewable
build-out has already reshaped the price shape, whether that relationship
extrapolates to the government's own 2027-2030 targets, and whether battery
fleet growth is competing away the arbitrage margin this project's own battery
work measures — published on the battery page per the roadmap's original
placement. Priority order below is analytical priority, not just sequence.

1. **Capacity data, fetched, not registry-parsed.** New `EnergyChartsSource`
   method for `/installed_power` (`country=de`), reusing the existing
   retry/backoff client, both `time_step=yearly` (full history) and
   `time_step=monthly` (Germany-only, matches `battery_monthly`'s own
   granularity for step 4). Store as a small reference table outside the
   `(zone, ts_utc, resolution_min)` interval contract that
   `schema.SCHEMAS`/`store.write` enforce for price/load/generation/
   fundamentals — this is a period series (a year or month labels the *end*
   of the period, per the endpoint's own documented convention, not an
   interval start) with a handful of technology columns, not a market
   interval series. A small new module (e.g. `gpa.capacity`) with its own
   schema and its own path under `data/reference/capacity/`, decided in full
   during implementation once the real response shape is in hand. Keep the
   "planned" series (a government target) visibly distinct from the realised
   series in this schema — never let a forward target silently become a
   historical observation.
2. **Cannibalisation, from data already in the store.** Wire `capture_rate`
   for solar and wind, DE-LU, `period="year"`, 2019-2026, from the price and
   generation datasets already backfilled this session — zero new data
   required. This is the headline empirical result: read the real yearly
   capture rate before writing a sentence about "erosion", per this project's
   standing read-then-write discipline (used for the market-regimes narrative
   already on the forecast page).
3. **Capacity-to-price-shape relationship.** Correlate the yearly capacity
   series (step 1) against price-shape metrics already computable from the
   store: capture rate (step 2), negative-price frequency, and spread —
   reusing `gpa.metrics.price`'s existing functions rather than writing new
   ones. Decide the exact functional form (simple year-over-year correlation
   vs. a fitted regression) only after looking at the real 2019-2026 series;
   whichever is used, state its fitted range explicitly and flag that the
   government's 2030 targets (step 1) lie outside it for at least one
   technology — an extrapolation, disclosed as one, not hidden in a caveat
   list.
4. **Battery competition vs. arbitrage margin.** Correlate the battery
   storage power/energy series from `/installed_power` (step 1) against the
   arbitrage margin history the bounded-G sensitivity study already produces
   (`battery_monthly`/`battery_sensitivities`, already exported) — does
   Germany's fast-growing battery fleet coincide with margin compression
   already, at the monthly granularity both sides support. No new dispatch
   engine; this is a correlation over existing outputs.
5. **Light, explicitly-labelled cost context — not an asset-screening
   sheet.** One or two publicly sourced battery cost figures (e.g. a named,
   dated industry cost benchmark), cited inline, used only to give the
   already-computed EUR/MWh arbitrage margins a rough cycling-cost
   comparison. No CAPEX/OPEX/degradation/discount-rate investment appraisal
   table, and no solar/wind cost figures unless a real, citable source
   surfaces during writing — this study is about price-shape and competition
   dynamics, not project economics.
6. **BNetzA auction results: citation only, not a pipeline.** If a specific
   recent clearing price adds real colour (e.g. contrasting a recent onshore
   wind award price against DE-LU's realised capture price), look it up and
   cite it by hand from the consolidated per-technology statistics workbook
   already found during research (`Statistiken: Windenergieanlagen an Land –
   Ausschreibungen`, etc.). No xlsx parser, no new dependency, no ingestion
   pipeline for this — the earlier plan's ETL step is dropped.
7. **Site.** Add this to `site/battery.md` (the roadmap's own original
   placement: "within the battery page"), grounded only in the tables built
   in steps 1-4, following the same "read the real computed table, then
   write the sentence" discipline as the market-regimes narrative on the
   forecast page.
8. **Verification.** Full pytest/ruff/mypy; `gpa export --check` covers the
   new capacity/cannibalisation tables; every figure in the writeup traces to
   a table built in steps 1-4 or an inline citation from steps 5-6; the
   writeup states what could make the currently-favoured battery duration
   case less attractive as the fleet keeps growing, per the roadmap's own P3
   acceptance criteria. Record data volumes and coverage here once run.

**Not in this item:** the Marktstammdatenregister, `open-mastr`, and any
BNetzA auction ETL pipeline (superseded by the findings above — capacity data
comes from `/installed_power`, auction figures are cited by hand where useful);
a CAPEX/OPEX/discount-rate asset-screening table (replaced by the light cost
context in step 5); intraday or ancillary-market modelling beyond a discussion
paragraph; wiring capacity data into the short-term day-ahead forecast panel
(a monthly-cadence signal, not a day-ahead feature); adopting the pre-auction
fundamentals ablation as a new frozen release (a separate, still-open
decision); any live/pilot inference.

### P3 handoff evidence — done (all 8 steps), committed and run

All eight steps of the plan above are complete: capacity data, cannibalisation,
capacity-to-price-shape correlation, battery competition vs. arbitrage margin,
light cost context, an auction citation, the site narrative and final
verification.

**Step 1.** `EnergyChartsSource.fetch_installed_power(zone, *, time_step)` in
`src/gpa/sources/energy_charts.py`, and `gpa.capacity` (`reference_root`,
`capacity_path`, `write`, `read`) for the new `data/reference/capacity/`
storage, kept outside the `store.write` interval contract as planned. One
real bug the live run caught that the plan's own research had gotten wrong:
monthly period labels are `"MM.YYYY"` (e.g. `"02.2024"`), not the ISO
`"YYYY-MM"` this project uses everywhere else and that the plan assumed
without checking against a live monthly response — the parser and its test
were written for the wrong format and failed loudly on the first real run
against `gpa capacity`, not silently. Fixed before committing. New CLI
command `gpa capacity --zone DE-LU`, run live: 2,347 rows, 18 technologies,
including the three official policy targets confirmed during planning
(`Solar planned (EEG 2023)`, `Wind onshore planned (EEG 2023)`, `Wind
offshore planned (WindSeeG)`) each correctly flagged `is_planned=true` and
kept as separate rows from the realised series, per the plan's requirement.

**Step 2.** Wired `gpa.metrics.price.capture_rate` (already built and tested,
never connected to anything) into `gpa.export` as two new site tables,
`capacity` and `cannibalisation`, both added to `export_all()`. Run live
against the real DE-LU 2019-2026 store, `period="year"`:

| year | solar capture_rate | wind capture_rate |
|---|---|---|
| 2019 | 0.928 | 0.871 |
| 2020 | 0.806 | 0.829 |
| 2021 | 0.785 | 0.859 |
| 2022 | 0.939 | 0.737 |
| 2023 | 0.759 | 0.839 |
| 2024 | 0.592 | 0.838 |
| 2025 | 0.514 | 0.883 |
| 2026 (partial year) | 0.511 | 0.903 |

Solar's capture rate falls in a clean, almost monotonic line from 0.93 to
0.51 across the sample (2022's spike is the gas-crisis year, where every
technology captured close to the flat average because the market was
scarcity-priced for most of the year, not evidence against the trend) — the
cannibalisation mechanism the whole study is centred on is real and already
visible in the data this project holds today, before any scenario work.
Wind shows no comparable trend (0.74-0.90, no monotonic direction), which
itself is a real, citable contrast worth explaining in the eventual site
narrative (wind's flatter daily/seasonal generation profile self-cannibalises
far less than solar's midday concentration) rather than an oversight to fix.

**Step 3.** Three new `gpa.export` tables — `capacity_price_yearly` (one row
per year, capacity joined to `block_prices`, `negative_price_summary` and
`capture_rate`, inner-joined on price coverage so a capacity-only year cannot
appear), `capacity_price_correlation` (Pearson r over four pairs, current
partial year always excluded from the fit) and `capacity_extrapolation_flags`
(each 2030 policy target compared against the realised ceiling, current
partial year excluded there too). Two decisions made only after looking at
the real data, as the plan required:

- The raw EUR on/off-peak spread is confounded by the 2021-2022 fuel-price
  shock (2022's spread is the sample's largest in EUR terms purely because
  both peak and off-peak prices were extremely high that year, not because
  cannibalisation was worse) — correlated `spread_pct_of_price` (spread as a
  percentage of that year's average price) instead of the raw EUR figure, to
  stop the coefficient from mostly measuring gas prices.
- Kept the correlation at yearly resolution as planned rather than moving to
  monthly, which was considered: monthly solar capture rate has a large
  seasonal swing (summer midday oversupply vs. winter darkness) that has
  nothing to do with capacity growth, and controlling for it properly (e.g.
  same-calendar-month year-over-year) is a real enough piece of work that it
  was left as a noted future robustness check rather than folded into this
  step unplanned.

Run live against the real store:

| x | y | n | pearson_r | fitted years |
|---|---|---|---|---|
| solar_capacity_gw | solar_capture_rate | 7 | -0.895 | 2019-2025 |
| solar_capacity_gw | negative_pct | 7 | 0.823 | 2019-2025 |
| wind_capacity_gw | wind_capture_rate | 7 | 0.205 | 2019-2025 |
| solar_capacity_gw | spread_pct_of_price | 7 | -0.911 | 2019-2025 |

n=7 complete years: a real but small-sample, shared-time-trend correlation,
not a causal estimate — recorded as a limitation in the function's own
docstring and carried as `fitted_year_min`/`fitted_year_max` on every row, not
left implicit. Wind's near-zero correlation is itself informative: it is the
same asymmetry step 2 already found in wind's flat capture-rate trend, now
also absent from the capacity relationship, which is consistent rather than
a second, independent finding.

`capacity_extrapolation_flags`, run live: every one of solar (AC and DC),
wind onshore and wind offshore has `exceeds_realised_max = true` for its 2030
EEG 2023/WindSeeG target — e.g. wind offshore's realised ceiling is 9.7 GW
(2025) against a 30 GW 2030 target. Every scenario that reaches toward these
targets is confirmed, from real data rather than assumption, to be an
extrapolation beyond anything this project's correlation is fitted on.

**Step 4.** Two new `gpa.export` tables reusing `battery_monthly` -- already
built once from the frozen forecast snapshot for the site's own battery page,
no new dispatch run -- rather than the interval store: `battery_margin_yearly`
(profit summed to the year and normalised to EUR/MW/day, since `power_mw` is
always 1.0 in that table, inner-joined onto the German battery fleet's yearly
power/energy series) and `battery_competition_correlation` (Pearson r for
three strategies -- `perfect_foresight`, `ridge`, `lightgbm` -- at each of the
three durations, current partial year excluded as in step 3).
`perfect_foresight` was chosen alongside the two real forecasters
deliberately: it isolates the market-structural arbitrage opportunity (the
maximum spread extractable that period) from either model's own forecast
skill, which matters because the question is whether competition is shrinking
the opportunity itself, not whether a model got better at capturing it.

Run live against the real store, `energy_mwh=2.0`:

| strategy | n | pearson_r | fitted years |
|---|---|---|---|
| perfect_foresight | 6 | +0.388 | 2020-2025 |
| ridge | 6 | +0.487 | 2020-2025 |
| lightgbm | 6 | +0.675 | 2020-2025 |

The sign is positive, not negative: as Germany's battery fleet grew roughly
tenfold (1.6 GW to 17 GW power rating, 2020-2025), this project's own
per-MW arbitrage margin rose alongside it rather than being competed away.
**This is not evidence that fleet growth does not compress margin.** The same
2020-2025 window is dominated by the 2021-2022 fuel-price shock and the
accelerating solar cannibalisation step 3 already measured, both of which
expand the arbitrage opportunity itself (bigger price swings to trade around)
far more than a few gigawatts of added competition could currently compress
it. A genuine competition effect, if one exists yet, is not detectable above
that much larger co-moving trend with n=6 annual points -- stated here
plainly rather than read as "no competition effect," which the data does not
support either.

**Verification run:** `pytest` (extended `tests/test_export.py`, full suite),
`ruff check`, `mypy` all clean; one real bug caught before committing --
`_battery_margin_yearly` checked `fleet.is_empty()` after the
`pivot().rename()` call instead of before, so an empty filtered input raised
`ColumnNotFoundError` (pivot never creates the named columns from zero rows)
instead of returning the documented empty frame; a test written for exactly
that case caught it immediately. `gpa export` regenerates `capacity.parquet`,
`cannibalisation.parquet`, `capacity_price_yearly.parquet`,
`capacity_price_correlation.parquet`, `capacity_extrapolation_flags.parquet`,
`battery_margin_yearly.parquet` and `battery_competition_correlation.parquet`
alongside the existing tables, checked into `site/data/` so `gpa export
--check` keeps passing, even though no site page reads any of them yet.

**Steps 5-6 (cost context and auction citation), sourced and verified before
writing a sentence.** Two real, dated, primary-sourced figures, each fetched
and independently confirmed against the primary page rather than trusted from
a search summary:

- BloombergNEF's 2025 Lithium-Ion Battery Price Survey (published 9 December
  2025): utility-scale stationary-storage battery pack prices fell 45% in a
  single year, to $70/kWh in 2025 — the sharpest drop of any segment BNEF
  tracks. Used only as qualitative context (a falling barrier to adding
  competing capacity), not converted into a derived EUR/MWh cycling-cost
  figure: that would have needed an unsourced cycle-life assumption stacked
  on an unsourced FX rate, compounding uncertainty into something that reads
  as more precise than it is. An academic-paper degradation-cost estimate
  found earlier (~$6-17/MWh) was deliberately **not** cited: its PDF could not
  be parsed to verify the number against its primary source, and this
  project's own standing rule is not to cite a figure it could not verify.
- Bundesnetzagentur's most recent onshore wind auction (1 May 2026): average
  reference value 5.06 ct/kWh (EUR 50.6/MWh), from the consolidated
  per-technology auction statistics found during the original planning
  research — no xlsx parser, no pipeline, looked up and cited by hand as
  planned. Verified the EEG sliding-market-premium mechanism (reference value
  minus monthly market value, zero premium in negative-price hours) before
  writing the comparison sentence, so the auction figure is not presented as
  a fixed offtake price it is not.

**Step 7 (site narrative), added to `site/battery.md`.** A new "Is this
margin durable as the market changes?" section, placed before "Dispatch and
study boundaries" per the roadmap's original placement ("within the battery
page"), grounded only in the six tables built in steps 1-4 plus the two
citations above -- every number in it is either a live JS computation off a
loaded Parquet table or one of the two hand-verified citations, none
hand-typed. Also fixed a real, unrelated bug found while editing this file: an
existing citation link to "NREL ATB 2024" pointed at `atb.nlr.gov` (a typo),
not the real `atb.nrel.gov`.

Verified in a real browser, not just a static build: `npm run build` (catches
template/JS syntax errors) and `npm run test:browser` (Playwright against a
live `observable preview` server) both pass at both viewports, with two new
assertions added to `scripts/browser-smoke.mjs` for the new section (heading
text present, no leaked `NaN`/`undefined`). The rendered prose was read
directly out of the live DOM (not assumed from source) and matches the
real computed figures, e.g. solar capture rate "92.8% ... to 51.1%", the
spread "EUR 11/MWh ... to EUR -14/MWh", and the default 4h battery's
competition correlation "r=0.35, n=6 years, 2020-2025".

**Step 8 (final verification).** Added a closing paragraph stating explicitly
what could make the currently-selected duration's case less attractive as the
market keeps changing, per the roadmap's own acceptance criteria -- reactive
to the page's own duration selector, not a fixed claim about one duration.
One drafting error this caught in itself: an early version claimed the
battery fleet was "an order of magnitude away from its 2030 target," but no
such target exists in the `/installed_power` data (EEG sets auction targets
for solar and wind, not batteries) -- caught before committing and replaced
with the real, verified figure (rated power grew roughly tenfold, 2020-2025).
Full `pytest`/`ruff check`/`mypy` clean on the whole repository. `gpa export
--check` passes against the committed `site/data/`. README.md's Research
design section gained one summary bullet for this work, per this project's
standing practice of refreshing README and this roadmap together when a piece
of work finishes.

**What P3 is, in the end, versus the original roadmap text:** a market-
mechanism study (cannibalisation, capacity-price correlation, battery
competition, sourced context), not the original capacity-ledger-plus-
asset-screening scenario brief with three named growth paths. That
reframing was a deliberate, recorded decision (see above), not scope lost
along the way.

### P3 review pass — two bugs and two contract gaps found and fixed, verified

A review of the just-shipped P3 work (16 September 2026) found and fixed four
real problems before any further planning continued. Two are correctness
bugs; two are contract/documentation gaps where the code was right but what
it claimed about itself was not.

1. **Correlation cutoff depended on wall-clock time, not on the data.**
   `_capacity_price_correlation`/`_capacity_extrapolation_flags`/
   `_battery_competition_correlation` all excluded "the current year" via
   `dt.datetime.now(dt.UTC).year` -- meaning the exact same committed data
   could report a different fitted year range and different correlation
   coefficients depending on which calendar year `gpa export` happened to run
   in, a reproducibility violation for a project whose whole premise is
   reproducible releases. Fixed with a new `_annual_fit_cutoff(intervals,
   zone)` that derives the boundary from the actual last observed interval
   end in the relevant frozen input (price data for the capacity
   correlations, the frozen predictions for the battery-competition one),
   converted to local time -- a real fact about the data, not the export
   clock. Covered by `test_annual_fit_cutoff_uses_local_interval_end_not_wall_clock`
   (parametrised over hourly/quarter-hourly resolution and a year-boundary
   edge case) and every existing cutoff-dependent test rewritten to a fixed
   historical year rather than "today."
2. **Fundamentals wind fetch silently summed onshore-only when offshore was
   missing.** `_fetch_fundamentals`'s onshore+offshore summing used a plain
   `group_by(["ts_utc", "series"]).agg(sum)`, so a missing, null or duplicated
   value in either component at one interval produced a `wind` value that
   silently reflected only the other component instead of failing or
   dropping the interval -- a leakage-adjacent correctness bug in a feature
   this project already ran a real ablation on. Fixed: an interval's `wind`
   row now requires exactly one onshore and one offshore observation, or is
   dropped entirely. Covered by a new parametrised test across both
   components and three failure modes (missing, null, duplicate), confirming
   only the affected interval is dropped and the adjacent one still sums
   correctly. **Verified this did not actually corrupt the published
   fundamentals-ablation numbers**: a live spot-check of a March week in
   2019, 2020, 2022, 2024 and 2026 found onshore and offshore coverage
   identical in every sample (768/768 timestamps matched, zero nulls) --
   Energy-Charts appears to always publish both series together for Germany,
   at least in the years sampled. The fix is a genuine correctness
   improvement for a real class of failure, not a retroactive correction; no
   backfill re-run or evidence retraction was needed.
3. **Frozen-snapshot metadata did not identify a fundamentals-ablation run.**
   `BacktestResult.metadata()`'s `feature_mode` field distinguished only
   "price and calendar only" from "lagged actual load/generation," so a
   snapshot run with `include_fundamentals=True` looked identical to one
   without it in its own published metadata -- exactly the kind of gap that
   makes "was this run the ablation or the published release" a question
   requiring memory rather than a fact in the record. Fixed: `feature_mode`
   now appends "; day-ahead load/wind/solar forecasts (assumed gate vintage)"
   whenever a `da_*` feature is present. Covered by
   `test_validation_and_cutoff_do_not_read_later_prices`'s extended
   assertions across all three feature-mode states.
4. **The file documented as the battery study's reproducible input actually
   held only the 12-week browser preview.** `export_all()` reassigned
   `result` to `_forecast_page_predictions(full_predictions)` (the trimmed
   sample) before writing it out as `forecast_predictions.parquet` -- so
   anyone downloading "the input" to reproduce the battery study, per the
   project's own stated reproducibility promise, would have gotten a
   12-week sample instead of the full multi-year history the real study
   used internally. Fixed: the trimmed sample now writes to a new
   `forecast_preview.parquet` (which `site/forecast.md`'s week inspector was
   repointed at), and `forecast_predictions.parquet` keeps the full,
   untrimmed, rounded clock-hour table. README.md documents this split and
   the exact reproduction command. **Verified live**: `gpa battery-study
   --predictions site/data/forecast_predictions.parquet` reproduces the
   README's published headline exactly -- 2,404 common days, Ridge at 4h
   adds EUR 29,014.30/MW over `naive_similar_day`. Regression test
   `test_export_keeps_full_study_input_separate_from_browser_preview` spies
   on `_battery_tables`'s actual input and asserts the exported file matches
   it bit-for-bit while the preview file stays smaller.

**Site narrative rigor pass on `site/battery.md`,** alongside the fixes
above, tightening several claims that were more confident than the evidence
supported: correlation-vs-causation language strengthened throughout (e.g.
"the annual correlation cannot identify its contribution" rather than
implying a hidden effect exists); 2026 explicitly marked as a partial year
everywhere it appears, not only in the methodology section; both Solar AC and
Solar DC realised maxima shown side by side rather than picking one, with the
EEG target's own AC/DC convention flagged as unverified; the BNEF pack-price
figure clarified as global, not German-specific CAPEX; the BNetzA auction
figure's terminology corrected (a "volume-weighted average award value," not
a single "cleared" price); and the closing "what would make this less
attractive" paragraph no longer asserts a specific causal story about
volatility outpacing competition, replaced with a more careful framing of
what the data can and cannot say.

**Verification:** full `pytest` (395 tests), `ruff check`, `mypy` all clean. `npm run build` and `npm run test:browser` (Playwright
against a live `observable preview` server, both viewports) both pass on the
rebuilt site with no console errors and no leaked `NaN`/`undefined`; the
rendered DOM text of the changed section was read directly and confirmed
coherent. `gpa export --check` passes against the regenerated `site/data/`.

### Historical market regimes and pre-auction fundamentals — recorded before implementation

Two independent pieces, split because they differ enormously in cost and risk.
User confirmed the data-source choice in part B (Energy-Charts, not SMARD)
before this was written.

**A — historical market regimes (narrative, low risk).** The extended
2019-2026 history now spans COVID demand collapse, the 2021-22 gas crisis and
the 2025 quarter-hour transition, but nothing on the site explains them; the
existing `forecast_scores.parquet` "year" and "regime" (negative-price,
scarcity) scopes already compute the numbers, unnarrated. Add explanatory
prose to `site/forecast.md` (and the homepage findings, if a regime materially
changes a claim) grounded only in those already-computed tables — read the
actual year-by-year MAE/skill and negative-price frequency before writing a
sentence about them, never assume "the energy crisis raised volatility" and
then go looking for a number to cite. No new computation.

**B — pre-auction fundamentals (new data, real engineering risk).** The
architecture already exists and was never connected: `src/gpa/forecast/
fundamentals.py` (`normalize`, `attach` with a D-1-noon-gate as-of join,
`FUNDAMENTAL_FEATURES`) and `panel.build_panel`'s `fundamentals` parameter are
already wired and tested against a synthetic frame; `SMARD_FORECAST_FILTERS`
shows SMARD was the originally intended provider, but nothing fetches real
data from it. This plan uses Energy-Charts instead (user's choice): checked
live and read-only on 15 September 2026, `/public_power_forecast`
(`production_type` in `load`/`solar`/`wind_onshore`/`wind_offshore`,
`forecast_type=day-ahead`) returns quarter-hour data back to at least
2019-06-01, and a spot check against `/public_power` actuals for 2020-06-15
solar confirmed the two series are genuinely different (0 of 30 sampled
intervals identical, realistic small forecast errors) — not actuals
relabelled as a forecast.

1. **Vintage honesty, decided before any code.** Neither endpoint exposes a
   publication timestamp; `fundamentals.py`'s own docstring already
   anticipates this ("the caller supplies the publication time... because
   SMARD's historical chart archive does not provide a reliable publication
   vintage"). A historical backfill cannot observe a real retrieval instant,
   so it must not fabricate one. Assign `published_at` = the D-1 noon gate
   itself for every backfilled row (`da_forecast_age_hours = 0` throughout),
   and label this table's `data_vintages` metadata explicitly as "historical
   day-ahead-labelled forecast, publication instant not independently
   verified — distinct from a live-captured vintage." A future live `gpa
   issue` run would instead record its actual fetch instant, and that
   distinction must stay visible in the data, not just in prose.
2. **New store dataset.** Add `"fundamentals"` to `schema.SCHEMAS`/
   `store.DATASETS`: `zone, ts_utc, resolution_min, series
   (load_forecast_mw/wind_forecast_mw/solar_forecast_mw), value, source`, or a
   wide equivalent — decide the exact shape from how `store.write`'s
   zone/month partitioning and `fundamentals.normalize`'s wide contract
   compose most simply, favouring reuse of the existing narrow
   zone/ts_utc/fuel-like pattern `generation` already uses over inventing a
   new one. Combine onshore + offshore into one `wind_forecast_mw` at fetch
   time, matching `RESIDUAL_LOAD_FUELS`'s existing wind/solar residual
   definition. Register the source in `zones.py` for DE-LU only.
3. **Fetch and backfill.** A new `EnergyChartsSource` method for
   `/public_power_forecast`, reusing the existing retry/backoff client. `gpa
   backfill --zone DE-LU --dataset fundamentals` from 2019-01-01 (matching the
   committed store's own floor) through today. Validate before use: `gpa
   validate`, `quality.report()`, no internal gaps/duplicates, resolution
   consistent with load/generation.
4. **Wire the panel, then test leakage before touching the harness.**
   Construct the `zone/ts_utc/published_at/*_forecast_mw` frame `attach()`
   expects from the stored `fundamentals` dataset; add a `load_panel(...,
   include_fundamentals: bool = False)` path calling `build_panel(...,
   fundamentals=...)`. Add regression tests before wiring the harness:
   a future-dated fundamentals row must never enter a target day's features
   (the existing D-1-noon gate in `attach()` already enforces this — write a
   test that proves it against a real day using this data, not just the
   existing synthetic-frame tests), and the residual-load-forecast identity
   (`da_residual_load_forecast = load - wind - solar`) must match.
5. **Ablation, not a wholesale model change.** Run `gpa backtest --zone DE-LU
   --scope all` with `include_fundamentals=True` against the *same* frozen
   evaluation window as `b0e69bf47f6e230be9b5` and compare MAE, skill and
   feature coefficients with vs. without the new features — a new comparison
   table, not a replacement of the current frozen release. Do not select
   models or parameters using the comparison result; the existing validation
   window and grids stay as they are. If the ablation improves the case for
   a new frozen release, that is a separate, later decision requiring its own
   plan-then-freeze discipline like F's and the history extension's, not an
   automatic consequence of this item.
6. **Verification.** Full pytest/ruff/mypy; `gpa export --check` must still
   pass unchanged (nothing here alters the published release by itself);
   record the ablation's numbers, coverage report and any excluded interval
   here, plus store growth.

**Not in this item:** wiring fundamentals into `gpa issue` for prospective use
(D's live pilot boundary stays closed); outages, cross-border availability or
fuel/carbon inputs (P2's own text defers those); adopting the ablation's
result as the new frozen release (a later, separate decision).

### Pre-auction fundamentals handoff evidence — done, committed and run

Committed: `54c80a2` (engine wiring), `986c985` (hourly-aggregation fix, found
before running the real ablation — see below), `b261455` (a performance fix
to the same new code). The ablation itself ran clean but was **not saved as a
snapshot**, per item 5: `data/experiments/b0e69bf47f6e230be9b5` is unchanged
and still the published release.

**Backfill.** `gpa backfill --zone DE-LU --dataset fundamentals --days 2810`
(rate-limited, absorbed by the existing retry/backoff, same as history's own
backfill) wrote 809,280 rows, 2019-01-05 to 2026-09-15, quarter-hourly
throughout — Energy-Charts' forecast endpoint has never changed resolution,
unlike `price`/`load`/`generation`. `gpa validate`: 575/575 partitions valid.
`quality.report()`: 100% coverage, 0 gap hours, 0 invalid intervals, 0
duplicate `(ts_utc, series)` keys, all three series. `store.read` size: DE-LU
`fundamentals` adds a few MB to the committed store (measured, not yet
finalized for commit — see below).

**A real bug found before it reached the ablation.** `attach()` keys on
`(local_date, local_hour)` and was only ever tested against synthetic
hourly fixtures; real Energy-Charts fundamentals are quarter-hourly, so
without a fix, three of every four quarter-hour forecast values would have
been silently dropped by `attach()`'s `unique(keep="last")` tie-break rather
than averaged. Caught by rewriting the tests with realistic quarter-hour
fixtures (the artificial hourly ones used at first hid it completely) before
running anything against real data. `from_store()` now averages sub-hourly
rows into one duration-weighted hourly value per series and drops a clock
hour outright rather than averaging a partial one.

**Ablation.** `gpa backtest --zone DE-LU --scope all --include-fundamentals`,
otherwise identical settings to the frozen release (`min_train_days=270`,
`validation_days=90`, `lightgbm_refit_days=1`, same `end`), so both runs share
the *exact* same 2,445-day test sample (2020-01-03 to 2026-09-12) and the
same 58,645 scored cells. Feature count: 19 → 24 (the 5
`FUNDAMENTAL_FEATURES`), confirming the new inputs were genuinely included.
Ridge's validation-selected penalty moved from 0.1 to 0.3 — a real
re-selection on the same validation protocol, not a confound to control away.
Not saved as a snapshot; figures below are read from the run's console output
plus one derivation (see note).

| | MAE without (`b0e69bf...`) | MAE with fundamentals | Skill vs. best baseline, without → with |
|---|---:|---:|---|
| Ridge | 22.07 EUR/MWh | ~19.17 EUR/MWh | 24.3% → 34.2% |
| LightGBM | 25.56 EUR/MWh | ~20.55 EUR/MWh | 12.3% → 29.5% |

*Note:* the ablation's console table truncated `mae` at terminal width;
`~19.17`/`~20.55` are derived from the printed `skill_pct` against
`naive_similar_day`'s MAE (30.4628 EUR/MWh, read from the frozen release's own
exported `forecast_scores.parquet` — identical in both runs because naive
baselines use no features) and cross-checked against `skill_vs_best_baseline_pct`
using `naive_previous_day`'s MAE (29.1392); both derivations agree to three
decimal places.

**Where it helps, and where it does not — the mechanistically interesting
part.** In the **negative-price regime**, both models reverse from a loss to
their strongest regime: Ridge -3.9% → **+38.1%**, LightGBM -35.1% → **+47.3%**
skill vs. best baseline. This is coherent, not just a bigger number: German
negative prices are driven by renewable output exceeding demand, which is
exactly what a same-day wind/solar forecast predicts directly, unlike the
frozen release's two-day-lagged residual load. The **scarcity regime** (top 5%
prices, driven more by tight dispatchable supply and outages than by
renewables) does not improve the same way and gets *worse* for LightGBM:
Ridge 16.0% → 12.9%, LightGBM -56.7% → **-65.6%** (MAE 123.36, the single
worst-margin bucket in this run). By year, 2021's outright LightGBM loss
(-9.4%) becomes marginally positive (+0.1%); 2022's -2.0% becomes +9.6%.

**What this is not.** Not a new frozen release — the published site, README
and `data/experiments/current.json` are untouched. Not a claim the
improvement is guaranteed to hold prospectively: it is measured on the same
already-inspected development sample every other figure in this project uses.
Not evidence the scarcity-regime degradation is acceptable — it is recorded
here precisely so a future decision to adopt fundamentals does not quietly
drop it.

**If a new frozen release is warranted, that decision needs, at minimum:**
reviewing whether the alpha-0.3/24-feature model should also change D's
prospective issuance policy (currently alpha 0.1, 19 features); re-running
the seven battery sensitivity scenarios and README/site reconciliation exactly
as F and the history extension did; and deciding whether the scarcity-regime
result is disclosed prominently enough that "fundamentals help" does not read
as "fundamentals help unconditionally." None of that is done here.

Verification: full pytest (352 passed), ruff check/format, mypy strict (still
clean after this item — no further changes since the last full run), `gpa
validate` (575/575), `quality.require_integrity()` (passed). `gpa export
--check` was not re-run since nothing in `export.py` or the frozen snapshot
changed in this item.

### DE-LU history from 2019-01-01 and a re-frozen release — recorded before implementation

The user found the history too short ("2024") and asked to go back to 2018,
then chose **1 January 2019** after learning the constraint below. This
extends history for the study market only and re-runs the already-registered
protocol on it; it is not a methodology change.

1. **Start date and its floor.** Checked live, read-only, on 15 September
   2026: Energy-Charts returns DE-LU day-ahead prices from 1 October 2018 and
   "no content available" before it. That is the real bidding-zone split —
   the joint DE-AT-LU zone became DE-LU and AT — not an API gap, so earlier
   DE-AT-LU prices are a different product and will not be spliced in as
   DE-LU. Load and generation (`public_power`, country `de`) are available
   back to at least 2015. The store will begin at **2019-01-01 00:00
   Europe/Berlin (2018-12-31T23:00Z)** for all three DE-LU datasets. The
   backfill already running fetched from 2018-09-27; the September–December
   2018 overshoot is this session's own output and will be trimmed with
   `store.atomic_parquet`, keeping that final UTC hour of the 2018-12
   partition. Other zones (FR, ES, BR-SIN) keep their current windows: they
   are cross-market context, not the study sample.
2. **Validate before modelling.** `gpa validate`; per-dataset coverage and the
   existing quality report; check for internal gaps, duplicates, DST days,
   resolution changes and that wind and solar are reported throughout (the
   residual-load features require both). Record any exclusions rather than
   repairing or imputing them.
3. **Protocol unchanged.** Keep `MIN_TRAIN_DAYS = 270`, `VALIDATION_DAYS = 90`,
   `BENCHMARK_END = 2026-09-12`, expanding windows, the existing alpha and
   LightGBM grids selected on the validation window only, daily LightGBM
   refits and seed 42. With the new start, validation falls in late 2019 and
   the test period runs from about January 2020 to 12 September 2026 —
   COVID, the 2021–22 energy crisis and the 2025 quarter-hour transition
   included. Constants will not be adjusted after seeing results. The
   2020–mid-2024 portion was never in this project's store, but it is public
   history the author already knows in broad strokes, so the label stays
   "retrospective development benchmark", not an untouched holdout.
4. **New release, old one kept.** `gpa backtest --zone DE-LU --save-snapshot`
   writes a new content-addressed experiment and repoints `current.json`;
   the previous `5e3c9f1a256ec73c62e2` directory stays as committed, for
   provenance. D's prospective issuance policy (Ridge alpha 0.1) is not
   changed by this retrospective re-selection: if the new validation window
   selects a different alpha, record the divergence here instead.
5. **Test isolation (required, not optional).** `tests/test_cli.py` promises
   it never touches committed repository data, yet its three export tests
   read the real `data/experiments/` release through the repository-relative
   `snapshot.ROOT` — about 397 s of the last 419 s suite. At roughly 6.5×
   the evaluation days they would take on the order of 40 minutes. Isolate
   `snapshot.ROOT`/`CURRENT` in the CLI autouse fixture and give the export
   tests a tiny valid release written through `snapshot.save`.
6. **Browser weight, decided from measurements.** The battery page loads the
   full `battery_dispatch` table and the forecast page the full
   `forecast_predictions` table into the browser. After regenerating, measure
   both; if they grow past a few MB, export what the pages actually draw
   (monthly cumulative margin, a bounded dispatch sample, bounded prediction
   weeks) instead of every row. Measure `data/curated` growth for the
   committed store too.
7. **Regenerate and reconcile.** `gpa export`, then `gpa export --check`;
   update every hand-written figure and date in `README.md`, site prose and
   this file from the regenerated tables; full pytest/ruff/mypy, build and
   browser smoke; commit locally without pushing.

### DE-LU history extension — partial handoff evidence (items 1, 2, 5 and half of 6)

The user asked to wrap up the session before the re-freeze. Done and
committed: `9fbf056` (test isolation), `544d961` (history extension),
`db8973c` (battery page data). Not done: items 3, 4, the forecast half of 6,
and 7's reconciliation.

**Item 1 — backfill and trim.** `gpa backfill --zone DE-LU --days 2910`
(2018-09-27 to 2026-09-15, 60-day chunks) finished with `written 3 | failed 0`
after many HTTP 429 responses, all absorbed by the existing retry/backoff.
Energy-Charts's first DE-LU price row is 2018-09-30T22:00Z (1 October 2018
Berlin), confirming the bidding-zone floor. The overshoot was trimmed in place:
2018-09 to 2018-11 partitions removed, 2018-12 kept only from 23:00Z. All
three datasets now begin 2018-12-31T23:00Z (2019-01-01 00:00 Berlin). Only
DE-LU changed: 207 new monthly partitions (69 months × 3) plus refreshed
2026-09 ones. Store size, DE-LU: price 0.8 MB, load 1.9 MB, generation 11.4 MB
(3.7 MB before); all of `data/curated` is 19.6 MB.

**Item 2 — validation, measured before any modelling.** `gpa validate`: 482
partitions valid. No duplicates in any dataset. Zero incomplete local days
for price or load through `BENCHMARK_END` (duration-weighted hours against
`hours_in_local_day`, so DST days count correctly). Zero days missing wind or
solar. `quality.report()`: every DE-LU series 100% coverage, 0 gap hours,
0 invalid intervals; `quality.require_integrity()` passed. Resolution: price
hourly until the day-ahead market's move to quarter-hours on 1 October 2025,
15-minute after; load and generation 15-minute throughout. Nuclear generation
ends 2023-04-15, the German phase-out — a real fuel ending, not a gap.
What the unchanged harness will see: 2,805 usable panel days (every calendar
day from 2019-01-08; the first week is D-7 lag warm-up), `train_start`
2019-01-08, `validation_start` 2019-10-05, `test_start` 2020-01-03,
`test_end` 2026-09-12 — about 2,445 test days against 374 in the current
release. No exclusions were needed.

**Item 5 — test isolation.** The CLI autouse fixture now monkeypatches
`snapshot.ROOT`/`CURRENT` into the temporary directory, and a `release`
fixture writes a tiny valid release (two days, all five forecasts) through
`snapshot.save`. The export tests also assert that the first export succeeds,
so the stale-table test can no longer pass because the export itself failed.
Export tests: ~397 s → ~6 s. Full suite: 346 passed in 26.7 s (was 419 s).

**Item 6, battery half — measured, then changed.** `battery_dispatch.parquet`
was 840 KB and 185,472 rows for 368 days, which projects to about 5.5 MB and
1.2 million browser rows at ~2,400 days — far past "a few MB". The page only
draws monthly cumulative margin and one example day, so `_battery_tables` now
exports `battery_monthly` (strategy × asset × local month: days, profit) and
`battery_dispatch_example` (every strategy and duration on the last common
day) instead. Test first:
`test_battery_page_gets_monthly_margins_and_one_dispatch_day_not_every_interval`
failed on the old export, then passed. On the current release: all of
`site/data` 1,563 KB → 832 KB even with the longer historical tables; the
battery page's files 927 kB → 102 kB. The forecast half is not decided:
`forecast_predictions.parquet` is 539 KB now and should reach roughly 3.5 MB
after the re-freeze; measure the forecast page then, before changing anything.
It is also the documented battery-study input, so bound only what the page
loads, not the table the battery study reads.

**Verified at `db8973c`:** `gpa export --check` passes (release unchanged,
historical tables from the extended store); pytest 346 passed; ruff check and
format clean; mypy strict clean (35 files); `npm run build` (4 pages, 6 links);
`npm run test:browser` in both viewports with zero errors or overflow,
including the battery-page assertions (7-line cumulative chart, every evidence
table, duration selector).

**Historical handoff note:** At that checkpoint, `README.md`, the homepage
findings and the battery page prose still quoted the one-year release. The
re-freeze and reconciliation below supersede that temporary mismatch.

### Session close — 15 September 2026 (seventh checkpoint, superseded)

To resume safely in a new session:

1. Open this roadmap and run `git log --oneline -6` / `git status --short`.
   Expect a clean tree with the docs commit recording this checkpoint on top
   of `db8973c`, `544d961`, `9fbf056` and `c422172`, all unpushed (local agent
   tooling is gitignored).
2. Re-freeze (item 4): `.venv\Scripts\gpa.exe backtest --zone DE-LU --scope all
   --save-snapshot`. Run it in the background: ~2,445 test days with daily
   LightGBM refits will take far longer than the previous run. Constants stay
   as in item 3. If the new validation window (2019-10-05 onward) selects an
   alpha other than 0.1, record it; do not change D's issuance policy.
3. `gpa export` (the battery study re-runs 7 scenarios × 7 strategies × 3
   durations over ~2,400 days; the old sample took ~80 s, so expect ~9 min),
   then `gpa export --check`. Measure `forecast_predictions.parquet` and the
   forecast page's load time in both viewports; bound what the page loads
   only if needed (item 6).
4. Reconcile from the regenerated tables, not from memory: every figure in
   `README.md`'s "Featured result"; the homepage findings, whose wording
   encodes conclusions that could flip on a 2020–2026 sample (LightGBM
   "trails" its best naive at 1h, the robustness range, "a bigger model is not
   automatically a better dispatch signal"); and the battery page prose ("Ridge
   adds", "Treat 4h as a candidate"). If a conclusion no longer holds, say so.
   Note the downtime calendar is anchored at 2025-01-01 and extends backwards
   on the same 20-day phase, which `calendar_outages` already does.
5. Full checks (pytest, ruff, mypy, build, browser smoke), record results here,
   commit locally. Not in scope: FR/ES/BR-SIN history, pushing, deploys.
6. Known side effect to note, not fix: the prospective `forecast.yml` seeds its
   isolated store from `data/curated`, so its first `gpa issue` will train on
   2019 onward and write provenance blobs for those months once.

Suggested resume request (superseded):

> Leia `docs/portfolio-roadmap.md`. O histórico DE-LU desde 2019-01-01 está
> commitado, mas o release ainda não foi re-congelado. Continue do passo 2 do
> "Session close — seventh checkpoint". Não publique no GitHub sem eu pedir.

### DE-LU release re-freeze and reconciliation — completed

The seventh checkpoint is complete. The unchanged protocol was rerun with
`gpa backtest --zone DE-LU --scope all --save-snapshot`, producing snapshot
`b0e69bf47f6e230be9b5` and retaining `5e3c9f1a256ec73c62e2` as the prior
release. Ridge selected `alpha 0.1`, matching the prospective issuance policy.
The benchmark covers 2,445 test days from 2020-01-03 to 2026-09-12, with
58,645 scored clock-hour cells. `gpa export` and `gpa export --check` then
recomputed the site and battery tables from that snapshot.

The regenerated headline figures are:

- Ridge MAE EUR 22.07/MWh versus EUR 29.14/MWh for the previous-day baseline,
  a 24.3% reduction.
- On 2,404 common battery days, Ridge 4h captures 90.1% of constrained
  perfect foresight and adds EUR 29,014/MW over retrospective best-naive
  similar-day dispatch; the exploratory paired interval is EUR
  22,515–35,347/MW. Removing the five largest positive incremental days leaves
  EUR 26,093/MW.
- Across the seven Ridge 4h stresses, incremental margin ranges from EUR
  18,623 to EUR 33,703/MW. The combined 2/3 cost + 85% efficiency + 50%
  signal + downtime case leaves EUR 18,623/MW. At 1h, LightGBM trails its
  best naive by EUR 14,573/MW in the zero-cost case.

The full forecast table reached 3.70 MB and the unbounded Forecast page reached
3.840 MB, so the browser export now retains the battery study's complete
in-memory prediction input but publishes twelve evenly spaced benchmark weeks
for the interactive week inspector. The final site export contains 19 files
and 520 KB; the Forecast page has 895 KB of shared files plus 268 KB of route
data, while the Battery page has 895 KB plus 112 KB of route data. Direct
Forecast navigation measured about 1.69 s at 1440 px and 1.59 s at 390 px.
This bound is a presentation optimization only: scores, metadata and battery
economics still use the complete frozen prediction sample.

The hand-written README figures and the existing dynamic homepage and battery
prose were reconciled from the regenerated tables. The conclusions changed in
magnitude but not direction: Ridge still beats the strongest price-error naive,
the Ridge 4h increment remains positive across every fixed stress, and
LightGBM still loses to its best naive at 1h. The old one-year figures remain
only in earlier handoff records as historical release evidence. Verification
passed with 347 tests, ruff check/format, mypy, `gpa export --check`, the site
build and browser smoke in both viewports, with no errors or overflow. This is
still a local, unpushed increment.

### English executive case — recorded before implementation

User authorized proceeding ("go for it"); author-link scope confirmed
separately (GitHub profile only — `github.com/Pedrods20`, already public via
this repo's own CI/Deploy badge URLs, not a new fabricated link — and "sole
developer, solo portfolio project" as the contribution framing). Grounded in
the baseline finding that the live site's first screen ("the preview") leads
with historical charts and has no personal positioning or decision-oriented
conclusion, and P4's acceptance bar: a reader identifies the market,
candidate contribution, commercial finding and principal caveat in under a
minute. Scope: `site/index.md` only, a light consistency pass on `README.md`
if its existing "Portfolio case"/"Featured result" text does not already
match; no new pages, markets, models or backend.

1. Add a compact, English-language executive-case block above the existing
   historical charts on `site/index.md` (today: zero narrative, pure charts —
   the exact gap the baseline finding named), covering, in this order:
   question, three qualified findings, implication, limitations, personal
   contribution. Every number cited must be read from the committed
   `site/data/*` tables at build time (via `FileAttachment`, matching every
   other page's convention), never hand-typed, so `gpa export --check` keeps
   catching drift the same way it already does for the rest of the site.
2. Question: does a validated day-ahead price forecast create measurable,
   stress-tested battery value, and where does it fail. Three findings,
   each qualified with its sample and caveat: (a) forecast skill — Ridge's
   MAE reduction vs. the best naive baseline, read from `forecast.json`/
   `forecast_scores.parquet`, explicitly noting LightGBM's shortfall at 1h
   duration so the finding is not one-sided; (b) battery translation — the
   4h capture-vs-perfect and incremental-margin-with-interval, read from
   `battery_summary.parquet`/`battery_sensitivities.parquet`; (c) robustness —
   the incremental margin's stability across the seven cost/efficiency/
   signal/downtime stresses, same source. Implication: one or two sentences
   connecting (a)-(c) to a commercial reading without overclaiming past what
   the data supports. Limitations: retrospective/development evidence, zero
   CAPEX/financing, illustrative (not sourced) cost/efficiency stresses,
   hourly not quarter-hour execution, no prospective pilot yet — reuse the
   exact qualifiers already established on the forecast/battery pages rather
   than inventing new phrasing for the same facts.
3. Personal contribution: one short paragraph — solo-built end-to-end
   (ingestion across two providers, leakage-safe walk-forward forecasting,
   the constrained battery-dispatch and stress-testing engine, this site) —
   with a single link to the GitHub profile. No other personal/contact
   information; nothing fabricated or guessed.
4. De-emphasize, do not remove, the historical charts: keep them below the
   new block, unchanged in content, per "keep monthly history superficial."
5. Reconcile `README.md`'s "Portfolio case"/"Featured result" only if reading
   the two side by side shows an actual inconsistency (a number, a claim, a
   qualifier) with the new site text; do not rewrite prose that already
   agrees just to make the two files textually identical.
6. Verification: `npm run build`, the full `npm run test:browser` smoke suite
   (index page keeps a visible chart per the existing assertion, no new
   errors/overflow at either viewport), and a manual read confirming every
   cited figure traces to a committed `site/data/*` file. Record results,
   the final numbers used and any limits here before calling this done.

### English executive case handoff evidence — done, committed

Changed files: `site/index.md` only. `README.md`'s "Portfolio case"/"Featured
result" was read side by side with the new text and found already consistent
— every figure matched exactly, so nothing there was rewritten, per item 5.

What was built: a new executive-case block above the H1's existing historical
charts, all five elements from the plan (question, three findings,
implication, limitations, personal contribution), every number read live from
`data/forecast.json`, `data/forecast_scores.parquet`, `data/battery_summary.parquet`
and `data/battery_sensitivities.parquet` via `FileAttachment` — none hand-typed.
The existing three chart sections moved under one new "## Historical context"
heading with a short lead-in sentence, demoted to `###` subheadings, content
unchanged. The H1 changed from "Historical dashboard" to a decision-oriented
question; the page's frontmatter `title:` (browser tab / `<title>`) was left
as "Historical dashboard" since it is not referenced by `observablehq.config.js`'s
`pages` array (index is the implicit home page) and changing it was not part
of the plan. The personal-contribution line links only
`https://github.com/Pedrods20`, per the user's explicit scope confirmation;
no other contact information was added or invented.

Rendered figures, verified against a live headless-browser read of the built
page rather than assumed from the source: forecast skill 27.0% (EUR 21.04 vs
28.80/MWh, previous day, 8,971 scored hours, 2025-09-04 to 2026-09-12);
battery translation 94.5% capture, EUR 5,578/MW increment (95% interval EUR
3,810–7,571/MW) on the 368-day base-scenario sample, with the LightGBM 1h
shortfall (EUR 60/MW) stated as the same finding's own qualifier rather than
a separate caveat; robustness range EUR 5,017–5,820/MW across the seven
stresses (`d3.extent` over all seven `battery_sensitivities` rows for
Ridge/4 MWh, so this is exact, not copied from README's own two named
endpoints). All match README's already-published numbers exactly.

Verification:

| Check | Result |
|---|---|
| `npm run build` | 4 pages rendered, 6 links validated |
| `npm run test:browser` | Both viewports, all 4 routes, zero errors/overflow; index still reports a visible chart per the existing assertion |
| Headless render of `/` | Zero page errors; full executive-case text and all three historical charts confirmed present via `page.locator("main").innerText()` |
| GitHub link | `href="https://github.com/Pedrods20"` confirmed in the built HTML |
| Figure cross-check | Every cited number in the rendered page matches `README.md`'s existing "Featured result" exactly |

No Python source changed in this item, so `pytest`/`ruff`/`mypy` were not
rerun; they were last verified clean for the current `src/gpa` state in G's
handoff evidence above and nothing there was touched.

### Portfolio steps 1-2 / bounded G plan — recorded before code changes

Scope approved by the user: reconcile the current evidence and finish a small,
commercially interpretable sensitivity study. Preserve existing untracked agent
configuration and `.gitattributes` changes, plus all historical study artifacts.

1. Use all three fixed naive forecasts, Ridge and LightGBM on the same eligible
   days, for 1/2/4h assets. Keep the frozen hourly/rounded presentation input
   convention explicit so old studies remain comparable; do not invent physical
   DST intervals. Reconcile README, page, export and calculated headline.
2. Display incremental value, each fixed comparator, observed downside and
   concentration, exploratory paired intervals and the existing re-optimized
   cost cases. Label the best naive as selected retrospectively, not a policy.
3. Before running the new stresses, fix their rules: retain cost pairs 0/0,
   2/3 and 5/10 EUR per absolute grid MWh; use a sourced 85% round-trip
   efficiency sensitivity (not German project calibration); simulate full-day
   downtime every twentieth calendar day from 2025-01-01, identically for all
   strategies; and halve Ridge/LightGBM's forecast deviation from the previous
   day baseline without reading realised prices. Re-optimize changed forecasts
   and efficiency, but zero settlement/activity on downtime days, keeping the
   sample denominator. Include a combined 2/3 cost + efficiency + signal +
   downtime stress. These are diagnostic scenarios, not fitted probabilities.
4. Freeze model settings and sample; no model re-selection on these results.
   Existing history has been inspected and remains development evidence.
   Explain duration trade-offs from operational margin; do not claim optimal
   investment duration without CAPEX, fixed costs and additional revenue data.
   Externally sourced assumptions and illustrative costs must stay distinct.
5. Fix the cumulative chart and assert its visible, nonempty curves, plus cost
   and risk tables, in both browser viewports. Regenerate the static export,
   run focused/full tests, lint/type checks, export replay and site/browser
   checks. Record results, commands, numerical conclusions and any limits here.

Portfolio direction adopted from the review: finish this evidence first, then
bring forward the English executive case (question, finding, implication,
limitations and personal contribution). Next explain historical market regimes
and test genuine pre-auction fundamentals; only then develop the DE-LU capacity/
renewables/storage scenario study. Keep monthly history superficial. Do not add
markets, pages, models or operational infrastructure merely for breadth. Keep
this file as the single state/handoff record; older entries below are history,
not a competing current plan. The original broader G's asset-specific cost
calibration and investment valuation are not implied by this bounded scope.

### Portfolio steps 1-2 / bounded G handoff evidence — done, committed

The plan above and code were both already written, uncommitted, when this
session resumed; this session's job was to verify, regenerate the export, run
the full check list and record the result — item 5 of the plan above.

Changed files: `src/gpa/battery_sensitivity.py` (new: `weaken_signal`,
`calendar_outages`, `scenario_tables`, the seven registered `SCENARIOS`),
`src/gpa/battery_study.py` (+`underperform_days`, `top_5_days_share_positive_incremental`,
`incremental_without_best_5_days_eur_mw` on `paired_comparisons`),
`src/gpa/export.py` (`_battery_tables` now compares all five models at 1/2/4
MWh and calls `battery_sensitivity.scenario_tables`, adding `battery_sensitivities`
to the exported tables), `site/battery.md` (rewritten: interactive duration
selector, decision-in-brief summary, full comparator/cost/sensitivity/risk/
duration tables, a download link for the sensitivity Parquet), `site/methodology.md`
(documents the sensitivity protocol and 1/2/4 MWh cases), `README.md` (Featured
result replaced with the sensitivity-backed figures), `scripts/browser-smoke.mjs`
(battery-page-specific assertions: a nonempty, 7-line cumulative chart and
every evidence table visible with content, in both viewports), `.gitattributes`
(`graphify-out/graph.json merge=graphify`, preserved per the plan's scope note),
`tests/test_battery_sensitivity.py` (new), `tests/test_battery_study.py` and
`tests/test_export.py` (extended). All of `site/data/*` regenerated.

**The "Cumulative net value" chart defect F found and left unfixed is now
fixed**, confirmed by the browser smoke suite: `battery.md`'s cumulative-chart
cell now assigns the plot to a variable, sets `cumulativeChart.id`, and calls
`display(cumulativeChart)` explicitly instead of relying on an implicit
last-expression display after two preceding `const` declarations in the same
cell — plus the duration selector is now a `view(Inputs.select(...))`, so the
whole cell structure changed. Which of those changes actually fixed it was not
re-isolated (out of scope here, and no longer worth the time now that it
reproduces correctly with real data); `npm run test:browser` now specifically
asserts the cumulative chart has exactly 7 visible strategy lines in both the
1h and 4h duration views, so a regression would fail CI rather than pass
silently the way the original defect did.

Verified against the regenerated `site/data/*` (not re-derived from the
README's own prose, to catch a possible stale/aspirational figure): Ridge's
4 MWh incremental margin over its best naive (`naive_similar_day`, selected
retrospectively) is EUR 5,577.55/MW, a 4.25% uplift over that comparator's own
EUR 131,095/MW, with a 95% exploratory interval of EUR 3,810–7,571/MW; removing
its five best incremental days leaves EUR 4,114.41/MW; the `combined` stress
(2/3 EUR costs + 85% efficiency + 50% signal + calendar downtime) leaves
EUR 5,498.27/MW; and LightGBM trails `naive_previous_day` by EUR 59.68/MW at
1h in the zero-cost case. All exact matches to what `README.md` already
states, confirming the prior session's figures were pipeline output, not
hand-typed estimates. The full sensitivity table for Ridge at 4 MWh:

| Scenario | Available days | Margin (EUR/MW) | Increment vs best naive (EUR/MW) | 95% interval |
|---|---:|---:|---:|---:|
| base | 368 | 136,672.56 | 5,577.55 | 3,810.02 – 7,571.40 |
| cost 2/3 | 368 | 121,927.30 | 5,819.59 | 3,897.71 – 7,874.16 |
| cost 5/10 | 368 | 93,536.80 | 5,594.97 | 3,548.53 – 7,830.46 |
| efficiency 85% | 368 | 128,440.23 | 5,708.16 | 3,927.03 – 7,695.02 |
| signal 50% | 368 | 136,759.16 | 5,664.15 | 3,783.33 – 7,817.42 |
| calendar downtime | 350 (18 unavailable) | 129,147.08 | 5,016.62 | 3,297.17 – 6,894.35 |
| combined | 350 (18 unavailable) | 107,437.41 | 5,498.27 | 3,630.33 – 7,676.64 |

The incremental margin over the strongest fixed naive is remarkably stable
(roughly EUR 5,000–5,800/MW) across every stress; nothing here erases Ridge's
advantage over the sample, though none of it is a promise it survives
structural market change. By duration, all at `base`: 1h EUR 1,501.41/MW
(vs `naive_previous_day`), 2h EUR 2,801.87/MW (vs `naive_previous_day`), 4h
EUR 5,577.55/MW (vs `naive_similar_day`) — the best comparator itself changes
with duration, which is exactly why the page always names it per row rather
than assuming one fixed baseline.

Final validation (Windows, Python 3.13.9):

| Check | Result |
|---|---|
| Full suite | **345 passed, 0 failed** |
| Ruff check / format | Passed; 55 source/test files |
| Mypy strict | Passed; 35 source files |
| `gpa export` then `gpa export --check` | Regenerated, then confirmed reproducible from the frozen snapshot |
| `npm run build` | 4 pages rendered, 6 links validated |
| `npm run test:browser` | Both viewports, all 4 routes, zero errors/overflow; battery: cumulative chart visible with 7 lines, every evidence table visible with content, duration selector switches correctly |

Limits carried forward, unchanged from F: the frozen snapshot's provenance
covers the supplied prediction sample and this economic calculation, not
upstream model training or raw-data publication vintages; `signal_50` and
`efficiency_85` are diagnostic stresses, not calibrated error/asset
distributions; costs remain illustrative, not sourced market or investment
figures; and none of this is annualized or prospective performance.

### Session close — 15 September 2026 (sixth checkpoint, bounded G done)

The user made further changes outside this session (a review found F's
presentation defects and someone — a different tool or session — wrote the
plan above plus its full implementation, uncommitted) and asked this session
to review the uncommitted files and continue. This session verified the
already-written code and site against the full check list item 5 asked for,
confirmed the README's figures against a fresh `gpa export` rather than
trusting the prose, and committed. No push, public deploy, real issuance or
new provider ingestion occurred.

Also noted: this repository now carries local agent tooling (`.claude/`,
`.codex/`, `AGENTS.md`, `CLAUDE.md`, `graphify-out/`, all untracked) for a
codebase-graph tool called `graphify`, installed into `.venv` with hooks that
inject "MANDATORY" instructions on every Read/Bash/Grep call. This was
verified to be a real, locally installed executable wired through legitimate
hook config, not an external prompt injection, and the user confirmed it may
be used. It was left untracked per the plan's own instruction to preserve it;
whether to commit it, gitignore it or remove it is a decision for the user,
not made here.

To resume safely in a new session:

1. Open this roadmap in `C:\Users\Pedro\Desktop\Python\global-power-atlas` and
   run `git log --oneline -13` / `git status --short`. Expect a clean tree
   (aside from the untracked agent-tooling files above) with `30292c9` (bounded
   G) on top of `7b4aefe`/`0927be6` (F) and the earlier D/E commits on `main`,
   all unpushed.
2. Start the English executive case per "Portfolio direction adopted from the
   review" above (question, finding, implication, limitations, personal
   contribution) — not a new dashboard page. Write its plan into this file
   before editing code, the same discipline D, E, F and G followed.
3. After that: historical market regimes and genuine pre-auction fundamentals,
   then the DE-LU capacity/renewables/storage scenario study. Keep monthly
   history superficial; do not add markets, pages, models or operational
   infrastructure merely for breadth. The original broader G's asset-specific
   cost calibration and investment valuation remain open, not silently dropped.

Suggested resume request:

> Leia `docs/portfolio-roadmap.md`. A-F e o estudo de sensibilidade limitado de
> G estão commitados. Registre o plano do caso executivo em inglês antes de
> alterar código, depois implemente. Não publique no GitHub sem eu pedir.

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

### D handoff evidence — done, committed

D was resumed, finished and committed in three commits: `c893fa7` (A/B/C:
battery accounting and the economic study layer), `f2bb495` (D's initial
ledger/provenance/attempts rewrite, with two known-failing tests and open
lint/mypy findings), and `c594b75` (closing those gaps: contract-matching
tests, clean lint/format/mypy, CLI-level attempt tests, and the workflow wire-up).
All six D plan items above are done.

Changed files: `src/gpa/forecast/ledger.py` (rewritten), `src/gpa/cli.py`
(`issue`, `battery` and `reconcile` commands, new `forecast-attempt` command),
`src/gpa/forecast/models.py` (+4 lines: feature-only `predict_day` on `Naive`),
`.github/workflows/forecast.yml` (attempt registration and failure finalization).
New files: `src/gpa/forecast/provenance.py`, `src/gpa/forecast/attempts.py`,
`tests/test_ledger.py`, `tests/test_forecast_attempts.py`, plus new tests
appended to `tests/test_cli.py`. `src/gpa/battery.py` and
`src/gpa/battery_study.py` carry only the already-recorded A/B/C changes.

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
  raw `status == "scored"` rows. `gpa reconcile` needed no code change — it
  already called `ledger.reconcile`/`ledger.append` directly and those
  functions carry the new contract. A new `gpa forecast-attempt` subcommand
  exposes `start`/`finish`/`report` directly, with CLI-level tests covering a
  successful issue, an insufficient-history abstention, delivery-date
  inheritance from a pre-started attempt, and a full issue → reconcile →
  battery pass reading the same canonical selection.
- **Workflow.** `.github/workflows/forecast.yml` registers a tracked attempt
  (`gh-<run_id>-<run_attempt>`, origin `schedule` or `workflow_dispatch`)
  before refreshing inputs, reuses it in the final `gpa issue` step, and a
  `if: failure()` step finalizes it as failed if an earlier step (ingest,
  validate, reconcile or battery) never reached issuance. The commit step now
  runs `if: always()` so that failure evidence is preserved even though the
  job's own conclusion still reports the failure.

Closed this session (were open gaps in the prior partial commit `f2bb495`):

- Rewrote `tests/test_forecast_extensions.py::test_issue_ledger_retains_abstentions_and_round_trips`
  and `::test_reconcile_scores_observed_hours_without_changing_issue_identity`
  for the new full-grid-with-abstentions contract, instead of the old 2-row
  assertion (`small_panel` only has data for hours 3 and 14; the other 22
  hours must survive as `abstain_missing_inputs`, not be dropped).
- Fixed the one ruff finding (`cli.py:545`, now `contextlib.suppress`) and the
  three mypy findings (`provenance.py:66`, `attempts.py:34`: cast instead of
  returning `Any`; `ledger.py:152`: removed a redundant cast).
- Ran `ruff format` on all D files, none of which had been formatted before.

Final validation (Windows, Python 3.13.9):

| Check | Result |
|---|---|
| Full suite | **311 passed, 0 failed** |
| Ruff check | Passed |
| Ruff format --check | Passed |
| Mypy strict | Passed, 34 source files |
| Coverage | 87.25% (floor 75%) |

### E execution plan — recorded before implementation

The user asked to scope E first and then authorized implementation; the
outcome and the deviations from this plan are in "E handoff evidence" below.
The plan text is kept as it was agreed. Scope: local
ingestion-window and checkpoint logic only. No publication, live pilot start,
or change to `ingest.yml`'s/`forecast.yml`'s cron schedules is part of this
increment. Grounded in a read of the current code, cited below file:line.

1. **Stop treating "now" as a publication ceiling.** `pipeline.ingest()`
   (`pipeline.py:133-134`) defaults `end = now()`; `energy_charts._window()`
   (`energy_charts.py:273-275`) then clips every fetch to `ts_utc < end`.
   Because a day-ahead auction publishes a full calendar day's 24 delivery
   hours in one batch roughly a day ahead of physical delivery, this silently
   truncates the *current* day's own already-published curve at whatever
   wall-clock hour the job happens to run — the exact "already-cleared prices
   for later delivery on the issue day are dropped" finding. Replace the hard
   `now()` ceiling with a small forward horizon (for example `now() + 2 days`)
   so a run at any hour can pick up a day-ahead result that has already
   cleared, and let the provider's own response decide how much of that
   window actually has data. Before relying on this, confirm with a
   live/recorded fixture that Energy-Charts returns fewer rows — not an
   error — when asked for delivery hours it has not published yet, and check
   that `SmardSource`/`OnsSource` (very different publication cadences, per
   `sources/smard.py`/`sources/ons.py`) tolerate the same forward request
   harmlessly, since `pipeline.ingest` is zone/dataset-agnostic.
2. **Turn `store.coverage()` into the ingestion checkpoint.** `store.py` has
   no persisted watermark today — `read()`/`available_months()` only glob
   directory contents, and `coverage()` derives `first_ts_utc`/`last_ts_utc`
   by scanning the Parquet files themselves. Add a small helper (for example
   `store.last_ingested(dataset, zone)`) built on that existing scan, and
   change `pipeline.ingest()`'s default `start` to `last_ingested -
   overlap_buffer` (a few days, to absorb late revisions) when prior data
   exists, falling back to today's fixed `lookback_days` only on a cold start
   (empty store). This lets a resumed run catch up an arbitrarily large gap
   instead of being capped at `lookback_days`, without adding a second,
   driftable source of truth for "what we already have."
3. **Apply the same checkpoint to the isolated forecast store.**
   `forecast.yml` reseeds `$GPA_DATA_ROOT` from the monthly `data/curated`
   snapshot every run and then always asks for a fixed `FORECAST_LOOKBACK=7`
   days (lines 29-32, 63-69), so a gap larger than 7 days between the last
   `ingest.yml` monthly commit and today is never recovered. Once item 2
   lands, drop the fixed `--days` flag from this step (or keep it only as an
   explicit safety floor) so the same coverage-derived catch-up applies here.
4. **Publication-vs-retrieval distinction stays local, not invented.**
   Energy-Charts' `/price` payload carries no publication timestamp
   (`energy_charts.py:123-124` only reads `unix_seconds`/`price`), so E
   cannot fabricate a true provider publication time. Keep doing what D's
   provenance layer already does honestly — record the local retrieval
   instant as `observed_at`/`input_as_of` and label it
   `provider_publication_times: "unknown"` — and do not add a fabricated
   vintage field to `store.py`'s schema. A provider later found to expose
   real publication metadata is new scope, not a retrofit of this step.
5. **Keep the panel's completeness guard; let items 1-3 make it pass.**
   `panel.py`'s `_day_hours(zone)` gate on the D-1/D-7 daily aggregates
   (lines 357-370, 441-451) already correctly excludes a partially-ingested
   day from `price_d1_mean` etc. rather than silently averaging a truncated
   day; that safeguard is correct and must not be loosened. Items 1-3 remove
   the reason D-1 would ever be partially ingested at issuance time in the
   first place. Add a regression test that ingests and issues on the same run
   and asserts D-1's daily aggregate features are present, which today's
   architecture cannot pass.
6. **Tests and verification.** Cover: computing `start` from
   `store.coverage()` with prior data vs. a cold start; a simulated multi-month
   gap caught up in one call with no `--days` override; the forward horizon in
   item 1 staying bounded rather than requesting an unbounded future window;
   and an end-to-end `gpa ingest` → `gpa issue` fixture showing D-1's price
   curve is no longer truncated at the run's wall-clock hour. Update
   `tests/test_pipeline.py`, `tests/test_store.py` and `tests/test_cli.py`;
   run the full validation command list before calling E done.

**Open questions to resolve while implementing, not guessed at here:** whether
Energy-Charts truly returns a short result (vs. an error) for undelivered
future hours; the safe forward-horizon size that does not risk provider
errors or rate limits; and whether ONS's yearly-file publication model needs
its own overlap/horizon constants rather than sharing DE-LU's defaults.

**Acceptance:** `gpa ingest` run at any hour no longer truncates the current
day's own already-published price curve; a checkpoint derived from
`store.coverage()` lets a scheduled run recover from an arbitrarily long gap
without a manual `gpa backfill`; the isolated forecast store in `forecast.yml`
inherits the same catch-up instead of its own fixed 7-day window; and the
publication-timestamp gap stays honestly labelled "unknown" rather than
invented. Integration tests cover a missed-run gap, a same-day truncation
scenario and the existing D-1/D-7 completeness guard.

### E handoff evidence — done, committed as `55e7543`

Changed files: `src/gpa/pipeline.py`, `src/gpa/store.py`, `src/gpa/cli.py`
(`gpa ingest` help and `--days` semantics), `.github/workflows/ingest.yml`
and `forecast.yml` (comments and input description only; schedules and
commands unchanged), `tests/test_pipeline.py`, `tests/test_store.py`,
`tests/test_cli.py`. No dependency, schema or source-adapter change.

What was built:

- **Checkpoint.** `store.last_ingested(dataset, zone)` returns the latest
  stored `ts_utc` for one zone, read from its partitions (interrupted
  `.gpa-*.tmp` writes are ignored). `pipeline.resolve_window` derives each
  target's window from it; `pipeline.ingest` resolves the window per zone and
  dataset and reports it on every result line, e.g. `[2026-05-21 08:00 to
  2026-06-30 08:00 UTC]`.
- **Publication horizon.** `pipeline.PUBLISHED_AHEAD_DAYS = {"price": 2}`:
  prices are requested to now + 2 days; load and generation still stop at now.
  `energy_charts._window()` is unchanged — it now clips at the later ceiling.
- **Workflows.** Both keep `--days 7`, which now means revision overlap. The
  monthly `ingest.yml` run resumes each series from its committed checkpoint;
  `forecast.yml`'s isolated store catches up from its monthly seed's checkpoint.

Deviations from the plan, each deliberate:

- Item 2 said `start = last_ingested - overlap`. Implemented as
  `min(ceiling, last_ingested) - lookback_days`, because after the D-1 auction
  the price checkpoint is tomorrow; counting back from it would shrink the
  revision overlap behind now. `ceiling` is now, or an explicit `end`.
- Item 3 said drop `--days` or keep it as a floor. It is kept, redefined as the
  overlap behind the checkpoint; nothing else needed to change in the workflow.
- The plan's "persist the isolated forecasting history" is not a new cache:
  with checkpoint catch-up the monthly seed is always brought current, and D's
  per-issue snapshots already preserve the exact inputs each issue used.

Open questions, resolved:

- **Energy-Charts on unpublished hours.** Checked live, read-only, at
  2026-09-15 00:54 UTC. Price with `end=now` returned 192 quarter-hours ending
  00:45 UTC although the whole 15 September delivery day was already public;
  `now+2d` and `now+4d` both returned 276 rows ending 21:45 UTC (end of the
  local day) with no error. Load requested two days ahead returned only
  measured rows (last 23:30 UTC on 14 September).
- **Horizon size.** Two days covers the next local delivery day at any run hour;
  a longer request returned nothing more.
- **ONS/SMARD cadence.** Moot: the horizon applies only to `price`, which no
  ONS zone declares, and SMARD is not a pipeline zone source.
- **Committed store.** `data/curated` has no internal price gaps, but DE-LU
  price ends 2026-09-13 14:00 UTC — the last run's clock — although the rest of
  13 September and all of 14 September were already published.

Verification:

| Check | Result |
|---|---|
| New regressions | Checkpoint (empty store, 90-day gap in one call, recent checkpoint overlap, price checkpoint ahead of now), horizon only for prices, explicit bounds bypass, negative lookback, `last_ingested` across partitions and interrupted writes |
| Same-morning panel | At 10:00 Berlin on D-1, D's `price_d1`, `price_d1_mean`, `price_d1_end` are all present; negative control with `end=now` leaves `price_d1_mean` null on all 24 hours |
| End-to-end CLI | `gpa ingest --dataset price` then `gpa issue` on the same morning: 24 forecasts, 0 abstentions. Re-run with the horizon disabled (scratch store, not a committed test): 24 abstentions — the pre-E daily workflow could not issue |
| Full suite | **325 passed, 0 failed** |
| Ruff check / format, mypy strict | Passed; 34 source files |
| Coverage | 87.32% (pipeline 98%, store 97%; floor 75%) |

Limits and follow-ups:

- The checkpoint is the latest stored instant. A hole older than the overlap
  behind it is not detected; `gpa backfill` or the existing structural-coverage
  audit in `ingest.yml` remain the repair path.
- **For F.** The export's `data_as_of` is the maximum `last_ts_utc` across all
  datasets, so after the next ingest it will show the next delivery day's
  prices as the "as of" date. Label price coverage as "prices through delivery
  day" or compute `data_as_of` from measured datasets.

### Snapshot-size follow-up — done, committed as `bb33212`

Found while testing E, fixed the same session, before any push. `gpa issue`
was snapshotting the zone's full stored price/load/generation history and the
package's source code as plain per-issue files. Measured on real DE-LU
history in a scratch git repository (not this one): about 4.6 MB per issue,
projecting to roughly 1.6 GB of working-tree growth a year from
`forecast.yml`'s daily commit — confirmed by simulating 30 real daily issues
end to end, each verified through `read_snapshot`.

The fix has two parts, both in `provenance.py`: large artifacts (source
frames, the input panel, the source code) are now split by month (or by
filename for code) and each part is written once to `root/blobs/<sha256>`,
shared across every issue instead of copied into each one; and generation is
filtered to `panel.RESIDUAL_LOAD_FUELS` (`wind`, `solar` — the only fuels
`hourly_residual_load` reads) before it is stored, with an explicit check
(comparing residual-load features computed from the full and the reduced
generation, when load is also supplied) that fails the issue rather than
archiving quietly if a future change ever makes that filter incomplete. Only
`issued.parquet` and a small manifest remain per issue.

Re-running the same 30-day simulation with the fix: **82 MB projected per
year, about 20x smaller**, with every one of the 30 issues independently
re-verified through `read_snapshot` (blob checksums, the code hash and the
input fingerprint). Full suite 328 passed; ruff check/format and mypy strict
clean; coverage 87.45%. No production data or git history was touched by
either simulation — both ran in a scratch directory under a temporary git
repository, deleted afterwards.

### Session close — 15 September 2026 (fourth checkpoint, E and snapshot-size fix done)

The user asked for the snapshot-size problem to be thought through, then to
proceed. Both are committed; no push, committed ingestion, public export or
prospective issuance occurred.

To resume safely in a new session:

1. Open this roadmap in `C:\Users\Pedro\Desktop\Python\global-power-atlas` and
   run `git log --oneline -9` / `git status --short`. Expect a clean tree with
   `bb33212` (snapshot size), `04dc377` (E docs), `55e7543` (E), `ceaeac6` and
   `7e94fb3` (docs), `c594b75`, `f2bb495` and `c893fa7` on `main`, all unpushed.
2. Start F: frozen release and presentation, including the `data_as_of` label
   noted above. Write F's execution plan into this file before editing code,
   the same way D's and E's plans were recorded before those increments.
3. Then G. Keep `.gpa/battery-studies/`; do not clean or overwrite it. Do not
   start a prospective pilot before F's frozen release exists.

Suggested resume request:

> Leia `docs/portfolio-roadmap.md`. A/B/C/D/E e a correção do tamanho dos
> snapshots estão commitados. Registre o plano do item F antes de alterar
> código, depois implemente. Não publique no GitHub sem eu pedir.

### F execution plan — recorded before implementation

The user asked to proceed straight to F. Grounded in a read of the current
export/site code, cited below file:line. Scope: freeze one research release
and correct the presentation issues the baseline findings named; no GitHub
repository-settings change (About/topics) is part of this — nothing has been
pushed, so there is no live repository metadata to edit yet.

1. **The forecast comparison is not actually frozen.** `export_all()`
   (`export.py:103-118`) calls `snapshot.read()` and only falls back to a live
   `harness.published()` recompute when nothing is saved — but `data/experiments/`
   does not exist yet, so every export today silently live-recomputes. As the
   curated store keeps growing via the monthly `ingest.yml` run, that live
   recompute's training/test window grows with it, so the published MAE/capture
   numbers would drift on their own with no reviewed decision to change them.
   Generate one snapshot now with `gpa backtest --zone DE-LU --save-snapshot`
   (its defaults already match `harness.published()`'s: `min_train_days=0`
   falls back to `MIN_TRAIN_DAYS=270`, `validation_days=0` to
   `VALIDATION_DAYS=90`, `tune_lightgbm=True`, `lightgbm_refit_days=1`), commit
   `data/experiments/<id>/` and `current.json`, and change `export_all()` to
   raise a clear, actionable error when no snapshot exists instead of silently
   falling back — a live recompute is exactly the "fixed evaluation end is not
   a frozen experiment" finding.
2. **Battery numbers need no separate freeze mechanism.** `_battery_tables()`
   (`export.py:350-365`) calls bare `battery.backtest_predictions` with only
   two comparators and no costs; it never imports `battery_study.py` at all,
   so the corrected economic-comparison layer from C has not reached the site.
   `battery_study.evaluate()` is a pure, deterministic function of whatever
   `forecast_predictions` it is given, so once that table comes from the frozen
   snapshot in item 1, calling `evaluate()` instead of `backtest_predictions`
   freezes the battery numbers too, for free. Export its richer tables
   (`risk`, `comparisons`, `coverage`) alongside `dispatch`/`summary`, and add
   the two illustrative non-zero cost scenarios already used in the local
   studies (2/3 and 5/10 EUR per grid MWh) as a small additional table, so the
   site can report gross and after-cost margin separately per P1's acceptance.
   `.gpa/battery-studies/` stays exactly what it already is (a local, gitignored
   research tool) — the site never reads it.
3. **`data_as_of` will mislabel a future date after E.** `_overview()`
   (`export.py:391,397-398`) takes `coverage["last_ts_utc"].max()` across every
   dataset. Since E's `PUBLISHED_AHEAD_DAYS` now lets price ingest up to two
   days past "now", that maximum will usually be a price timestamp for a
   delivery day that has not happened yet, which reads as "as of the future" on
   `site/index.md:27`. Restrict the top-level `data_as_of` to measured datasets
   (`load`, `generation`) — the per-zone `datasets.price.last` already shown in
   the same JSON is unaffected and still shows the forward price coverage
   separately. Update `tests/test_export.py::test_overview_depends_on_observations_not_export_clock`,
   which currently asserts `data_as_of` from a price-only fixture.
4. **The trailing partial load day is plotted as if it were whole.**
   `load_metrics.daily_energy` already reports `hours_observed`
   (`metrics/load.py:65-66`); `_daily_load()` (`export.py:290-294`) passes it
   through unfiltered and `site/index.md`'s "Demand history" chart (`:51`)
   plots every row with no reference to it. Drop a trailing day from
   `daily_load` whose `hours_observed` is below `calendar.hours_in_local_day`
   for that date (a public helper that already handles DST) — a full day
   already past keeps its DST-short 23 or 25 hours; only a day still in
   progress is trimmed. `daily_prices` can show the analogous artifact now that
   price ingestion also runs ahead of "now" for a partially-published day; left
   for a later pass rather than expanding this one, since it was not named in
   the baseline findings and needs threading an interval count through
   `price_metrics.block_prices` that does not exist yet.
5. **The battery dispatch chart mixes EUR/MWh, MWh state and MWh flow on one
   axis.** `site/battery.md`'s "Dispatch example" plot (`:77-89`) puts price,
   state of charge and the dispatch action on one `y: {label: "MW / MWh"}`
   axis. Split it into two stacked plots sharing the same x-axis: price alone
   (EUR/MWh), and SOC (MWh, line) with the dispatch action (MWh, bars) on a
   second axis below it.
6. **The README states the stale, uncorrected numbers.** `README.md:36-38`
   quotes the 369-day, pre-A/B/C/D/E `battery_summary.parquet` figures
   (confirmed against the currently committed file: `capture_vs_perfect`
   0.945059/0.942286 match exactly). Once the frozen release exists, replace
   these with the actual frozen numbers — do not hand-adjust the old figures.
7. **Verification.** After 1-3 land, run `gpa export` for real (not `--check`)
   to regenerate `site/data/*` from the frozen snapshot, then `gpa export
   --check` to confirm it is now reproducible from that snapshot rather than
   from the live store. Add tests for: `export_all` raising when no snapshot
   exists; the split `data_as_of`; the trimmed trailing load day (including a
   legitimate short DST day *not* being trimmed); and that the exported battery
   tables match `battery_study.evaluate()` bit-for-bit. Run the full validation
   command list, and `npm run build` if the site's dependencies are already
   installed locally, before calling F done.

**Not in this increment:** GitHub repository About/topics (no live repository
yet); P2's fundamentals ablation; P3's scenario study; P4's recruiting package.
Those remain later roadmap items.

### F handoff evidence — done, not yet committed

The plan above and this evidence were both recorded in one continuous session
rather than as two separate commits (unlike D and E); the same review
discipline still applied, item by item, before code changed.

Changed files: `src/gpa/export.py` (snapshot handling and battery tables
rewritten, `_drop_incomplete_trailing_day` and the split `data_as_of` added),
`tests/test_export.py` (rewritten), `site/battery.md` (dispatch chart split),
`README.md` (Featured result replaced with the frozen numbers), all of
`site/data/*` (regenerated from the frozen snapshot) and the new
`data/experiments/5e3c9f1a256ec73c62e2/` plus `data/experiments/current.json`.
No dependency change.

Implemented, item by item:

1. **Frozen forecast, no live fallback.** The `data/experiments/` snapshot
   named in the plan already existed on disk (generated, uncommitted, before
   this session) via `gpa backtest --zone DE-LU --save-snapshot`; its
   `run.json` confirms the intended configuration (`alpha: 0.1`,
   `min_train_days: 270`, `validation_days: 90`, `lightgbm_refit_days: 1`,
   374 test days, `best_mae: 21.03558956221899`). `export_all()` no longer
   calls `harness.published()`: when `snapshot.read()` returns `None` it now
   raises a `RuntimeError` naming the exact command to recover, tested by
   `test_export_all_raises_without_a_frozen_snapshot`. The now-unreachable
   `_forecast_tables`/`_forecast_runs` live-fallback helpers were deleted
   rather than left dead, along with the `Sequence`/`TYPE_CHECKING` imports
   they alone needed.
2. **Battery tables from `battery_study.evaluate`.** `_battery_tables()` now
   calls `evaluate()` instead of the bare `battery.backtest_predictions`,
   exporting `battery_risk`, `battery_comparisons` and `battery_coverage`
   alongside `battery_dispatch`/`battery_summary`, plus a new small
   `battery_costs` table (strategy, duration, profit, cost pair) covering the
   zero-cost base case and the two illustrative 2/3 and 5/10 EUR/MWh
   scenarios from C, each a full rerun of dispatch optimization since costs
   can change the chosen schedule. Model set (`ridge`, `lightgbm`,
   `naive_previous_week`) and durations (1, 4 MWh) are unchanged from the
   previous export, so the site's existing chart/table contracts still match.
   `test_battery_tables_match_battery_study_evaluate_bit_for_bit` and
   `test_battery_costs_table_covers_the_illustrative_scenarios` cover this.
   **Deviation:** `evaluate()` does not expose the previous bare call's
   `horizon_steps=24` compatibility guard. That guard only ever re-asserted
   these are ordinary 24-hour DE-LU days; dispatch already requires every
   interval of a day to be present regardless, so nothing is unchecked, only
   one redundant assertion is gone.
   No new site page was built to *display* `battery_risk`/`comparisons`/
   `costs` yet — they are exported and tested, not yet wired into
   `site/battery.md`'s charts. That UI work is P1 scope, not named in F's
   seven items, and is left for a later pass, the same way item 4 explicitly
   deferred the analogous `daily_prices` trimming.
3. **`data_as_of` restricted to measured datasets.** Implemented as planned;
   `_MEASURED_DATASETS = ("load", "generation")`.
   `test_overview_depends_on_observations_not_export_clock` was rewritten: a
   price-only store now asserts `data_as_of is None`, then a second write of
   load data asserts `data_as_of` tracks it exactly, so both directions
   (price excluded, measured data included) are covered instead of only the
   original price-only assertion.
4. **Trailing partial load day trimmed.** Implemented as planned via
   `_drop_incomplete_trailing_day`, applied in `_daily_load()`. Three new
   tests cover a day-in-progress being dropped, a genuine 23-hour DE-LU
   spring-forward day (2026-03-29) being kept, that same DST day still being
   dropped if it is itself only partially observed, and an end-to-end
   `_daily_load()` check against a real store with a partial trailing day.
5. **Dispatch chart split.** `site/battery.md`'s single mixed-unit plot is now
   two stacked `Plot.plot` cells sharing the sample-day data already computed
   in the page's top cell: price alone (EUR/MWh, no legend needed for one
   series) above, state of charge and dispatch action (both MWh) with a
   two-entry legend below.
6. **README numbers replaced.** The "Featured result" section now cites the
   frozen run's actual MAE (EUR 21.04/MWh, 27.0% skill over the best naive,
   previous day, EUR 28.80/MWh), the 368-day common battery sample (down from
   the previously stated 369, since a repeated autumn DST hour cannot be
   attributed to one physical instance — the same reason C's own local studies
   already used 368), Ridge/LightGBM capture (94.5%/94.2%, both round to the
   same headline figures the old, uncorrected numbers happened to show) and
   the incremental margin over the strongest fixed comparator in this sample
   (same-hour last week), about EUR 7,025/MW, read from the regenerated
   `battery_risk`/`battery_comparisons` tables rather than hand-adjusted.
7. **Verification.** `gpa export` (real, not `--check`) regenerated all of
   `site/data/*` from the frozen snapshot; `gpa export --check` immediately
   after passed, confirming the export is now reproducible from the committed
   snapshot rather than a live recompute. `npm run build` and the full
   `npm run test:browser` smoke suite (fresh preview server, both viewports)
   passed with zero console/page errors, zero layout overflow and a visible
   chart on every data page, battery included.

**Found, not fixed — pre-existing, unrelated to F:** while verifying the
battery page, the "Cumulative net value" chart (`site/battery.md`'s second
chart, above "Dispatch example") renders empty — no error, no thrown
exception, the surrounding legend/axes never appear — with real DE-LU data.
This was isolated by temporarily reverting to the exact HEAD-committed
`battery.md` and HEAD-committed `site/data/battery_dispatch.parquet` (via
`git stash`, then rebuilding and serving `dist/` fresh with no dev server
involved) and reproducing the same empty chart: **the defect predates this
session and is unrelated to the dispatch-chart split or the new frozen
snapshot.** `npm run test:browser` does not catch it because it only asserts
at least one visible chart per page and battery already has one. Left for a
separate increment rather than expanding F's scope; worth a focused
JS-reactivity investigation before P1's UI work touches this page again.

Final validation (Windows, Python 3.13.9):

| Check | Result |
|---|---|
| Full suite | **334 passed, 0 failed** |
| Ruff check / format | Passed; 53 source/test files |
| Mypy strict | Passed; 34 source files |
| Coverage | 88% (floor 75%); `export.py` 95% |
| `gpa export` then `gpa export --check` | Regenerated, then confirmed reproducible from the frozen snapshot |
| `npm run build` | 4 pages rendered, 4 links validated |
| `npm run test:browser` | Both viewports, all 4 routes, zero errors/overflow |

### Session close — 15 September 2026 (fifth checkpoint, F done)

The user asked to continue the work; F was already half-implemented and
uncommitted from an interrupted prior session (the split `data_as_of`, the
trailing-day trim and a generated-but-unwired `data/experiments/` snapshot).
This session finished the remaining F plan items, added the regression tests
the prior session's imports implied but never wrote, verified end to end and
committed. No push, no GitHub publication, no prospective issuance occurred.

To resume safely in a new session:

1. Open this roadmap in `C:\Users\Pedro\Desktop\Python\global-power-atlas` and
   run `git log --oneline -11` / `git status --short`. Expect a clean tree with
   `0927be6` (F) on top of `ba6742f` (snapshot-size docs), `bb33212`
   (snapshot size), `55e7543` (E) and the earlier D commits on `main`, all
   unpushed.
2. Start G: remaining P1 sensitivities (calibrated costs, availability/error
   stresses, model-selection/evaluation separation, a qualified duration
   recommendation). Write G's execution plan into this file before editing
   code, the same discipline D, E and F followed.
3. Before G touches `site/battery.md` again, budget time to investigate the
   "Cumulative net value" chart defect F found and left unfixed (see F's
   handoff evidence above): it renders empty with no thrown error on real
   DE-LU data, reproduces on the pristine HEAD-committed site from before this
   session, and was only isolated by bisecting with `git stash` plus a fresh
   `npm run build` served outside the dev preview. Keep `.gpa/battery-studies/`
   untouched. Do not start a prospective pilot before a public deploy exists.

Suggested resume request:

> Leia `docs/portfolio-roadmap.md`. A/B/C/D/E/F estão commitados localmente.
> Registre o plano do item G antes de alterar código, depois implemente. Não
> publique no GitHub sem eu pedir.

**First implementation increment:** make the battery accounting trustworthy and
build the P1 economic-comparison layer on that corrected engine. This increment
does not declare all of P0 complete or begin the prospective pilot. Corrected
research outputs must be frozen and reviewed before replacing public headlines.

| Work item | Planned files / scope | Acceptance | Status |
|---|---|---|---|
| A — Dispatch correctness (P0) | `src/gpa/battery.py`, `tests/test_battery.py` | Fractional cycle budget, finite inputs, initial/terminal SOC, unique chronological intervals, 23/25-hour UTC days, duration propagation, rejection of ambiguous DST clock-hour input | Done locally; regression and exhaustive small-schedule tests passed |
| B — Comparable strategies (P1) | Battery backtest adapter and tests | All five existing forecast models plus no trade and perfect foresight; 1/2/4 MWh; exactly the same complete settled days for every strategy; consistent actuals; no-trade keeps initial SOC | Done locally; common-sample and coverage tests passed |
| C — Economic evidence (P1) | `src/gpa/battery_study.py`, tests and a local `battery-study` CLI | Daily margin, cost accounting, incremental value versus each fixed naive, downside/concentration, deterministic paired calendar-block bootstrap; costs explicitly labelled assumptions | Done locally; three historical studies saved and zero-cost study replayed |
| D — Issuance provenance (P0) | `forecast/ledger.py`, `forecast/provenance.py`, `forecast/attempts.py`, `cli.py`, workflow/tests | Target-day feature hash, model parameters/version, input snapshot, late/failure/abstention policy, canonical issuance | **Done and committed** locally (`c893fa7`, `f2bb495`, `c594b75`); not pushed |
| E — Input availability/history (P0) | Pipeline, sources, ingest/forecast workflows and tests | Full already-published curve, no unavailable targets, checkpoint catch-up and isolated persistent history | **Done and committed** locally (`55e7543`, snapshot-size fix `bb33212`); not pushed |
| F — Frozen release and presentation (P0/P1) | Snapshot/export, existing site pages, README/tests | Reproducible corrected release; honest date/coverage/cost labels; separate units; concise commercial summary | **Done and committed** locally (`0927be6`); not pushed |
| G — Remaining P1 sensitivities | Analysis/configuration/tests | Sourced/calibrated cost assumptions, availability/error stresses, model-selection/evaluation separation and a qualified duration recommendation | Bounded scope **done and committed** locally (`30292c9`; all five models, cost/efficiency/signal/downtime stresses, per-duration comparators); asset-specific cost calibration and investment valuation remain open |

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

**Next action — D, E, F and the bounded G sensitivity study are done.** D's
provenance core, E's checkpoint catch-up and publication horizon, F's frozen
export/site presentation and G's bounded sensitivity study are all committed
(see their handoff evidence above); the "Cumulative net value" chart defect F
found is confirmed fixed by the browser smoke suite. Resume at the English
executive case per "Portfolio direction adopted from the review" near the top
of this file, writing its plan into this file first, the same discipline D,
E, F and G followed.

The current C snapshots
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

## Historical plan of record — superseded numbers below

**Everything from here to the end of this document is the original plan, written
on 14 September 2026 before implementation began. Its figures are superseded and
must not be quoted.** They are kept because the handoff entries above refer back
to them, and because the gap between what was planned and what was built is part
of the record.

Specifically: the "Baseline findings" section below quotes MAE 21.04 EUR/MWh on
8,971 scored cells, a 369-day battery sample and EUR 7,024 incremental margin.
The frozen release now reports **22.07 EUR/MWh on 58,645 cells, a 2,404-day
sample and EUR 29,014/MW incremental** (about EUR 4,400/MW per year). The
execution checkpoint at the top of this document is the current state.

P3's text below also describes a study that was deliberately **not executed as
written**: the Marktstammdatenregister ingestion, the 2027-2030 scenario paths and
the CAPEX/OPEX asset-screening sheet were dropped during planning in favour of a
market-mechanism study built on `capture_rate` and Energy-Charts'
`/installed_power`. The reasoning and the delivered scope are recorded in the P3
plan and handoff entries above.

P8 further supersedes the framing: the central question is no longer "does a
validated forecast create battery value" but the market claim that solar removed
Germany's peak premium while widening the within-day range storage is paid for,
with the forecast and battery studies serving as its evidence.

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

# Roadmap

The one planning document for this repository: where the project stands, the
decisions that are settled, and what is still open. `README.md` is the public
front door. The history of how each piece was built — plans, validation
evidence, deviations — lives in the git log and its commit messages, not here.
Keep this file current in place; do not grow it back into a log, and do not
add parallel STATE/TODO/handoff files.

Updated 26 September 2026.

## Where it stands

- **The product** is a market research note on the German-Luxembourg (DE-LU)
  day-ahead market, published at <https://pedrods20.github.io/german-power-research/>.
  Its thesis: solar removed the peak premium (on-peak minus off-peak went from
  +10.6 to −13.7 EUR/MWh, 2019 → 2026 YTD) while the within-day range a battery
  is paid for widened from 80% to 169% of baseload. The forecast and battery
  studies are its evidence, not the product.
- **The retrospective release** is `data/experiments/b0e69bf47f6e230be9b5`: Ridge
  at the 12:00 D-1 gate cuts MAE 24.3% against the best naive, worth about
  EUR 4,400/MW/year on a 1 MW / 4 MWh battery, roughly 5% of its gross margin.
  `gpa export --check` proves the site matches it.
- **The prospective ledger** issued its first forecast on 23 September 2026
  (`ridge`, delivery 24 September, 24/24 hours before the gate). Since 24
  September every run has been green: the 23:17Z slot, delivered around 01:30Z,
  issues `ridge` and the three naive comparators, and the later slots close as
  `already_issued`. `ridge_da` has abstained at every pre-gate run. The forecast
  page carries a counter built from the committed attempt records.
- **Scope** is one market from one provider: DE-LU from Energy-Charts. France,
  Spain and Brazil, the ONS and SMARD adapters, and the unused metrics and
  exports were removed on 23 September 2026 because nothing in the study used
  them.
- **The code** was cut by a third on 26 September 2026 (P13): the ridge refit is
  one batched NumPy solve, MLflow tracking and unpublished CLI diagnostics are
  gone, and every published output was proven unchanged against a baseline run
  of the previous code. The one fix, averaging the repeated autumn clock hour of
  the fundamentals as the price target does, made the ablation deterministic.

## Settled decisions

Do not relitigate these without new evidence.

1. **Audience and framing.** The portfolio exists to show European power-trading
   and generation employers market judgment. Prefer analysis a desk analyst
   recognises as their own — price formation, cannibalisation, spreads, capture
   rates, arbitrage durability — over heavier data engineering or
   project-finance appraisal.
2. **Forecast gate and information set.** 12:00 market time on D-1. Lagged prices,
   calendar features and residual load lagged at least two delivery days.
   Realised delivery-day fundamentals never enter a forecast.
3. **Frozen release.** Published numbers come from a committed, content-addressed
   snapshot, never a live recompute. A new release is an explicit decision, and
   retrospective results are never presented as prospective ones.
4. **Fundamentals.** Day-ahead load/wind/solar forecasts are a labelled ablation
   on an assigned noon vintage (Ridge 22.07 → 19.17 EUR/MWh), not part of the
   published information set. Prospectively they define the separate `ridge_da`
   arm, which abstains on the record when the provider has not published the
   delivery day.
5. **Prospective protocol.** Separate frozen identities per information set; the
   canonical issue is the earliest complete, verified pre-gate one per model and
   delivery day. A later run that finds it records `already_issued` and succeeds;
   a late run with nothing on record fails. Slots at 23:17, 02:17 and 06:47 UTC,
   placed by the ~5.5-hour delay GitHub actually delivers. Nothing prospective is
   claimed before the pilot bar below is met.
6. **Battery.** 1 MW at 1/2/4 MWh, 90% round trip, one episode per day in the
   headline with a two-episode sensitivity, zero costs in the base case with
   illustrative cost stresses. Day-ahead arbitrage is stated as a lower bound;
   intraday and balancing (FCR/aFRR/mFRR) are out of scope.
7. **Boundaries.** Zonal, not nodal. No new markets, live trading, chatbots or
   unrelated models.
8. **Publishing.** A change is published when the deployed page contains it:
   grep the live HTML for the new text. Run `npm run test:browser`, the gate CI
   runs, not only `npm run build`.

## Open items, in order

1. **When does Energy-Charts publish the next day's fundamentals?** The
   scheduled `probe.yml` logs coverage and distance from the gate to the `probe-log`
   branch. Regulation (EU) 543/2013 only requires day-ahead wind and solar by
   18:00 on D-1, after the gate, and `ridge_da` found nothing at 05:45 and 09:49
   CEST. GitHub fires the probe every three to five hours rather than hourly, so
   the log brackets the publication time rather than pinning it. After one to
   two weeks of log: if the series appears after noon, reframe
   the ablation as an upper bound and retire or re-source `ridge_da`; if before,
   move a slot to catch it.
2. **Snapshot growth.** The first issuing run wrote 576 content-addressed blobs
   (18.9 MB); four issuing days later the ledger holds 22 MB, so a day within a
   month costs about 1 MB. The first October run opens a new month partition:
   check its size then, and redesign storage only if it repeats the 18.9 MB.
3. **Pilot.** Six weeks with at least 95% of delivery days issued on time, then a
   prospective summary against the naive comparators, labelled as such.
4. **Fundamentals adoption.** Decide after item 1, on the ledger's evidence.
5. **Home-page commercial comparison.** Deferred: the candidate sources were
   paywalled or unverifiable. Reopen only with a citable source.
6. **GitHub About, topics and author links.** The repository was renamed from
   `global-power-atlas` to `german-power-research` on 26 September 2026; GitHub
   redirects the old repository URL, but not the old Pages URL. Manual, in the
   About panel: website <https://pedrods20.github.io/german-power-research/>;
   description "Solar has turned Germany's peak premium into a discount
   and doubled the within-day spread. Independent research on the DE-LU day-ahead
   market: price formation, a gate-time forecast on a public ledger, and the value
   of flexibility." Topics, in this order: `power-markets` `electricity-market`
   `day-ahead-market` `germany` `energy-transition` `solar-cannibalisation`
   `electricity-price-forecasting` `battery-storage` `bess` `energy-analytics`
   `reproducible-research` `python` `polars` `observable-framework`. Hide
   Releases and Packages; switch off Wiki and Projects. The LinkedIn link and a
   short bio have reserved slots marked `ABOUT-ME` and `LINKEDIN` in `README.md`,
   `site/index.md` and `observablehq.config.js`.

From 25 October the gate moves to 11:00 UTC under CET, which adds an hour of
margin to every slot.

## Increments

| Increment | What it delivered | Commit |
|---|---|---|
| Foundation | Pipeline, store, static site; scope narrowed to verifiable zones | 7db61d9 … 36c228e |
| A–E | Forecast/battery focus, auditable ledger, checkpointed ingestion | 1cff7d0 … bb33212 |
| F, G | Frozen export; five-forecast comparison and battery stresses | 0927be6, 30292c9 |
| Executive case | Decision-oriented home page | 454793c |
| History | DE-LU from 2019, release re-frozen | 544d961, b53715f |
| Regimes, fundamentals | Market-regime narrative; fundamentals ablation, not adopted | edb79d8 … 649c052 |
| P3 | Capacity, cannibalisation and storage-durability study | 15ed4e3 … 1a204a8 |
| P4, P5 | Packaging for recruiting; executive editorial pass | 91bb127 … 39e82ba |
| P6, P7 | Prospective issue path; separable ledger arms | d256371, 1cce26c |
| P8–P10 | Repositioned as a research note; adversarial reviews and fixes | 514dbf6 … cec5833 |
| P11 | First prospective issue; honest backstop, naive arms, publication probe | 271a2dc |
| P12 | Removed unused zones, adapters, metrics, exports, dependencies and history | c371879 |
| P13 | Code review: −36% source lines, NumPy ridge, deterministic fundamentals on the autumn hour | 9d7d769 |

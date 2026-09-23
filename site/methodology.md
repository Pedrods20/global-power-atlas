---
title: Methodology
---

# Methodology

This is a static research artifact. Source observations are validated and
aggregated before they reach the browser; no page fetches a provider directly
and no browser credential is required.

## Scope

The historical context covers four zones. Forecasting and battery valuation are
focused on Germany-Luxembourg (DE-LU), where the project has its deepest price,
load and generation history — roughly seven and a half years against about two
years for the context zones. That asymmetry is deliberate: DE-LU is the only
market here with enough history to support a walk-forward benchmark, and the
other zones are shown as context rather than modelled.

| Zone | Data | Provider | Committed coverage | Use |
|---|---|---|---|---|
| DE-LU | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | From 2018-12-31, 94 monthly partitions | Forecast reference market |
| DE-LU | Day-ahead load/wind/solar forecasts | [Energy-Charts](https://www.energy-charts.info/) | From 2019-01-05 | Labelled ablation and the prospective `ridge_da` arm |
| France | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | From 2024-09-01 | Historical comparison |
| Spain | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | From 2024-09-01 | Historical comparison |
| Brazil (SIN) | Load, generation | [ONS](https://www.ons.org.br/) | From 2024-09-01 | System comparison |

The dashboard refreshes monthly. Its purpose is historical context around the
forecast study, not continuous market monitoring.

## Two spreads, and why both are reported

The [market view](./) turns on a distinction that is easy to lose:

- The **block spread** is the mean of on-peak intervals minus the mean of
  off-peak intervals, where membership follows the market's own block definition
  (08:00–20:00 local, weekdays, for DE-LU). It is what a fixed block contract
  pays, it is defined by the clock, and it goes negative when midday solar pushes
  the on-peak block below the hours surrounding it.
- The **within-day range** is the day's highest interval price minus its lowest,
  averaged over complete local days. It is what a storage asset is paid, because
  a battery charges at the day's low and discharges at its high wherever in the
  day those happen to fall.

A day is complete at 23 observed hours, which is what the spring clock change
leaves. Partial days are dropped rather than scaled, because a day the provider
covered until noon has a genuinely smaller range and averaging it in would report
a falling spread that is really a reporting gap.

**Seasonality, and what a partial year costs.** The within-day range is
seasonal: averaged over 2019-2025 it runs about 65-70 EUR/MWh in January and
February against 113-134 in August and September, because a solar-shaped day has
a deeper midday trough in summer. A year that ends in September therefore sits
above its own full-year average. Measured on the complete years in this store,
January-to-September runs **+4.0% (2022), +3.5% (2023) and +5.2% (2025)** above
the full year, with 2019, 2020 and 2024 within ±4.5% in the other direction and
2021 an outlier at −36% because the gas spike landed in its fourth quarter.
Discount the current partial year's range figures by roughly that much. Reproduce
with `gpa.metrics.price.intraday_spread(period="month")` over the committed store.

Both series are published as a percentage of the same year's own average price.
That normalisation is what separates a price-level shock from a change in daily
shape; without it, 2022 dominates every chart and the structural change since is
invisible. The **hourly shape** table applies the same normalisation to the mean
price of each local clock hour.

## Sources, lineage and revisions

No page fetches a provider directly, and no source in this project requires a
credential. Each adapter is documented here because provenance, publication lag
and revision behaviour change how far a result can be pushed.

**Energy-Charts** (`api.energy-charts.info`) is published by the Fraunhofer
Institute for Solar Energy Systems ISE under CC BY 4.0. It republishes ENTSO-E
and SMARD figures through an open API, which is why it carries the European
zones here while an ENTSO-E Transparency token is obtained: the underlying
numbers are the ones the system operators publish. Four endpoints are used —
`/price` for day-ahead prices, `/public_power` for load and generation by fuel,
`/public_power_forecast` for the day-ahead fundamentals, which feed the labelled
ablation below and the prospective `ridge_da` arm but never the published
retrospective baseline, and `/installed_power` for installed capacity by technology
together with the government's own 2030 targets. Two provider behaviours are
handled explicitly rather than assumed: the `end` parameter is inclusive, and
the German series changed resolution without notice, so interval length is
measured from the returned timestamps instead of being hardcoded.

**SMARD** (`smard.de/app/chart_data`) is the Bundesnetzagentur's market-data
platform and is implemented as a second, independent German adapter, so the long
DE-LU price and fundamentals history can be backfilled without depending on the
rate-limited Energy-Charts mirror. It is registered and tested but no zone is
routed to it yet: DE-LU currently takes every dataset, fundamentals included,
from Energy-Charts, and every row in the committed store records Energy-Charts
or ONS as its source. The retrospective archive and the prospective run read the
same provider; what separates them is the publication vintage each can claim.

**ONS** is read through two endpoints on purpose, because they do not share a
publication lag. Generation comes from the hourly energy balance, one CSV per
calendar year on a public S3 bucket, whose contents trail real time by roughly
two days; load comes from the verified-load API, which stays within about an
hour. Timestamps are Brasília local time, and the pre-2019 daylight-saving
transitions are resolved explicitly rather than left to a library default.

**Revisions.** Stored observations are the providers' latest revisions, not
publication-time snapshots. Lagging every fundamental prevents a delivery-day
value from entering a forecast, but it cannot prove that the exact stored
revision was the one visible at the historical gate. This is the single most
important limitation on the retrospective result, and it is restated wherever a
figure depends on it. A prospective run does not share it, because it records
its own fetch time.

## Time and units

Every source timestamp is stored as a UTC-aware interval start and interpreted
through the zone's market timezone before grouping by day, month or local hour.
This prevents UTC day boundaries and daylight-saving changes from moving energy
between trading days.

- Price is currency/MWh and may be zero or negative.
- Load and generation are average MW over the stated interval.
- Energy is always `MW * interval minutes / 60`.
- Historical daily prices and monthly generation mix are duration-weighted.
- Incomplete observations remain visible as incomplete; they are not filled.

The European price history handles the transition from hourly to quarter-hourly
products on a per-window basis. The published forecast remains an hourly
analytical benchmark so it does not pretend to predict every traded
quarter-hour.

## Forecast protocol

The target is the DE-LU day-ahead price represented as a local clock-hour
average. The information set is restricted to what is available before the
day-ahead gate:

- lagged prices;
- calendar and market-local hour features;
- lagged residual load, defined as demand minus wind and solar.

The retrospective panel does not substitute realised delivery-day demand or
generation forecasts. Every model is refit in an expanding walk-forward loop,
with dates strictly before its prediction date. The benchmark compares naive
baselines, per-hour Ridge and pooled LightGBM. Hyperparameters are chosen on a
validation period before the evaluation period.

The scorecard reports MAE, RMSE, bias, pinball loss and empirical interval
coverage. Results are also broken down by market block, price regime, calendar
year and hour so aggregate skill cannot hide a weak operating regime.

Day-ahead load/wind/solar forecasts (Energy-Charts) are backfilled from
2019-01-05 for DE-LU, but are not part of the published information set above.
The provider exposes no publication timestamp for the historical archive, so
every backfilled row is assigned the D-1 noon market gate as a research-policy
vintage rather than an observed retrieval instant. An ablation adding these
features is shown as a labelled diagnostic on the
[Forecasting page](./forecast), not folded into the published baseline:
adopting it as a new frozen release is a separate decision this project has
not made, and the assigned vintage is precisely why it cannot be made from the
archive alone.

The prospective path resolves that differently, because for the day it is
forecasting it is not reading an archive: it records the instant it actually
retrieved the forecast. Rather than one arm that silently changes inputs with
provider timing, each run issues two frozen identities — `ridge` on the
information set above, `ridge_da` on that set plus these features — and the
ledger records both, so they can be scored against each other. `ridge_da`
abstains, and says so, when the provider has not published the delivery day
before the gate.

The limitation that remains is stated rather than hidden. Only the delivery
day's snapshot carries an observed vintage; `ridge_da`'s training history keeps
the assigned D-1 gate, because the archive holds no observed instant for it and
a model cannot be fitted on an empty history. The assumption therefore shapes
how that arm is fitted, never what its issued forecast was permitted to know —
which is the claim a reader would challenge. Training on observed vintages
throughout becomes possible only once this ledger has accumulated enough of
them. For the same reason the retrieval age is recorded on every issue as
evidence but is not itself a fitted feature: it is identically zero across any
training window, so it could only decorate the model, never inform it.

See the complete result on the [Forecasting page](./forecast).

## Capacity and market structure

The Battery page also correlates DE-LU price-shape metrics (capture rate,
on/off-peak spread, negative-price frequency) against Germany's installed
solar, wind and battery-storage capacity from Energy-Charts'
`/installed_power` endpoint, including the government's own EEG
2023/WindSeeG 2030 targets. This is a country series (Germany), not a DE-LU
bidding-zone series; Luxembourg's small share of DE-LU capacity is not in
this data and is not estimated. Every correlation is annual with roughly
seven complete-year points, excludes the current partial year using the
underlying data's own last observed interval (not the day the site happens
to be rebuilt), and is reported with its exact fitted range: this is a
real, small-sample co-movement, not a fitted causal model, and none of it
identifies whether battery-fleet growth is separately compressing this
project's own arbitrage margin. Two figures on that page are external
citations verified against their primary source rather than computed from
this project's data: a BloombergNEF battery-price survey and a Bundesnetzagentur
onshore-wind auction result, both dated.

## Battery valuation

The battery study translates forecast error into an economic decision. The
published base case is:

| Assumption | Value |
|---|---:|
| Power | 1 MW |
| Energy cases | 1, 2 and 4 MWh |
| Round-trip efficiency | 90% |
| Dispatch horizon | Complete eligible local day |
| Daily cycling policy | At most one charge-then-discharge episode (headline); a two-episode case is run separately |
| Initial and terminal SOC | 0 MWh |
| Operating and degradation cost | 0 in the base case; configurable |

The optimizer uses forecast prices to choose a full-day schedule, then settles
it against the observed price; there is no intraday re-optimization. The
schedule is built by a backward dynamic program whose phase dimension counts
episodes: charging after a discharge opens the next episode, and is refused once
the day's allowance is spent. The headline allows one episode, which is the
conservative reading of "one cycle a day"; the two-episode stress relaxes exactly
that assumption and nothing else, and is reported beside the headline rather than
replacing it.

Within a single episode, terminal SOC equal to initial SOC makes throughput
exactly twice the peak SOC excursion, so bounding that peak enforces the
per-episode cycle budget. That equivalence is per-episode: with several episodes
the daily ceiling is the product of the per-episode budget and the episode count,
which is why both bounds are declared separately. `no_trade` is
the zero-value baseline, while `perfect_foresight` runs the same physical
optimizer using realised prices only as an upper bound. Neither result is a
trading recommendation.

The [Battery page](./battery) is a pre-computed historical backtest. A future
prospective ledger will be evaluated separately after its delivery prices are
known.

All three fixed naive models, Ridge and LightGBM share the same complete days.
The strongest naive is ranked retrospectively over the sample, not selected
daily as an oracle or promoted to a prospective policy. The battery page shows
each fixed comparison, illustrative re-optimized costs, observed downside,
incremental concentration and paired exploratory block-bootstrap intervals.
Its fixed sensitivity protocol includes 85% efficiency, signal attenuation
toward D-1, whole-day calendar downtime and a two-episode day. These are diagnostic assumptions,
not a German asset calibration. Cost rates apply to charge plus discharge at
the grid boundary; capital and fixed/lifetime costs remain outside the model.
See the [Battery page](./battery) for exact definitions and source attribution.

## Assumptions register

Every published figure rests on the assumptions below. The battery's physical
parameters are in the table above; this register collects the rest, with the
reason each one was chosen and what it costs the result.

| Assumption | Choice | Rationale | Consequence if wrong |
|---|---|---|---|
| Forecast gate | 12:00 market time on D-1 | The day-ahead auction's order book closes at midday for next-day delivery, so this is the last instant a bidder's information set is fixed | A later gate would report hindsight as skill |
| Target resolution | Local clock-hour, duration-weighted | Day-ahead coupling moved to 15-minute market time units for delivery from 1 October 2025; the hourly figure is an analytical aggregate from then on | The benchmark does not price a traded quarter-hour product |
| Information set | Lagged prices, calendar features, residual load lagged ≥ 2 delivery days | Stored revisions cannot certify publication-time vintages, so realised delivery-day fundamentals are excluded | Reported skill is lower than a fundamentals-driven model would show; the ablation quantifies the gap |
| Fundamentals vintage | Backfilled day-ahead forecasts carry an assigned D-1 noon vintage; a prospective issue records the instant it actually read the delivery day's snapshot | The historical archive exposes no publication timestamp, while a live run can observe its own for the day it is forecasting | Retrospectively those features inform only the labelled ablation, never the published baseline; prospectively they define a separate `ridge_da` arm whose training history still carries the assigned vintage, so the assumption reaches the fit and not the issued information set |
| Walk-forward protocol | Expanding window, refit with dates strictly before each forecast day | Mirrors how a model would actually be maintained in production | A fixed split would hide regime-dependent decay |
| Hyperparameter selection | Frozen on a validation window preceding the test period | Selection inside the evaluation window reports a tuned fit as out-of-sample | Published scores would be optimistically biased |
| Evaluation stance | Retrospective development benchmark on already-inspected history | Honest label for a sample that has been examined during development | Not an untouched holdout; a prospective ledger is still required |
| Common sample | All five strategies scored on identical days and hours | Prevents a model from winning by being evaluated on easier cells | Comparisons would not be like-for-like |
| Missing data | Left missing; incomplete intervals excluded, never imputed | A provider gap is information, not a zero | Fewer scored cells, but no invented observations |
| Costs | Zero in the base case; illustrative non-zero cases rerun separately | Rates are not calibrated German project estimates | Margins are gross of asset-specific costs and of all capital costs |
| Daily cycling | One charge-then-discharge episode in the headline | The conservative reading of a one-cycle-a-day asset, and the binding constraint on almost every day in the sample | Understates a real German battery, which cycles more than once; the two-episode stress measures by how much, and shows the extra margin needs no forecast |
| Revenue stack | Day-ahead arbitrage only | The only market this project ingests prices for | A lower bound on a German battery's revenue: continuous intraday and the balancing markets (FCR, aFRR, mFRR, tendered via [regelleistung.net](https://www.regelleistung.net/)) are not modelled, nor is the fact that capacity committed to balancing cannot simultaneously arbitrage |
| Storage fleet composition | Inferred from the duration ratio, not observed | Energy-Charts publishes installed battery power and energy as one aggregate with no residential/grid-scale split, and the ratio is the only composition signal the store contains | The market view's third finding rests on ~1.5 hours being a home-storage signature. If the aggregate is in fact grid-scale heavy, the argument that competition has not arrived loses its mechanism, though not the observation that margin has not compressed. The Marktstammdatenregister would settle it and is outside this project's ingestion |
| Storage competition set | Batteries and pumped hydro, both from the installed-capacity series | Pumped hydro arbitrages the same daily spread and is the incumbent a battery-only view would miss | Demand-side response, industrial flexibility and cross-border flexibility are not counted, so the competing fleet here is a lower bound |
| Scope | Zonal, not nodal | The day-ahead auction clears at bidding-zone level | Congestion, basis and transmission constraints are outside the result |

## Quality controls

Validation happens at the source boundary with strict schemas for price, load
and generation. The checks enforce canonical units, UTC timestamps, allowed
resolutions, valid fuel labels, unique interval keys and plausible ranges.

The forecast and dispatch tests additionally check leakage boundaries, market
local time, missing observations, SOC limits, power limits, terminal SOC,
efficiency and cost treatment. `gpa export --check` regenerates the static
tables in a temporary directory and compares them with the committed files, so
the dashboard cannot silently drift from the Python analysis.

## Reproduce

```bash
pip install -e ".[dev]"
gpa validate
gpa audit
gpa export
npm ci
npm run build
```

The [source repository](https://github.com/Pedrods20/global-power-atlas) contains
the validated monthly Parquet store, forecast code, battery optimizer and test
suite. The historical output is deliberately versioned so the figures shown on
the site are reproducible.

## References

Data providers, whose terms govern the underlying observations:

- Energy-Charts, Fraunhofer Institute for Solar Energy Systems ISE —
  [energy-charts.info](https://www.energy-charts.info/), API at
  `api.energy-charts.info`, licensed CC BY 4.0
- SMARD, Bundesnetzagentur — [smard.de](https://www.smard.de/)
- ONS, Operador Nacional do Sistema Elétrico —
  [ons.org.br](https://www.ons.org.br/)
- ENTSO-E Transparency Platform, upstream of the European figures —
  [transparency.entsoe.eu](https://transparency.entsoe.eu/)

Market rules and structure:

- EPEX SPOT, basics of the power market, for the day-ahead auction's midday gate
  closure — [epexspot.com](https://www.epexspot.com/en/basicspowermarket)
- NEMO Committee, Single Day-Ahead Coupling: the transition from hourly to
  15-minute market time units on the trading day of 30 September 2025, for
  delivery on 1 October 2025 —
  [nemo-committee.eu/sdac](https://www.nemo-committee.eu/sdac)
- Bundesnetzagentur, consolidated onshore wind auction statistics —
  [bundesnetzagentur.de](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/Ausschreibungen/Wind_Onshore/BeendeteAusschreibungen/start.html)
- EEG 2023 and WindSeeG 2030 capacity targets, as republished in Energy-Charts'
  `/installed_power` series

Technical and cost references, used as stated external comparisons and never as
calibration for this project's own figures:

- NREL Annual Technology Baseline 2024, utility-scale battery storage —
  [atb.nrel.gov](https://atb.nrel.gov/electricity/2024/utility-scale_battery_storage)
- BloombergNEF, 2025 Lithium-Ion Battery Price Survey, published 9 December 2025 —
  [about.bnef.com](https://about.bnef.com/insights/clean-transport/lithium-ion-battery-pack-prices-fall-to-108-per-kilowatt-hour-despite-rising-metal-prices-bloombergnef/)

## Limitations

The forecast is retrospective until a separately recorded four-to-six-week
prospective period is complete. The published information set uses lagged
realised fundamentals, not operator forecasts: day-ahead load/wind/solar
forecasts are backfilled but carry an assigned, not observed, publication
vintage, so they inform only the labelled ablation on the Forecasting page,
not the published baseline. Whether they earn a place in the information set is
left to the prospective ledger, which issues them as a separate `ridge_da` arm
beside the published one and observes the delivery day's vintage rather than
assuming it; until that ledger has run, the question is open rather than
settled.
The study is zonal, not nodal;
congestion, basis and transmission constraints are outside scope. Brazilian
data is a national system comparison, not a wholesale price market.

<style>
main.observablehq > table { display: block; max-width: 100%; overflow-x: auto; }
</style>

---
title: Methodology
---

# Methodology

Global Power Atlas is a static research artifact. Source observations are
validated and aggregated before they reach the browser; no page fetches a
provider directly and no browser credential is required.

## Scope

The historical context covers four zones. Forecasting and battery valuation are
focused on Germany-Luxembourg (DE-LU), where the project has its deepest price,
load and generation history.

| Zone | Data | Provider | Use |
|---|---|---|---|
| DE-LU | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | Forecast reference market |
| France | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | Historical comparison |
| Spain | Price, load, generation | [Energy-Charts](https://www.energy-charts.info/) | Historical comparison |
| Brazil (SIN) | Load, generation | [ONS](https://www.ons.org.br/) | System comparison |

The dashboard refreshes monthly. Its purpose is historical context around the
forecast study, not continuous market monitoring.

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
vintage rather than an observed retrieval instant; a prospective run would
instead record its real fetch time. An ablation adding these features is
shown as a labelled diagnostic on the [Forecasting page](./forecast), not
folded into the published baseline: adopting it as a new frozen release is a
separate decision this project has not made.

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
| Daily cycling policy | At most one charge/discharge cycle |
| Initial and terminal SOC | 0 MWh |
| Operating and degradation cost | 0 in the base case; configurable |

The optimizer uses forecast prices to choose a full-day schedule with at most
one charge-then-discharge episode; there is no intraday re-optimization.
The schedule is then settled against the observed price. `no_trade` is
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
toward D-1 and whole-day calendar downtime. These are diagnostic assumptions,
not a German asset calibration. Cost rates apply to charge plus discharge at
the grid boundary; capital and fixed/lifetime costs remain outside the model.
See the [Battery page](./battery) for exact definitions and source attribution.

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

## Limitations

The forecast is retrospective until a separately recorded four-to-six-week
prospective period is complete. The published information set uses lagged
realised fundamentals, not operator forecasts: day-ahead load/wind/solar
forecasts are backfilled but carry an assigned, not observed, publication
vintage, so they inform only the labelled ablation on the Forecasting page,
not the published baseline. The study is zonal, not nodal;
congestion, basis and transmission constraints are outside scope. Brazilian
data is a national system comparison, not a wholesale price market.

<style>
main.observablehq > table { display: block; max-width: 100%; overflow-x: auto; }
</style>

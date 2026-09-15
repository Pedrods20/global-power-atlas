---
title: Price forecasting
---

# Forecasting the day-ahead price

A reproducible comparison of three naive forecasts, per-hour ridge regression and a pooled LightGBM model for Germany-Luxembourg. The question is whether a fitted model improves on repeating a known price, and where it falls short.

```js
const metadata = await FileAttachment("data/forecast.json").json();
const run = metadata.runs.find((d) => d.zone === "DE-LU");
const scores = [...await FileAttachment("data/forecast_scores.parquet").parquet()];
const daily = [...await FileAttachment("data/forecast_daily.parquet").parquet()];
const predictions = [...await FileAttachment("data/forecast_predictions.parquet").parquet()];
const names = new Map([
  ["naive_previous_day", "Previous day"],
  ["naive_previous_week", "Previous week"],
  ["naive_similar_day", "Similar day"],
  ["ridge", "Ridge"],
  ["lightgbm", "LightGBM"],
]);
const label = (m) => names.get(m) ?? m;
const number = (v) => v == null ? "n/a" : v.toFixed(2);
const percent = (v) => v == null ? "n/a" : `${v.toFixed(1)}%`;
const overall = scores.filter((d) => d.scope === "overall").sort((a, b) => a.mae - b.mae);
const ridge = overall.find((d) => d.model === "ridge");
const trees = overall.find((d) => d.model === "lightgbm");
const color = {domain: [...names.values()], range: ["#999999", "#CC79A7", "#009E73", "#0072B2", "#D55E00"], legend: true};
```

**Retrospective development benchmark.** This history was already inspected during development. The comparison is not an untouched final test or a record of forecasts issued before delivery. The historical evaluation is capped at ${run.benchmark_end}, so daily ingestion cannot silently consume the future evaluation period.

The stored inputs contain the providers' latest revisions. Publication-time versions of load and generation are unavailable: lagging the values avoids using future delivery dates but cannot prove that the exact stored revision was available at the historical forecast gate.

## Target and information set

- **Target:** duration-weighted hourly average of the DE-LU day-ahead prices, in EUR/MWh. Since the move to 15-minute products, this is an analytical hourly benchmark, not a forecast of each traded quarter-hour.
- **Forecast gate:** noon in the market timezone on the day before delivery. Prices for that preceding delivery day have already cleared in the previous auction.
- **Inputs:** lagged prices, calendar variables, and residual load from at least two delivery days earlier. Residual load is demand minus wind and solar; both fuels must actually be reported, including explicit zeros.
- **Published benchmark inputs:** operator forecasts of demand, wind and solar for the delivery day are not part of this retrospective run. The prospective panel accepts publication-time snapshots when supplied; realised delivery-day values are never substituted for them.

Only complete, contiguous UTC hours enter the panel. A provider transition left hourly-spaced records marked as 15-minute intervals in September 2025; those incomplete hours are excluded rather than assigned an inferred duration. Missing observations stay missing.

The spring clock change has no invented hour. The two occurrences of an autumn clock hour are combined into a duration-weighted mean; the scoreboard gives each resulting clock-hour cell equal weight. Daily feature means retain the actual 23, 24 or 25 hours and are withheld for incomplete days. This benchmark does not resolve the two autumn contracts separately.

## Experiment design

Training begins ${run.train_start}; validation begins ${run.validation_start}. The evaluation runs from ${run.test_start} to ${run.test_end}. All five models are scored on the same ${run.scored_hours.toLocaleString("en")} clock-hour cells, out of ${run.eligible_hours.toLocaleString("en")} target cells in that period. Missing lagged features and insufficient per-hour training history explain excluded cells.

The ridge penalty and LightGBM tree configuration are selected on the preceding validation window and frozen for evaluation. Every model refits with dates strictly before its forecast day. LightGBM uses one model across hours, with market-local hour as an additional known input; ridge conditions on hour through separate regressions. The published run records the candidate grid, chosen settings, refit cadence and seed 42. No early stopping or tuning uses evaluation prices.

```js
Inputs.table(overall.map((d) => ({
  Model: label(d.model), Hours: d.n, MAE: d.mae, RMSE: d.rmse,
  Bias: d.bias, "Skill vs best naive": d.skill_vs_best_baseline_pct,
})), {format: {MAE: number, RMSE: number, Bias: number, "Skill vs best naive": percent}, layout: "auto"})
```

```js
Plot.plot({
  title: "Mean absolute error on the common evaluation sample",
  subtitle: "Lower is better. Every model is scored on the same dates and hours.",
  width, height: 250, marginLeft: 110,
  x: {label: "MAE, EUR/MWh", grid: true}, y: {label: null, domain: overall.map((d) => label(d.model))},
  color,
  marks: [Plot.barX(overall, {x: "mae", y: (d) => label(d.model), fill: (d) => label(d.model), tip: true}), Plot.ruleX([0])],
})
```

Ridge MAE is **${number(ridge.mae)} EUR/MWh**; LightGBM MAE is **${number(trees.mae)} EUR/MWh**. The LightGBM change relative to ridge is **${percent((1 - trees.mae / ridge.mae) * 100)}**: positive means lower error. This comparison describes this sample; it is not a claim of future trading profitability.

## Where the models fail

```js
const scope = view(Inputs.select(new Map([["Price regime", "regime"], ["Market block", "block"], ["Calendar year", "year"], ["Hour of day", "hour"]]), {label: "Break down by", value: "regime"}));
const split = scores.filter((d) => d.scope === scope);
```

```js
Plot.plot({
  title: "Error by market condition", width,
  height: scope === "hour" ? 550 : 270, marginLeft: 100,
  x: {label: "MAE, EUR/MWh", grid: true}, y: {label: null}, color,
  marks: [Plot.dot(split, {x: "mae", y: "bucket", stroke: (d) => label(d.model), r: 5, tip: true}), Plot.ruleX([0])],
})
```

```js
Inputs.table(split.filter((d) => ["ridge", "lightgbm"].includes(d.model)).map((d) => ({
  Model: label(d.model), Bucket: d.bucket, Hours: d.n, MAE: d.mae, RMSE: d.rmse,
  Bias: d.bias, "Skill vs best naive": d.skill_vs_best_baseline_pct,
})), {format: {MAE: number, RMSE: number, Bias: number, "Skill vs best naive": percent}, layout: "auto"})
```

Negative prices and the top 5% of evaluation prices are diagnostic regimes assigned after the fact. They are never model inputs. Bias is realised price minus forecast: positive bias means underprediction. Negative skill means a model lost to the best naive baseline in that bucket.

```js
const failures = scores.filter((d) => ["ridge", "lightgbm"].includes(d.model) && d.skill_vs_best_baseline_pct <= 0).sort((a, b) => a.skill_vs_best_baseline_pct - b.skill_vs_best_baseline_pct);
```

There are **${failures.length} model/bucket comparisons** with no improvement over the best naive baseline. These include overlapping breakdowns, so they are not independent failures or additive counts of hours.

```js
const selectedModel = view(Inputs.select(new Map([["LightGBM", "lightgbm"], ["Ridge", "ridge"]]), {label: "Inspect model", value: "lightgbm"}));
const modelDaily = daily.filter((d) => d.model === selectedModel);
```

```js
Plot.plot({
  title: `Daily error: ${label(selectedModel)}`, width, height: 260, marginLeft: 55,
  x: {type: "utc", label: null}, y: {label: "MAE, EUR/MWh", grid: true},
  marks: [Plot.lineY(modelDaily, {x: (d) => new Date(d.local_date), y: "mae", stroke: "#0072B2", tip: true})],
})
```

## Predictive intervals

Each model uses quantiles of its own previous 56 observed errors at the same clock hour. Current-day errors never enter calibration. With gaps, these are 56 observations rather than necessarily 56 consecutive calendar days. Opening observations have no interval and remain in point-error scoring.

```js
const intervalRows = overall.map((d) => ({
  Model: label(d.model), Hours: d.n_interval,
  "Observed coverage": d.interval_coverage_pct,
  "Nominal coverage": 80, "Mean width (EUR/MWh)": d.interval_mean_width,
  "Mean pinball": d.mean_pinball,
}));
Inputs.table(intervalRows, {format: {"Observed coverage": percent, "Nominal coverage": percent, "Mean width (EUR/MWh)": number, "Mean pinball": number}, layout: "auto"})
```

The nominal 80% interval is an empirical target, not a coverage guarantee under changing market conditions. Pinball loss and mean interval width are reported alongside coverage because an excessively wide interval can cover prices while offering little useful precision. These metrics use full-precision predictions before the chart export rounds values to cents.

## Inspect a week

The week selector uses twelve evenly spaced weeks from the benchmark so the
browser stays responsive as the historical release grows. Forecast scores and
the downloadable research snapshot retain the complete evaluation sample.

```js
const weeks = [...new Set(predictions.map((d) => d3.utcMonday(new Date(d.local_date)).toISOString().slice(0, 10)))].sort();
const week = view(Inputs.select(weeks, {label: "Week beginning", value: weeks[Math.floor(weeks.length / 2)]}));
const begin = new Date(week);
const end = d3.utcDay.offset(begin, 7);
const weekRows = predictions.filter((d) => d.model === selectedModel && new Date(d.local_date) >= begin && new Date(d.local_date) < end);
const stamp = (d) => d3.utcHour.offset(new Date(d.local_date), d.local_hour);
```

```js
Plot.plot({
  title: `${label(selectedModel)} against realised hourly price`,
  subtitle: "Clock-hour benchmark, with the empirical 80% interval.",
  width, height: 320, marginLeft: 55,
  x: {type: "utc", label: "market-local clock hour (displayed on a synthetic axis)"},
  y: {label: "EUR/MWh", grid: true},
  marks: [
    Plot.areaY(weekRows, {x: stamp, y1: "q10", y2: "q90", fill: "#0072B2", fillOpacity: .15}),
    Plot.lineY(weekRows, {x: stamp, y: "forecast", stroke: "#0072B2", tip: true}),
    Plot.lineY(weekRows, {x: stamp, y: "actual", stroke: "currentColor", tip: true}),
    Plot.ruleY([0]),
  ],
})
```

## Reproduce and inspect the experiment

```bash
pip install -e ".[dev,tracking]"
gpa backtest --track --scope all
mlflow ui --backend-store-uri sqlite:///.gpa/mlflow/mlflow.db
gpa export
```

Tracking is optional and local. Each MLflow run records the comparison metrics, selected LightGBM parameters and search, ridge validation search, predictions, input panel, source snapshot, dependency versions and Git state. The input-panel SHA-256 is `${run.input_sha256}`. `gpa backtest` and the site build work without MLflow installed.

The prospective path is now operationalised by `gpa issue` and `gpa reconcile`: a scheduled job seeds an isolated store, refreshes a short input window, reconciles older ledger rows and issues before the market gate. The first separately recorded run is still needed before its metrics can be accepted. Historical battery dispatch and economic evaluation are published on the Battery page; the prospective battery result follows reconciliation and remains separate from the retrospective scores above.

<style>
main.observablehq > table { display: block; max-width: 100%; overflow-x: auto; }
</style>

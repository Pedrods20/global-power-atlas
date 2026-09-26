---
title: Forecast evidence
---

# The linear model wins where the money is

A reproducible comparison of three naive forecasts, a per-hour ridge regression
and a pooled LightGBM model for Germany-Luxembourg, under one rule: the forecast
may use only what a bidder holds when the day-ahead order book closes at noon on
D-1.

Ridge wins overall, which is unremarkable. What is worth reading is *where* the
gradient-boosted model loses. Its accuracy in ordinary hours does not survive
into negative-price and scarcity hours — the tails a storage asset is paid to
get right — and a model that is better on average while worse in the tails is
the wrong model for a dispatch decision. That is the finding this page exists to
document, alongside the protocol that makes it checkable.

```js
const metadata = await FileAttachment("data/forecast.json").json();
const run = metadata.runs.find((d) => d.zone === "DE-LU");
const scores = [...await FileAttachment("data/forecast_scores.parquet").parquet()];
const daily = [...await FileAttachment("data/forecast_daily.parquet").parquet()];
const predictions = [...await FileAttachment("data/forecast_preview.parquet").parquet()];
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

**Retrospective development benchmark.** This history was already inspected during development, so it is development evidence rather than an untouched final test or a record of forecasts issued before delivery. The evaluation is capped at ${run.benchmark_end}, so daily ingestion cannot silently consume the future evaluation period. Stored inputs carry the providers' latest revisions: lagging every fundamental avoids using future delivery dates, but it cannot prove the exact stored revision was the one available at the historical gate.

## Target and information set

- **Target:** duration-weighted hourly average of the DE-LU day-ahead prices, in EUR/MWh. European day-ahead coupling moved from hourly to 15-minute market time units on the trading day of 30 September 2025, for delivery on 1 October 2025; from that point the hourly figure is an analytical aggregate rather than a traded product, so this benchmark is deliberately not presented as a forecast of each traded quarter-hour.
- **Forecast gate:** noon in the market timezone on the day before delivery — the moment the day-ahead auction's order book closes for the following delivery day. The gate is set by the market's own deadline rather than by data convenience: it is the last instant at which a real bidder's information set is fixed. Prices for the preceding delivery day have already cleared in the previous auction and are therefore legitimately available.
- **Inputs:** lagged prices, calendar variables, and residual load from at least two delivery days earlier. Residual load is demand minus wind and solar; both fuels must actually be reported, including explicit zeros. Residual load is used because it, not raw demand, is what the remaining dispatchable stack has to serve — and therefore what sets the price.
- **Published benchmark inputs:** operator forecasts of demand, wind and solar for the delivery day are not part of this retrospective run, because the historical archive cannot say when each value became available. A prospective issue is not in that position for the day it is forecasting: it records the instant it actually read that snapshot. So rather than one arm whose inputs change with provider timing, each scheduled run issues two frozen identities — `ridge` on the information set above and `ridge_da` on that set plus these features — and the ledger keeps both, which is what lets them be compared. `ridge_da` abstains and records the abstention when the provider has not published the delivery day before the gate. Its training history still carries the assigned D-1 vintage, so that assumption reaches how it is fitted and not what its issued forecast knew. Realised delivery-day values are never substituted for a forecast.

The restriction matters commercially, not just methodologically. Delivery-day
wind, solar and demand explain a great deal of the price, so a model given the
realised values will report an error a desk could never achieve — the number
would describe hindsight, not skill. The honest cost of that discipline is
visible further down this page: the labelled ablation shows what the same
protocol produces when day-ahead fundamentals are allowed in.

Only complete, contiguous UTC hours enter the panel. A provider transition left hourly-spaced records marked as 15-minute intervals in September 2025; those incomplete hours are excluded rather than assigned an inferred duration. Missing observations stay missing.

The spring clock change has no invented hour. The two occurrences of an autumn clock hour are combined into a duration-weighted mean; the scoreboard gives each resulting clock-hour cell equal weight. Daily feature means retain the actual 23, 24 or 25 hours and are withheld for incomplete days. This benchmark does not resolve the two autumn contracts separately.

## Experiment design

Training begins ${run.train_start}; validation begins ${run.validation_start}. The evaluation runs from ${run.test_start} to ${run.test_end}. All five models are scored on the same ${run.scored_hours.toLocaleString("en")} clock-hour cells, out of ${run.eligible_hours.toLocaleString("en")} target cells in that period. Missing lagged features and insufficient per-hour training history explain excluded cells.

The ridge penalty and LightGBM tree configuration are selected on the preceding validation window and frozen for evaluation. Every model refits with dates strictly before its forecast day. LightGBM uses one model across hours, with market-local hour as an additional known input; ridge conditions on hour through separate regressions. The published run records the candidate grid, chosen settings, refit cadence and seed 42. No early stopping or tuning uses evaluation prices.

The two fitted models are a deliberate contrast of designs rather than a search for a winner. Each delivery hour is close to its own product — the morning ramp, the midday solar trough and the evening peak are priced by different parts of the stack — so per-hour ridge gives every hour its own coefficients on a smaller sample, while pooled LightGBM shares all hours' data and has to recover the hour effect from a feature. The three naive baselines are not straw men: repeating yesterday's price, last week's price or the last similar day is what a desk actually falls back on when no model is trusted, so they are the standard any fitted model has to clear before it earns its complexity.

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

**How to read these numbers.** MAE is the average absolute distance between the forecast and the price that actually cleared, in EUR per MWh, over every scored hour. Skill against the best naive baseline is the fraction of that error removed relative to the strongest simple alternative — a relative measure, so it says nothing on its own about how much money the improvement is worth. Bias is realised price minus forecast, so a positive bias means the model systematically underpredicts. None of these translate mechanically into margin: an error reduction concentrated in flat hours is worth far less to a storage asset than the same reduction around the daily spread. That translation is measured separately, on the [battery page](./battery), and it is the reason this project reports both.

## Does more information help?

```js
const ablation = [...await FileAttachment("data/fundamentals_ablation.parquet").parquet()];
const ablationRow = (model, includeFundamentals) => ablation.find((d) => d.model === model && d.include_fundamentals === includeFundamentals);
const ridgeBase = ablationRow("ridge", false);
const ridgeAbl = ablationRow("ridge", true);
const lightgbmBase = ablationRow("lightgbm", false);
const lightgbmAbl = ablationRow("lightgbm", true);
```

Day-ahead load/wind/solar forecasts are backfilled (Energy-Charts, from
2019) but excluded from the published information set above: the historical
archive carries no publication timestamp, so every value is assigned the
D-1 noon gate as a research-policy vintage rather than an observed
retrieval instant (see [methodology](./methodology)). Adding them anyway,
as a labelled ablation run on the identical ${ridgeBase ? ridgeBase.test_start : "?"}
to ${ridgeBase ? ridgeBase.test_end : "?"} evaluation window used above:

```js
ridgeBase && ridgeAbl && lightgbmBase && lightgbmAbl
  ? Inputs.table([
      {Model: "Ridge", "MAE, published": ridgeBase.mae, "MAE, with fundamentals": ridgeAbl.mae, "Change": (1 - ridgeAbl.mae / ridgeBase.mae) * 100},
      {Model: "LightGBM", "MAE, published": lightgbmBase.mae, "MAE, with fundamentals": lightgbmAbl.mae, "Change": (1 - lightgbmAbl.mae / lightgbmBase.mae) * 100},
    ], {format: {"MAE, published": number, "MAE, with fundamentals": number, "Change": percent}, layout: "auto"})
  : html`<p class="note">Ablation not yet run locally: <code>gpa fundamentals-ablation</code>.</p>`
```

**This is a labelled diagnostic, not the published forecast.** Adopting
these features into a new frozen release is a separate decision this
project has not made; the scoreboard, chart and every other number on this
page use the published, fundamentals-free information set. A positive
change above is measured on the identical frozen test window as the
published run, using the exact same protocol with one flag added — not a
reselected model or a different evaluation period. It may also be an upper
bound: EU rules do not require the wind and solar forecasts to be public
before the gate, and when they actually appear is being measured (see the
prospective section below).

## Where the models fail

```js
const byYear = (model) => scores.filter((d) => d.scope === "year" && d.model === model).sort((a, b) => a.bucket.localeCompare(b.bucket));
const ridgeYearly = byYear("ridge");
const lightgbmYearly = byYear("lightgbm");
const worstYear = ridgeYearly.reduce((a, b) => (b.mae > a.mae ? b : a));
const calmestYear = ridgeYearly.reduce((a, b) => (b.mae < a.mae ? b : a));
const lightgbmLossYears = lightgbmYearly.filter((d) => d.skill_vs_best_baseline_pct <= 0).map((d) => d.bucket);
const recentYears = lightgbmYearly.filter((d) => d.bucket >= "2024" && d.bucket <= run.test_end.slice(0, 4));
const lightgbmAheadRecently = recentYears.every((d) => {
  const r = ridgeYearly.find((e) => e.bucket === d.bucket);
  return r && d.mae < r.mae;
});
const regime = (bucket) => scores.find((d) => d.scope === "regime" && d.bucket === bucket && d.model === "ridge");
const regimeOf = (bucket, model) => scores.find((d) => d.scope === "regime" && d.bucket === bucket && d.model === model);
const negative = regime("negative");
const scarcity = regime("scarcity");
```

The 2019-2026 sample is not one market. Ridge's own annual MAE moves from
**${number(calmestYear.mae)} EUR/MWh in ${calmestYear.bucket}** — a calm,
low-demand year — to **${number(worstYear.mae)} EUR/MWh in ${worstYear.bucket}**,
about **${(worstYear.mae / calmestYear.mae).toFixed(1)}×** higher, during the
European gas-price shock. Both fitted models score every day the same way
throughout; the swing is the market, not a change in method.

The two models do not fail the same way. LightGBM lost outright to the best
naive baseline in ${lightgbmLossYears.length ? lightgbmLossYears.join(" and ") : "no year"}
— the most volatile years in the sample — while Ridge kept a positive skill
margin every year, including those. ${lightgbmAheadRecently ? `From 2024
onward, in the calmer market since, LightGBM's annual MAE has been at or below
Ridge's every year` : `LightGBM has not consistently closed that gap since`} —
this is one sample's ordering, not a general claim that either model dominates.

The same pattern holds by regime, not just by year: in negative-price hours,
**${label(negative.model)}** trails the previous-day baseline
(${percent(negative.skill_vs_best_baseline_pct)}), and LightGBM trails it far
more (${percent(regimeOf("negative", "lightgbm").skill_vs_best_baseline_pct)}).
In the scarcest 5% of hours, Ridge still beats the baseline
(${percent(scarcity.skill_vs_best_baseline_pct)}) but LightGBM loses badly
(${percent(regimeOf("scarcity", "lightgbm").skill_vs_best_baseline_pct)}).
A tree model's accuracy in typical hours does not carry over to the tails,
where the euros actually are.

<details>
<summary>Explore the full breakdown by market block, price regime, calendar year and hour of day</summary>

```js
const scope = view(Inputs.select(new Map([["Price regime", "regime"], ["Market block", "block"], ["Calendar year", "year"], ["Hour of day", "hour"]]), {label: "Break down by", value: "regime"}));
```

```js
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
```

```js
const modelDaily = daily.filter((d) => d.model === selectedModel);
```

```js
Plot.plot({
  title: `Daily error: ${label(selectedModel)}`, width, height: 260, marginLeft: 55,
  x: {type: "utc", label: null}, y: {label: "MAE, EUR/MWh", grid: true},
  marks: [Plot.lineY(modelDaily, {x: (d) => new Date(d.local_date), y: "mae", stroke: "#0072B2", tip: true})],
})
```

</details>

<details>
<summary>Predictive intervals</summary>

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

</details>

<details>
<summary>Inspect a week</summary>

The week selector uses twelve evenly spaced weeks from the benchmark so the
browser stays responsive as the historical release grows. Forecast scores and
the downloadable research snapshot retain the complete evaluation sample.

```js
const weekModel = view(Inputs.select(new Map([["LightGBM", "lightgbm"], ["Ridge", "ridge"]]), {label: "Inspect model", value: "lightgbm"}));
```

```js
const weeks = [...new Set(predictions.map((d) => d3.utcMonday(new Date(d.local_date)).toISOString().slice(0, 10)))].sort();
```

```js
const week = view(Inputs.select(weeks, {label: "Week beginning", value: weeks[Math.floor(weeks.length / 2)]}));
```

```js
const begin = new Date(week);
const end = d3.utcDay.offset(begin, 7);
const weekRows = predictions.filter((d) => d.model === weekModel && new Date(d.local_date) >= begin && new Date(d.local_date) < end);
const stamp = (d) => d3.utcHour.offset(new Date(d.local_date), d.local_hour);
```

```js
Plot.plot({
  title: `${label(weekModel)} against realised hourly price`,
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

</details>

## Reproduce and inspect the experiment

```bash
pip install -e ".[dev]"
gpa backtest --scope all
gpa fundamentals-ablation
gpa export --check
```

The frozen release under `data/experiments/` keeps the predictions, scores, penalty and tree searches, ridge coefficients and the input panel, content-addressed together with a hash of the forecasting code. The input-panel SHA-256 is ${html`<code>${run.input_sha256}</code>`}; `gpa export --check` proves every published table still matches it.

The prospective path is operationalised by `gpa issue` and `gpa reconcile`: a scheduled job seeds an isolated store, refreshes a short input window, reconciles older ledger rows and issues before the market gate. Each issue keeps its own immutable evidence — the input panel, the source frames it read, the feature list, the model configuration and the instant each input was observed — so a later reader can check what the forecast knew rather than take it on trust.

**The ledger has started.** Eleven scheduled runs between 14 and 22 September 2026 all arrived hours after the gate — GitHub's scheduler is best-effort — and `gpa issue` refused every one rather than backdate a forecast. The first run to clear a gate did so at 07:49 UTC on 23 September, 2h11m early, and issued `ridge` for all 24 hours of the 24th. Every run now also issues the three naive comparators at the same gate on the same inputs, so the ledger can test the claim this page makes — value over the best naive — rather than only report error. The counter below is built from the committed attempt records, each delivery day counted once at the best outcome any attempt reached:

```js
const ledgerStatus = await FileAttachment("data/ledger_status.json").json();
```

```js
ledgerStatus.arms.length
  ? Inputs.table(ledgerStatus.arms, {
      columns: ["model", "days", "issued", "partial", "abstained", "late", "failed", "first_delivery", "last_delivery"],
      header: {model: "Arm", days: "Days tried", first_delivery: "First day", last_delivery: "Latest day"},
      layout: "auto"
    })
  : html`<p class="note">No attempt records in this build.</p>`
```

<p class="note">Ledger as of ${ledgerStatus.as_of ? ledgerStatus.as_of.slice(0, 16).replace("T", " ") + " UTC" : "—"}, the latest attempt in this build; the site is rebuilt on each release, not on each run. One day is not a record: nothing from the ledger is compared with the retrospective scores until it covers the pilot's six weeks, with at least 95% of delivery days issued on time.</p>

That path was also meant to settle the fundamentals question by experiment, and its first result is a question about the market rather than the model. `ridge_da` abstained on all 24 hours of that first run: at 09:49 CEST Energy-Charts had not yet published the delivery day's load, wind and solar forecasts, just as a manual check had found nothing at 05:45 CEST a week earlier. This may be structural. Commission Regulation (EU) 543/2013 requires day-ahead wind and solar forecasts by 18:00 Brussels time on D-1, six hours after the gate, and only the load forecast before it. If the public series routinely appears after noon, `ridge_da` can never issue, and the ablation above measures information a bidder would not have held from this source at the gate — an upper bound, not an attainable gain; a desk closes that gap by buying commercial weather-driven forecasts before the auction. Rather than argue it, an hourly probe (`gpa probe-fundamentals`) now logs how much of the next delivery day the provider serves at each check and how far that check sits from the gate. The ablation's framing will follow the measurement.

Historical battery dispatch and economic evaluation are published on the Battery page; the prospective battery result follows reconciliation and remains separate from the retrospective scores above.

<style>
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: .5rem 0 .5rem 1rem;
  color: var(--theme-foreground-muted);
}
</style>

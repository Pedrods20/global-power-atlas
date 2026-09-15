---
title: Battery value
---

# Does a better forecast create battery value?

A DE-LU day-ahead arbitrage study: compare a forecast-guided battery with
three fixed simple strategies, then test how much of its advantage survives
costs, downtime and weaker forecast signals. This is an hourly analytical
benchmark, not a forecast of each traded quarter-hour or a complete asset valuation.

```js
const summary = [...await FileAttachment("data/battery_summary.parquet").parquet()];
const monthlyMargins = [...await FileAttachment("data/battery_monthly.parquet").parquet()];
const dispatchExample = [...await FileAttachment("data/battery_dispatch_example.parquet").parquet()];
const risk = [...await FileAttachment("data/battery_risk.parquet").parquet()];
const comparisons = [...await FileAttachment("data/battery_comparisons.parquet").parquet()];
const costs = [...await FileAttachment("data/battery_costs.parquet").parquet()];
const stresses = [...await FileAttachment("data/battery_sensitivities.parquet").parquet()];
const coverage = [...await FileAttachment("data/battery_coverage.parquet").parquet()];
const cannibalisation = [...await FileAttachment("data/cannibalisation.parquet").parquet()];
const capacityYearly = [...await FileAttachment("data/capacity_price_yearly.parquet").parquet()];
const capacityCorrelation = [...await FileAttachment("data/capacity_price_correlation.parquet").parquet()];
const extrapolationFlags = [...await FileAttachment("data/capacity_extrapolation_flags.parquet").parquet()];
const competitionCorrelation = [...await FileAttachment("data/battery_competition_correlation.parquet").parquet()];
const gw = (value) => value == null ? "n/a" : value.toFixed(1);
const labels = new Map([
  ["ridge", "Ridge"], ["lightgbm", "LightGBM"],
  ["naive_previous_day", "Previous day"], ["naive_previous_week", "Previous week"],
  ["naive_similar_day", "Similar day"], ["perfect_foresight", "Perfect foresight"],
  ["no_trade", "No trade"],
]);
const scenarioNames = new Map([
  ["base", "Base: 90% efficiency, zero costs"],
  ["cost_2_3", "Costs: 2 / 3 EUR per grid MWh"],
  ["cost_5_10", "Costs: 5 / 10 EUR per grid MWh"],
  ["efficiency_85", "85% round-trip efficiency"],
  ["signal_50", "50% forecast deviation from D-1"],
  ["calendar_downtime", "One unavailable day in twenty"],
  ["combined", "2/3 costs + 85% + 50% signal + downtime"],
]);
const name = (value) => labels.get(value) ?? value;
const euro = (value) => value == null ? "n/a" : value.toLocaleString("en", {maximumFractionDigits: 0});
const pct = (value) => value == null ? "n/a" : `${(100 * value).toFixed(1)}%`;
const renderTable = (id, rows, format = {}) => {
  const table = Inputs.table(rows, {format, rows: rows.length, layout: "auto"});
  table.id = id;
  return table;
};
```

```js
const duration = view(Inputs.select([1, 2, 4], {label: "Battery duration at 1 MW (hours)", value: 4}));
```

```js
const asset = (row) => row.energy_mwh === duration;
const fitted = (row) => ["ridge", "lightgbm"].includes(row.strategy);
const headline = stresses.find((d) => asset(d) && d.strategy === "ridge" && d.scenario === "base");
const batteryDays = headline.days;
const selectedRisk = risk.filter((d) => asset(d) && fitted(d));
const selectedPairs = comparisons.filter((d) => asset(d) && fitted(d));
const baselineValue = summary.find((d) => asset(d) && d.strategy === headline.best_naive).profit_eur;
const monthly = monthlyMargins.filter(asset).map((d) => ({strategy: d.strategy, month: String(d.month), profit: d.profit_eur}));
const cumulative = d3.groups(monthly, (d) => d.strategy).flatMap(([strategy, values]) => {
  let total = 0;
  return values.sort((a, b) => a.month.localeCompare(b.month)).map((d) => ({...d, total: total += d.profit}));
});
const sample = dispatchExample.filter((d) => asset(d) && d.strategy === "ridge");
const sampleDate = d3.max(sample, (d) => String(d.local_date));
```

## Decision in brief

For the selected ${duration}h battery, Ridge adds **EUR ${euro(headline.incremental_vs_best_naive_eur_mw)}/MW**
over **${name(headline.best_naive)}**, the strongest fixed naive *in this observed sample*:
a ${pct(headline.incremental_vs_best_naive_eur_mw / baselineValue)} uplift over that comparator,
not the percentage reduction in price forecast error.

The comparison covers **${batteryDays} common eligible days**, from ${headline.sample_start}
to ${headline.sample_end}. These are sample-period margins, **not annualized returns**.
The base case excludes variable operating and degradation costs; the nonzero
cases below rerun the optimizer. CAPEX, fixed OPEX, taxes, financing and other
market revenues are outside this study.

The exploratory paired 95% interval for that incremental margin is
EUR ${euro(headline.ci_low_eur_mw)}–${euro(headline.ci_high_eur_mw)}/MW.
It is conditional on already-inspected history, not proof of future performance.

## Compare the alternatives

All five forecast strategies use the same eligible days and physical constraints.
The best naive is chosen once per asset/scenario over the whole sample, never
hour by hour or day by day. That retrospective ranking is a diagnostic, not a
deployable model-selection rule. All three fixed comparisons remain visible.

```js
renderTable("battery-scoreboard", summary.filter(asset).map((d) => ({
  Strategy: name(d.strategy), Days: d.days,
  "Operating margin (EUR/MW)": d.profit_eur / d.power_mw,
  "Equivalent cycles": d.equivalent_cycles,
  "Capture vs perfect": d.capture_vs_perfect,
})), {"Operating margin (EUR/MW)": euro, "Equivalent cycles": euro, "Capture vs perfect": pct})
```

```js
renderTable("battery-comparisons", selectedPairs.map((d) => ({
  Model: name(d.strategy), Comparator: name(d.baseline),
  "Increment (EUR/MW)": d.incremental_eur_mw,
  "95% low": d.ci_low_eur_mw, "95% high": d.ci_high_eur_mw,
})), {"Increment (EUR/MW)": euro, "95% low": euro, "95% high": euro})
```

```js
const cumulativeChart = Plot.plot({
  title: "Cumulative operating margin — zero-cost base",
  subtitle: `${duration}h battery; partial months contain only eligible days.`,
  width, height: 320, marginLeft: 70,
  x: {type: "utc", label: null}, y: {label: "EUR per MW", grid: true},
  color: {domain: [...labels.values()], legend: true},
  marks: [
    Plot.lineY(cumulative, {x: (d) => new Date(`${d.month}-01T00:00:00Z`), y: "total", z: "strategy", stroke: (d) => name(d.strategy), tip: true}),
    Plot.ruleY([0]),
  ],
});
cumulativeChart.id = "battery-cumulative";
display(cumulativeChart);
```

## Costs and robustness

Costs apply to **absolute grid-side charging plus discharging MWh**, not to
capacity or battery-side cycles. The 2/3 and 5/10 cost pairs are illustrative
variable/degradation charges, **not calibrated German project estimates**.
Margin after those charges is still not net investment profit.

```js
renderTable("battery-costs", costs.filter((d) => asset(d) && fitted(d)).map((d) => ({
  Model: name(d.strategy),
  "Variable / degradation": `${d.variable_cost_eur_mwh} / ${d.degradation_cost_eur_mwh}`,
  "Gross margin (EUR)": d.gross_revenue_eur,
  "Stated costs (EUR)": d.operating_cost_eur + d.degradation_cost_eur,
  "After-cost margin (EUR)": d.profit_eur,
  "Best naive": name(d.best_naive),
  "Increment (EUR/MW)": d.incremental_vs_best_naive_eur_mw,
})), {"Gross margin (EUR)": euro, "Stated costs (EUR)": euro, "After-cost margin (EUR)": euro, "Increment (EUR/MW)": euro})
```

The following fixed stresses focus on Ridge, the featured forecast. They do not
retune it. Equivalent results for all strategies are available in the downloadable data.

```js
html`<a href=${await FileAttachment("data/battery_sensitivities.parquet").url()} download="battery_sensitivities.parquet">Download all sensitivity results (Parquet)</a>`
```

```js
renderTable("battery-sensitivities", stresses.filter((d) => asset(d) && d.strategy === "ridge").sort((a, b) => [...scenarioNames.keys()].indexOf(a.scenario) - [...scenarioNames.keys()].indexOf(b.scenario)).map((d) => ({
  Scenario: scenarioNames.get(d.scenario),
  "Available days": d.available_days,
  "Margin (EUR/MW)": d.profit_eur_mw,
  "Best naive": name(d.best_naive),
  "Increment (EUR/MW)": d.incremental_vs_best_naive_eur_mw,
  "95% low": d.ci_low_eur_mw, "95% high": d.ci_high_eur_mw,
})), {"Margin (EUR/MW)": euro, "Increment (EUR/MW)": euro, "95% low": euro, "95% high": euro})
```

The **85% efficiency** case uses a technical reference from
[NREL ATB 2024](https://atb.nrel.gov/electricity/2024/utility-scale_battery_storage),
not a claim about this hypothetical asset. The **50% signal** case halves each
fitted forecast's deviation from the previous-day forecast, without using
realised prices. It need not worsen MAE or profit: this is a signal-dependence
diagnostic, not a calibrated forecast-error distribution.

ATB accounts for augmentation within fixed O&M rather than the per-throughput
charges used here. Its cost framework therefore does **not** validate our
illustrative EUR/MWh rates; do not combine both approaches without checking for
double-counted degradation costs.

**Downtime** is one complete unavailable day every twenty calendar days, anchored
at 1 January 2025 and shared by all strategies. Activity and settlement are zero
on those days; observations and the common sample denominator remain intact.
It assumes the outage is known before scheduling, not a mid-cycle failure or an
imbalance penalty. The combined case applies 2/3 costs, 85% efficiency, 50% signal
and the same downtime calendar.

## Downside and concentration

```js
renderTable("battery-risk", selectedRisk.map((d) => ({
  Model: name(d.strategy),
  "Loss days": d.loss_days, "Worst day (EUR)": d.worst_day_eur,
  "Max drawdown (EUR)": d.max_drawdown_eur,
  "Top 5 days / positive margin": d.top_5_days_share_positive_margin,
})), {"Worst day (EUR)": euro, "Max drawdown (EUR)": euro, "Top 5 days / positive margin": pct})
```

Against its sample-best naive, Ridge underperforms on **${headline.underperform_days}
of ${batteryDays} days**. Its five largest positive incremental days account for
**${pct(headline.top_5_days_share_positive_incremental)}** of all positive incremental
margin. Removing those five gains leaves **EUR
${euro(headline.incremental_without_best_5_days_eur_mw)}/MW** incremental margin.
This removal is an ex-post concentration test, not a trading rule.

Intervals use 2,000 paired, non-overlapping seven-calendar-day block resamples
(seed 20260914). Missing dates are not compressed. These intervals do not account
for model selection, multiple comparisons, structural change or omitted costs.

## What can we conclude about duration?

```js
renderTable("battery-durations", stresses.filter((d) => d.strategy === "ridge" && d.scenario === "base").sort((a, b) => a.energy_mwh - b.energy_mwh).map((d) => ({
  "Duration (h)": d.energy_mwh / d.power_mw,
  "Margin (EUR/MW)": d.profit_eur_mw,
  "Best naive": name(d.best_naive),
  "Increment (EUR/MW)": d.incremental_vs_best_naive_eur_mw,
  "Increment / MWh capacity": d.incremental_vs_best_naive_eur_mw * d.power_mw / d.energy_mwh,
})), {"Margin (EUR/MW)": euro, "Increment (EUR/MW)": euro, "Increment / MWh capacity": euro})
```

Longer duration can capture more absolute arbitrage margin but requires more
energy capacity. Compare the incremental value per MW **and per MWh of capacity**;
do not choose an investment simply because its gross margin is larger.
Treat 4h as a candidate for a follow-up asset business case, not the proven
optimal duration. Project CAPEX, fixed OPEX, availability terms, lifetime
degradation and additional revenues are required before ranking investments.

## Is this margin durable as the market changes?

```js
const solarCannibal = cannibalisation.filter((d) => d.fuel === "solar").sort((a, b) => a.period.localeCompare(b.period));
const windCannibal = cannibalisation.filter((d) => d.fuel === "wind").sort((a, b) => a.period.localeCompare(b.period));
const firstSolar = solarCannibal[0];
const lastSolar = solarCannibal[solarCannibal.length - 1];
const lastWind = windCannibal[windCannibal.length - 1];
const yearlyByYear = new Map(capacityYearly.map((d) => [d.year, d]));
const firstSolarYear = yearlyByYear.get(firstSolar.period);
const lastSolarYear = yearlyByYear.get(lastSolar.period);
const solarCapacityMultiple = lastSolarYear.solar_capacity_gw / firstSolarYear.solar_capacity_gw;
const solarCaptureCorr = capacityCorrelation.find((d) => d.x === "solar_capacity_gw" && d.y === "solar_capture_rate");
const spreadCorr = capacityCorrelation.find((d) => d.x === "solar_capacity_gw" && d.y === "spread_pct_of_price");
const windCaptureCorr = capacityCorrelation.find((d) => d.x === "wind_capacity_gw" && d.y === "wind_capture_rate");
const solarTarget = extrapolationFlags.find((d) => d.technology === "Solar AC");
const windOnshoreTarget = extrapolationFlags.find((d) => d.technology === "Wind onshore");
const windOffshoreTarget = extrapolationFlags.find((d) => d.technology === "Wind offshore");
const foresightCompetition = competitionCorrelation.find((d) => d.strategy === "perfect_foresight" && d.energy_mwh === duration);
```

Solar's own capture rate — what a solar generator actually earns, divided by
the flat average price — fell from **${pct(firstSolar.capture_rate)}** in
${firstSolar.period} to **${pct(lastSolar.capture_rate)}** in ${lastSolar.period},
while DE-LU's installed solar capacity grew roughly **${solarCapacityMultiple.toFixed(1)}×**
(correlation ${solarCaptureCorr.pearson_r.toFixed(2)}, n=${solarCaptureCorr.n}
complete years, ${solarCaptureCorr.fitted_year_min}-${solarCaptureCorr.fitted_year_max}).
The on/off-peak spread moved with it, from **EUR ${euro(firstSolarYear.spread)}/MWh**
in ${firstSolar.period} to **EUR ${euro(lastSolarYear.spread)}/MWh** in
${lastSolar.period} (correlation ${spreadCorr.pearson_r.toFixed(2)}) — on-peak
hours are now, on average, *cheaper* than off-peak, the textbook signature of
solar cannibalisation. Wind shows no comparable trend
(correlation ${windCaptureCorr.pearson_r.toFixed(2)}): its flatter daily and
seasonal output self-cannibalises far less than solar's midday concentration.

This is a real, already-visible co-movement, not a fitted causal model: with
only ${solarCaptureCorr.n} complete annual points, most series that both trend
over the period will correlate whether or not one drives the other.

Government targets assume this trend continues, and then some. DE-LU's
realised solar capacity has never exceeded **${gw(solarTarget.realised_max_gw)} GW**
(${solarTarget.realised_max_year}); Germany's EEG 2023 target for 2030 is
**${gw(solarTarget.planned_2030_gw)} GW**. Onshore wind's realised ceiling is
**${gw(windOnshoreTarget.realised_max_gw)} GW** against a ${gw(windOnshoreTarget.planned_2030_gw)} GW
target; offshore wind's is **${gw(windOffshoreTarget.realised_max_gw)} GW**
against **${gw(windOffshoreTarget.planned_2030_gw)} GW**. Every one of these
targets is an extrapolation beyond anything this project's own correlation is
fitted on — a real gap to weigh against the trend above, not a forecast of it.

Does competition from other batteries already show up as compressed arbitrage
margin? Not yet, in this sample. For the selected ${duration}h battery,
perfect-foresight margin correlates **positively** with Germany's own battery
fleet (r=${foresightCompetition.pearson_r.toFixed(2)}, n=${foresightCompetition.n}
years, ${foresightCompetition.fitted_year_min}-${foresightCompetition.fitted_year_max};
the same sign holds for the forecast-driven strategies too) — not negatively.
The same years saw the 2021-2022 fuel-price shock and the cannibalisation
above expand the arbitrage opportunity faster than a still-small competing
fleet could compress it. **This is not evidence that competition does not
erode margin**, only that, so far, a much larger co-moving trend swamps
whatever effect it may already be having.

That fleet is growing fast, and batteries just got a lot cheaper: utility-scale
stationary-storage pack prices fell 45% in a single year, to $70/kWh in 2025
([BloombergNEF, 2025 Lithium-Ion Battery Price Survey](https://about.bnef.com/insights/clean-transport/lithium-ion-battery-pack-prices-fall-to-108-per-kilowatt-hour-despite-rising-metal-prices-bloombergnef/),
published 9 December 2025) — the sharpest drop of any segment BNEF tracks. A
falling barrier to adding competing capacity is exactly the condition under
which the "not yet" above would be expected to change.

Auction results add one more data point, with a caveat attached. Germany's
most recent onshore wind auction (1 May 2026) cleared at an average reference
value of 5.06 ct/kWh — EUR 50.6/MWh
([Bundesnetzagentur, consolidated onshore wind auction statistics](https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/Ausschreibungen/Wind_Onshore/BeendeteAusschreibungen/start.html)),
well below DE-LU's realised ${lastWind.period} wind capture price of
**EUR ${euro(lastWind.capture_price)}/MWh**. This is not a fixed offtake price:
EEG's sliding market premium tops a project up to the reference value only
when the wholesale price is below it, and pays nothing in negative-price hours
— so a reference value under today's capture price does not by itself mean new
wind is unprofitable at the margin, only that its guaranteed floor sits well
under what the market has recently paid.

**What would make the ${duration}h case above less attractive.** Its margin
is currently earned in a market where volatility has been growing faster than
competition — reverse either half of that and the case weakens. A calmer
price shape (the gas-crisis premium unwinding) or storage finally growing past
the point where it visibly compresses spreads rather than just riding them
would shrink the spread this battery is paid to exploit, not just this
project's forecast advantage over a naive strategy. None of the correlations
above are strong enough, at n=${foresightCompetition.n} years, to say when
that turn arrives — only that a battery fleet whose own rated power grew
roughly tenfold in five years, now getting markedly cheaper to add to, makes
it a real possibility within this decade rather than a remote one.

## Dispatch and study boundaries

A full-day schedule is chosen from the forecast, then settled against observed
prices. There is one charge-then-discharge episode at most, 1 MW power,
90% round-trip efficiency in the base case, a 0.25 MWh SOC grid and zero initial
and terminal SOC each day. There is **no rolling intraday re-optimization**.
Perfect foresight uses the same optimizer and constraints as an upper bound;
no trade provides an explicit zero alternative.

```js
Plot.plot({
  title: `${duration}h Ridge dispatch — ${sampleDate}`,
  width, height: 180, marginLeft: 65,
  x: {label: null, axis: null}, y: {label: "EUR/MWh", grid: true},
  marks: [Plot.lineY(sample, {x: "local_hour", y: "actual", stroke: "#333333", tip: true}), Plot.ruleY([0])],
})
```

```js
Plot.plot({
  width, height: 200, marginLeft: 65,
  x: {label: "Market-local hour"}, y: {label: "MWh", grid: true},
  color: {domain: ["State of charge", "Action"], range: ["#0072B2", "#D55E00"], legend: true},
  marks: [
    Plot.lineY(sample, {x: "local_hour", y: "soc_mwh", stroke: "State of charge", tip: true}),
    Plot.barY(sample, {x: "local_hour", y: "action_mwh", fill: "Action", fillOpacity: .55, tip: true}),
    Plot.ruleY([0]),
  ],
})
```

The economic input is the frozen forecast's clock-hour presentation table,
rounded to two decimals before dispatch. All strategies share ${coverage[0].common_days}
complete settled days out of ${coverage[0].candidate_days} candidate days. Incomplete
days and ambiguous clock-only DST days are excluded, not imputed or silently
treated as physical quarter-hour trades. Full-precision forecast scores and
rounded-input battery economics serve different, explicitly recorded purposes.

This is **retrospective development evidence**, already inspected. Forecast
parameters are not reselected by these sensitivities, and no untouched or
prospective result is claimed. Historical provider revisions are not guaranteed
to match their original publication-time values. Intraday, ancillary services,
imbalance settlement, market impact, CAPEX and financing are not modelled.
See the [forecast protocol](./forecast) and [methodology](./methodology).

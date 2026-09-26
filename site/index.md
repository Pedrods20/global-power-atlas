---
title: Market view
---

# German power: price shape and battery value

```js
const yearly = [...await FileAttachment("data/capacity_price_yearly.parquet").parquet()]
  .filter((d) => d.zone === "DE-LU")
  .sort((a, b) => a.year.localeCompare(b.year));
const shape = [...await FileAttachment("data/price_shape.parquet").parquet()];
const marginYearly = [...await FileAttachment("data/battery_margin_yearly.parquet").parquet()];
const capacity = [...await FileAttachment("data/capacity.parquet").parquet()];
const ledgerStatus = await FileAttachment("data/ledger_status.json").json();
const ridgeArm = ledgerStatus.arms.find((d) => d.model === "ridge");
const ledgerDate = ledgerStatus.as_of ? ledgerStatus.as_of.slice(0, 10) : "this build";
const yearlyCap = capacity.filter((d) => d.time_step === "yearly" && !d.is_planned);
const capAt = (technology, period) => yearlyCap.find((d) => d.technology === technology && String(d.period) === period);
const pumped = yearlyCap
  .filter((d) => d.technology === "Hydro pumped storage")
  .sort((a, b) => String(a.period).localeCompare(String(b.period)));
const pumpedFirst = pumped[0];
const pumpedLast = pumped[pumped.length - 1];
const fleetHours = (period) => {
  const power = capAt("Battery storage (power)", period);
  const energy = capAt("Battery storage (capacity)", period);
  return power && energy && power.value ? energy.value / power.value : null;
};
const first = yearly[0];
const last = yearly[yearly.length - 1];
const latestFull = yearly[yearly.length - 2];
const crisis = yearly.find((d) => d.year === "2022");
const foresight4 = marginYearly
  .filter((d) => d.strategy === "perfect_foresight" && d.energy_mwh === 4)
  .sort((a, b) => a.year.localeCompare(b.year));
const fleetFirst = foresight4[0];
const fleetLast = foresight4[foresight4.length - 1];
const foresight = (period) => foresight4.find((d) => d.year === period);
const eur = (v) => v.toLocaleString("en", {maximumFractionDigits: 0});
const num = (v) => v.toFixed(1);
const pct = (v) => `${v.toFixed(0)}%`;
const rate = (v) => v.toFixed(2);
```

This study examines changes in Germany–Luxembourg day-ahead prices and their implications for battery dispatch. It also tests how much a price forecast adds over simple scheduling strategies.

**[Forecast results](./forecast) · [Battery economics](./battery) · [Methodology](./methodology)**

## 1. The on-peak premium has turned negative

The on-peak/off-peak spread moved from **EUR ${num(first.spread)}/MWh in ${first.year}** to **EUR ${num(last.spread)}/MWh in ${last.year} YTD**. Solar capture fell from **${rate(first.solar_capture_rate)}** to **${rate(last.solar_capture_rate)}**, alongside an increase in installed solar capacity from ${num(first.solar_capacity_gw)} to ${num(last.solar_capacity_gw)} GW.

A block average can mask low midday prices and higher evening prices. Storage valuation needs to account for when those prices occur and how long they last.

## 2. Daily spreads remain relevant for storage

```js
const spreadSeries = yearly.flatMap((d) => [
  {year: d.year, metric: "On-peak minus off-peak block", value: d.spread_pct_of_price},
  {year: d.year, metric: "Within-day high minus low", value: d.intraday_spread_pct_of_price},
]);
```

```js
Plot.plot({
  title: "Two spreads, one market, opposite directions",
  subtitle: "Each as a percentage of that year's own average price, so a price-level shock cannot flatter either series. 2026 is a partial year.",
  width, height: 340, marginLeft: 60, marginBottom: 40,
  x: {type: "band", label: null},
  y: {label: "% of that year's baseload price", grid: true},
  color: {legend: true, domain: ["On-peak minus off-peak block", "Within-day high minus low"], range: ["#CC79A7", "#0072B2"]},
  marks: [
    Plot.lineY(spreadSeries, {x: "year", y: "value", stroke: "metric", strokeWidth: 2.5, marker: "circle", tip: true}),
    Plot.ruleY([0]),
  ],
})
```

The average daily high–low range increased from **${pct(first.intraday_spread_pct_of_price)} of baseload in ${first.year}** to **${pct(last.intraday_spread_pct_of_price)} in ${last.year} YTD**. This is a measure of price dispersion, not a directly achievable battery margin: dispatch also depends on sequence, duration, efficiency and asset constraints.

```js
const shapeYears = [first.year, latestFull.year, last.year];
const shapeRows = shape.filter((d) => shapeYears.includes(d.year));
```

```js
Plot.plot({
  title: "Where the money moved inside the day",
  subtitle: "Mean price by market-local clock hour, indexed to each year's own average price. 100 is that year's baseload.",
  width, height: 340, marginLeft: 60,
  x: {label: "hour, market local time", ticks: [0, 4, 8, 12, 16, 20, 23], grid: true},
  y: {label: "% of that year's baseload price", grid: true},
  color: {legend: true, domain: shapeYears, range: ["#999999", "#E69F00", "#0072B2"]},
  marks: [
    Plot.lineY(shapeRows, {x: "local_hour", y: "pct_of_baseload", stroke: "year", strokeWidth: 2.5, tip: true}),
    Plot.ruleY([100], {strokeDasharray: "3,3"}),
  ],
})
```

The hourly profile shows lower midday prices and a more pronounced evening premium. To assess the value of that shape, the table below models a **1 MW / 4 MWh battery** with perfect foresight and at most one daily charge–discharge episode. These margins are an upper bound under the model assumptions, before operating, degradation and capital costs.

```js
Inputs.table(
  foresight4.map((d) => ({
    Year: d.year === last.year ? `${d.year} (partial)` : d.year,
    "EUR/MW/d": Math.round(d.eur_per_mw_day),
    GW: d.battery_power_gw,
    GWh: d.battery_energy_gwh,
  })),
  {format: {GW: num, GWh: num}, layout: "auto", rows: 12}
)
```

Perfect-foresight margin was **EUR ${eur(foresight(crisis.year).eur_per_mw_day)}/MW/day in ${crisis.year}**, compared with **EUR ${eur(fleetLast.eur_per_mw_day)}/MW/day in ${last.year} YTD**. The latter period retains substantial modelled arbitrage value despite a lower average price level. This historical comparison does not establish future bankability.

## 3. Storage competition needs a closer look

Germany’s recorded battery fleet reached **${num(fleetLast.battery_power_gw)} GW / ${num(fleetLast.battery_energy_gwh)} GWh**, equivalent to **${fleetHours(last.year).toFixed(2)} hours** of average duration. Pumped hydro adds approximately **${num(pumpedLast.value)} GW** of installed capacity.

```js
Inputs.table(
  ["2022", "2023", "2024", "2025", last.year].map((period) => ({
    Year: period === last.year ? `${period} (partial)` : period,
    GW: capAt("Battery storage (power)", period).value,
    GWh: capAt("Battery storage (capacity)", period).value,
    Hours: fleetHours(period),
  })),
  {
    format: {
      GW: num,
      GWh: num,
      Hours: (v) => v.toFixed(2),
    },
    layout: "auto",
    rows: 6,
  }
)
```

The battery series does not separate residential from grid-scale assets. Average duration alone cannot identify that split or the capacity actively competing for day-ahead spreads. These aggregate data also cannot isolate the effect of storage growth from weather, fuel prices and other market changes.

## 4. Forecast accuracy adds modest dispatch value

```js
const meta = await FileAttachment("data/forecast.json").json();
const run = meta.runs.find((d) => d.zone === "DE-LU");
const scores = [...await FileAttachment("data/forecast_scores.parquet").parquet()];
const overall = scores.filter((d) => d.scope === "overall");
const ridgeScore = overall.find((d) => d.model === "ridge");
const bestBaseline = overall
  .filter((d) => d.model.startsWith("naive_"))
  .sort((a, b) => a.mae - b.mae)[0];
const sens = [...await FileAttachment("data/battery_sensitivities.parquet").parquet()];
const base4 = sens.find((d) => d.strategy === "ridge" && d.energy_mwh === 4 && d.scenario === "base");
const sampleYears = base4.days / 365.25;
```

Ridge reduced hourly mean absolute error by **${num(run.best_skill_vs_best_baseline_pct)}%** versus the strongest naive price benchmark: **EUR ${num(ridgeScore.mae)}/MWh**, compared with **EUR ${num(bestBaseline.mae)}/MWh**.

For the 1 MW / 4 MWh battery, Ridge-based dispatch added approximately **EUR ${eur(base4.incremental_vs_best_naive_eur_mw / sampleYears)}/MW/year** over the strongest fixed naive dispatch strategy. That increment represents **${pct((base4.incremental_vs_best_naive_eur_mw / base4.profit_eur) * 100)}** of Ridge’s modelled gross margin. The price and dispatch benchmarks are selected separately.

Ridge underperformed that dispatch comparator on **${base4.underperform_days} of ${base4.days} days**. Most of the historical margin was already captured by a simple strategy; the forecast’s incremental contribution needs to be assessed against costs and downside.

[Compare the forecasts](./forecast) · [Review dispatch results and sensitivities](./battery)

## Risks to the outlook

- **Fuel prices and weather:** changes in residual demand and marginal generation costs can alter both midday and evening prices.
- **Competing flexibility:** storage, demand response and cross-border flows can reduce the spreads available to an individual asset.
- **Market design:** product resolution and support arrangements affect price formation and the relevance of an hourly benchmark.

## Scope and evidence

This is a retrospective development study on history already inspected during model development. Historical inputs contain provider revisions; their exact availability at past auction gates cannot be verified.

The forecast uses lagged prices, calendar features and residual load lagged by at least two delivery days. The target is an hourly price average. The battery headline assumes 90% round-trip efficiency, one charge–discharge episode per day and zero initial and terminal state of charge. Margins exclude operating, degradation and capital costs; separate sensitivities test illustrative variable costs. Intraday and balancing revenues are outside scope.

**${last.year} is a partial year.** Comparisons with complete years are sensitive to seasonality and should not be read as full-year forecasts.

**Prospective monitoring:** as of ${ledgerDate}, the published arm has issued forecasts for ${ridgeArm ? ridgeArm.issued : 0} of ${ridgeArm ? ridgeArm.days : 0} attempted delivery days. This pilot is reported separately from the historical results. [Protocol and current status](./forecast).

**Sources:** Energy-Charts / Fraunhofer ISE, including figures redistributed from ENTSO-E and SMARD. [Definitions, assumptions and attribution](./methodology).

## About me

**Pedro Cabral** — power-market research focused on fundamentals, price formation and the commercial implications of the energy transition. My regional experience covers Brazil, Chile and Argentina; this independent project applies that analytical approach to European power and storage.

[Research repository](https://github.com/Pedrods20/german-power-research) · [GitHub profile](https://github.com/Pedrods20)

## Market context

```js
const currency = await FileAttachment("data/data_as_of.json").json();
const dailyPrices = [...await FileAttachment("data/daily_prices.parquet").parquet()];
const mix = [...await FileAttachment("data/generation_mix.parquet").parquet()];
const priceRows = dailyPrices.filter((d) => String(d.zone) === "DE-LU");
const mixRows = mix.filter((d) => String(d.zone) === "DE-LU" && d.fuel !== "imports");
const fuelColor = {coal: "#5A4632", gas: "#56B4E9", oil: "#000000", nuclear: "#CC79A7", hydro: "#0072B2", hydro_pumped_storage: "#7FB3D5", wind: "#009E73", solar: "#F0E442", biomass: "#8B6F47", geothermal: "#B15928", waste: "#999999", battery: "#E69F00", other: "#BBBBBB"};
const fuelOrder = Object.keys(fuelColor);
```

Data through **${currency.data_as_of ? currency.data_as_of.slice(0, 10) : "the latest monthly export"}**.

```js
Plot.plot({
  title: "DE-LU daily average day-ahead price",
  subtitle: "All hours. The 2021-22 gas shock is a level event; the shape change above is not.",
  width, height: 280, marginLeft: 55,
  x: {type: "utc", label: null}, y: {label: "EUR/MWh", grid: true},
  marks: [Plot.lineY(priceRows, {x: (d) => new Date(d.date), y: "all_hours", stroke: "#0072B2", tip: true}), Plot.ruleY([0])],
})
```

```js
Plot.plot({
  title: "DE-LU monthly generation mix",
  subtitle: "Share of generated energy; imports and storage are not treated as primary generation.",
  width, height: 320, marginLeft: 55,
  x: {type: "band", label: null, tickRotate: -40}, y: {label: "% of generation", grid: true, domain: [0, 100]},
  color: {domain: fuelOrder, range: fuelOrder.map((f) => fuelColor[f]), legend: true},
  marks: [Plot.barY(mixRows, {x: "month", y: "share_pct", fill: "fuel", order: fuelOrder, tip: true}), Plot.ruleY([0])],
})
```

<div class="note">Historical data is refreshed monthly. Missing observations remain missing; the charts never turn a provider gap into zero.</div>

<style>
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: .5rem 0 .5rem 1rem;
  color: var(--theme-foreground-muted);
}
</style>

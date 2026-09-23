---
title: Market view
---

# Solar killed Germany's peak premium. The spread a battery earns doubled.

```js
const yearly = [...await FileAttachment("data/capacity_price_yearly.parquet").parquet()]
  .filter((d) => d.zone === "DE-LU")
  .sort((a, b) => a.year.localeCompare(b.year));
const shape = [...await FileAttachment("data/price_shape.parquet").parquet()];
const marginYearly = [...await FileAttachment("data/battery_margin_yearly.parquet").parquet()];
const first = yearly[0];
const last = yearly[yearly.length - 1];
const latestFull = yearly[yearly.length - 2];
const crisis = yearly.find((d) => d.year === "2022");
const foresight4 = marginYearly
  .filter((d) => d.strategy === "perfect_foresight" && d.energy_mwh === 4)
  .sort((a, b) => a.year.localeCompare(b.year));
const fleetFirst = foresight4[0];
const fleetLast = foresight4[foresight4.length - 1];
const eur = (v) => v.toLocaleString("en", {maximumFractionDigits: 0});
const num = (v) => v.toFixed(1);
const pct = (v) => `${v.toFixed(0)}%`;
const rate = (v) => v.toFixed(2);
```

Germany's day-ahead price no longer pays a premium for the hours the market used
to call peak. It pays for the hours solar cannot reach. That is not a smaller
opportunity for flexibility — it is a larger one, and the two facts get confused
because they show up in different statistics.

This page makes the case from primary system-operator data, then tests it the
only way that settles it: by dispatching a battery against realised prices and
measuring what the shape is actually worth.

---

## 1. The peak premium is gone

On-peak hours in DE-LU were worth **EUR ${num(first.spread)}/MWh** more than
off-peak in ${first.year}. In ${last.year} they are worth **EUR
${num(last.spread)}/MWh** — the on-peak block now clears *below* the hours
around it. Solar's capture rate, what a solar plant earns against the average
price, fell from **${rate(first.solar_capture_rate)}** to
**${rate(last.solar_capture_rate)}** while installed capacity went from
${num(first.solar_capacity_gw)} to ${num(last.solar_capacity_gw)} GW. Hours
below zero went from ${num(first.negative_pct)}% of the year to
**${num(last.negative_pct)}%**.

Wind did not do this to itself: its capture rate is
${rate(first.wind_capture_rate)} then and ${rate(last.wind_capture_rate)} now.
Wind blows across the day and across the seasons. Solar arrives in the same six
hours every day, in every plant at once, and that concentration is the whole
mechanism.

## 2. The trade a battery makes got bigger

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

The block spread and the within-day range answer different questions. A fixed
block contract pays the first. A battery is paid the second, because it charges
at the day's low and discharges at its high wherever in the day those fall.

Scaled by each year's own price level, the within-day range went from
**${pct(first.intraday_spread_pct_of_price)}** of baseload in ${first.year} to
**${pct(last.intraday_spread_pct_of_price)}** in ${last.year}. The gas crisis is
the control that makes this readable: ${crisis.year} was by far the most
expensive year in the sample, and its range was
**${pct(crisis.intraday_spread_pct_of_price)}** of baseload — no wider, in
relative terms, than ${first.year}. **${crisis.year} was a price-level shock.
What has happened since is a change in shape**, and shape is what storage is
paid for.

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

In ${first.year} the German day was a plateau: daytime a little above average,
night below it. It is now a trough between two peaks. The midday hours solar
floods have fallen under the night, and the evening ramp — when solar has gone
and demand has not — is the most expensive part of the day by a wider margin
than before. A battery charges in a trough that did not used to exist and
discharges into a peak that got sharper.

Dispatching a 1 MW / 4 MWh battery against realised DE-LU prices with perfect
foresight and one cycle a day puts a number on it:

```js
Inputs.table(
  foresight4.map((d) => ({
    Year: d.year === last.year ? `${d.year} (partial)` : d.year,
    "EUR/MW/day": Math.round(d.eur_per_mw_day),
    "Fleet GW": d.battery_power_gw,
    "Fleet GWh": d.battery_energy_gwh,
  })),
  {format: {"Fleet GW": num, "Fleet GWh": num}, layout: "auto", rows: 12}
)
```

## 3. Competition has not arrived — and the fleet's duration says why

Germany's battery fleet grew from ${num(fleetFirst.battery_power_gw)} GW to
**${num(fleetLast.battery_power_gw)} GW** across this sample and the arbitrage
value per MW did not compress. It rose.

The fleet's own numbers suggest the reason. At
${num(fleetLast.battery_power_gw)} GW and ${num(fleetLast.battery_energy_gwh)}
GWh, its average duration is about
**${(fleetLast.battery_energy_gwh / fleetLast.battery_power_gw).toFixed(1)}
hours** — the signature of household storage behind the meter, sized to shift a
household's own evening consumption, not of grid-scale assets bidding into the
same midday trough this study trades. Capacity counted in gigawatts is not the
same as capacity competing for this spread.

This is an observation about a growing fleet, not a fitted competition model.
Annual data cannot separate a fleet effect from the gas-price unwind or the
weather. What it does establish is that compression has not yet reached the
price at a fleet size where a shallow-duration explanation fits the data better
than the absence of any effect at all.

---

## Does forecasting this shape pay? Partly — and that is the finding.

The shape is only worth something to an operator who knows in advance which
hours are which. That is a separate, testable claim, and it is where most of
this project's engineering went.

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

A per-hour ridge regression, using only what a bidder holds at the 12:00 gate on
the day before delivery, cuts day-ahead price error by
**${num(run.best_skill_vs_best_baseline_pct)}%** against the best naive
alternative — EUR ${num(ridgeScore.mae)}/MWh against EUR
${num(bestBaseline.mae)}/MWh, over ${run.scored_hours.toLocaleString("en")}
scored hours.

Converted into dispatch, that skill is worth **EUR
${eur(base4.incremental_vs_best_naive_eur_mw / sampleYears)}/MW per year**
(EUR ${eur(base4.mean_daily_incremental_eur_mw)}/MW per day) more than repeating
the last similar day — roughly
**${pct((base4.incremental_vs_best_naive_eur_mw / base4.profit_eur) * 100)}** of
the battery's gross margin. The naive strategy earns the rest, because most of
the value sits in the shape, which repeats, rather than in the day-to-day
deviation, which is what a forecast adds. Ridge loses to that naive on
**${base4.underperform_days} of ${base4.days} days**.

That ratio is the honest commercial summary, and it points away from where the
effort went: **the shape is the asset; the forecast is a margin on top of it.**
Protocol and failure modes on the [forecast page](./forecast); dispatch, costs,
stresses and downside on the [storage page](./battery).

## What would make this wrong

- **The gas premium unwinds further.** Part of the widened range is still an
  expensive, volatile gas stack setting the evening price. A cheaper marginal
  unit compresses the top of the day without touching the midday trough.
- **The fleet's duration lengthens.** Section 3 rests on German storage being
  short and domestic. Grid-scale 2–4h additions attack exactly the trough this
  trade depends on, and that build-out is underway.
- **Market design changes.** The move to 15-minute day-ahead products in October
  2025, and any change to negative-price support rules, alter both the trade and
  the measurement of it.
- **This is already-inspected history.** It is development evidence from a
  frozen, reproducible release — not an untouched holdout and not a prospective
  record. A separately recorded prospective ledger is running and is not yet
  long enough to score.

## Premises

The forecast may use only information held at **12:00 market time on D-1**, when
the day-ahead order book closes: lagged prices, calendar features and residual
load lagged at least two delivery days. The target is the duration-weighted
local clock-hour price, an analytical benchmark rather than a per-quarter-hour
trade. The asset is a deliberately simple 1 MW battery at 1, 2 or 4 MWh, 90%
round-trip, starting and ending each day empty; the headline allows one
charge-then-discharge episode a day, with a two-episode case run and reported
separately. The base case carries zero operating and degradation cost, and
non-zero cases are rerun rather than folded in. Day-ahead arbitrage alone is a
**lower bound** on a German battery's revenue: intraday and balancing markets
are outside this study. Full definitions on the
[methodology page](./methodology).

**Sources.** Prices, load, generation and installed capacity come from
Energy-Charts (Fraunhofer ISE, CC BY 4.0), which republishes ENTSO-E and SMARD
figures; Brazilian system data comes from ONS. Two figures on the storage page
are external citations — BloombergNEF and the Bundesnetzagentur — dated where
they appear.

**Built solo, end to end:** ingestion across two public system-data providers
covering four markets, leakage-safe walk-forward forecasting, the constrained
dispatch and stress-testing engine, and this site.
[github.com/Pedrods20](https://github.com/Pedrods20).

---

## Market context

```js
const zones = await FileAttachment("data/zones.json").json();
const dailyPrices = [...await FileAttachment("data/daily_prices.parquet").parquet()];
const mix = [...await FileAttachment("data/generation_mix.parquet").parquet()];
const priceRows = dailyPrices.filter((d) => String(d.zone) === "DE-LU");
const mixRows = mix.filter((d) => String(d.zone) === "DE-LU" && d.fuel !== "imports");
const fuelColor = {coal: "#5A4632", gas: "#56B4E9", oil: "#000000", nuclear: "#CC79A7", hydro: "#0072B2", hydro_pumped_storage: "#7FB3D5", wind: "#009E73", solar: "#F0E442", biomass: "#8B6F47", geothermal: "#B15928", waste: "#999999", battery: "#E69F00", other: "#BBBBBB"};
const fuelOrder = Object.keys(fuelColor);
```

Data through **${zones.data_as_of ? zones.data_as_of.slice(0, 10) : "the latest monthly export"}**.

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
main.observablehq > table { display: block; max-width: 100%; overflow-x: auto; }
</style>

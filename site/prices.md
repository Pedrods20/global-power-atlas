---
title: Prices
---

# Prices

How price behaves, not only where it sits. Four things decide whether a market pays for the plant connected to it: where the block spread sits, how fat the upper tail is, how often price falls below zero, and how fast it moves.

```js
const dailyPrices = await FileAttachment("data/daily_prices.parquet").parquet();
const durationCurve = await FileAttachment("data/price_duration.parquet").parquet();
const negatives = await FileAttachment("data/negative_prices.parquet").parquet();
const volatility = await FileAttachment("data/volatility.parquet").parquet();

const prices = [...dailyPrices];
const duration = [...durationCurve];
const negativeRows = [...negatives];
const volRows = [...volatility];

const zoneColor = new Map([
  ["DE-LU", "#0072B2"],
  ["FR", "#332288"],
  ["ES", "#88CCEE"],
]);
const zoneOrder = [...zoneColor.keys()].filter((z) => prices.some((p) => p.zone === z));
const colorScale = {
  domain: zoneOrder,
  range: zoneOrder.map((z) => zoneColor.get(z)),
  legend: true,
};
const currencyOf = new Map(prices.map((d) => [d.zone, d.currency]));
```

## Peak against off-peak

The spread below is the mean of on-peak intervals minus the mean of off-peak intervals, where membership follows each market's own block rule. European peakload runs 08:00 to 20:00 CET, Monday to Friday, with public holidays not adjusted for.

This is not the daily maximum minus the daily minimum. That quantity is intraday range, it is much larger, and it is the most common thing mislabelled as a peak spread.

```js
Plot.plot({
  title: "On-peak minus off-peak, daily",
  subtitle: "Below zero means the peak block cleared cheaper than the hours around it.",
  width,
  height: 340,
  marginLeft: 55,
  x: { type: "utc", label: null },
  y: { label: "spread, per MWh", grid: true },
  color: colorScale,
  marks: [
    Plot.areaY(prices, {
      x: (d) => new Date(d.date),
      y: "spread",
      fill: "zone",
      z: (d) => `${d.zone}-${d.segment}`,
      fillOpacity: 0.12,
      curve: "step",
    }),
    Plot.lineY(prices, {
      x: (d) => new Date(d.date),
      y: "spread",
      stroke: "zone",
      z: (d) => `${d.zone}-${d.segment}`,
      strokeWidth: 1,
      tip: true,
    }),
    Plot.ruleY([0], { stroke: "currentColor" }),
  ],
})
```

<div class="note">

**A negative spread is a market state, not a data error.** When enough solar is installed, midday output pushes the middle of the day below the shoulders, and the on-peak block ends up cheaper than off-peak. Germany and Spain both reach this routinely now. It is the mechanism that erodes solar's own revenue, and it is quantified as the [capture rate](./supply).

</div>

## The price duration curve

Sort every settlement interval from most to least expensive and plot it against the share of time. The left edge is scarcity, and a market that recovers fixed costs in a handful of hours lives there. Where the curve crosses zero on the right is renewable surplus, and the width of that crossing is the clearest single measure of how often the system has more must-run output than demand.

```js
const logScale = view(Inputs.toggle({label: "Symlog y-axis", value: true}));
```

```js
Plot.plot({
  title: "Price duration curve",
  subtitle: "Every interval held for each zone, sorted descending.",
  width,
  height: 380,
  marginLeft: 60,
  x: { label: "% of observed time at or above this price", grid: true, domain: [0, 100] },
  y: {
    label: "per MWh",
    grid: true,
    type: logScale ? "symlog" : "linear",
    constant: 20,
  },
  color: colorScale,
  marks: [
    Plot.ruleY([0], { stroke: "currentColor", strokeOpacity: 0.5 }),
    Plot.lineY(duration, {
      x: "exceedance_pct",
      y: "price",
      stroke: "zone",
      strokeWidth: 1.4,
      tip: true,
    }),
  ],
})
```

<div class="note">

The symlog axis is on by default because a linear one compresses the whole interesting middle of the curve into a flat line in order to show a handful of scarcity intervals. Symlog keeps both tails legible and, unlike a log axis, handles the negative prices rather than dropping them.

</div>

## Negative prices

```js
Plot.plot({
  title: "Intervals priced below zero, by month",
  subtitle: "Frequency tracks renewable penetration against system flexibility.",
  width,
  height: 300,
  marginLeft: 55,
  x: { label: null, tickRotate: -40 },
  y: { label: "% of observed time", grid: true },
  color: colorScale,
  marks: [
    // One line per zone rather than stacked bars. Stacking would add two
    // markets' percentages together, and that sum means nothing.
    Plot.lineY(negativeRows, {
      x: "local_month",
      y: "negative_pct",
      stroke: "zone",
      strokeWidth: 2,
      marker: "circle",
      tip: true,
    }),
    Plot.ruleY([0]),
  ],
})
```

Frequency alone understates the problem. Many scattered negative intervals are a nuisance; one long unbroken run is a curtailment event, because a thermal unit that shuts down cannot come back within the hour. The table below reports the longest run in each month alongside the count.

```js
const negTable = negativeRows
  .filter((d) => d.n_negative > 0)
  .map((d) => ({
    Zone: d.zone,
    Month: d.local_month,
    "Negative intervals": d.n_negative,
    "% of month": Math.round(d.negative_pct * 10) / 10,
    "Hours below zero": Math.round(d.negative_hours),
    "Longest run (h)": Math.round(d.max_run_hours * 10) / 10,
    "Deepest": Math.round(d.min_price * 10) / 10,
    "Mean when negative": Math.round(d.mean_negative * 10) / 10,
  }))
  .sort((a, b) => d3.descending(a["Longest run (h)"], b["Longest run (h)"]));
```

```js
Inputs.table(negTable, { rows: 14, layout: "auto" })
```

## Volatility

Volatility here is the rolling 30-day standard deviation of the day-on-day change in daily mean price, annualised by the square root of 365.

Two choices differ from financial convention and both are deliberate. The return is an arithmetic difference rather than a log return, because price reaches zero and goes negative and a log return is undefined exactly there. The annualisation uses 365 days rather than the 252 trading days of an exchange, because spot electricity settles every day of the year. Using 252 understates annualised volatility by about twenty percent.

```js
Plot.plot({
  title: "Rolling 30-day volatility, annualised",
  subtitle: "In currency per MWh, so the level is interpretable rather than a percentage of an undefined base.",
  width,
  height: 320,
  marginLeft: 55,
  x: { type: "utc", label: null },
  y: { label: "per MWh, annualised", grid: true },
  color: colorScale,
  marks: [
    Plot.lineY(volRows.filter((d) => d.volatility != null), {
      x: (d) => new Date(d.date),
      y: "volatility",
      stroke: "zone",
      strokeWidth: 1.2,
      tip: true,
    }),
    Plot.ruleY([0]),
  ],
})
```

## Block summary

```js
const byZoneMonth = d3.rollups(
  prices,
  (v) => ({
    on: d3.mean(v, (d) => d.on_peak),
    off: d3.mean(v, (d) => d.off_peak),
    all: d3.mean(v, (d) => d.all_hours),
  }),
  (d) => d.zone,
).map(([zone, m]) => ({
  Zone: zone,
  Currency: currencyOf.get(zone),
  "On-peak": Math.round(m.on * 100) / 100,
  "Off-peak": Math.round(m.off * 100) / 100,
  "Spread": Math.round((m.on - m.off) * 100) / 100,
  "All hours": Math.round(m.all * 100) / 100,
}));
```

```js
Inputs.table(byZoneMonth, { layout: "auto" })
```

<div class="note">

Averages across the whole period held for each zone, in each market's own currency. They are not comparable across rows as levels. The spread and its sign are comparable, because both are differences within one market.

</div>

<style>
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.4rem 0 0.4rem 1rem;
  margin: 1.5rem 0;
  color: var(--theme-foreground-muted);
  font-size: 0.92rem;
}
.note strong { color: var(--theme-foreground); }
</style>

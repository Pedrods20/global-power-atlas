---
title: Demand
---

# Demand

Demand is the half of the market that does not respond to price. Its shape decides how much capacity a system must carry, and the shape differs far more between markets than the level does.

```js
const dailyLoad = await FileAttachment("data/daily_load.parquet").parquet();
const profile = await FileAttachment("data/load_profile.parquet").parquet();
const factor = await FileAttachment("data/load_factor.parquet").parquet();

const load = [...dailyLoad];
const profileRows = [...profile];
const factorRows = [...factor];

const zoneColor = new Map([
  ["ERCOT", "#D55E00"],
  ["DE-LU", "#0072B2"],
  ["BR-SIN", "#009E73"],
  ["AU-NSW1", "#CC79A7"],
]);
const zoneOrder = [...zoneColor.keys()].filter((z) => load.some((d) => d.zone === z));
const colorScale = {
  domain: zoneOrder,
  range: zoneOrder.map((z) => zoneColor.get(z)),
  legend: true,
};
```

## The daily load curve

Average demand by local hour of day. This is the shape most people mean by "the load curve": the morning ramp, the midday plateau or dip, and the evening peak.

Each series is normalised to its own mean so that a 7 GW market and an 80 GW market share an axis. The vertical distance between the peak and the trough is what a system has to build for and cannot sell most of the time.

```js
Plot.plot({
  title: "Average demand by local hour",
  subtitle: "Normalised to each market's own mean. Hours are in each market's own trading time, not UTC.",
  width,
  height: 340,
  marginLeft: 55,
  x: { label: "hour of local day", domain: [0, 23], grid: true, ticks: 12 },
  y: { label: "relative to market mean", grid: true },
  color: colorScale,
  marks: [
    Plot.ruleY([1], { stroke: "currentColor", strokeOpacity: 0.35, strokeDasharray: "3,3" }),
    Plot.lineY(profileRows, {
      x: "local_hour",
      y: "normalised",
      stroke: "zone",
      strokeWidth: 2,
      curve: "catmull-rom",
      tip: true,
    }),
  ],
})
```

<div class="note">

**Local hour, not UTC hour.** Grouping by UTC calendar hour would place the Australian evening peak in the middle of the chart and the Brazilian one three hours off. The Australian series uses Australian Eastern Standard Time year-round, because that is what AEMO settles on, not the civil clock in Sydney.

</div>

## Load duration and load factor

Load factor is average demand divided by peak demand. A high number means a flat system that baseload plant can serve. A low number means a peaky system whose capacity sits idle most of the time, so it needs more installed megawatts for every megawatt-hour it delivers.

It is the cleanest single number for comparing demand shape independently of market size, and it is the number that decides how expensive a system is to serve.

```js
Plot.plot({
  title: "Monthly load factor",
  subtitle: "Average demand divided by peak demand, within each local month.",
  width,
  height: 320,
  marginLeft: 55,
  x: { label: null, tickRotate: -40 },
  y: { label: "load factor", grid: true, domain: [0, 1], percent: false },
  color: colorScale,
  marks: [
    Plot.lineY(factorRows, {
      x: "month",
      y: "load_factor",
      stroke: "zone",
      strokeWidth: 2,
      marker: "circle",
      tip: true,
    }),
  ],
})
```

```js
const factorSummary = d3.rollups(
  factorRows.filter((d) => d.load_factor != null),
  (v) => ({
    lf: d3.mean(v, (d) => d.load_factor),
    peak: d3.max(v, (d) => d.peak_load_mw),
    avg: d3.mean(v, (d) => d.avg_load_mw),
  }),
  (d) => d.zone,
).map(([zone, m]) => ({
  Zone: zone,
  "Mean load factor": Math.round(m.lf * 1000) / 1000,
  "Average demand (MW)": Math.round(m.avg),
  "Peak demand (MW)": Math.round(m.peak),
  "Idle capacity at peak (MW)": Math.round(m.peak - m.avg),
}));
```

```js
Inputs.table(factorSummary, { layout: "auto" })
```

## Energy consumed

```js
Plot.plot({
  title: "Daily energy consumed",
  subtitle: "Power integrated over each interval's real duration, so a 23-hour or 25-hour clock-change day is measured rather than assumed.",
  width,
  height: 320,
  marginLeft: 60,
  x: { type: "utc", label: null },
  y: { label: "GWh per day", grid: true, transform: (d) => d / 1000 },
  color: colorScale,
  marks: [
    Plot.lineY(load, {
      x: (d) => new Date(d.date),
      y: "energy_mwh",
      stroke: "zone",
      strokeWidth: 1,
      tip: true,
    }),
  ],
})
```

## Seasonality

```js
const seasonal = load.map((d) => ({
  ...d,
  month: +d.date.slice(5, 7),
  year: d.date.slice(0, 4),
}));
```

```js
Plot.plot({
  title: "Demand by calendar month, relative to each market's annual mean",
  subtitle: "Northern and southern hemisphere seasons run in opposite directions, which is why a global average of raw demand means nothing.",
  width,
  height: 320,
  marginLeft: 55,
  x: { label: "month", domain: d3.range(1, 13), tickFormat: (m) => "JFMAMJJASOND"[m - 1] },
  y: { label: "relative to market mean", grid: true },
  color: colorScale,
  marks: [
    Plot.ruleY([1], { stroke: "currentColor", strokeOpacity: 0.35, strokeDasharray: "3,3" }),
    Plot.lineY(
      seasonal,
      Plot.groupX(
        { y: "mean" },
        {
          x: "month",
          y: "energy_mwh",
          stroke: "zone",
          strokeWidth: 2,
          curve: "catmull-rom",
          tip: true,
        },
      ),
    ),
  ],
  // Normalising inside the plot would need a second pass; the mean line above
  // makes the relative reading legible without one.
})
```

<div class="note">

Brazil and Australia peak in the southern summer, Germany in the northern winter, and ERCOT in the northern summer. Averaging raw demand across them produces a flat line that describes no system. That is why every cross-market figure on this site is either normalised or kept in separate series.

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

---
title: Global Power Atlas
toc: false
---

```js
const zones = await FileAttachment("data/zones.json").json();
const freshnessTable = await FileAttachment("data/freshness.parquet").parquet();
const dailyPrices = await FileAttachment("data/daily_prices.parquet").parquet();
const dailyLoad = await FileAttachment("data/daily_load.parquet").parquet();
const mix = await FileAttachment("data/generation_mix.parquet").parquet();
const negatives = await FileAttachment("data/negative_prices.parquet").parquet();

const prices = [...dailyPrices];
const load = [...dailyLoad];
const mixRows = [...mix];
const negativeRows = [...negatives];
// Age is computed here, not baked into the export, so it is right at the
// moment the page is opened rather than at the moment it was built.
const freshnessRows = [...freshnessTable].map((d) => {
  const lag = d.last_ts_utc
    ? Math.max(0, (Date.now() - new Date(d.last_ts_utc)) / 3600000)
    : null;
  return { ...d, lag_hours: lag, stale: lag === null || lag > d.max_lag_hours };
});
```

```js
// One colour per zone, reused on every chart on every page so a reader learns
// the mapping once. Okabe-Ito, which stays distinguishable for the most common
// forms of colour vision deficiency.
const zoneColor = new Map([
  ["ERCOT", "#D55E00"],
  ["DE-LU", "#0072B2"],
  ["BR-SIN", "#009E73"],
  ["AU-NSW1", "#CC79A7"],
  ["PJM", "#E69F00"],
  ["CAISO", "#56B4E9"],
  ["FR", "#332288"],
  ["ES", "#88CCEE"],
  ["JP-TOKYO", "#AA4499"],
  ["BR-SECO", "#117733"],
  ["BR-NE", "#999933"],
]);

const zoneOrder = [...zoneColor.keys()].filter((z) => prices.some((p) => p.zone === z));
const colorScale = {
  domain: zoneOrder,
  range: zoneOrder.map((z) => zoneColor.get(z)),
  legend: true,
};

const fuelColor = {
  coal: "#5A4632", gas: "#56B4E9", oil: "#000000", nuclear: "#CC79A7",
  hydro: "#0072B2", hydro_pumped_storage: "#7FB3D5", wind: "#009E73",
  solar: "#F0E442", biomass: "#8B6F47", geothermal: "#B15928",
  waste: "#999999", battery: "#E69F00", other: "#BBBBBB",
};

const fuelOrder = Object.keys(fuelColor);
```

<div class="hero">
  <h1>Global Power Atlas</h1>
  <h2>Supply, demand and price across major power markets, rebuilt from primary and documented public sources.</h2>
</div>

Wholesale electricity is not one market. It is dozens of them, each with its own trading day, settlement interval and definition of a peak hour. This site normalises selected markets onto one model while retaining their currencies, time zones and source boundaries.

Every number here is computed in the market's own local time, over the interval duration the operator actually published, with negative prices left in. The [methodology](./methodology) says exactly how, and what each figure does not mean.

## Data freshness

```js
const stale = freshnessRows.filter((d) => d.stale);
const manualStale = stale.filter((d) => d.manual);
const scheduledStale = stale.filter((d) => !d.manual);
const worst = d3.max(freshnessRows, (d) => d.lag_hours ?? 0);
```

```js
html`<div class="freshness ${stale.length ? "freshness-warn" : "freshness-ok"}">
  ${stale.length === 0
    ? html`<strong>All ${freshnessRows.length} series are current.</strong>
        The oldest is ${Math.round(worst)} hours behind real time, which is inside
        the limit set for its provider.`
    : html`<strong>${stale.length} of ${freshnessRows.length} series are past their limit.</strong>
        ${scheduledStale.length
          ? `${scheduledStale.length} on the scheduled feed: ${scheduledStale.map((d) => d.zone + " " + d.dataset).join(", ")}. `
          : ""}
        ${manualStale.length
          ? `${manualStale.length} refreshed by hand: ${manualStale.map((d) => d.zone + " " + d.dataset).join(", ")}.`
          : ""}`}
</div>`
```

Providers do not publish at the same speed, so each series carries its own
limit rather than one global threshold. Brazilian generation is allowed four
days because the ONS hourly balance trails real time by about two. The two CCEE
price zones are allowed a month because the provider refuses automated clients
and they are imported by hand. The [methodology](./methodology) sets out each
limit and the reason behind it.

```js
Plot.plot({
  title: "Age against each series' own freshness limit",
  subtitle: "Past the dashed line is a series older than its provider's expected lag allows.",
  width,
  height: 420,
  marginLeft: 160,
  x: { label: "% of the series' limit", grid: true },
  y: { label: null },
  color: { domain: [false, true], range: ["#0072B2", "#D55E00"], legend: false },
  marks: [
    Plot.barX(freshnessRows.filter((d) => d.lag_hours != null), {
      x: (d) => (d.lag_hours / d.max_lag_hours) * 100,
      y: (d) => d.zone + " " + d.dataset,
      fill: "stale",
      sort: { y: "x", reverse: true },
      tip: true,
    }),
    Plot.ruleX([100], { stroke: "currentColor", strokeDasharray: "3,3" }),
    Plot.ruleX([0]),
  ],
})
```

## Market coverage

```js
const zoneRows = zones.zones.map((z) => {
  const parts = Object.entries(z.datasets);
  const ok = parts.filter(([, d]) => d.status === "ok");
  const freshest = ok.length ? Math.min(...ok.map(([, d]) => d.last ? Math.max(0, (Date.now() - new Date(d.last.replace(" ", "T"))) / 3600000) : Infinity)) : null;
  return {
    Zone: z.code,
    Market: z.name,
    Region: z.region,
    Operator: z.operator,
    "Market time": z.timezone + (z.observes_market_dst ? "" : " (no DST)"),
    Currency: z.currency,
    Datasets: `${ok.length} of ${parts.length}`,
    "Freshest (h)": freshest === null || !isFinite(freshest) ? null : Math.round(freshest),
  };
});
```

```js
Inputs.table(zoneRows, {
  sort: "Zone",
  reverse: false,
  layout: "auto",
  format: {
    "Freshest (h)": (v) => (v === null ? "pending" : `${v}h`),
  },
})
```

<div class="note">

Each dataset is published only after its source adapter, schema validation and chart export work end to end. Dataset availability differs by zone: ERCOT, PJM and CAISO currently supply load and generation through EIA-930; US wholesale prices require a separate source.

</div>

## Where prices have been

```js
Plot.plot({
  title: "Daily average price, all hours",
  subtitle: "Each series in its own currency per MWh. Levels are not comparable across markets; shape and volatility are.",
  width,
  height: 320,
  marginLeft: 55,
  x: { type: "utc", label: null },
  y: { label: "per MWh", grid: true },
  color: colorScale,
  marks: [
    Plot.ruleY([0], { stroke: "currentColor", strokeOpacity: 0.4 }),
    Plot.lineY(prices, {
      x: (d) => new Date(d.date),
      y: "all_hours",
      stroke: "zone",
      z: (d) => `${d.zone}-${d.segment}`,
      strokeWidth: 1,
      tip: true,
    }),
  ],
})
```

<div class="note">

Prices are shown in the currency the operator publishes. Converting them to a single currency would fold exchange-rate drift into what reads as a power-price signal, which is a real and commonly published error. Comparison across markets belongs on normalised quantities such as the [capture rate](./supply) and the [load factor](./demand).

</div>

## How often price falls below zero

```js
const negByZone = d3.rollups(
  negativeRows.filter((d) => d.n_intervals > 0),
  (v) => d3.sum(v, (d) => d.negative_hours) / d3.sum(v, (d) => d.observed_hours) * 100,
  (d) => d.zone,
).map(([zone, pct]) => ({ zone, pct }));
```

```js
Plot.plot({
  title: "Share of observed hours priced below zero",
  subtitle: "Over the observed period held for each zone; frequency alone does not identify the cause.",
  width,
  height: 200,
  marginLeft: 80,
  x: { label: "% of observed time", grid: true },
  y: { label: null },
  color: colorScale,
  marks: [
    Plot.barX(negByZone, {
      x: "pct",
      y: "zone",
      fill: "zone",
      sort: { y: "x", reverse: true },
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## What the systems are made of

```js
const latestMonth = d3.max(mixRows, (d) => d.month);
const latestMix = mixRows.filter((d) => d.month === latestMonth && d.fuel !== "imports");
```

```js
Plot.plot({
  title: `Generation mix by energy, ${latestMonth}`,
  subtitle: "Share of megawatt-hours generated, not of installed capacity. Pumped storage can be negative when it consumed more than it produced.",
  width,
  height: 260,
  marginLeft: 80,
  x: { label: "% of generation", grid: true },
  y: { label: null },
  color: { domain: fuelOrder, range: fuelOrder.map((f) => fuelColor[f]), legend: true },
  marks: [
    Plot.barX(latestMix, {
      x: "share_pct",
      y: "zone",
      fill: "fuel",
      order: fuelOrder,
      tip: true,
    }),
    Plot.ruleX([0]),
  ],
})
```

## Demand shape

```js
Plot.plot({
  title: "Daily energy consumed",
  subtitle: "Integrated from the operator's own interval data, so a 23-hour or 25-hour clock-change day is measured, not assumed.",
  width,
  height: 280,
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

---

Go deeper on [prices](./prices), [demand](./demand) or [supply](./supply). If you intend to reuse a number, read the [methodology](./methodology) first.

<style>
.freshness {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.6rem 1rem;
  margin: 1rem 0 1.5rem;
  font-size: 0.92rem;
  line-height: 1.5;
}
.freshness-ok { border-left-color: #009E73; }
.freshness-warn { border-left-color: #D55E00; }

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  font-family: var(--sans-serif);
  margin: 2rem 0 3rem;
  text-wrap: balance;
  text-align: center;
}
.hero h1 {
  margin: 0;
  padding: 0;
  max-width: none;
  font-size: clamp(2.2rem, 7vw, 4rem);
  font-weight: 900;
  line-height: 1;
  background: linear-gradient(30deg, var(--theme-foreground-focus), #009E73);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero h2 {
  margin: 1rem 0 0;
  max-width: 42em;
  font-size: 1.1rem;
  font-style: initial;
  font-weight: 400;
  line-height: 1.5;
  color: var(--theme-foreground-muted);
}
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.4rem 0 0.4rem 1rem;
  margin: 1.5rem 0;
  color: var(--theme-foreground-muted);
  font-size: 0.92rem;
}
.note strong { color: var(--theme-foreground); }
</style>

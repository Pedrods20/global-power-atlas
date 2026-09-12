---
title: Fuel & carbon
---

# Fuel, carbon and thermal spreads

These historical spreads align Germany/Luxembourg's monthly all-hours day-ahead price with monthly fuel, carbon and foreign-exchange references. They are screening indicators for a fixed-efficiency plant, not forward curves, dispatch simulations or realised plant margins.

```js
const spreadFile = await FileAttachment("data/europe_spreads.parquet").parquet();
const spreads = [...spreadFile];
```

## Indicative clean spreads

```js
const spreadLines = spreads.flatMap((d) => [
  {month: d.month, series: "Clean spark (50% gas)", value: d.clean_spark_eur_mwh},
  {month: d.month, series: "Clean dark (38% coal)", value: d.clean_dark_eur_mwh},
]);
```

```js
Plot.plot({
  title: "Germany/Luxembourg historical clean spreads",
  subtitle: "Monthly spot power less benchmark fuel and EUA costs; EUR/MWh electric.",
  width,
  height: 360,
  marginLeft: 60,
  x: {label: null},
  y: {label: "EUR/MWh", grid: true},
  color: {domain: ["Clean spark (50% gas)", "Clean dark (38% coal)"], range: ["#D55E00", "#5A4632"], legend: true},
  marks: [
    Plot.ruleY([0], {stroke: "currentColor", strokeOpacity: 0.5}),
    Plot.lineY(spreadLines, {x: "month", y: "value", stroke: "series", marker: "circle", tip: true}),
  ],
})
```

## Input references

```js
const inputLines = spreads.flatMap((d) => [
  {month: d.month, series: "Power", value: d.power_eur_mwh},
  {month: d.month, series: "Gas, thermal", value: d.gas_eur_mwhth},
  {month: d.month, series: "EUA", value: d.eua_eur_tco2},
]);
```

```js
Plot.plot({
  title: "Monthly power, gas and EUA references",
  subtitle: "Different units share the axis only to show direction; inspect the tooltip for values.",
  width,
  height: 320,
  marginLeft: 60,
  x: {label: null},
  y: {label: "reference value", grid: true},
  color: {domain: ["Power", "Gas, thermal", "EUA"], range: ["#0072B2", "#56B4E9", "#009E73"], legend: true},
  marks: [Plot.lineY(inputLines, {x: "month", y: "value", stroke: "series", marker: "circle", tip: true})],
})
```

The clean spark calculation assumes 50% net gas efficiency and 0.20196 tCO₂/MWh thermal. The clean dark calculation assumes 38% net coal efficiency, 0.34056 tCO₂/MWh thermal and 6.978 MWh thermal per metric tonne of coal (equivalent to 6,000 kcal/kg). No variable operations, start costs, plant outages, basis differentials or hedging effects are included.

Fuel inputs come from the [World Bank Pink Sheet](https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/world-bank-commodities-price-data-the-pink-sheet): Natural gas, Europe in USD/MMBtu and Coal, Australian in USD/metric tonne. USD values use the [ECB monthly USD/EUR reference rate](https://data.ecb.europa.eu/data/datasets/EXR). Carbon is the volume-weighted mean successful EUA primary-auction price from [EEX](https://www.eex.com/en/markets/environmentals/eu-ets1-eu-ets2-auctions/eu-ets1-auctions). These broad references can differ materially from a plant's local delivered fuel or hedge price.

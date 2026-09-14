---
title: Historical dashboard
---

# Historical dashboard

This is the context layer for the forecasting work. It is deliberately small:
monthly price, demand and energy-mix history, with no real-time operational
features.

The product focus is [price forecasting](./forecast) and [battery value](./battery).

```js
const zones = await FileAttachment("data/zones.json").json();
const dailyPrices = [...await FileAttachment("data/daily_prices.parquet").parquet()];
const dailyLoad = [...await FileAttachment("data/daily_load.parquet").parquet()];
const mix = [...await FileAttachment("data/generation_mix.parquet").parquet()];
const selectedZone = "DE-LU";
const priceRows = dailyPrices.filter((d) => String(d.zone) === selectedZone);
const loadRows = dailyLoad.filter((d) => String(d.zone) === selectedZone);
const mixRows = mix.filter((d) => String(d.zone) === selectedZone && d.fuel !== "imports");
const zoneColor = new Map([["DE-LU", "#0072B2"]]);
const fuelColor = {coal: "#5A4632", gas: "#56B4E9", oil: "#000000", nuclear: "#CC79A7", hydro: "#0072B2", hydro_pumped_storage: "#7FB3D5", wind: "#009E73", solar: "#F0E442", biomass: "#8B6F47", geothermal: "#B15928", waste: "#999999", battery: "#E69F00", other: "#BBBBBB"};
const fuelOrder = Object.keys(fuelColor);
```

Reference market: **DE-LU**. Data through **${zones.data_as_of ? zones.data_as_of.slice(0, 10) : "the latest monthly export"}**.

## Price history

```js
priceRows.length
  ? Plot.plot({
      title: `${selectedZone} daily average price`,
      subtitle: "All hours, in the operator's published currency.",
      width, height: 300, marginLeft: 55,
      x: {type: "utc", label: null}, y: {label: "per MWh", grid: true},
      marks: [Plot.lineY(priceRows, {x: (d) => new Date(d.date), y: "all_hours", stroke: zoneColor.get(selectedZone), tip: true}), Plot.ruleY([0])],
    })
  : html`<p class="note">${selectedZone} has no published price series in this project.</p>`
```

## Demand history

```js
Plot.plot({
  title: `${selectedZone} daily energy consumed`,
  subtitle: "Integrated from the operator's interval load series.",
  width, height: 300, marginLeft: 60,
  x: {type: "utc", label: null}, y: {label: "GWh per day", grid: true},
  marks: [Plot.lineY(loadRows, {x: (d) => new Date(d.date), y: (d) => d.energy_mwh / 1000, stroke: zoneColor.get(selectedZone), tip: true})],
})
```

## Energy mix history

```js
Plot.plot({
  title: `${selectedZone} monthly generation mix`,
  subtitle: "Share of generated energy; imports and storage are not treated as primary generation.",
  width, height: 340, marginLeft: 55,
  x: {label: null, tickRotate: -40}, y: {label: "% of generation", grid: true, domain: [0, 100]},
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

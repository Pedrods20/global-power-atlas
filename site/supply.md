---
title: Supply
---

# Supply

What is actually generating, how clean it is, and what each technology earns for its trouble.

```js
const mixFile = await FileAttachment("data/generation_mix.parquet").parquet();
const carbonFile = await FileAttachment("data/carbon_intensity.parquet").parquet();
const captureFile = await FileAttachment("data/capture_rates.parquet").parquet();

const mixRows = [...mixFile];
const carbonRows = [...carbonFile];
const captureRows = [...captureFile];

const zoneColor = new Map([
  ["ERCOT", "#D55E00"],
  ["DE-LU", "#0072B2"],
  ["BR-SIN", "#009E73"],
  ["AU-NSW1", "#CC79A7"],
]);
const zoneOrder = [...zoneColor.keys()].filter((z) => mixRows.some((d) => d.zone === z));
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
const fuelScale = {
  domain: fuelOrder,
  range: fuelOrder.map((f) => fuelColor[f]),
  legend: true,
};
```

## Generation mix over time

```js
const pickedZone = view(Inputs.select(zoneOrder, {label: "Zone", value: zoneOrder[0]}));
```

```js
Plot.plot({
  title: `Monthly generation mix, ${pickedZone}`,
  subtitle: "Share of megawatt-hours generated. Net interchange is excluded because imports are not generation.",
  width,
  height: 360,
  marginLeft: 55,
  x: { label: null, tickRotate: -40 },
  y: { label: "% of generation", grid: true },
  color: fuelScale,
  marks: [
    // Stacked bars rather than a stacked area: the x axis is an ordinal month
    // label, and an area over an ordinal scale interpolates between categories
    // that have no space between them. Fuels genuinely sum to the whole, so
    // the stack itself is meaningful here.
    Plot.barY(
      mixRows.filter((d) => d.zone === pickedZone),
      {
        x: "month",
        y: "share_pct",
        fill: "fuel",
        order: fuelOrder,
        tip: true,
      },
    ),
    Plot.ruleY([0]),
  ],
})
```

<div class="note">

**Shares of energy, not of capacity.** A system can be half wind by nameplate and a quarter wind by output, and conflating the two is how installed-capacity milestones get reported as though they were generation milestones. Pumped storage appears as its own fuel and can be negative over a month in which it consumed more than it produced, which is normal and leaves the other shares summing to more than 100 by that amount.

</div>

## Renewable penetration

```js
Plot.plot({
  title: "Renewable share of generation",
  subtitle: "Hydro, wind, solar, biomass and geothermal. Pumped storage and waste are excluded.",
  width,
  height: 320,
  marginLeft: 55,
  x: { label: null, tickRotate: -40 },
  y: { label: "% of generation", grid: true, domain: [0, 100] },
  color: colorScale,
  marks: [
    Plot.lineY(
      carbonRows.filter((d) => d.basis === "operational" && d.renewable_pct != null),
      {
        x: "month",
        y: "renewable_pct",
        stroke: "zone",
        strokeWidth: 2,
        marker: "circle",
        tip: true,
      },
    ),
  ],
})
```

<div class="note">

This definition is ours and is applied identically to every market, which makes these numbers comparable in a way each operator's own published figure is not. Operators disagree about whether to count biomass, waste, behind-the-meter rooftop solar and pumped storage, and they rarely say which choice they made.

</div>

## Carbon intensity

Two bases are shown and they are never mixed. Operational intensity counts direct combustion only, which is what system operators publish. Lifecycle intensity uses IPCC AR5 median values covering construction, fuel cycle and decommissioning. For nuclear and wind they differ by more than an order of magnitude, and reporting one while labelling it the other is the most common way a carbon figure becomes wrong.

```js
const basis = view(Inputs.radio(["operational", "lifecycle"], {label: "Basis", value: "operational"}));
```

```js
Plot.plot({
  title: `Carbon intensity of generation, ${basis} basis`,
  subtitle: "Gaps are deliberate: a month is withheld when too much of its generation has no known emission factor.",
  width,
  height: 320,
  marginLeft: 60,
  x: { label: null, tickRotate: -40 },
  y: { label: "g CO₂ per kWh", grid: true },
  color: colorScale,
  marks: [
    Plot.lineY(
      carbonRows.filter((d) => d.basis === basis && d.intensity_g_per_kwh != null),
      {
        x: "month",
        y: "intensity_g_per_kwh",
        stroke: "zone",
        strokeWidth: 2,
        marker: "circle",
        tip: true,
      },
    ),
    Plot.ruleY([0]),
  ],
})
```

### Why Brazil has no line

```js
const coverage = d3.rollups(
  carbonRows.filter((d) => d.basis === "operational"),
  (v) => d3.mean(v, (d) => d.coverage_pct),
  (d) => d.zone,
).map(([zone, pct]) => ({
  Zone: zone,
  "Generation with a known emission factor": Math.round(pct * 10) / 10 + "%",
  "Intensity published": pct >= 95 ? "yes" : "withheld",
}));
```

```js
Inputs.table(coverage, { layout: "auto" })
```

<div class="note">

The Brazilian operator publishes hydro, wind and solar separately and folds every thermal unit into one aggregate column with no fuel breakdown. This project maps that column to an unresolved bucket rather than guessing, so Brazilian coverage sits near 87 percent and falls below the 95 percent threshold.

Note what the covered 87 percent consists of: hydro, wind and solar, all of which carry a zero operational factor. Renormalising over them returns 0 g/kWh for a system that is not carbon free, because what was left out is precisely the emitting fleet. That is the number the previous version of this project published.

A coverage figure alone does not catch this, since 87 percent reads as reassuring. Refusing to publish is the correct answer, and the coverage column is how you can tell the difference between a clean grid and an unmeasured one.

</div>

## Capture rates

The capture price is what a technology actually earns: its output weighted by the price prevailing when that output happened. The capture rate divides that by the simple time-weighted average price.

A rate below one means the technology produces when the market is cheap. This is cannibalisation, and it is the mechanism that erodes solar and wind revenue as penetration grows, independently of any subsidy design.

```js
Plot.plot({
  title: "Capture rate by technology",
  subtitle: "Generation-weighted, not approximated by a fixed window of daylight hours.",
  width,
  height: 340,
  marginLeft: 55,
  x: { label: null, tickRotate: -40 },
  y: { label: "capture price ÷ baseload price", grid: true },
  color: colorScale,
  fy: { label: null },
  marks: [
    Plot.ruleY([1], { stroke: "currentColor", strokeOpacity: 0.4, strokeDasharray: "3,3" }),
    Plot.lineY(captureRows.filter((d) => d.capture_rate != null), {
      x: "month",
      y: "capture_rate",
      stroke: "zone",
      fy: "fuel",
      strokeWidth: 2,
      marker: "circle",
      tip: true,
    }),
  ],
})
```

<div class="note">

The dashed line at 1.0 is the break-even against a flat baseload profile. Solar sits below it wherever penetration is meaningful, and falls further as more is built. Wind tends to sit closer to 1.0 because its output correlates less tightly with a single time of day.

This is weighted by megawatt-hours actually generated. A common shortcut weights by a fixed window of "solar hours" instead, which ignores cloud, season and the installed base, and drifts further from the truth the more the answer matters.

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

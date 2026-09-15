---
title: Battery value
---

# Battery value

This DE-LU study converts the price forecast into a constrained dispatch
decision. It uses a 1 MW battery, 90% round-trip efficiency, a 24-interval
rolling horizon and one-cycle-per-day capacity as a transparent first case.
The dispatch is settled on the observed price, while `perfect_foresight` uses
that same physical optimiser with the realised price path only as an upper
bound. The current benchmark is hourly; quarter-hour products are not silently
mixed into it.

The published base case leaves variable operating and degradation costs at
zero because no asset-specific calibration is assumed yet. Both costs are
explicit inputs to the optimizer and settlement, so the study can be rerun
with an auditable cost curve before prospective acceptance.

```js
const summary = [...await FileAttachment("data/battery_summary.parquet").parquet()]
  .filter((d) => d.zone === "DE-LU");
const dispatch = [...await FileAttachment("data/battery_dispatch.parquet").parquet()]
  .filter((d) => d.zone === "DE-LU");
const labels = new Map([
  ["ridge", "Ridge"],
  ["lightgbm", "LightGBM"],
  ["naive_previous_week", "Same hour last week"],
  ["perfect_foresight", "Perfect foresight"],
  ["no_trade", "No trade"],
]);
const fmt = (v) => v == null ? "n/a" : v.toFixed(0);
const table = summary.map((d) => ({
  Battery: `${d.energy_mwh.toFixed(0)}h`,
  Strategy: labels.get(d.strategy) ?? d.strategy,
  "Net value (EUR/MW)": d.profit_eur,
  "Equivalent cycles": d.equivalent_cycles,
  "Capture vs perfect": d.capture_vs_perfect,
}));
const scored = dispatch.filter((d) => d.strategy !== "no_trade");
const sampleDate = d3.max(scored, (d) => d.local_date);
const sample = dispatch.filter((d) => d.energy_mwh === 4 && d.strategy === "ridge" && d.local_date === sampleDate);
```

## Economic scoreboard

```js
Inputs.table(table, {format: {"Net value (EUR/MW)": fmt, "Equivalent cycles": (v) => v.toFixed(1), "Capture vs perfect": (v) => v == null ? "n/a" : `${(v * 100).toFixed(1)}%`}, layout: "auto"})
```

```js
const monthly = d3.rollups(
  dispatch.filter((d) => d.profit_eur != null),
  (v) => d3.sum(v, (d) => d.profit_eur),
  (d) => d.strategy, (d) => d.energy_mwh, (d) => d.local_date.slice(0, 7),
).flatMap(([strategy, durations]) => durations.flatMap(([duration, months]) => months.map(([month, profit]) => ({strategy, duration, month, profit}))));
const cumulative = d3.groups(monthly, (d) => `${d.strategy}|${d.duration}`).flatMap(([key, values]) => {
  let total = 0;
  return values.sort((a, b) => a.month.localeCompare(b.month)).map((d) => ({...d, total: total += d.profit, key}));
});
Plot.plot({
  title: "Cumulative net value",
  subtitle: "Rolling-horizon dispatch on observed prices; one curve per strategy and battery duration.",
  width, height: 380, marginLeft: 65,
  x: {type: "utc", label: null}, y: {label: "EUR per MW", grid: true},
  color: {domain: [...new Set(cumulative.map((d) => labels.get(d.strategy) ?? d.strategy))], legend: true},
  marks: cumulative.length ? [
    Plot.lineY(cumulative, {x: (d) => new Date(`${d.month}-01`), y: "total", z: "key", stroke: (d) => labels.get(d.strategy) ?? d.strategy, tip: true}),
    Plot.ruleY([0]),
  ] : [Plot.ruleY([0])],
})
```

## Dispatch example

Two stacked charts share the same local-hour axis so price (EUR/MWh) is never
plotted on the same scale as the battery's physical state and flow (MWh).

```js
Plot.plot({
  title: `4h battery dispatch — ${sampleDate ?? "no scored day"}`,
  subtitle: "Ridge forecast-guided strategy; settled against the observed price.",
  width, height: 200, marginLeft: 65, marginBottom: 20,
  x: {label: null, axis: null},
  y: {label: "EUR/MWh", grid: true},
  marks: sample.length ? [
    Plot.lineY(sample, {x: "local_hour", y: "actual", stroke: "#333333", tip: true}),
    Plot.ruleY([0]),
  ] : [Plot.ruleY([0])],
})
```

```js
Plot.plot({
  subtitle: "State of charge (line) and dispatch action (bars); positive action discharges to the market.",
  width, height: 260, marginLeft: 65,
  x: {label: "Market-local hour"}, y: {label: "MWh", grid: true},
  color: {domain: ["State of charge", "Action"], range: ["#0072B2", "#D55E00"], legend: true},
  marks: sample.length ? [
    Plot.lineY(sample, {x: "local_hour", y: "soc_mwh", stroke: "State of charge", tip: true}),
    Plot.barY(sample, {x: "local_hour", y: "action_mwh", fill: "#D55E00", fillOpacity: .55, tip: true}),
    Plot.ruleY([0]),
  ] : [Plot.ruleY([0])],
})
```

The dispatch is a historical economic backtest, not a live trading
recommendation. The `perfect_foresight` comparison is constrained by the same
power, energy, efficiency and terminal-SOC rules; it is not a claim that future
prices were observable. Prospective ledger evaluation uses `gpa battery` after
`gpa reconcile`, and remains separate from this monthly static page until a
four-to-six-week acceptance period is complete.

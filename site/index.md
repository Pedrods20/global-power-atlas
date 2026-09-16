---
title: Historical dashboard
---

# Does a validated day-ahead forecast create battery value?

```js
const forecastMeta = await FileAttachment("data/forecast.json").json();
const run = forecastMeta.runs.find((d) => d.zone === "DE-LU");
const forecastScores = [...await FileAttachment("data/forecast_scores.parquet").parquet()];
const overallScores = forecastScores.filter((d) => d.scope === "overall");
const ridgeScore = overallScores.find((d) => d.model === "ridge");
const bestBaselineScore = overallScores
  .filter((d) => ["naive_previous_day", "naive_previous_week", "naive_similar_day"].includes(d.model))
  .sort((a, b) => a.mae - b.mae)[0];
const batterySummary = [...await FileAttachment("data/battery_summary.parquet").parquet()];
const batterySensitivities = [...await FileAttachment("data/battery_sensitivities.parquet").parquet()];
const ridge4Summary = batterySummary.find((d) => d.strategy === "ridge" && d.energy_mwh === 4);
const ridge4Base = batterySensitivities.find((d) => d.strategy === "ridge" && d.energy_mwh === 4 && d.scenario === "base");
const lightgbm1Base = batterySensitivities.find((d) => d.strategy === "lightgbm" && d.energy_mwh === 1 && d.scenario === "base");
const ridge4Scenarios = batterySensitivities.filter((d) => d.strategy === "ridge" && d.energy_mwh === 4);
const [worstIncrement, bestIncrement] = d3.extent(ridge4Scenarios, (d) => d.incremental_vs_best_naive_eur_mw);
const baselineLabels = new Map([
  ["naive_previous_day", "previous day"], ["naive_previous_week", "previous week"], ["naive_similar_day", "similar day"],
]);
const baselineName = (model) => baselineLabels.get(model) ?? model;
const euro = (v) => v.toLocaleString("en", {maximumFractionDigits: 0});
const price = (v) => v.toFixed(2);
const pct = (v) => `${v.toFixed(1)}%`;
```

A DE-LU day-ahead price forecast, evaluated end to end into a constrained
battery-dispatch decision. This page states the question, the qualified
findings and the caveats in under a minute; the [forecast](./forecast) and
[battery](./battery) pages carry the full protocol and evidence.

**Why this market.** Germany-Luxembourg is the deepest power market in Europe
and the reference for continental price formation. With the nuclear phase-out
completed in April 2023, wind and solar now set the price for a large and
growing share of hours and negative prices are routine — the price shape a
storage asset is paid to exploit, and the one a forecast has to get right on
the days that carry the value.

**Question.** Does a statistically validated day-ahead price forecast create
measurable battery-dispatch value — and where does it fail to?

**Finding 1 — forecast skill.** Ridge reduces day-ahead price error by
**${pct(run.best_skill_vs_best_baseline_pct)}** against the strongest naive
baseline (**${price(ridgeScore.mae)} EUR/MWh** vs. **${price(bestBaselineScore.mae)} EUR/MWh**,
${baselineName(bestBaselineScore.model)}), on ${run.scored_hours.toLocaleString("en")}
scored clock-hours from ${run.test_start} to ${run.test_end}.

**Finding 2 — battery translation.** On a shared ${ridge4Base.days}-day sample,
a Ridge-guided 1 MW / 4 MWh battery captures **${pct(ridge4Summary.capture_vs_perfect * 100)}**
of the constrained perfect-foresight value and adds **EUR ${euro(ridge4Base.incremental_vs_best_naive_eur_mw)}/MW**
over the strongest fixed comparator (95% exploratory interval EUR
${euro(ridge4Base.ci_low_eur_mw)}–${euro(ridge4Base.ci_high_eur_mw)}/MW). This
does not hold at every duration or for every model: at 1h, LightGBM trails
its best naive by EUR ${euro(Math.abs(lightgbm1Base.incremental_vs_best_naive_eur_mw))}/MW
— a bigger model is not automatically a better dispatch signal.

**Finding 3 — robustness.** That incremental margin stays within EUR
${euro(worstIncrement)}–${euro(bestIncrement)}/MW across seven fixed stresses
(illustrative operating/degradation costs, 85% efficiency, a weakened forecast
signal, and shared calendar downtime) — evidence it is not an artifact of one
favorable assumption. See the [battery page](./battery) for every stress.

**Implication.** A leakage-safe forecast, evaluated on a common sample against
every simple alternative, can translate into dispatch value that survives
several independent stress tests — but the value is duration- and
model-dependent, and rewards disciplined evaluation over defaulting to the
most complex model.

**Premises.** Five assumptions drive every number above. The forecast may use
only information available at **12:00 market time on the day before delivery**,
the moment the day-ahead auction's order book closes. The information set is
therefore lagged prices, calendar features and residual load lagged at least two
delivery days — no realised delivery-day fundamentals. The target is the
duration-weighted local clock-hour price, an analytical benchmark rather than a
per-quarter-hour trade. The asset is a deliberately simple 1 MW battery at 1, 2
or 4 MWh, 90% round-trip, one cycle per day, starting and ending each day empty.
The base case carries zero operating and degradation cost; non-zero cases are
rerun and shown separately rather than folded into the headline. Full definitions
are on the [methodology page](./methodology).

**Sources.** Prices, load and generation come from Energy-Charts (Fraunhofer
ISE, CC BY 4.0), which republishes ENTSO-E and SMARD figures; Brazilian system
data comes from ONS. Capacity and the official 2030 targets come from
Energy-Charts' installed-capacity series. Two figures on the battery page are
external citations — BloombergNEF and the Bundesnetzagentur — and are dated
where they appear.

**Limitations.** This is retrospective development evidence — already-inspected
history, not an untouched holdout or a prospective result. Costs are
illustrative, not sourced or calibrated; CAPEX, financing and other revenue
streams are excluded. The benchmark is hourly, not each traded quarter-hour.
A separately recorded prospective pilot has not yet run.

**Personal contribution.** Designed and built end to end, solo: ingestion
pipelines across two public system-data providers covering four markets,
leakage-safe walk-forward forecasting, the constrained battery-dispatch and
stress-testing engine, and this site.
[github.com/Pedrods20](https://github.com/Pedrods20).

## Historical context

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

This is deliberately small: monthly price, demand and energy-mix history for
the reference market, **DE-LU**, with no real-time operational features. Data
through **${zones.data_as_of ? zones.data_as_of.slice(0, 10) : "the latest monthly export"}**.

### Price history

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

### Demand history

```js
Plot.plot({
  title: `${selectedZone} daily energy consumed`,
  subtitle: "Integrated from the operator's interval load series.",
  width, height: 300, marginLeft: 60,
  x: {type: "utc", label: null}, y: {label: "GWh per day", grid: true},
  marks: [Plot.lineY(loadRows, {x: (d) => new Date(d.date), y: (d) => d.energy_mwh / 1000, stroke: zoneColor.get(selectedZone), tip: true})],
})
```

### Energy mix history

```js
Plot.plot({
  title: `${selectedZone} monthly generation mix`,
  subtitle: "Share of generated energy; imports and storage are not treated as primary generation.",
  width, height: 340, marginLeft: 55,
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

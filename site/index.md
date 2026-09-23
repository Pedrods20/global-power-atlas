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
const capacity = [...await FileAttachment("data/capacity.parquet").parquet()];
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

Germany's day-ahead price no longer pays a premium for the hours the market used
to call peak. It pays for the hours solar cannot reach. That is a larger
opportunity for flexibility, not a smaller one, and the two facts get confused
because they show up in different statistics.

**DE-LU, ${first.year} → ${last.year}:** on-peak premium **EUR ${num(first.spread)}
→ ${num(last.spread)}/MWh** · within-day range **EUR ${num(first.intraday_spread)}
→ ${num(last.intraday_spread)}/MWh** (${pct(first.intraday_spread_pct_of_price)}
→ ${pct(last.intraday_spread_pct_of_price)} of baseload) · solar capture rate
**${rate(first.solar_capture_rate)} → ${rate(last.solar_capture_rate)}** ·
negative hours **${num(first.negative_pct)}% → ${num(last.negative_pct)}%**

The case below is built from primary system-operator data, then tested the only
way that settles it: by dispatching a battery against realised prices and
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

Part of that is the block definition, and saying so makes the point sharper
rather than weaker. DE-LU's on-peak block is 08:00–20:00 local, a window drawn
when demand shaped the day. It now straddles the cheapest hours and the most
expensive ones at once, so its average says less every year. "The peak premium
is gone" is partly "the block no longer describes the peak" — which is exactly
why the within-day range in the next section, which makes no assumption about
*when* the extremes fall, is the measure that still works.

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
foresight and one cycle a day puts a number on it. EUR/MW/d is that battery's
margin per megawatt per day; GW and GWh are Germany's installed battery fleet
that year:

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

**${crisis.year} is still the best year in the table, and that is not a
contradiction.** A battery is paid in euros, so ${crisis.year}'s EUR
${eur(foresight(crisis.year).eur_per_mw_day)}/MW/day beats ${last.year}'s EUR
${eur(fleetLast.eur_per_mw_day)}. The claim is not that today is richer than the
gas crisis. It is that today's spread is reached from a *completely different
place*: ${last.year} delivers **${pct((last.intraday_spread / crisis.intraday_spread) * 100)}
of ${crisis.year}'s absolute daily range on a baseload price
${pct(Math.abs(last.baseload_price / crisis.baseload_price - 1) * 100)} lower**.

That distinction is the whole commercial argument. ${crisis.year}'s spread came
from an expensive, volatile marginal unit, and it left with the gas price.
${last.year}'s comes from the shape of the day, which is set by an installed
base that is still being built. A spread that depends on a fuel shock is one a
lender discounts; a spread that depends on 118 GW of solar is one they can
underwrite.

## 3. Competition has not arrived — and the fleet's duration says why

Germany's battery fleet grew from ${num(fleetFirst.battery_power_gw)} GW to
**${num(fleetLast.battery_power_gw)} GW** across this sample and the arbitrage
value per MW did not compress. It rose.

**First, the incumbent nobody counts.** Germany has roughly
${num(pumpedLast.value)} GW of pumped hydro arbitraging this exact spread, and it
is not new: ${num(pumpedFirst.value)} GW in ${pumpedFirst.period}, the earliest
year this provider's series covers, and essentially flat every year since. Storage competition did not begin with batteries. What that
flatness means is that the incumbent fleet is not what changed: the spread
widened while the longest-duration, most capable arbitrage fleet in the market
stood still.

**Second, the batteries that did arrive are the wrong shape for this trade.** At
${num(fleetLast.battery_power_gw)} GW and ${num(fleetLast.battery_energy_gwh)}
GWh, the fleet's average duration is about
**${fleetHours(last.year).toFixed(2)} hours**. More telling than the level is
that it barely moved while the fleet grew more than fivefold. GW and GWh are
installed battery power and energy; Hours is the ratio, the fleet's average
duration:

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

A fleet whose duration holds near 1.5 hours through a fivefold build-out is not
changing composition — it is adding more of the same thing. Roughly 1.5 hours is
what a behind-the-meter home system is sized for: shifting a household's own
evening consumption, not bidding a four-hour midday trough. Gigawatts installed
is not the same as capacity competing for this spread.

**What this argument rests on, and what would break it.** The provider publishes
the fleet as one aggregate and does not split residential from grid-scale, so
the composition here is inferred from the duration ratio rather than observed
directly. The inference is testable and the register that would settle it, the
Marktstammdatenregister, is outside this project's ingestion. Read the last two
rows as the early signal against the argument: duration has ticked from
${fleetHours("2024").toFixed(2)} to ${fleetHours(last.year).toFixed(2)} hours,
which is what grid-scale entry looks like when it starts.

Annual data cannot separate a fleet effect from the gas-price unwind or the
weather either. What the data does establish is narrow and worth stating plainly:
compression has not yet reached the price, at a fleet size where a
shallow-duration explanation fits better than no effect at all.

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

For scale, that battery's whole gross margin under Ridge is about **EUR
${eur(base4.profit_eur_mw / sampleYears)}/MW per year** over the sample, before
any operating, degradation or capital cost. Against that total, the forecast's
contribution is the part worth arguing about.

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

Which raises a fair question: why build the forecast at all? Because the
number above is only trustworthy if the protocol producing it is. The 12:00
gate, the vintage discipline and the walk-forward boundaries are what stop a
model from quietly scoring itself on information no bidder held, and they are
what turn "our model beats the market" into a claim someone can check. That
discipline is the transferable part, and it is the reason this project publishes
where the model loses as prominently as where it wins.

Protocol and failure modes on the [forecast page](./forecast); dispatch, costs,
stresses and downside on the [storage page](./battery).

## What would make this wrong

- **The gas premium unwinds further.** Part of the widened range is still an
  expensive, volatile gas stack setting the evening price. A cheaper marginal
  unit compresses the top of the day without touching the midday trough.
- **The fleet's duration lengthens.** Section 3 rests on German storage being
  short and domestic. Grid-scale 2–4h additions attack exactly the trough this
  trade depends on, and the first sign is already in the table there: average
  duration ticked from ${fleetHours("2024").toFixed(2)} to
  ${fleetHours(last.year).toFixed(2)} hours in two years. This is the risk to
  watch, and it is measurable every quarter.
- **Market design changes.** The move to 15-minute day-ahead products in October
  2025, and any change to negative-price support rules, alter both the trade and
  the measurement of it.
- **This is already-inspected history.** It is development evidence from a
  frozen, reproducible release — not an untouched holdout and not a prospective
  record, and the prospective ledger that would change that **has not yet issued
  a single forecast**. Eleven scheduled runs between 14 and 22 September 2026 all
  failed, every one of them at the same step: GitHub's scheduler is best-effort
  and delivered them hours after the midday gate, so `gpa issue` refused to
  backdate a forecast — the tool working, not failing. The schedule has been moved
  to two early slots and a defect in the fundamentals arm fixed, but until a run
  actually issues before a gate, nothing prospective is claimed here.

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
are outside this study.

${last.year} is a partial year, and the direction of that bias is known rather
than waved at. Within-day range is seasonal, peaking in late summer, so a
January-to-September year runs above its own full-year average: measured on the
complete years in this store, by **+4.0% (2022), +3.5% (2023) and +5.2%
(2025)**. Discount ${last.year}'s range figures by roughly that much; it does
not change the direction of anything above. Reproduce with
`gpa.metrics.price.intraday_spread(period="month")`. Full definitions on the
[methodology page](./methodology).

**Sources.** Prices, load, generation and installed capacity come from
Energy-Charts (Fraunhofer ISE, CC BY 4.0), which republishes ENTSO-E and SMARD
figures; Brazilian system data comes from ONS. Two figures on the storage page
are external citations — BloombergNEF and the Bundesnetzagentur — dated where
they appear.

## Author

**Pedro Cabral.** Built solo, end to end: ingestion across two public
system-data providers covering four markets, leakage-safe walk-forward
forecasting, the constrained dispatch and stress-testing engine, and this site.

Code, data and the full audit trail:
**[github.com/Pedrods20/global-power-atlas](https://github.com/Pedrods20/global-power-atlas)**
· profile: [github.com/Pedrods20](https://github.com/Pedrods20)

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
</style>

---
title: Methodology
---

# Methodology

Everything needed to reproduce, or to refuse to trust, any number on this site.

```js
const zones = await FileAttachment("data/zones.json").json();
```

## Sources

Every series comes from the system or market operator, or from a named redistributor of the operator's own data. Source observations are not synthesised. Carbon intensities and thermal spreads are calculated estimates with explicit assumptions. A zone with no data for a dataset shows as pending rather than being filled in.

| Zone | Dataset | Provider | Credential | Licence |
|---|---|---|---|---|
| DE-LU | price, load, generation | Energy-Charts, Fraunhofer ISE | none | CC BY 4.0 |
| FR, ES | price, load, generation | Energy-Charts, Fraunhofer ISE | none | CC BY 4.0 |
| BR-SIN | generation | ONS open data, hourly subsystem balance | none | CC BY 4.0 |
| BR-SIN | load | ONS verified-load API, half-hourly | none | CC BY 4.0 |
| European spread references | gas and coal: World Bank; EUA: EEX; FX: ECB | public downloads | none | provider-specific |

Germany, France and Spain are served through Energy-Charts rather than ENTSO-E directly. The underlying figures are the same ones ENTSO-E publishes, and the switch to a direct ENTSO-E Transparency connection is a change of adapter, not of method.

Every registered zone comes from a source that needs no credential and no manual import. Markets whose only price source required a subscription key, returned HTTP 403 to automated clients, or could only be imported by hand from a CSV were removed from the registry rather than kept half-covered; see "Removed for lack of independent verification" below.

```js
const freshness = zones.zones.flatMap((z) =>
  Object.entries(z.datasets).map(([dataset, d]) => ({
    Zone: z.code,
    Dataset: dataset,
    Source: d.source,
    Status: d.status,
    Rows: d.rows ?? null,
    From: d.first ? String(d.first).slice(0, 10) : null,
    To: d.last ? String(d.last).slice(0, 10) : null,
    "Age (h)": d.last ? Math.round(Math.max(0, (Date.now() - new Date(d.last.replace(" ", "T"))) / 3600000)) : null,
  })),
);
```

```js
Inputs.table(freshness, { rows: 30, layout: "auto" })
```

Latest observation across datasets: ${zones.data_as_of ? zones.data_as_of.slice(0, 16).replace("T", " ") + " UTC" : "pending"}. Each dataset's own coverage is listed above; ages are calculated when this page loads.

### Structural coverage

Freshness above answers "how recent is the latest observation." That is a
different question from "how much of the declared history is actually
present, per fuel." A feed can be perfectly fresh and still carry an internal
gap, a fuel category the provider only started reporting partway through, or
(rarer, and more serious) two observations that overlap in time. The table
below is computed by [`gpa.quality`](https://github.com/Pedrods20/global-power-atlas/blob/main/src/gpa/quality.py)
over each fuel's own first-to-last observed span, so a technology added last
year is not penalised for the months before it existed. It is enforced in CI
and on every scheduled ingest by `gpa audit`, which fails the run on any
overlapping or otherwise invalid observation; coverage below 100% is reported
here rather than failing anything, since a gap is a fact about the provider,
not a broken pipeline.

```js
const dataQuality = await FileAttachment("data/data_quality.parquet").parquet();
```

```js
const coverageRows = [...dataQuality]
  .map((d) => ({
    Zone: d.zone,
    Dataset: d.dataset,
    Fuel: d.fuel,
    Rows: Number(d.rows),
    "Coverage %": d.coverage_pct == null ? null : Math.round(d.coverage_pct * 100) / 100,
    "Gap (h)": d.gap_hours == null ? null : Math.round(d.gap_hours * 10) / 10,
    Invalid: Number(d.invalid),
  }))
  .sort((a, b) => (a["Coverage %"] ?? -1) - (b["Coverage %"] ?? -1) || b.Invalid - a.Invalid);
```

```js
Inputs.table(coverageRows, { rows: 48, layout: "auto" })
```

Worst rows first. 100% is the expected value for almost every row: a gap
below it is not automatically a defect, since some come from a fuel a
provider added partway through the window (its own span still shows 100%) or
from a real short outage on the provider's side. `Invalid` above zero would
mean an overlapping timestamp or a value outside its physical range, and
would already have failed CI before reaching this page.

## Time

This is where most power market analysis goes wrong, so it is worth being explicit.

**Every instant is stored UTC-aware and interpreted through the market's own timezone.** Nothing is ever grouped by UTC calendar day or UTC calendar hour. A UTC day boundary falls in the middle of the Australian afternoon and two hours into the German night, so grouping on it silently smears every daily figure across two trading days.

**A local day has 23, 24 or 25 hours.** Daily means divide by the hours that actually existed. Daily energy is the sum of power times each interval's real duration, never a mean multiplied by 24.

Brazil abolished daylight saving in 2019, so its local time has been a constant UTC-3 since then. Files covering 2018 and earlier do contain the ambiguous and non-existent hours of a transition, and those are resolved explicitly rather than left to a library default.

## Blocks

On-peak and off-peak are market products with different definitions in each market. They are never the daily maximum and minimum, which is intraday range and a much larger number.

```js
const blocks = zones.zones.map((z) => ({
  Zone: z.code,
  "Market time": z.timezone,
  "Observes DST": z.observes_market_dst ? "yes" : "no",
  "On-peak definition": z.peak_block,
  Caveat: z.peak_note ? "see below" : "",
}));
```

```js
Inputs.table(blocks, { layout: "auto" })
```

European peakload excludes Saturday and public holidays are not adjusted for; the Brazilian ponta window is not a traded product at all, which is why it carries the caveat below rather than being read as a market clearing block.

```js
const caveats = zones.zones.filter((z) => z.peak_note);
```

<div class="warn">

${caveats.map((z) => html`<p><strong>${z.code}.</strong> ${z.peak_note}</p>`)}

</div>

## Units

| Quantity | Stored as | Conversion |
|---|---|---|
| Price | money per MWh, in the currency the operator publishes | none applied |
| Load | average MW over the interval beginning at the timestamp | energy = MW × minutes ÷ 60 |
| Generation | average MW over the interval, per fuel | energy = MW × minutes ÷ 60 |

The settlement interval is measured from the spacing of the timestamps, never assumed. This matters because providers change it without notice: Energy-Charts moved the European day-ahead auction from hourly to quarter-hourly products on 2025-10-01, a switch that is now handled per request window rather than inferred once across a whole backfill (see "Rebuilding the record" below). A backfill spanning that change shifts each era by its own interval.

All stored timestamps mark an interval's **start**. An independent check exists for this: solar generation should peak within a few minutes of local solar noon regardless of provider convention, and computing the generation-weighted centroid of each zone's solar output around the equinoxes (when the equation of time is smallest) confirms interval-start labelling for three of the four zones. Spain's centroid sits closer to the interval-end hypothesis by a small margin (about seven minutes at quarter-hour resolution); this is noted as an open question in `TODO.md` rather than asserted as a defect, since the check is a coarse heuristic and the margin is within its own noise.

**No currency conversion is applied.** Converting a multi-year price series at a single spot rate, which is a common shortcut, folds exchange-rate drift into what is presented as a power-price signal. Converting at historical rates is defensible but strips the FX variance out of volatility without saying so. Both are avoided by keeping each market in its own currency and comparing only normalised quantities across markets.

## Prices

**Negative prices are preserved and counted.** They are economically meaningful observations, and filtering them out changes averages, tails and volatility. Their cause cannot be established from price alone.

**No log returns.** Price reaches zero and goes negative, so a log return is undefined precisely where the market is interesting. Every return here is an arithmetic difference in money per MWh.

**Volatility annualises on 365 days, not 252.** The 252 convention counts the trading days of a financial exchange. Spot electricity settles every calendar day, including weekends and holidays, so scaling by the square root of 252 understates annualised volatility by about twenty percent.

## Fuels and carbon

Every provider's fuel vocabulary is mapped onto one canonical taxonomy. An unmapped technology becomes `other` rather than being dropped, so a new fuel appears in the mix instead of silently vanishing.

Pumped storage is kept separate from hydro because it is a load as often as a generator, and folding it into hydro overstates renewable share. Net interchange is excluded from mix shares because imports are not generation.

Renewable means hydro, wind, solar, biomass and geothermal. Biomass is included because every operator and statistical agency counts it, not because its lifecycle balance is uncontroversial. Waste is excluded because only its biogenic fraction would qualify and that fraction is unknown here.

Carbon intensity is published on two bases that are never mixed:

| Fuel | Operational, g/kWh | Lifecycle, g/kWh |
|---|---|---|
| Coal | 950 | 820 |
| Gas | 400 | 490 |
| Oil | 700 | 650 |
| Biomass | 0 | 230 |
| Nuclear | 0 | 12 |
| Hydro | 0 | 24 |
| Wind | 0 | 11 |
| Solar | 0 | 48 |
| Geothermal | 0 | 38 |

Operational factors are direct combustion only, matching what system operators publish. Lifecycle values are IPCC AR5 Annex III medians. Biomass counts as zero at the stack under the standard convention that books biogenic carbon to the land sector; that is a convention, not a physical claim, and it is the largest judgement call in the table.

**An intensity is withheld when coverage is poor.** If less than 95 percent of a period's generation has a known factor, no number is published and the coverage is reported instead.

That threshold is strict on purpose. Brazil is the case that sets it: the ONS balance publishes hydro, wind and solar separately and folds every thermal unit into one aggregate column with no fuel breakdown. Brazilian coverage lands near 87 percent, and the missing 13 percent is not a random sample of the fleet, it is the entire emitting fleet. Renormalising over the clean remainder returns an intensity of roughly zero for a system that is not carbon free, which is exactly what the previous version of this project reported.

A coverage number alone cannot catch that, because 87 percent looks reassuring. The rule of thumb behind the 95 percent threshold is that uncovered generation is almost always unresolved thermal output, so even a tenth of it left out can move the answer by more than a hundred grams per kWh.

## Coverage and interpretation

The earliest and latest years and months may be partial. Gaps remain missing observations; they must not be read as zero prices or zero demand. Comparisons need matching observation periods and units. Negative prices alone do not establish curtailment or identify its cause. Technology-based emission factors estimate generation intensity rather than measuring hourly emissions.

## Fuel, carbon and spread assumptions

The spread page uses monthly, historical references. German/Luxembourg all-hours day-ahead power is aligned with World Bank European natural gas and Australian coal. Dollar fuel prices are converted with the ECB monthly average USD/EUR rate. EEX EUA primary-auction prices are volume-weighted across successful general-allowance auctions; aviation contracts are excluded.

Clean spark assumes 50% net efficiency and 0.20196 tCO₂/MWh thermal. Clean dark assumes 38% net efficiency, 0.34056 tCO₂/MWh thermal, and 6.978 MWh thermal per tonne of coal (6,000 kcal/kg). The outputs are screening indicators rather than traded forward spreads or realised margins. They omit local fuel basis, transport, variable operations, starts, outages and hedging.

## Publication lag

Providers do not publish at the same speed, and the difference is large enough
to matter when reading a "latest" figure.

Brazilian **generation** is the slowest series here. The ONS hourly subsystem
balance is republished several times a day, but its contents trail real time by
roughly two days. That is the provider's lag, not a collection failure, and no
faster ONS source for generation by technology exists. Brazilian generation,
fuel mix and renewable share should therefore be read as ending about two days
before every other market on this site.

Brazilian **load** does not share that lag. It comes from the ONS verified-load
API instead, which carries the same two years of history at half-hourly
resolution and stays within about an hour of real time.

Two details of that API are worth recording, because both fail silently rather
than loudly:

- Its own national aggregate, `cod_areacarga=SIN`, answers with every value
  zeroed. National load is summed from the four submarket areas instead, and a
  timestamp is only kept when all four reported. Summing three of four would
  understate national demand by roughly the missing area's share while looking
  like an ordinary observation.
- The Southeast area code is `SECO`. The `SE` code used by older ONS endpoints
  now returns an empty list rather than an error, so a stale code silently drops
  about a third of national demand.

A value of exactly zero from that API means an interval has not been measured
yet, not that an area drew no power, so those rows are dropped rather than
stored as zero demand.

## Rebuilding the record

Every partition in the store was rebuilt from scratch from cached provider
responses after the resolution-transition defect above was found, rather than
patched in place, so that a mixed-resolution window is re-fetched and
re-labelled consistently end to end instead of only at the seam. Two
independent checks then confirmed the rebuild rather than the adapter checking
itself:

- **Prices against a second publisher.** DE-LU and FR day-ahead prices are
  compared against Bundesnetzagentur SMARD, which redistributes the same
  auction result through a separate pipeline; ES against OMIE's own
  `marginalpdbc` daily files. Both comparisons match to the cent over the full
  two-year history, with zero mismatches.
- **Brazilian load against a second ONS publication.** The verified-load API
  the store uses is checked against the ONS hourly subsystem balance, which is
  collected and republished independently. The two differ in level by about 2
  percent, consistent with the two APIs using slightly different load
  definitions rather than a timestamp or resolution error, and the median
  absolute difference is smallest at zero offset, confirming the stored
  timestamps are not shifted.

Energy-Charts redistributes ENTSO-E data, so comparing it against itself would
have proven nothing; both external references are collected independently of
Energy-Charts.

## Freshness rules

Every series carries a limit on how stale it may get, declared per zone and
dataset rather than as one global threshold, because the providers publish at
genuinely different speeds and those differences are legitimate.

| Series | Limit | Why |
|---|---|---|
| Default | 36 hours | Most feeds land within twelve hours; this absorbs one missed run plus a provider's own delay. |
| BR-SIN generation | 96 hours | The ONS hourly balance trails real time by about two days. |

The scheduled run checks these after it commits, so a stale feed raises an
alarm without discarding a day of good observations from every other market.
A breach opens an issue on the repository rather than only turning a badge red.

## Known limitations

- **Only four zones are registered, deliberately.** Every market that needed a
  credential (a US EIA key, a PJM Data Miner subscription), returned HTTP 403
  to automated clients (ERCOT, CCEE), or depended on a hand-imported CSV was
  removed rather than kept as a partially reproducible, partially trusted
  entry. The four that remain are the ones this project can both collect and
  independently verify without a human step; see "Rebuilding the record"
  above. `TODO.md` records what was dropped and why.
- **Brazilian thermal is unresolved**, as described above.
- **Zone boundaries differ.** Germany is the DE-LU bidding zone; France and
  Spain are their national bidding zones; Brazil is the SIN, the whole
  interconnected national system rather than a submarket. Nothing here
  measures nodal congestion.
- **Fuel references carry basis risk.** World Bank Europe gas and Australian
  coal are broad monthly benchmarks; they are not a German plant's delivered
  or hedged fuel price. EEX primary-auction EUA prices can differ from
  secondary-market executions.
- **Revisions.** Operators restate published figures for days afterwards. The
  store upserts on the natural key, so re-running a window converges on the
  restatement rather than duplicating it, but a figure read today may differ
  slightly from the same figure read last week.

## Reproducing any number

```
git clone https://github.com/Pedrods20/global-power-atlas
cd global-power-atlas
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"

gpa backfill --days 30      # fill the store from the keyless sources
gpa validate                # check every partition against its contract
gpa stats                   # what the store holds
gpa benchmarks              # refresh World Bank, EEX and ECB references
gpa export                  # rebuild the tables this site reads
pytest                      # 100+ tests, including every rule on this page
```

Any figure can be interrogated directly:

```
gpa query "SELECT zone, count(*) FILTER (WHERE price < 0) * 100.0 / count(*) AS pct_negative FROM price GROUP BY 1"
```

The rules described on this page are enforced by tests, not by convention. The daylight-saving, holiday, block-boundary and negative-price cases each have a named test that fails if the behaviour changes.

<style>
main.observablehq > table {
  display: block;
  max-width: 100%;
  overflow-x: auto;
}
.observablehq-pre-container {
  max-width: 100%;
  overflow-x: auto;
}
.warn {
  border-left: 3px solid #E69F00;
  background: color-mix(in srgb, #E69F00 6%, transparent);
  padding: 0.6rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.92rem;
}
.warn p { margin: 0.4rem 0; }
</style>

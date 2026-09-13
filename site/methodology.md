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
| ERCOT | load, generation | US Energy Information Administration, API v2 | free key | US Government public domain |
| DE-LU | price, load, generation | Energy-Charts, Fraunhofer ISE | none | CC BY 4.0 |
| BR-SIN | generation | ONS open data, hourly subsystem balance | none | CC BY 4.0 |
| BR-SIN | load | ONS verified-load API, half-hourly | none | CC BY 4.0 |
| AU-NSW1 | price, load | AEMO aggregated price and demand archive | none | AEMO terms of use |
| AU-NSW1 | generation | OpenElectricity, The Superpower Institute | none | CC BY 4.0 |
| PJM, CAISO | load, generation | US Energy Information Administration, API v2 | free key | US Government public domain |
| CAISO | day-ahead price | CAISO OASIS, SP15 trading hub | none | CAISO OASIS terms |
| FR, ES | price, load, generation | Energy-Charts, Fraunhofer ISE | none | CC BY 4.0 |
| JP-TOKYO | day-ahead price | Japan Electric Power Exchange | none | provider terms |
| BR-SECO, BR-NE | hourly PLD | CCEE open data | none; official local CSV fallback | open-data terms |
| European spread references | gas and coal: World Bank; EUA: EEX; FX: ECB | public downloads | none | provider-specific |

Australia deliberately uses two providers. Price and demand come from AEMO's own archive rather than a redistributor; only the fuel split needs a third party, because that archive does not carry one.

Germany is served through Energy-Charts rather than ENTSO-E directly. The underlying figures are the same ones ENTSO-E publishes, and the switch to a direct ENTSO-E Transparency connection is a change of adapter, not of method.

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

Latest observation across datasets: `${zones.data_as_of ? zones.data_as_of.slice(0, 16).replace("T", " ") + " UTC" : "pending"}`. Each dataset's own coverage is listed above; ages are calculated when this page loads.

## Time

This is where most power market analysis goes wrong, so it is worth being explicit.

**Every instant is stored UTC-aware and interpreted through the market's own timezone.** Nothing is ever grouped by UTC calendar day or UTC calendar hour. A UTC day boundary falls in the middle of the Australian afternoon and two hours into the German night, so grouping on it silently smears every daily figure across two trading days.

**A local day has 23, 24 or 25 hours.** Daily means divide by the hours that actually existed. Daily energy is the sum of power times each interval's real duration, never a mean multiplied by 24.

**Market time is not always civil time.** AEMO settles every region of the National Electricity Market on Australian Eastern Standard Time all year and never applies daylight saving, even though New South Wales civil clocks do. The Australian zone therefore carries two timezones: `Australia/Brisbane` for the market and `Australia/Sydney` for civil life. Reading NEM data on the Sydney clock invents a 23-hour trading day the market does not have.

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

Two details catch people out. NERC on-peak **includes Saturday**, so a five-weekday assumption is wrong for North America. And NERC does **not** move a Saturday holiday to the preceding Friday the way the US federal calendar does, so the two calendars disagree in some years.

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

The settlement interval is measured from the spacing of the timestamps, never assumed. This matters because providers change it without notice: Energy-Charts moved German data from hourly to quarter-hourly, and the NEM moved from 30-minute to 5-minute settlement in October 2021. A backfill spanning that change shifts each era by its own interval.

AEMO stamps its settlement rows **interval-ending**, so a row marked 00:05 describes 00:00 to 00:05. This project stores interval-starting instants, so the resolution is subtracted from every AEMO timestamp. Omitting that shift moves the whole series forward by one interval and misaligns price against generation.

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

## Reading the Californian price

Three properties of CAISO OASIS shape what is stored, and each fails quietly if
mishandled.

**An LMP is five numbers, and only one of them is the price.** Every interval
carries a total alongside its energy, congestion and loss components, plus a
greenhouse-gas term that exists because California prices carbon into dispatch
through cap-and-trade. Only the total is stored. Taking every row would write
congestion and loss into the price column.

**An empty result arrives as XML inside a successful response.** A window with
no data still returns HTTP 200 and a valid archive, but the entry is an
OASIS report carrying error code 1000. Parsing that as CSV yields a table whose
only column is the XML declaration.

**Rate limiting also arrives as HTTP 200.** Exceeding the acceptable-use policy
returns an HTML paragraph asking for a five-second pause, not a 429, so a
generic retry layer cannot see it. Requests here are paced at six seconds.

## Known limitations

- **ERCOT and PJM have no price series.** EIA supplies balancing-authority demand and generation but publishes no price at all. ERCOT returns HTTP 403 to automated clients on every host it publishes on, and PJM's Data Miner requires a registered subscription key. California is the exception: CAISO OASIS is open, so it is the one large US market with a price here.
- **The Californian price is one hub, not a system price.** CAISO is nodal. The series stored here is the SP15 day-ahead trading hub, which is the reference most Californian forward trades settle against, while the load and generation alongside it cover the whole balancing authority. Compare its shape against other markets rather than its level against a European bidding-zone price.
- **US solar excludes rooftop.** EIA's hourly fuel-type series covers utility-scale plant only, so a US solar share is not directly comparable against a market whose operator reports behind-the-meter output.
- **Brazilian thermal is unresolved**, as described above.
- **Zone boundaries differ.** US load and generation cover balancing authorities, not price hubs or settlement nodes. Germany is the DE-LU bidding zone, Australia is New South Wales, Japan is the Tokyo price area, and CCEE prices are two separate submarkets, Southeast/Central-West and Northeast, which are the two that dominate Brazilian price formation. South and North are published by CCEE but are deliberately not registered here. Nothing here measures nodal congestion.
- **CCEE automated access can return HTTP 403.** The committed series came from the official `pld_horario_2026` CSV and the same parser accepts future official files through `GPA_CCEE_IMPORT_DIR`.
- **Fuel references carry basis risk.** World Bank Europe gas and Australian coal are broad monthly benchmarks; they are not a German plant's delivered or hedged fuel price. EEX primary-auction EUA prices can differ from secondary-market executions.
- **Revisions.** Operators restate published figures for days afterwards. The store upserts on the natural key, so re-running a window converges on the restatement rather than duplicating it, but a figure read today may differ slightly from the same figure read last week.

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

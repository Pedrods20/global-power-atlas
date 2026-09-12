---
title: Methodology
---

# Methodology

Everything needed to reproduce, or to refuse to trust, any number on this site.

```js
const zones = await FileAttachment("data/zones.json").json();
```

## Sources

Every series comes from the system or market operator, or from a named redistributor of the operator's own data. Nothing is modelled, interpolated or synthesised. A zone with no data for a dataset shows as pending rather than being filled in.

| Zone | Dataset | Provider | Credential | Licence |
|---|---|---|---|---|
| ERCOT | load, generation | US Energy Information Administration, API v2 | free key | US Government public domain |
| DE-LU | price, load, generation | Energy-Charts, Fraunhofer ISE | none | CC BY 4.0 |
| BR-SIN | load, generation | ONS open data, hourly subsystem balance | none | CC BY 4.0 |
| AU-NSW1 | price, load | AEMO aggregated price and demand archive | none | AEMO terms of use |
| AU-NSW1 | generation | OpenElectricity, The Superpower Institute | none | CC BY 4.0 |

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
    "Age (h)": d.age_hours ?? null,
  })),
);
```

```js
Inputs.table(freshness, { rows: 12, layout: "auto" })
```

Data as of `${zones.generated_at.slice(0, 16).replace("T", " ")} UTC`.

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

**Negative prices are preserved and counted.** They are the signature of inflexible supply meeting renewable surplus, and filtering them out deletes exactly the observations that matter.

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

## Known limitations

- **ERCOT is pending a credential.** The adapter is written and tested; it needs a free EIA key.
- **US solar excludes rooftop.** EIA's hourly fuel-type series covers utility-scale plant only, so a US solar share is not directly comparable against a market whose operator reports behind-the-meter output.
- **Brazilian thermal is unresolved**, as described above.
- **One zone per market.** ERCOT is a single hub, not its 8,000 settlement nodes. Germany is the DE-LU bidding zone, not its four control areas. Australia is New South Wales alone, not the whole NEM. Nothing here says anything about congestion or locational spreads.
- **No fuel or carbon price feed yet.** The spark, dark and clean spread functions are implemented and tested against worked examples, but nothing on this site plots one, because a gas, coal and EUA price source has not been wired in.
- **Revisions.** Operators restate published figures for days afterwards. The store upserts on the natural key, so re-running a window converges on the restatement rather than duplicating it, but a figure read today may differ slightly from the same figure read last week.

## Reproducing any number

```
git clone https://github.com/pedrocabral/global-power-atlas
cd global-power-atlas
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"

gpa backfill --days 30      # fill the store from the keyless sources
gpa validate                # check every partition against its contract
gpa stats                   # what the store holds
gpa export                  # rebuild the tables this site reads
pytest                      # 100+ tests, including every rule on this page
```

Any figure can be interrogated directly:

```
gpa query "SELECT zone, count(*) FILTER (WHERE price < 0) * 100.0 / count(*) AS pct_negative FROM price GROUP BY 1"
```

The rules described on this page are enforced by tests, not by convention. The daylight-saving, holiday, block-boundary and negative-price cases each have a named test that fails if the behaviour changes.

<style>
.warn {
  border-left: 3px solid #E69F00;
  background: color-mix(in srgb, #E69F00 6%, transparent);
  padding: 0.6rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.92rem;
}
.warn p { margin: 0.4rem 0; }
</style>

# Global Power Atlas

Supply, demand and price across wholesale electricity markets on four continents, rebuilt daily from primary sources and published as a static site.

**Live site:** _pending first deploy_ · **Methodology:** [`site/methodology.md`](site/methodology.md)

---

## What this is

A reproducible pipeline that collects hourly load, generation by fuel, and clearing prices from the system operators themselves, normalises them into one comparable model, and publishes the result. There is no backend, no database server and no API key in the browser. A scheduled job writes Parquet into this repository, and a static site builds from those files.

The point of the project is not that it draws charts. It is that the numbers underneath the charts are defensible. Wholesale power data is full of traps that generic time-series tooling walks straight into, and the [methodology page](site/methodology.md) documents how each one is handled.

## Coverage

| Zone | Market | Operator | Source | Key required |
|---|---|---|---|---|
| `ERCOT` | Texas | ERCOT | EIA v2 | yes, free |
| `DE-LU` | Germany-Luxembourg | 50Hertz, Amprion, TenneT, TransnetBW | Energy-Charts | no |
| `BR-SIN` | Brazil National Interconnected System | ONS | ONS open data | no |
| `AU-NSW1` | Australia NEM, New South Wales | AEMO | OpenNEM | no |

One zone per continent, proven end to end. Adding a zone is an entry in [`src/gpa/zones.py`](src/gpa/zones.py) once its source adapter exists; the CLI, the store and the site pick it up with no further change.

## What two years of data shows

Every figure below is computed by this repository from the sources above, over roughly two years to September 2026, and can be reproduced with the commands further down.

**The peak-to-off-peak spread is collapsing, and in Australia it has inverted.**

| Year | DE-LU spread, EUR/MWh | AU-NSW1 spread, AUD/MWh |
|---|---|---|
| 2024 | 44.59 | 87.99 |
| 2025 | 17.11 | 38.01 |
| 2026 | 11.98 | −1.60 |

These are real block spreads, on-peak mean minus off-peak mean under each market's own block definition, not intraday range. New South Wales now clears its business-day peak block *below* the hours around it. Solar has eaten the shape that peaking plant was built to sell into.

**Negative prices are no longer exceptional.**

| Zone | Intervals below zero | Deepest | Longest unbroken run |
|---|---|---|---|
| AU-NSW1 | 10.58% of 210,204 five-minute intervals | −1,000 AUD/MWh, the market floor | 11.7 hours |
| DE-LU | 3.79% of 27,129 quarter-hourly intervals | −250.32 EUR/MWh | 20.0 hours |

A twenty-hour unbroken run below zero is not a price signal, it is a curtailment event. Frequency alone would have missed it.

**Capture rates quantify the cannibalisation directly.**

| Zone | Solar | Wind |
|---|---|---|
| DE-LU | 0.563 | 0.876 |
| AU-NSW1 | 0.512 | 0.864 |

Generation-weighted, not approximated by a fixed window of daylight hours. German solar earns 53.48 EUR/MWh against a time-weighted average of 94.91. Wind holds up far better because its output is not pinned to one time of day.

**Demand shape differs more than demand level.** Brazil runs a load factor of 0.780, Germany 0.700, New South Wales 0.568. The Australian system carries roughly the same peak-to-average burden on a 13 GW peak that Brazil carries on 106 GW.

**Brazil's carbon intensity is deliberately blank.** The operator folds every thermal unit into one aggregate column, so 87% of generation has a known emission factor and the missing 13% is the entire emitting fleet. Renormalising over the clean remainder returns 0 g/kWh for a system that is not carbon free. Germany reports 281 g/kWh and New South Wales 557 g/kWh on the same operational basis, both above 99% coverage.

## Why the numbers are trustworthy

These are the decisions that separate this from a dashboard that merely renders:

- **Market time, not UTC days.** Every instant is stored UTC-aware and interpreted through the zone's market timezone. Nothing is ever grouped by UTC calendar day. The Australian NEM carries a separate civil timezone because AEMO settles on Australian Eastern Standard Time all year, so market time and the clock in Melbourne disagree for half the year.
- **A local day has 23, 24 or 25 hours.** Daily means divide by the hours that existed, not by 24.
- **Peak and off-peak are market blocks.** NERC on-peak is hour-ending 0700 through 2200, Monday through Saturday, excluding the six NERC holidays. European peakload is 08:00 to 20:00 CET, Monday through Friday, holidays included. They are not the daily maximum and minimum.
- **Negative prices are preserved and counted.** They are the signature of renewable penetration, so nothing filters them out. Because price can be zero or negative, log returns are undefined, and this project uses arithmetic differences instead. Volatility annualises at 365 days, not the 252 trading days of a financial exchange, because spot power settles every day of the year.
- **MW and MWh are different units.** Power is integrated over the interval's real duration. Nothing assumes a 60-minute interval.
- **Every table is validated at the boundary.** Schema contracts in [`src/gpa/schema.py`](src/gpa/schema.py) reject a source that changed shape, naming the offending column, before anything reaches storage.

Brazil is a deliberate caveat rather than a silent gap. It is included for supply and demand, and its peak block is labelled as a distribution-tariff construct, not a traded product, because Brazil has settled a genuinely hourly PLD since 2021.

## Architecture

```
GitHub Actions (daily cron)
        │
        ▼
   Python ETL  ──fetch──►  EIA · Energy-Charts · ONS · OpenNEM
        │
        │  validate against schema contracts
        ▼
   Parquet, partitioned by zone and month, committed to this repo
        │
        ▼
   Observable Framework (static build)
        │
        ▼
   GitHub Pages
```

History accumulates in git rather than expiring from a cache, so any past state of the dataset is recoverable with `git checkout`, and backtesting is the natural use case rather than an impossible one.

## Repository layout

```
src/gpa/           Python package
  zones.py         canonical zone registry; everything derives from here
  calendar.py      market calendars, DST, peak/off-peak blocks
  schema.py        schema contracts for the three fact tables
  store.py         partitioned Parquet store and DuckDB queries
  sources/         one adapter per upstream provider
  metrics/         load, price, mix and spread analytics
  cli.py           gpa backfill | ingest | validate | stats
data/curated/      committed Parquet, partitioned by zone and month
site/              Observable Framework site
tests/             contract, calendar and metric tests
```

## Reproduce it

Requires Python 3.11 or newer and Node 18 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use source .venv/bin/activate elsewhere
pip install -e ".[dev]"

gpa zones                      # list the registry
gpa backfill --days 30         # fill data/curated from keyless sources
gpa validate                   # check every Parquet file against its contract
pytest

cd site && npm install && npm run build
```

`ERCOT` additionally needs a free [EIA API key](https://www.eia.gov/opendata/register.php) in `EIA_API_KEY`. Every other zone works with no credentials at all.

## Data sources and licensing

| Source | Provider | Terms |
|---|---|---|
| [EIA Open Data v2](https://www.eia.gov/opendata/) | US Energy Information Administration | US Government public domain |
| [Energy-Charts](https://api.energy-charts.info/) | Fraunhofer ISE | CC BY 4.0 |
| [ONS Open Data](https://dados.ons.org.br/) | Operador Nacional do Sistema Eletrico | CC BY 4.0 |
| [OpenNEM](https://opennem.org.au/) | The Superpower Institute | CC BY 4.0 |

Attribution for each series appears on the methodology page alongside the retrieval date.

## License

MIT for the code. The underlying data remains under the terms of its provider.

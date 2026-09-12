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

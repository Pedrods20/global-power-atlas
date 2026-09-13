# Global Power Atlas

Supply, demand and price across wholesale electricity markets in five continents, rebuilt from primary and documented public sources and published as a static site.

**Live site:** [Open dashboard](https://pedrods20.github.io/global-power-atlas/) · **Methodology:** [`site/methodology.md`](site/methodology.md)

---

## What this is

A reproducible pipeline that collects hourly load, generation by fuel, and clearing prices from the system operators themselves, normalises them into one comparable model, and publishes the result. There is no backend, no database server and no API key in the browser. A scheduled job writes Parquet into this repository, and a static site builds from those files.

The point of the project is not that it draws charts. It is that the numbers underneath the charts are defensible. Wholesale power data is full of traps that generic time-series tooling walks straight into, and the [methodology page](site/methodology.md) documents the conventions and limitations.

![Dashboard preview](docs/screenshots/dashboard-1440.png)

## Coverage

| Zone | Market | Operator | Source | Key required |
|---|---|---|---|---|
| `ERCOT` | Texas | ERCOT | EIA v2 | yes, free |
| `DE-LU` | Germany-Luxembourg | 50Hertz, Amprion, TenneT, TransnetBW | Energy-Charts | no |
| `BR-SIN` | Brazil National Interconnected System | ONS | ONS open data | no |
| `AU-NSW1` | Australia NEM, New South Wales | AEMO | AEMO / OpenElectricity | no |
| `PJM` | Eastern US | PJM Interconnection | EIA v2 | yes, free |
| `CAISO` | California | California ISO | EIA v2 | yes, free |
| `FR` | France | RTE | Energy-Charts | no |
| `ES` | Spain | Red Eléctrica | Energy-Charts | no |
| `JP-TOKYO` | Japan, Tokyo area | JEPX | JEPX | no |
| `BR-SECO`, `BR-NE` | Brazilian PLD submarkets | CCEE | CCEE open data | local official CSV fallback |

Coverage is dataset-specific. The three US balancing authorities carry hourly demand and generation from EIA, while US wholesale price is a separate integration. Brazilian PLD remains separated by CCEE submarket and is never presented as one national price.

**Providers do not publish at the same speed.** Most zones here sit within a few hours of real time. Brazilian generation is the exception: the ONS hourly balance trails real time by about two days, and no faster ONS source for generation by technology exists. Brazilian generation, fuel mix and renewable share therefore end about two days before every other market on the site. Brazilian load does not share that lag, because it comes from the ONS verified-load API instead and stays within about an hour. The [methodology page](site/methodology.md) sets out both, along with two ways that API fails silently if read carelessly.

## Analytical questions

- How do on-peak and off-peak prices differ under each zone's block definition?
- What fraction of observed time clears below zero, and how long do negative-price runs last?
- How do generation-weighted solar and wind revenues compare with baseload prices?
- How do load shape, generation mix and renewable share vary by market?
- What do historical German clean spark and dark screening spreads show after benchmark fuel and EUA costs?

The site reports observed data, not causal attribution. A negative-price episode does not by itself prove curtailment, and a falling block spread does not isolate the effect of solar. These hypotheses need additional dispatch, outage and constraint data.

The initial store covers approximately September 2024 to September 2026. The first and last calendar years are partial; provider gaps and different settlement resolutions also matter. Compare matched date ranges before making annual trend claims. Carbon intensity is an estimate from technology factors, withheld below 95% known-factor coverage; Brazil's unresolved thermal category currently prevents publication.

## Why the numbers are trustworthy

These are the decisions that separate this from a dashboard that merely renders:

- **Market time, not UTC days.** Every instant is stored UTC-aware and interpreted through the zone's market timezone. Nothing is ever grouped by UTC calendar day. The Australian NEM carries a separate civil timezone because AEMO settles on Australian Eastern Standard Time all year, so market time and the clock in Melbourne disagree for half the year.
- **A local day has 23, 24 or 25 hours.** Daily means divide by the hours that existed, not by 24.
- **Peak and off-peak are market blocks.** NERC on-peak is hour-ending 0700 through 2200, Monday through Saturday, excluding the six NERC holidays. European peakload is 08:00 to 20:00 CET, Monday through Friday, holidays included. They are not the daily maximum and minimum.
- **Negative prices are preserved and counted.** They are economically meaningful observations, so nothing filters them out. Because price can be zero or negative, log returns are undefined, and this project uses arithmetic differences instead. Volatility annualises at 365 days, not the 252 trading days of a financial exchange, because spot power settles every day of the year.
- **MW and MWh are different units.** Power is integrated over the interval's real duration. Nothing assumes a 60-minute interval.
- **Every table is validated at the boundary.** Schema contracts in [`src/gpa/schema.py`](src/gpa/schema.py) reject a source that changed shape, naming the offending column, before anything reaches storage.

Brazil is a deliberate caveat rather than a silent gap. It is included for supply and demand, and its peak block is labelled as a distribution-tariff construct, not a traded product, because Brazil has settled a genuinely hourly PLD since 2021.

## Architecture

```
GitHub Actions (daily cron)
        │
        ▼
   Python ETL  ──fetch──►  EIA · Energy-Charts · ONS · AEMO · JEPX · CCEE
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
data/curated/      committed interval Parquet, partitioned by zone and month
data/reference/    committed monthly fuel, EUA and FX references
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

npm ci
gpa export
npm run build
npm run dev
```

The US zones need a free [EIA API key](https://www.eia.gov/opendata/register.php) in `EIA_API_KEY`. CCEE can be read automatically when the provider permits it; `GPA_CCEE_IMPORT_DIR` points to official CSV downloads when access returns HTTP 403.

## Data sources and licensing

| Source | Provider | Terms |
|---|---|---|
| [EIA Open Data v2](https://www.eia.gov/opendata/) | US Energy Information Administration | US Government public domain |
| [Energy-Charts](https://api.energy-charts.info/) | Fraunhofer ISE | CC BY 4.0 |
| [ONS Open Data](https://dados.ons.org.br/) | Operador Nacional do Sistema Eletrico | CC BY 4.0 |
| [OpenNEM](https://opennem.org.au/) | The Superpower Institute | CC BY 4.0 |
| [AEMO](https://www.aemo.com.au/energy-systems/electricity/national-electricity-market-nem/data-nem/market-data-nemweb) | Australian Energy Market Operator | AEMO terms of use |
| [JEPX](https://www.jepx.jp/electricpower/market-data/spot/) | Japan Electric Power Exchange | provider terms |
| [CCEE Open Data](https://dadosabertos.ccee.org.br/dataset/pld_horario) | Câmara de Comercialização de Energia Elétrica | open-data terms |
| [World Bank Pink Sheet](https://www.worldbank.org/en/research/commodity-markets) | World Bank | CC BY 4.0 |
| [EEX EU ETS auctions](https://www.eex.com/en/markets/environmentals/eu-ets1-eu-ets2-auctions/eu-ets1-auctions) | European Energy Exchange | provider terms |
| [ECB exchange rates](https://data.ecb.europa.eu/data/datasets/EXR) | European Central Bank | ECB data terms |

Attribution for each series appears on the methodology page alongside the retrieval date.

## License

MIT for the code. The underlying data remains under the terms of its provider.

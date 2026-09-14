# Global Power Atlas

Reproducible electricity-market analytics with a focused research question:
can a leakage-safe day-ahead price forecast create measurable value for battery
dispatch?

[![CI](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Pedrods20/global-power-atlas/actions/workflows/ci.yml)
[![Deploy](https://github.com/Pedrods20/global-power-atlas/actions/workflows/deploy.yml/badge.svg)](https://github.com/Pedrods20/global-power-atlas/actions/workflows/deploy.yml)

**[Open the live dashboard](https://pedrods20.github.io/global-power-atlas/)** ·
[Forecast case study](site/forecast.md) · [Battery case study](site/battery.md) ·
[Methodology](site/methodology.md)

![Dashboard preview](docs/screenshots/dashboard-1440.png)

## Portfolio case

The project turns public power-system data into a compact decision workflow:

1. Validate interval prices, demand and generation by fuel in market time.
2. Forecast the DE-LU day-ahead price using only information available before
   the market gate.
3. Convert the forecast into a constrained battery dispatch and settle it on
   realised prices.

The dashboard is intentionally small: monthly historical context, the forecast
benchmark, the battery value translation and one methodology page. The value is
in the research design and the audit trail, not in a large collection of charts.

```text
Operator data → validated Parquet → walk-forward forecast → battery dispatch → static site
```

## Featured result

The current historical DE-LU backtest covers 369 complete days. For a 1 MW / 4
MWh battery, Ridge captures 94.5% of the constrained perfect-foresight value;
LightGBM captures 94.2%. The no-trade baseline is included explicitly.

These are retrospective development results, not prospective trading returns.
The published base case has zero asset-specific operating and degradation cost;
both costs are parameters in the optimizer and must be calibrated before using
the result for an investment decision.

## Research design

- **Information set:** lagged prices, calendar variables and lagged residual
  load; no realised delivery-day fundamentals enter the retrospective forecast.
- **Validation:** expanding walk-forward evaluation with naive baselines, Ridge
  and pooled LightGBM. Hyperparameters are selected before the evaluation
  period.
- **Battery:** explicit power, energy, efficiency, SOC, terminal SOC and
  one-cycle-per-day constraints. Forecast-guided dispatch is settled against
  observed prices; perfect foresight is an upper bound under the same physics.
- **Time and units:** UTC-aware source timestamps are interpreted through each
  market's local clock. Interval duration is carried explicitly, including the
  European hourly-to-quarter-hour transition.

## Scope and data

The committed historical store covers four zones from two public sources:

| Area | Historical series | Role in the portfolio |
|---|---|---|
| Germany-Luxembourg (DE-LU) | Price, load, generation | Forecast and battery reference market |
| France (FR) | Price, load, generation | Cross-market historical context |
| Spain (ES) | Price, load, generation | Cross-market historical context |
| Brazil (SIN) | Load, generation | Non-price system comparison |

The monthly ingestion workflow refreshes the historical dashboard. A separate
market-clock workflow can issue and reconcile DE-LU forecasts; prospective
acceptance remains deliberately separate from the static historical pages.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

gpa validate
gpa export

npm ci
npm run build
```

Useful commands:

```bash
gpa backtest --zone DE-LU --scope all
gpa issue --zone DE-LU
gpa reconcile --zone DE-LU
gpa battery --zone DE-LU
```

The repository has no runtime backend. The site reads only the small,
pre-computed files under `site/data`; browser-visible credentials are never
required.

## Quality checks

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest -q
npm run build
```

The forecast and battery pages state their assumptions and limitations beside
the result. The historical dashboard is descriptive; it is not investment or
trading advice.

## Repository map

```text
src/gpa/forecast/   leakage-safe panel, models, walk-forward scoring and ledger
src/gpa/battery.py  constrained dispatch and economic backtest
src/gpa/export.py   deterministic static-site data products
site/               four focused Observable Framework pages
data/curated/       versioned interval observations, partitioned by month
tests/              schema, source, forecast, dispatch and site-contract tests
scripts/             browser smoke test used by CI
```

## Limitations and next step

The forecast is an hourly analytical benchmark and does not predict each
quarter-hour trade. The study is zonal rather than nodal, so congestion and
basis are outside scope. The next credible step is a separately recorded
four-to-six-week prospective ledger, followed by reconciliation and battery
acceptance; extending the historical backtest would not answer that question.

## License

MIT for the code. Underlying observations remain subject to their providers'
terms; attribution is listed on the [methodology page](site/methodology.md).

# Global Power Atlas

Reproducible electricity-market analytics with a focused research question:
can a temporally evaluated day-ahead price forecast create measurable value for battery
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
2. Benchmark the DE-LU day-ahead price with lagged inputs and explicit
   publication-vintage limitations.
3. Convert the forecast into a constrained battery dispatch and settle it on
   realised prices.

The dashboard is intentionally small: monthly historical context, the forecast
benchmark, the battery value translation and one methodology page. The value is
in the research design and the audit trail, not in a large collection of charts.

```text
Operator data → validated Parquet → walk-forward forecast → battery dispatch → static site
```

## Featured result

The current release is a frozen, content-addressed DE-LU backtest
(`data/experiments/`, committed alongside the site data it produced), not a
live recompute that would silently drift as the historical store grows.
Ridge's day-ahead price MAE is EUR 22.07/MWh over 58,645 scored clock-hour
cells, a 24.3% reduction against the best naive baseline (EUR 29.14/MWh,
previous day). On the resulting 2,404-day common sample, a 1 MW / 4 MWh battery
captures 90.1% of the constrained perfect-foresight value with Ridge (81.8%
with LightGBM). Ridge adds **EUR 29,014/MW (4.8%)** over similar-day dispatch,
the strongest of all three fixed naive comparators in this observed sample.
The exploratory paired 95% interval is EUR 22,515-35,347/MW; removing the five
largest positive incremental days still leaves EUR 26,093/MW. These are
sample-period figures, not annualized returns.

These are retrospective development results from a reproducible release, not
prospective trading returns. The published base case has zero asset-specific
operating and degradation cost; both costs are explicit optimizer inputs, and
the site reports both illustrative non-zero cost cases and fixed efficiency,
signal-attenuation and calendar-downtime stresses. The combined case leaves
EUR 18,623/MW incremental margin, not a forecast of future profit. All five
models are compared at 1/2/4h; LightGBM underperforms the best naive by EUR 14,573/MW
at 1h in the zero-cost case. Higher model complexity is not automatically more
valuable, and larger absolute battery margin does not establish the best
investment duration without CAPEX and fixed/lifetime costs.
Reproduce with `gpa export --check` against the committed snapshot.

## Research design

- **Information set:** lagged prices, calendar variables and lagged residual
  load; no realised delivery-day fundamentals enter the retrospective forecast.
  Stored provider revisions cannot certify original publication-time vintages.
- **Validation:** expanding walk-forward evaluation with naive baselines, Ridge
  and pooled LightGBM. Hyperparameters are selected before the evaluation
  period; this already-inspected history remains development evidence, not an
  untouched final test. Sensitivities do not reselect the models or parameters.
- **Battery:** explicit power, energy, efficiency, SOC, terminal SOC and
  one-cycle-per-day constraints. Forecast-guided dispatch is settled against
  observed prices; perfect foresight is an upper bound under the same physics.
- **Market structure:** the battery page also correlates DE-LU's own
  renewable and storage capacity (Energy-Charts, including Germany's official
  2030 targets) against solar's capture-rate erosion (0.93 to 0.51,
  2019-2026), the on/off-peak spread going negative, and this project's own
  arbitrage margin. Competition from Germany's fast-growing battery fleet is
  not yet visibly compressing that margin, stated as "not detectable against
  a larger co-moving trend," not "no effect."
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

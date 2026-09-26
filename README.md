# German Power Market Research
### Day-ahead price shape, forecasting and battery value

Independent research by **Pedro Cabral**, a power-market analyst focused on Brazil, Chile and Argentina, extending that work to the Germany–Luxembourg market.

This study examines how the daily price profile has changed and what that means for battery dispatch. It then tests whether a price forecast adds value over simple scheduling strategies.

**[Explore the analysis](https://pedrods20.github.io/german-power-research/)** · [Forecast results](https://pedrods20.github.io/german-power-research/forecast) · [Battery economics](https://pedrods20.github.io/german-power-research/battery)

## Key findings

**1. A weaker peak premium can coexist with wider daily spreads.**  
The on-peak/off-peak spread fell from **+€10.6/MWh in 2019 to −€13.7/MWh in 2026 YTD**, while the average daily high–low range increased relative to baseload prices. For storage valuation, the timing and duration of low and high prices matter more than the block spread alone.

**2. Solar capture has deteriorated.**  
Solar's capture rate fell from **93% to 51%** over the same period. This is consistent with increasing pressure on midday prices as solar capacity expands. The analysis documents the relationship; it does not isolate solar's causal contribution from fuel prices, weather or other market changes.

**3. Better price forecasts deliver a smaller improvement in dispatch value.**  
Ridge regression reduced hourly mean absolute error by **24.3%** versus the strongest naive price benchmark. For a **1 MW / 4 MWh battery**, forecast-based dispatch added approximately **€4,400/MW/year** over the strongest fixed naive dispatch strategy in the historical sample. The comparison strategy differs between the price and dispatch tests.

## Commercial interpretation

Much of the modelled battery margin was already captured by a simple similar-day forecast. The incremental value of a more accurate model therefore needs to be assessed through dispatch outcomes, costs and downside.

Ridge underperformed the strongest naive dispatch comparator on **827 of 2,404 days**. The study reports these losses alongside average gains and tests efficiency, costs, downtime and a second daily charge–discharge episode.

Storage build-out is a risk to future spreads. Aggregate fleet capacity and average duration alone cannot establish how much storage competes in day-ahead arbitrage or whether competition has reduced spreads relative to what they would otherwise have been.

## Explore the work

| Page | What it covers |
|---|---|
| [Market view](https://pedrods20.github.io/german-power-research/) | Price shape, capture rates, negative prices and storage capacity |
| [Forecast evidence](https://pedrods20.github.io/german-power-research/forecast) | Model comparisons, performance in price extremes and prospective monitoring |
| [Storage value](https://pedrods20.github.io/german-power-research/battery) | Dispatch margins, incremental forecast value and sensitivity cases |
| [Methodology](https://pedrods20.github.io/german-power-research/methodology) | Assumptions, data provenance, evaluation protocol and reproduction instructions |

## Scope and limitations

- **Historical research:** forecast evaluation covers 2020–2026 on history inspected during development. Results are not a live trading track record. The prospective pilot is reported separately.
- **Battery assumptions:** 1 MW / 4 MWh, 90% round-trip efficiency, at most one charge–discharge episode per day, and zero initial and terminal state of charge. Headline margins exclude operating, degradation and capital costs; separate sensitivities include illustrative variable costs.
- **Market coverage:** day-ahead prices at bidding-zone level. Intraday trading and balancing services are outside scope. Hourly averages do not represent individual 15-minute products.
- **Comparability:** 2026 market figures are year-to-date and subject to seasonality. Historical inputs contain provider revisions, rather than verified publication-time snapshots.

**Data:** [Energy-Charts / Fraunhofer ISE](https://www.energy-charts.info/), including figures redistributed from ENTSO-E and SMARD. Detailed attribution and definitions are in the [methodology](site/methodology.md).

## About me

**Pedro Cabral** — power-market research focused on fundamentals, price formation and the commercial implications of the energy transition. My regional experience covers Brazil, Chile and Argentina; this independent project applies that analytical approach to European power and storage.

[GitHub profile](https://github.com/Pedrods20)

Code: [MIT](LICENSE). Underlying data remains subject to provider terms.

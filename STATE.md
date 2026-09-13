# PROJECT STATE

Last updated: 2026-09-13 · Repository: `global-power-atlas` · Replaces:
`power-pulse-global`

## Current result

Global Power Atlas is a Python ETL and Observable Framework static site. It
stores validated interval data as Parquet, computes market-aware aggregates,
and publishes them without a backend or browser-visible credentials.

- 11 registered zones across five continents.
- 3,584,484 interval/fuel observations in 591 validated monthly partitions.
- Price: DE-LU, AU-NSW1, FR, ES, JP-TOKYO and two CCEE submarkets
  (Southeast/Central-West and Northeast). South and North were dropped by
  decision on 2026-09-12.
- Load and generation: DE-LU, AU-NSW1, BR-SIN, ERCOT, PJM, CAISO, FR and ES,
  subject to each provider's reported categories.
- 24 aligned months of World Bank fuel, EEX EUA and ECB FX references.
- Historical German clean spark and clean dark screening spreads with explicit
  efficiency, emissions and coal-energy assumptions.

Steps 1-6 of the original delivery are complete. [`TODO.md`](TODO.md) now
holds the forward roadmap: 14 open items, ordered by how much each one affects
the credibility of the published work. Treat it as the authoritative list of
what is still missing.

## Completed in the current delivery

- Export freshness checks compare table values instead of Parquet binary
  metadata; build-clock fields were removed from committed metadata.
- Scheduled ingestion now triggers deployment even when the commit is created
  by `GITHUB_TOKEN`.
- Duration-weighted load/price metrics and gap-aware negative-price runs are
  covered by regression tests.
- German missing price months were backfilled.
- Browser smoke covers every page at 1440 px and 390 px, checking JavaScript
  errors, chart presence and horizontal overflow. Screenshots are under
  `docs/screenshots/`.
- EIA parsing was fixed and two years of ERCOT, PJM and CAISO load/generation
  were backfilled.
- France and Spain were added through Energy-Charts.
- Tokyo day-ahead price was added through official JEPX fiscal-year CSVs.
- Official CCEE `pld_horario_2026.csv` was imported for BR-SECO, BR-S, BR-NE
  and BR-N: 6,117 consecutive hourly rows per submarket, without gaps,
  duplicates or null prices.
- Official monthly fuel/carbon/FX references and a dedicated spread page were
  added.
- Public repository created at
  <https://github.com/Pedrods20/global-power-atlas>; `EIA_API_KEY` is stored as
  an encrypted Actions secret. The local `.env` is ignored.
- Commit `aa1ea71` was pushed to `main`; CI run `34725613826` and Pages deploy
  run `34725613889` completed successfully.
- The public site at <https://pedrods20.github.io/global-power-atlas/> passed the
  six-route desktop/mobile browser smoke against the hosted build.

## Completed on 2026-09-13

- France and Spain were backfilled from 4 to 25 monthly partitions each, so
  every Energy-Charts zone now carries the same two years.
- CCEE 2024 and 2025 were imported from the official CSVs: 23,661 contiguous
  hourly observations per submarket from 2024-01-01, no gaps or duplicates.
- The Brazilian South and North submarkets were removed from the registry and
  their partitions deleted, at the user's request.
- Brazilian load was moved off the balance file and onto the ONS verified-load
  API. The lag fell from 46 hours to under one hour. The series was rebuilt at
  half-hourly resolution: 35,040 observations over exactly 730 days.
- Brazilian generation keeps its roughly two-day lag, which is the provider's
  and has no faster ONS source. It is now documented under "Publication lag" in
  the methodology instead of looking like a collection failure.
- Six routes pass the browser smoke at 1440 px and 390 px with no JavaScript
  errors and no horizontal overflow.

## Architecture

```
GitHub Actions daily cron
  → gpa ingest / gpa benchmarks
  → schema validation
  → partitioned Parquet committed to git
  → gpa export
  → Observable Framework static build
  → GitHub Pages
```

Important paths:

- `src/gpa/zones.py`: market registry, timezone, currency and block rules.
- `src/gpa/sources/`: adapters; fetch and normalize, but do not analyse or
  write.
- `src/gpa/schema.py`: canonical interval contracts and fuel taxonomy.
- `src/gpa/store.py`: monthly Parquet upserts and DuckDB views.
- `src/gpa/metrics/`: price, load, mix and spread calculations.
- `src/gpa/benchmarks.py`: World Bank, EEX and ECB reference ingestion.
- `src/gpa/export.py`: compact site tables and deterministic freshness check.
- `site/`: Observable pages; `site/data/` contains committed exports.

## Domain rules

- Store UTC-aware interval-start timestamps; group through market-local time.
- Integrate MW over each observation's duration; never assume an hourly row.
- Keep market time separate from civil time where required by AEMO.
- Apply each market's declared peak block. Do not substitute daily max/min.
- Preserve negative and zero prices. Use arithmetic price changes.
- Keep currencies separate except where historical ECB FX is explicitly part
  of a European spread calculation.
- Keep CCEE PLD by submarket and BR-SIN physical load/generation separate.
- Missing observations stay missing. Do not synthesize provider data.
- Withhold carbon intensity below 95% known-factor generation coverage.
- Label carbon intensity and thermal spreads as estimates with assumptions.

## Provider constraints

- EIA requires `EIA_API_KEY`; the key is in ignored local configuration and
  GitHub Actions secrets.
- The ONS verified-load API exposes a `SIN` aggregate that answers with every
  value zeroed, so national load is summed from its four submarket areas and a
  timestamp is only kept when all four reported. Its Southeast code is `SECO`;
  the older `SE` returns an empty list rather than an error.
- The ONS hourly balance trails real time by about two days. No faster ONS
  source for generation by technology exists.
- CCEE returned HTTP 403 to automated requests on this workstation. The
  official file is under `data/raw/ccee/`, and `GPA_CCEE_IMPORT_DIR` in the
  local `.env` enables its parser. Raw downloads are ignored; curated output
  is committed.
- Energy-Charts can return HTTP 429 during long backfills; the HTTP layer
  retries and honours `Retry-After`.
- OpenElectricity rejects hourly windows longer than 32 days.
- US EIA-930 data contains balancing-authority load/generation, not hub or
  nodal wholesale prices.
- Fuel and carbon inputs are monthly reference benchmarks and carry basis risk.

## Resume instructions

Work from `C:\Users\Pedro\Desktop\Python\global-power-atlas`. Read
`TODO.md` first and continue from its first unchecked action. Do not use the
old `power-pulse-global` directory.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\gpa.exe validate
.\.venv\Scripts\gpa.exe export
.\.venv\Scripts\gpa.exe export --check
npm run build
```

After changes to ingestion or metrics, regenerate and commit `site/data`.
Agree the next scope with the user before beginning a new increment; the
roadmap is a menu, not a queue to work through unprompted. Keep `TODO.md`
current as the handoff record.

The largest known gaps are France and Spain holding three months against two
years elsewhere, CCEE holding 2026 only, and the three US zones carrying no
wholesale price at all.

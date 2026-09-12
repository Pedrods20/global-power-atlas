# Delivery checklist and AI handoff

Work resumed from Claude's commits `7db61d9` and `cbc012d` in
`C:\Users\Pedro\Desktop\Python\global-power-atlas`. The user authorized steps
1–6, including creation of a public repository and GitHub Pages publication.
Keep this file current so another AI can resume from the first unchecked item.

## 1. Automation

- [x] Deterministic exports and a value-based `gpa export --check` command.
- [x] Deploy after scheduled ingestion, including bot commits via `workflow_run`.
- [x] Regression tests for the corrected behavior.

## 2. Browser validation

- [x] Inspect all five pages at 1440 px and 390 px with Playwright/Chrome.
- [x] Verify charts, JavaScript errors and horizontal overflow.
- [x] Save desktop/mobile screenshots and `scripts/browser-smoke.mjs`.

## 3. Methodology and presentation

- [x] Remove unsupported causal claims and label partial periods.
- [x] Reconcile README, methodology and STATE after all step 6 markets land.
- [x] Document final provenance, limitations and reproduction commands.

## 4. Publication

- [x] Create public repository `Pedrods20/global-power-atlas`.
- [x] Add encrypted `EIA_API_KEY` GitHub Actions secret.
- [x] Push commit `aa1ea71`, configure Pages with GitHub Actions, and verify the
  hosted site after successful CI and deploy runs.

## 5. ERCOT

- [x] Configure EIA credential in ignored `.env` and Actions secret.
- [x] Backfill two years: 17,519 load and 138,543 generation observations.
- [x] Keep ERCOT price out until a separate supported public source is integrated.

## 6. Expansion

- [x] Import official local CCEE `pld_horario_2026.csv`: 6,117 contiguous hours
  for each of BR-SECO, BR-S, BR-NE and BR-N; no gaps, duplicates or nulls.
- [x] Add PJM and CAISO via EIA; backfills completed without failures.
- [x] Add France and Spain via Energy-Charts; backfills completed without failures.
- [x] Add Tokyo day-ahead price via JEPX; 35,040 observations imported.
- [x] Integrate World Bank fuel, EEX EUA and ECB FX references; publish 24
  monthly clean spark/dark observations with visible engineering assumptions.
- [x] Validate observations and regenerate the site: 113 tests; Ruff lint and
  format; 435 partitions; deterministic export; six-page Observable build.
- [x] Browser-check all six pages at 1440 px and 390 px with no JavaScript
  errors or overflow; refresh the portfolio screenshots.
- [x] Publish the expansion and verify CI/Pages on GitHub.

## Current state and next actions

- The CCEE source must use the local file through `GPA_CCEE_IMPORT_DIR`; direct
  automated downloads returned HTTP 403 on this computer.
- Curated backfills already exist for ERCOT, PJM, CAISO, FR, ES and JP-TOKYO.
- Scheduled ingestion explicitly excludes the four CCEE zones while automated
  access is blocked; refresh them manually from new official CSVs.
- Verified baseline: 2,380,416 stored observations, 13 zones, 24 spread months,
  113 passing tests, zero credential-scan hits. `.env` and `data/raw/` are
  confirmed ignored.
- Steps 1–6 are complete. The public site is
  `https://pedrods20.github.io/global-power-atlas/`; its six routes passed the
  browser smoke against the hosted build at both viewport sizes.
- CI run `34725613826` and Deploy run `34725613889` completed successfully for
  commit `aa1ea71`. The next AI should begin by agreeing a new scope with the
  user; do not infer a step 7 from the older roadmap.

## Useful commands

Run commands from the project directory with the project environment activated:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\gpa.exe export
.\.venv\Scripts\gpa.exe export --check
npm run build
node scripts/browser-smoke.mjs
```

External credentials and provider restrictions must remain documented as
pending and must never be replaced with synthetic observations.

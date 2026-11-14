# Meta data sources

Hand-built files. Every row needs a source and a date. **`activity_tags.csv` is written by humans only** (never by code or an LLM).
`data.load_meta(name)` validates each file on load (SPEC §3.2) and raises `ValueError` on a bad one. A file that does not
exist yet loads as `None`, and the checks that need it report `unknown` (never a guess).

| File | Columns | Status |
|---|---|---|
| `asset_meta.csv` | `ticker, mer_pct, mer_source_url, mer_as_of, has_lookthrough, themes` | Loader + H12/H13 built (P1.2). File not built yet (P1 step 10): H12 and H13 report `unknown` |
| `etf_sector_weights.csv` | `etf, sector, weight, source_url, as_of` | Loader built (P1.2). File TODO (P2 step 13) |
| `activity_tags.csv` | `ticker, tag, evidence_summary, source_url, as_of, added_by` | Loader + H11 built (P1.2). File not built yet (P1 step 10): H11 reports `unknown` |

## Formats and validation

**`asset_meta.csv`**: one row per ticker (duplicates rejected).
- `mer_pct`: percent points (0.18 = 0.18 %), not a fraction. `0` for single stocks and crypto (no source needed).
  For an ETF, fill it from the issuer page, with `mer_source_url` and `mer_as_of`. Leave it blank when unknown: H12 then
  reports `unknown`. A row with a `mer_pct` > 0, or an ETF row of the starter universe with any `mer_pct`, and no
  `mer_source_url` is rejected.
- `has_lookthrough`: `true` / `false` (blank = false). Whether ETF constituent data is available.
- `themes`: semicolon list (`ai;semis`), parsed to a list of strings on load; blank = no theme. Lowercase snake_case, same
  vocabulary as `limits.max_theme_weight` keys (H13 compares exact strings).

**`etf_sector_weights.csv`**: `weight` is a fraction (0-1); `source_url` and `as_of` are required on every row.

**`activity_tags.csv`**: `ticker, tag, source_url, as_of, added_by` are required on every row (a row missing any is
rejected); `evidence_summary` is free text. `tag` uses the vocabulary of `limits.excluded_activities` (`tobacco`,
`accounting_fraud_last_5y`, `significant_environmental_controversy`, `significant_social_controversy`,
`fossil_fuel_producer`, ...). `added_by` is a human team member. A ticker with no row for an excluded tag is **not**
cleared: H11 reports `unknown` for it. Only a sourced row can produce a H11 `violation`, and its `source_url` is carried in
the check (`evidence`).

For each file, record here when it is built: source, retrieval date, processing, limits.

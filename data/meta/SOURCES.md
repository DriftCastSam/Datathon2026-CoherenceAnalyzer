# Meta data sources

Hand-built files. Every row needs a source and a date. **`activity_tags.csv` is written by humans only** (never by code or an LLM).

| File | Columns | Status |
|---|---|---|
| `asset_meta.csv` | `ticker, mer_pct, mer_source_url, mer_as_of, has_lookthrough, themes` | TODO (P1 step 10) |
| `etf_sector_weights.csv` | `etf, sector, weight, source_url, as_of` | TODO (P2 step 13) |
| `activity_tags.csv` | `ticker, tag, evidence_summary, source_url, as_of, added_by` | TODO (P1 step 10) |

For each file, record here: source, retrieval date, processing, limits.

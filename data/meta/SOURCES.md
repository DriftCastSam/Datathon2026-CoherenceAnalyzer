# Meta data sources

Hand-built files. Every row needs a source and a date. **`activity_tags.csv` is written by humans only** (never by code or an LLM).
`data.load_meta(name)` validates each file on load (SPEC §3.2) and raises `ValueError` on a bad one. A file that does not
exist yet loads as `None`, and the checks that need it report `unknown` (never a guess).

| File | Columns | Status |
|---|---|---|
| `asset_meta.csv` | `ticker, mer_pct, mer_source_url, mer_as_of, has_lookthrough, themes` | Built 2026-11-14 (see below). 5 of 9 ETF MERs sourced; XIU.TO, XBB.TO, ZST.TO, ESGV blank -> H12 `unknown` |
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

### `asset_meta.csv`: built 2026-11-14

- **MERs** were read from the issuer product pages on 2026-11-14. `mer_as_of` is that retrieval date, because none of
  the pages prints an as-of date next to the fee.
  - SPY 0.0945 %: SSGA, "Gross Expense Ratio".
  - QQQ 0.18 %: Invesco, "total expense ratio", from a footnote on the QQQ home page.
  - AGG 0.03 % and TLT 0.15 %: iShares, "Expense Ratio" (the page does not say net or gross).
  - GLD 0.40 %: spdrgoldshares.com, "Expense Ratio".
- **Not sourced yet:** XIU.TO and XBB.TO (BlackRock Canada pages load the fees block by script), ZST.TO (the BMO page
  timed out) and ESGV (the Vanguard page is script-rendered). Their `mer_pct` is blank, so H12 reports `unknown`. A team
  member should read each MER (not the management fee) from the issuer page or Fund Facts PDF, then fill in `mer_pct`,
  `mer_source_url` and `mer_as_of`.
- `mer_pct` is `0` for single stocks and crypto (no fund fee).
- `has_lookthrough` is `false` everywhere, because `etf_sector_weights.csv` is P2.
- **`themes` are a team assumption, not sourced data:**
  - `tech_heavy` on QQQ and on the US/Canadian tech names (AAPL, MSFT, NVDA, GOOGL, AMZN, SHOP.TO, CSU.TO).
  - Finer tags: `ai`, `semis`, `ecommerce`, `software`, `ev`, `high_beta`.
  - Energy: `oil_gas` (XOM, CNQ.TO), `pipelines` (ENB.TO).
  - Other: `esg_screened` (ESGV), `long_duration` (TLT), `crypto` (BTC-USD, ETH-USD).
  - SPY and XIU.TO carry no theme, even though they contain tech: H13 counts a tagged ETF in full (SPEC §7.3), so tagging
    a broad index would overstate the exposure.

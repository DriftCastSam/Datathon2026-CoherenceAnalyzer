# Meta data sources

Hand-built files. Every row needs a source and a date. **`activity_tags.csv` is written by humans only** (never by code or an LLM).
`data.load_meta(name)` validates each file on load (SPEC §3.2) and raises `ValueError` on a bad one. A file that does not
exist yet loads as `None`, and the checks that need it report `unknown` (never a guess).

| File | Columns | Status |
|---|---|---|
| `asset_meta.csv` | `ticker, mer_pct, mer_source_url, mer_as_of, has_lookthrough, themes` | Built 2026-11-14 (see below). All 9 ETF MERs sourced from issuer documents |
| `etf_sector_weights.csv` | `etf, sector, weight, source_url, as_of` | Built 2026-11-15 by an agent from issuer files (D19), **human review pending**. XIU.TO, SPY, QQQ, ESGV |
| `etf_country_weights.csv` | `etf, country, weight, source_url, as_of` | Built 2026-11-15 by an agent (D19), **human review pending**. All ETFs but ZST.TO (not sourced) and GLD (no country) |
| `etf_top_holdings.csv` | `etf, holding_ticker, holding_name, weight, source_url, as_of` | Built 2026-11-15 by an agent (D19), **human review pending**. Top 15 of XIU.TO, SPY, QQQ, ESGV |
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

**`etf_sector_weights.csv`** (ETF sector look-through, SPEC §3.2 / §5): one row per (ETF, sector), for the four ETFs
**XIU.TO, SPY, QQQ, ESGV**. A **human** copies the GICS sector weights from the issuer's holdings page (iShares for
XIU.TO, State Street for SPY, Invesco for QQQ, Vanguard for ESGV) and records the page in `source_url` and the date of
the holdings in `as_of`. D19 (owner, 2026-11-15): an agent may fetch this file and the two below from issuer files,
and a human reviews it before the demo; the review is recorded at the end of this file.
- `weight` is a fraction (0-1), not a percent: 40 % is `0.40`. A weight outside 0-1 is rejected.
- `source_url` and `as_of` are required on every row, as are `etf` and `sector`; a row missing any is rejected on load.
- The weights of one ETF should sum to 1; the engine treats an ETF whose sum is off by more than 0.01 as not covered
  (kept as `Broad market`, nothing guessed). Use one sector vocabulary (GICS names such as `Information Technology`,
  `Financials`, `Health Care`), the same as `assets_universe.csv` `sector` and `identity.employer_sector`.
- Effect: with the file present and every held equity ETF covered, the engine reports `lookthrough: true`,
  `groups.sector` splits each ETF across its sectors, H6 adds the ETF part, and H10 no longer reports `Broad market`
  as `unknown`. A header-only file or no file leaves the engine output unchanged (`lookthrough: false`). H11 on an ETF
  stays `unknown` (sector weights are not activity evidence).
- Retrieval date, source pages and any processing: record them here when the file is built.

**`activity_tags.csv`**: `ticker, tag, source_url, as_of, added_by` are required on every row (a row missing any is
rejected); `evidence_summary` is free text. `tag` uses the vocabulary of `limits.excluded_activities` (`tobacco`,
`accounting_fraud_last_5y`, `significant_environmental_controversy`, `significant_social_controversy`,
`fossil_fuel_producer`, ...). `added_by` is a human team member. A ticker with no row for an excluded tag is **not**
cleared: H11 reports `unknown` for it. Only a sourced row can produce a H11 `violation`, and its `source_url` is carried in
the check (`evidence`).

For each file, record here when it is built: source, retrieval date, processing, limits.

### `asset_meta.csv`: built 2026-11-14

- **MERs (US ETFs)** were read from the issuer product pages on 2026-11-14. For these, `mer_as_of` is the retrieval date,
  because none of the pages prints an as-of date next to the fee.
  - SPY 0.0945 %: SSGA, "Gross Expense Ratio".
  - QQQ 0.18 %: Invesco, "total expense ratio", from a footnote on the QQQ home page.
  - AGG 0.03 % and TLT 0.15 %: iShares, "Expense Ratio" (the page does not say net or gross).
  - GLD 0.40 %: spdrgoldshares.com, "Expense Ratio".
- **MERs (from issuer PDFs)** were retrieved 2026-11-14 and the text extracted with `pdftotext`. The product pages load
  their fee blocks by script, so the PDFs were used instead. `mer_as_of` is the date printed for the figure.
  - XIU.TO 0.17 % and XBB.TO 0.10 %: BlackRock fund fact sheets, "Management Expense Ratio (MER)", data as at
    31-Oct-2026. MER, not the management fee (0.15 % and 0.09 %).
  - ZST.TO 0.17 %: BMO ETF Facts (CAD units) dated January 23, 2026, "Management expense ratio (MER)", "As of June 30,
    2025". This is the latest MER the issuer has published.
  - ESGV 0.09 %: Vanguard fact sheet as of September 30, 2026, "Expense ratio" ("as reported in the most recent
    prospectus").
- `mer_pct` is `0` for single stocks and crypto (no fund fee).
- `has_lookthrough` is `false` everywhere, because `etf_sector_weights.csv` is P2.
- **`themes` are a team assumption, not sourced data:**
  - `tech_heavy` on QQQ and on the US/Canadian tech names (AAPL, MSFT, NVDA, GOOGL, AMZN, SHOP.TO, CSU.TO).
  - Finer tags: `ai`, `semis`, `ecommerce`, `software`, `ev`, `high_beta`.
  - Energy: `oil_gas` (XOM, CNQ.TO), `pipelines` (ENB.TO).
  - Other: `esg_screened` (ESGV), `long_duration` (TLT), `crypto` (BTC-USD, ETH-USD).
  - SPY and XIU.TO carry no theme, even though they contain tech: H13 counts a tagged ETF in full (SPEC §7.3), so tagging
    a broad index would overstate the exposure.

### ETF look-through files: built 2026-11-15 (agent), human review: PENDING

Retrieval date for everything: **2026-11-15**. Raw files are in `/tmp/etf_raw/` and parsing scripts in `/tmp/etf_scripts/` (`build.py`, `common.py`, `ish.py`). All Python ran with `-I`.
`as_of` is always the holdings or breakdown date that the issuer states, not the retrieval date.
Weights are fractions with 4 decimals. Nothing was renormalised: rows keep the issuer's figures, so sums are off by the issuer's rounding.

Output files (in `data/meta/`): `etf_sector_weights.csv`, `etf_country_weights.csv`, `etf_top_holdings.csv`.

#### Sourcing rule (D19)
`data/meta/SOURCES.md` says `etf_sector_weights.csv` is filled by **a human** from the issuer pages ("code and LLMs never fill it").
These files were built by an agent with scripts that read the issuer files. Every number traces to an issuer file or page, but a human should review them and update SOURCES.md before you treat this as meeting that rule.
CLAUDE.md's hard "humans only" invariant covers only `activity_tags.csv`, which was not touched. SOURCES.md was not edited.

#### Source URLs
| key | URL | issuer date |
|---|---|---|
| XIU csv | https://www.blackrock.com/ca/investors/en/products/239832/ishares-sptsx-60-index-etf/1464253357814.ajax?fileType=csv&fileName=XIU_holdings&dataType=fund | Nov 13, 2026 |
| XBB csv | https://www.blackrock.com/ca/investors/en/products/239493/ishares-canadian-universe-bond-index-etf/1464253357814.ajax?fileType=csv&fileName=XBB_holdings&dataType=fund&asOfDate=20261113 | Nov 13, 2026 |
| AGG csv | https://www.ishares.com/us/products/239458/ishares-core-u-s-aggregate-bond-etf/latest-holdings.csv | Nov 13, 2026 |
| TLT csv | https://www.ishares.com/us/products/239454/ishares-20%20-year-treasury-bond-etf/latest-holdings.csv | Nov 12, 2026 |
| SPY daily xlsx | https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx | "As of 12-Nov-2026" |
| SPY product page | https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy | "Fund Sector Breakdown as of Nov 12 2026" |
| QQQ holdings (Invesco API behind invesco.com/qqq-etf) | https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&productType=ETF | effectiveDate 2026-11-12 |
| QQQ country (Invesco API) | https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/weightedHoldings/fund?idType=ticker&productType=ETF&breakdown=country | effectiveDate 2026-10-31 |
| QQQ sector (Invesco API, ICB, **not used**) | .../weightedHoldings/fund?idType=ticker&productType=ETF&breakdown=sector | 2026-10-31 |
| ESGV daily holdings (Vanguard advisors site API) | https://advisors.vanguard.com/investments/products/api/funds/4393/holdings/daily | dailyEffectiveDate 2026-11-12 |
| GICS reference: iShares ITOT | https://www.ishares.com/us/products/239724/ishares-core-sp-total-us-stock-market-etf/latest-holdings.csv | Nov 12, 2026 |
| GICS reference: iShares ACWI | https://www.ishares.com/us/products/239600/ishares-msci-acwi-etf/latest-holdings.csv | Nov 12, 2026 |
| GICS reference: iShares SOXX | https://www.ishares.com/us/products/239705/ishares-phlx-semiconductor-etf/latest-holdings.csv | Nov 12, 2026 |

#### etf_sector_weights.csv
- **XIU.TO** (as_of 2026-11-13, source: XIU csv). I summed the `Weight (%)` column by the `Sector` column. iShares labels GICS sectors, but abbreviates one: `Communication` became `Communication Services`. The non-equity rows (2 Cash rows, cash collateral, 1 futures row; sector `Cash and/or Derivatives`) became `Cash and other` = 0.0031. Sum 1.0004 (issuer rounding to 2 dp).
- **SPY** (as_of 2026-11-12, source: SPY product page). The SSGA daily holdings xlsx **has no sectors**: its `Sector` column is `-` for all 505 rows. I therefore used the page's "Fund Sector Breakdown" (GICS, 11 sectors, read from the page's hidden `fund-sector-breakdown` JSON), as published. There is no cash row on that breakdown, and it sums to exactly 100.00%. The xlsx shows 0.096% USD cash, which the breakdown leaves out.
- **QQQ** (as_of 2026-11-12, source: QQQ holdings API). Invesco publishes only **ICB** sectors (Technology, Telecommunications, Basic Materials...). Rather than map those, I computed GICS weights from the FULL daily holdings file (106 lines): each equity's `percentageOfTotalNetAssets` times its GICS sector.
  - The SSGA SPY file carries no GICS sectors, so I took each constituent's GICS sector from iShares holdings files instead. Matching was by ticker, in this order: ITOT, then ACWI, then SOXX (all 2026-11-12). That gave 92 names from ITOT, 8 from ACWI (SHOP, ASML, MELI, PDD, CCEP, FER, TRI, NBIS) and 1 from SOXX (ARM, which is not in ITOT or ACWI). I checked every non-ITOT match by name.
  - Non-equity lines became `Cash and other` = 0.0016: USD cash 0.1396%, cash collateral 0.0175%, NQZ6 future +0.1341% and its contra line -0.1341%.
  - Sum 0.9999.
  - Caveat: the sector weights date from 2026-11-12, while Invesco's own (ICB) sector page dates from 2026-10-31.
- **ESGV** (as_of 2026-11-12, source: ESGV daily holdings). Vanguard's own sector breakdown on the fund page is ICB (and from 2026-09-30), so I did not use it. The daily holdings give each stock's **GICS sub-industry** (139 distinct values, e.g. "Systems Software").
  - I mapped each sub-industry to its GICS sector using the data alone. For each ESGV holding whose ticker is also in iShares ITOT/ACWI/SOXX, I took the iShares GICS sector.
  - All 138 non-blank sub-industries mapped, and every one mapped to exactly one sector (no conflicts). No mapping came from my own knowledge.
  - `Cash and other` = 1 - equity sum = 1 - 0.99211 = 0.0079. Vanguard lists the parts:
    - short-term reserves 0.341%;
    - 0.45% of other net assets that Vanguard does not itemise;
    - 0.423% in futures and total-return swaps, shown at notional (mostly S&P 500 and Nasdaq-100 e-mini futures). These are not looked through.
  - Three earn-out/CVR lines with blank weights were dropped (market value about $21 in total).

#### etf_country_weights.csv
- **XIU.TO** (2026-11-13, XIU csv). I summed the `Location` column over the equity rows; the non-equity rows went to `Cash and other`. Result: Canada 0.9746, United States 0.0166, Bermuda 0.0061 (iShares' country of the issuer), cash 0.0031. Sum 1.0004.
- **SPY** (2026-11-12, SPY daily xlsx). SSGA publishes no country data for SPY (the page only says "Investment Geography: United States"), and the xlsx has no country column.
  - I aggregated the SPY xlsx weights, giving each holding the `Location` of the same ticker in iShares ITOT (2026-11-12). Every matched constituent is United States in iShares' data.
  - The `US DOLLAR` line became `Cash and other` (0.0010). One contra line (3e-8) did not match.
  - Sum 0.9997, as published; the xlsx weights themselves sum to 99.967%. The residual was not put into cash.
  - The `source_url` is the SSGA xlsx (the weights); the locations come from the ITOT file above.
- **QQQ** (2026-10-31, QQQ country API). This is Invesco's own country breakdown, used as published: US 96.998%, Netherlands 1.120, Canada 1.055, Brazil 0.370, China 0.236, UK 0.221. It has no cash row. Sum 1.0001 after rounding to 4 dp.
  - Caveat: it dates from 2026-10-31, while the QQQ sector and top-holdings rows date from 2026-11-12.
- **ESGV** (2026-11-12, ESGV daily holdings). Vanguard gives the `country` field as `US` for all 1201 equity lines. That gives United States 0.9921 and `Cash and other` 0.0079, the same remainder as in the sector file.
  - Caveat: Vanguard's characteristics show "foreign holdings 0.1%" (2026-09-30), but the daily holdings file codes every line as US. I kept the holdings file.
- **XBB.TO** (2026-11-13, XBB csv). The plain csv link on the product page (without `asOfDate`) returned an empty file ("Fund Holdings as of -"), so I added the `&asOfDate=20261113` parameter.
  - `Location` was summed over the Fixed Income rows. The issuer's label `Supranational` (0.0045) was kept as published; it is not a country.
  - Cash 0.0002. Sum 1.0003.
- **AGG** (2026-11-13, AGG csv). The published `Weight (%)` column sums to only **81.71%**: 10,934 of the 13,535 lines show 0.00 because of 2-dp rounding (thousands of small MBS pools).
  - So I recomputed the weights as each line's `Market Value` / total market value. This is the only re-derivation from the issuer's numbers, and it sums to exactly 1.
  - `Location` summed over Fixed Income lines. `Supranational` (0.0094) was kept as published, and so was `Korea (South)`, iShares' spelling.
  - `Cash and other` = 0.0038 net: money market 2.435%, USD cash -2.07% (pending settlements), collateral 0.01%.
  - Rows show 0.0000 for South Africa and Finland, which are below 0.005% (kept for completeness). Sum of the rounded rows is 0.9998.
- **TLT** (2026-11-12, TLT csv). United States 1.0000 (treasuries); the money market and cash lines became `Cash and other` 0.0002. Sum 1.0002 as published.
- **ZST.TO: NOT SOURCED, so it is absent from the file.**
  - The BMO GAM site (bmogam.com) only answers browser-like HTTP/1.1 requests. Its product page loads its data client-side from a GraphQL backend that is not public (Transmission Media "livedoc", token-based). I did not reverse-engineer it.
  - The issuer holdings file I could get is `https://df.bmogam.com/assets/static/reports/etf-funds-holdings/Holdings_Extract_en_US_ZST_20261113.xlsx` (holdings as of 2026-11-13, saved in `/tmp/etf_raw/bmo/`). It has **no country column**: only weight, name, ISIN, par, coupon, maturity, sector and asset class.
  - Assigning countries from issuer names (e.g. "Ventas Canada Finance", "VW Credit Canada", "Daimler Truck Finance Canada") would have been guesswork, so I did not do it. Every ISIN in that file is `CA...`, but ISIN prefix is the country of issue, not of the issuer, so I did not use it either.
  - Next step: a human reads the "Geographic Allocation" chart on the BMO ZST page in a browser, or the ZST ETF Facts / fact sheet.

#### etf_top_holdings.csv (top 15 per ETF after merging share classes)
- Sources and dates are the same as for the sector rows: XIU csv 2026-11-13, SPY xlsx 2026-11-12, QQQ holdings API 2026-11-12 (equity and ADR lines only), and ESGV daily holdings 2026-11-12. Weights are the issuer's weight per line divided by 100. `holding_name` is the issuer's own spelling, except for merged rows.
- Share-class merges (weights added into one row):
  - Alphabet: `GOOGL` + `GOOG` became `GOOGL` "Alphabet", in SPY, QQQ and ESGV.
  - Fox (`FOXA`+`FOX`) and News Corp (`NWSA`+`NWS`) were merged in SPY and ESGV, but neither reaches the top 15.
  - Berkshire: SPY holds only BRK.B (no merge needed), shown as `BRK.B` "Berkshire Hathaway".
  - XIU had no dual classes in its holdings.
- Tickers: XIU names get `.TO`, e.g. RY.TO, TD.TO, SHOP.TO, ENB.TO, CNQ.TO, MFC.TO, CNR.TO, BN.TO. Names outside the universe use their TSX root plus `.TO` (BMO.TO, BNS.TO, CM.TO, AEM.TO, CP.TO, SU.TO, TRP.TO). US names keep the issuer's ticker.
- QQQ's list includes `SPCX` "Space Exploration Technologies Corp" (2.70%), exactly as in Invesco's holdings.
- Top-15 coverage: XIU.TO 0.6005, SPY 0.4507, QQQ 0.6036, ESGV 0.4842.

#### Validation
- `data.etf_sector_shares(load_meta('etf_sector_weights'))` returns keys ESGV, QQQ, SPY, XIU.TO, so all 4 are covered.
- Per-ETF sums:
  - sector: XIU.TO 1.0004, SPY 1.0000, QQQ 0.9999, ESGV 1.0000.
  - country: XIU.TO 1.0004, SPY 0.9997, QQQ 1.0001, ESGV 1.0000, XBB.TO 1.0003, AGG 0.9998, TLT 1.0002.


**Review:** _not yet done. Reviewer name, date, and which rows were checked against the issuer page go here._

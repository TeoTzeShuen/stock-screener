# Handoff: US dividend stock screen (one-off snapshot)

Written 10 November 2026. This file summarises decisions from a planning chat so a local Claude Code instance can build the screen. Spelling in all outputs and comments: British English.

## 1. Goal

A one-off, reproducible screen of the US-listed stocks in baseline.md, across six hand-picked groups, for a group project. It is not a recurring pipeline. Run once, freeze the data, and produce an Excel file showing which stocks pass each criterion and why.

## 2. Decisions already made

- **Scope:** US-listed only (SGX dropped).
- **Data source:** `yfinance` (unofficial, no key). Acceptable because this is a single run. Throttle requests and cache everything.
- **Not using:** Google Finance in Sheets (lacks yield, cash flow, dividend history, leverage data). EODHD, Polygon/Massive and Tiingo free tiers lack fundamentals or are too limited. SEC EDGAR was considered but ADRs (NVS, AZN, SONY) file 20-F, so yfinance is simpler.
- **Sector average = peer median of our own picks** (details in section 5), not an official sector average.
- **Determinism:** fetch once, save raw responses to disk with the as-of date, run all filters only from that snapshot. Re-running on the same snapshot must give identical output.

## 3. Screening criteria

All thresholds live in a config file (YAML or a dict at the top of the script), never hard-coded in the logic.

| # | Criterion | Rule |
|---|---|---|
| 1 | Dividend yield | Trailing 12-month dividends per share / price at the as-of date. Threshold in config, **default 3.0%** (see note below). |
| 2 | Beta | Below 1.0. Compute ourselves: 5 years of monthly returns on adjusted close vs SPY. Do not use Yahoo's beta field. |
| 3 | FCF covers dividend | Latest annual (operating cash flow minus capex) must be positive, and FCF / total cash dividends paid must be at least 1.0x. |
| 4 | Dividend record | A dividend paid in each of the last five full calendar years (2021 to 2025 for this run). Use dividend history, not the financial statements. |
| 5 | Leverage | Debt-to-equity below the group median, **or** interest coverage (EBIT / interest expense) above 3.0x. |
| 6 | Size | Market cap above USD 1 billion. |

**Yield threshold note:** the original ask was 4%, adjusted for the market. The US market yields far less than Singapore's (the S&P 500 is roughly 1 to 2%), so a flat 4% mostly surfaces unusual or stressed payers. I proposed about 3% for US names as a starting point. The user has not confirmed this figure, so keep it configurable and report results at the config value. Optionally also report how many stocks would pass at 4%. Context for the write-up: Singapore residents generally face 30% US withholding tax on dividends, so a 3% gross yield is about 2.1% net (confirm with a tax adviser; this is explanatory context, not part of the logic).

## 4. Universe

Read baseline.md (in the current working directory). It holds the stock list, split into six groups. Treat it as the source of truth for the universe and its group assignments, and load it from there rather than hard-coding tickers. If you want a machine-friendly copy, derive a `universe.csv` (`ticker,name,group`) from it.

## 5. Group benchmark for debt-to-equity

- Compute the **median** D/E per group, not the mean (small samples and outliers).
- Compute it over **every name in the group**, including non-payers and names that fail other filters. Otherwise the benchmark gets easier as the screen gets stricter.
- **Exclude negative or zero equity** from the median. Those names are "not computable" on D/E and can only pass on interest coverage. Likely affected (verify against the data, do not assume): MO, MCD, SBUX, AZO, BA.
- State D/E definition explicitly (total debt including leases / total shareholders' equity) and use the same one for every ticker.
- In the output and write-up, describe it as "peer median of our selected names", not an official sector average.
- Known weakness to mention in the methodology: groups mix business models (healthcare has pharma, insurers and distributors; discretionary includes F and GM with captive finance arms). An optional improvement is sub-groups, but the default is the six groups as given.

## 6. Known problems with the universe

- **HONA** (Honeywell Aerospace) was spun off and began trading on 29 June 2026. It has no five-year dividend record, so it fails criterion 4 by construction. Keep it in the table, flag it, and include its D/E in the aerospace median only if its balance sheet data is sensible.
- **NVS, AZN, SONY** are ADRs of foreign companies. Check that yfinance returns usable statements for them, convert currencies to USD where relevant, and flag clearly if data is partial. Do not silently pass or fail them.
- **Likely non-dividend payers:** AMZN, TSLA, PLTR, VRTX, RKLB, KTOS, and BA (dividend suspended). These should come out as "fail" on yield and record. Do not treat missing dividends as an error, but distinguish "no dividends" from "data fetch failed".

## 7. Pipeline

1. **Fetch** (one pass, sequential, with a short sleep between tickers, retry with backoff on 429 or empty responses):
   - dividend history, annual cash flow, balance sheet and income statement, market cap and price, 5 years of monthly adjusted prices for the ticker, and SPY.
   - Pin the `yfinance` version in `requirements.txt`.
2. **Snapshot:** write raw responses to `data/raw_<asof-date>/` (parquet or JSON), plus a manifest with as-of date, package versions, and a per-ticker fetch status. All later steps read only from here.
3. **Compute metrics** per ticker. Look up statement rows **by name** and map to `missing` if absent, never to zero. Annual statements usually cover only about four years, which is enough for criteria 3 and 5. Criterion 4 uses dividend history.
4. **Group medians** (section 5).
5. **Apply filters** as separate pure functions, each returning `pass`, `fail` or `no_data`, with thresholds from config.
6. **Export** `results.xlsx` (openpyxl):
   - Sheet `Screen`: one row per ticker with group, raw metric values, one pass/fail/no_data column per criterion, and an overall result (`pass` only if all six pass; `no_data` if none failed but some were missing).
   - Sheet `Group medians`: median D/E per group, the count of names used, and the names excluded as not computable.
   - Sheet `Assumptions`: thresholds, definitions, as-of date, package versions, known caveats.
   - Sheet `Fetch log`: per-ticker status and any missing fields.

## 8. Validation (do before reporting results)

- Spot-check about ten tickers against their latest 10-K or investor-relations figures, prioritising narrow passes and fails. Record the comparison in a short `validation.md`.
- Sanity checks: yields between 0 and about 15%, beta between about -0.5 and 3, market caps consistent with known sizes, and unit scales consistent across tickers.
- Confirm re-running from the saved snapshot reproduces `results.xlsx` exactly.

## 9. Constraints and notes

- Data is for personal and academic use. Yahoo's terms do not permit redistribution, so do not publish the raw data.
- Do not give investment advice. Frame outputs as a screening exercise, and note that a high yield alone can signal a payout under strain.
- Treat the yield threshold, the dividend-record definition (calendar years) and the D/E definition as assumptions to confirm with the user if anything looks odd.

## 10. Open questions for the user

1. Confirm the US yield threshold (3.0% default versus the original 4%).
2. Keep NVS, AZN, SONY and HONA in the screen, or drop them?
3. Keep the six groups as given, or split mixed ones (healthcare, discretionary) into sub-groups for the D/E median?

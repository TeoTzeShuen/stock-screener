# Validation of the dividend screen (snapshot of 10 November 2026)

This is a screening exercise for a group project, not investment advice.

## 1. Fetch

All 116 tickers and SPY fetched with status `ok` (see the `Fetch log` sheet). No ticker needed a manual fix.

## 2. Spot-check against 10-K filings

Snapshot figures (Yahoo Finance) were compared with the values tagged in each company's latest 10-K, taken
from SEC EDGAR's XBRL company-facts service. Tickers were chosen for narrow passes and fails. Figures in USD
millions, for the fiscal year used by the screen.

| Ticker | Why chosen | Operating cash flow | Capex | Dividends paid | Equity | Interest expense | Verdict |
|---|---|---|---|---|---|---|---|
| PEP | Narrowest pass on C3 (FCF cover 1.004x) | 12,087 = 12,087 | 4,415 = 4,415 | 7,638 = 7,638 | 20,406 = 20,406 | 1,121 = 1,121 | Match |
| KMB | Narrowest fail on C3 (0.987x) | 2,777 = 2,777 | 1,138 = 1,138 | 1,660 = 1,660 | 1,502 = 1,502 | 256 = 256 | Match |
| KR | Narrow fail on C5 (interest cover 2.88x) | 7,311 = 7,311 | 3,855 = 3,855 | 885 = 885 | 5,927 = 5,927 | 639 is **net** of interest income in the 10-K | Match, see note 1 |
| MDLZ | Narrow pass on C1 (yield 3.04%) | 4,514 = 4,514 | 1,279 = 1,279 | 2,487 = 2,487 | 25,838 = 25,838 | 599 = 599 | Match |
| PM | Narrow pass on C1 (3.11%), negative equity | 12,233 = 12,233 | 1,569 = 1,569 | 8,624 = 8,624 | -9,994 = -9,994 | 1,587 = 1,587 | Match |
| MO | Passes, negative equity | 9,290 = 9,290 | 216 = 216 | 6,960 = 6,960 | -3,502 = -3,502 | 1,177 = 1,177 | Match |
| PFE | Fails C3 (0.93x); D/E 0.74 against median 0.75 | 11,704 = 11,704 | 2,629 = 2,629 | 9,771 = 9,771 | 86,476 = 86,476 | 2,671 = 2,671 | Match |
| HSY | Passes | 2,277 = 2,277 | Yahoo 528 against 455 tagged as PP&E purchases | 1,085 = 1,085 | 4,637 = 4,637 | 225 = 225 | Match, see note 2 |
| PAYX | Passes (fiscal year to May 2026) | 2,557 = 2,557 | 235 = 235 | 1,590 = 1,590 | 3,735 = 3,735 | 270 = 270 | Match |
| BMY | Passes | 14,156 = 14,156 | 1,311 = 1,311 | 5,045 = 5,045 | 18,473 = 18,473 | 1,891 = 1,891 | Match |
| GIS | Fails C5 (interest cover 1.7x) | 2,166 = 2,166 | 540 = 540 | 1,315 = 1,315 | 7,368 = 7,368 | 570 = 581 gross less 11.5 capitalised | Match |
| CLX | Fails C3 (0.67x) | not confirmed | not confirmed | not confirmed | not confirmed | not confirmed | Not verified, see note 3 |

Notes:

1. **KR.** Yahoo's "interest expense" of 639 is Kroger's net interest expense (gross is about 838 after adding
   back 199 of interest income). On the gross figure, interest cover would be about 2.4x rather than 2.88x.
   Either way it is below 3.0x, and D/E (4.17) is above the staples median, so the C5 fail stands.
2. **HSY.** Yahoo's capex includes about 74 of capitalised software on top of property, plant and equipment.
   FCF cover is 1.61x on Yahoo's figure and 1.68x on the narrower one; a pass either way.
3. **CLX.** EDGAR's XBRL data returned no annual values for the year to 30 June 2026, so Clorox could not be
   confirmed from this source. It is not a narrow case (FCF cover 0.67x).

Not checked against filings: prices, market caps, dividends per share and the dividend history. These come
only from Yahoo. Dividend history was checked for internal consistency instead (section 4).

## 3. Sanity checks

| Check | Result |
|---|---|
| Yields between 0 and about 15% | All within range. Highest: GIS 7.0%, PFE 5.9%, LYB 5.8%, MO 5.7% |
| Beta between about -0.5 and 3 | One outside: RKLB 3.15 (flagged in the output; plausible for a volatile stock). Lowest: NOC -0.13 |
| Market caps consistent with known sizes | Range from USD 4bn (DOX) to USD 2,886bn (AMZN); nothing out of scale. All 116 pass C6 |
| Unit scales consistent | Statement figures matched 10-K values to the million in every spot-check. SONY reports in JPY (flagged); only within-statement ratios are used for it, so no conversion is needed |
| Negative or zero equity | Found in the data, not assumed: MO, PM, ABBV, CAH, MCK, HPQ, VRSN, TDG, SBUX, MCD, LOW, BKNG, MAR, YUM, ORLY, AZO. BA had positive equity at its latest year-end (D/E 9.98), so it is in the aerospace median |

## 4. Data issues found and how they are handled

- **SONY: dividend history incomplete.** Yahoo's history for the ADR stops at March 2025, although Yahoo
  itself shows a later ex-dividend date and an annual rate of USD 0.16 (about 0.6%). C1 is therefore
  `no_data` rather than a silent fail, and SONY's overall result is `no_data`. On Yahoo's indicated rate
  it would fail the yield test.
- **AZN: one of two payments missing from the trailing window.** Only the February 2026 payment appears.
  Trailing yield 1.14%; Yahoo's indicated rate implies 1.69%. A fail on either figure. Flagged.
- **Ex-date timing (IBM, PPG, F).** Their November 2025 ex-dates fall just outside the 12-month window and
  the November 2026 ones had not occurred, so only three quarterly payments are counted. IBM (2.06%
  trailing, 2.75% indicated) and PPG (1.72%, 2.36%) fail either way; F passes C1 either way. Flagged in
  the output. No overall result changes.
- **COR and SBUX: empty fiscal 2026 column.** Yahoo lists the year to September 2026 with no figures yet.
  The screen uses the newest year with data (fiscal 2025) and flags it.
- **Interest expense missing for the latest year: LLY, NKE, PLTR.** Treated as missing, not zero. NKE and
  PLTR pass C5 on D/E. LLY is `no_data` on C5 (D/E 1.60 is above the healthcare median) and fails overall
  on yield regardless.
- **HONA.** Balance sheet at 31 December 2025 pre-dates the spin-off (USD 0.3bn debt against USD 0.8bn of
  annual interest expense), so its D/E is excluded from the aerospace median and not used in C5. Beta is
  `no_data` (three complete months). It fails C4 by construction.
- **NVS and AZN** returned full statements in USD. NVS fails only on yield (2.92% against 3.0%), which is
  a narrow fail on a single annual payment.

## 5. Reproducibility

`python src/screen.py` was run twice on the frozen snapshot. Both runs produced a `results.xlsx` with the
same SHA-256 hash, so the output is byte-identical. The script embeds fixed timestamps in the workbook for
this purpose. The hash is not recorded here because any later change to `config.yaml` (for example the
yield threshold) legitimately changes the file.

## 6. Excel formulas

Everything derivable in `results.xlsx` is an Excel formula: dividends per share, payment counts, yield and
dividend years (from the `Dividends` sheet), beta (from `Prices` and `Returns`), FCF, FCF cover, D/E,
interest cover, the peer median D/E and every pass/fail result. A copy was opened in Excel and recalculated:

- all 812 status cells (116 tickers, six criteria plus overall) matched the script's own results;
- all 12 formula-driven metrics per ticker matched the script's values to within 1e-9;
- the six peer medians matched, and no cell on `Screen` returned an error;
- setting the yield threshold to 4% in Excel gave the same five passes as the script.

## 7. Result at the configured thresholds

- 8 pass, 107 fail, 1 `no_data` (SONY) at the 3.0% yield threshold.
- Passing: PEP, MDLZ, MO, PM, HSY (consumer staples), BMY (healthcare), PAYX, DOX (tech).
- At a 4.0% yield threshold 5 pass: PEP, MO, BMY, PAYX, DOX.
- Narrow passes worth a second look in the write-up: PEP (FCF cover 1.004x), MDLZ (yield 3.04%),
  PM (yield 3.11%).

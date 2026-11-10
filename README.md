# US dividend stock screen

A reproducible screening exercise over the US-listed stocks in `baseline.md`.

## Dependencies

Python 3 (built and run on 3.15.2) and the packages pinned in `requirements.txt`:

| Package | Version | Used for |
|---|---|---|
| `yfinance` | 1.8.0 | Fetching data from Yahoo Finance |
| `curl_cffi` | 0.17.0 | HTTP client used by yfinance |
| `pandas` | 3.1.1 | Reading the snapshot and computing metrics |
| `numpy` | 2.5.4 | Beta calculation |
| `openpyxl` | 3.1.5 | Writing the Excel workbook |
| `PyYAML` | 6.0.3 | Reading `config.yaml` |

## How to run

```
pip install -r requirements.txt
python src/fetch.py         # one throttled pass over Yahoo Finance, writes data/raw_<asof-date>/
python src/screen.py        # builds results.xlsx from the snapshot, no network access
```

The raw snapshot and the output workbook are not in this repository (Yahoo's terms do not permit
redistribution), so `fetch.py` has to be run first. It refuses to run unless `asof_date` in `config.yaml`
is today's date, because Yahoo only serves current data; set it before fetching.

## Files

| File | Purpose |
|---|---|
| `baseline.md` | Source of truth for the universe and its six groups |
| `config.yaml` | Every threshold and assumption used by the filters |
| `src/universe.py` | Parses `baseline.md`, derives `universe.csv` |
| `src/fetch.py` | One throttled pass over Yahoo Finance (yfinance), writes the raw snapshot and manifest |
| `src/screen.py` | Metrics, peer medians, the six filters, and the Excel export |
| `data/raw_<asof-date>/` | Frozen raw responses. Yahoo's terms do not permit redistribution, so do not publish this folder |
| `results.xlsx` | Output: `Screen`, `Thresholds`, `Group medians`, `Assumptions`, `Fetch log`, plus the formula inputs `Dividends`, `Prices`, `Returns` |

## Thresholds by group

Each of the six thresholds can differ by group. `config.yaml` holds the defaults under `thresholds` and
optional per-group overrides under `group_thresholds`; anything not overridden uses the default.

```yaml
group_thresholds:
  "Tech (Not AI)":
    dividend_yield_min: 0.02
  "Consumer staples":
    dividend_yield_min: 0.035
    beta_max: 0.8
```

Keys that can be overridden: `dividend_yield_min`, `beta_max`, `fcf_cover_min`,
`dividend_record_min_years`, `interest_cover_min`, `market_cap_min_usd`. Group names must match
`baseline.md` exactly; an unknown group or key stops the script with an error.

In the workbook, the `Thresholds` sheet has a default row and one row per group. A blank group cell uses
the default; a number overrides it for that group. The `Applied` columns on `Screen` show the thresholds
used for each stock.

## Changing things in Excel

Everything derivable in `results.xlsx` is a formula. Edit the yellow cells on the `Thresholds` sheet, or any
raw input on `Screen`, `Dividends` or `Prices`, and the metrics, peer medians, pass/fail results and live
summary recalculate. Re-running `screen.py` resets the workbook to the snapshot and the `config.yaml`
defaults, so change `config.yaml` instead if a new threshold should be permanent.

The `Dividends` and `Prices` sheets hold raw Yahoo data, so do not publish the workbook.

`python src/screen.py <path>` writes the workbook somewhere else, which helps when `results.xlsx` is open
in Excel.

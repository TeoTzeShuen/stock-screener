"""Compute metrics, apply the six filters and export results.xlsx.

Reads only from the frozen snapshot in data/raw_<asof-date>/ (never from the network),
so re-running on the same snapshot reproduces results.xlsx byte for byte.

Usage:  python src/screen.py
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import zipfile
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from universe import ROOT, load_universe

PASS, FAIL, NO_DATA = "pass", "fail", "no_data"
CRITERIA = ["C1 yield", "C2 beta", "C3 FCF cover", "C4 dividend record", "C5 leverage", "C6 size"]

# Statement rows are looked up by name; alternatives are tried in order.
ROWS = {
    "ocf": ("cashflow", ["Operating Cash Flow"]),
    "capex": ("cashflow", ["Capital Expenditure"]),
    "dividends_paid": ("cashflow", ["Cash Dividends Paid", "Common Stock Dividend Paid"]),
    "total_debt": ("balance_sheet", ["Total Debt"]),
    "equity": ("balance_sheet", ["Stockholders Equity"]),
    "ebit": ("income_stmt", ["EBIT"]),
    "interest_expense": ("income_stmt", ["Interest Expense", "Interest Expense Non Operating"]),
}


# ----------------------------------------------------------------------------- loading

def load_frame(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(raw["data"], index=raw["index"], columns=raw["columns"], dtype="float64")


def load_statement(path: Path) -> pd.DataFrame | None:
    """Annual statement with row names as index and fiscal year-end dates as columns (newest first)."""
    df = load_frame(path)
    if df is None or df.empty:
        return None
    df.columns = [str(c)[:10] for c in df.columns]
    return df[sorted(df.columns, reverse=True)]


def load_series(path: Path, column: str) -> pd.Series | None:
    """Date-indexed series (dates as ISO yyyy-mm-dd strings, oldest first)."""
    df = load_frame(path)
    if df is None:
        return None
    s = df[column] if column in df.columns else pd.Series(dtype="float64")
    s.index = [str(i)[:10] for i in s.index]
    return s.dropna().sort_index()


def lookup(stmt: pd.DataFrame | None, names: list[str], col: str | None) -> float | None:
    """Value of the first named row present for the given fiscal year; None (missing) if absent."""
    if stmt is None or col is None or col not in stmt.columns:
        return None
    for name in names:
        if name in stmt.index:
            v = stmt.at[name, col]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return float(v)
    return None


# ----------------------------------------------------------------------------- metrics

# Yahoo sometimes adds a column for a fiscal year that has just ended before the figures are
# loaded. The "latest annual" column is the newest one in which a core row is populated.
CORE_ROWS = {"cashflow": ["Operating Cash Flow"], "balance_sheet": ["Stockholders Equity", "Total Debt"],
             "income_stmt": ["EBIT", "Pretax Income"]}


def latest_column(stmt: pd.DataFrame | None, core: list[str]) -> str | None:
    if stmt is None:
        return None
    for col in stmt.columns:
        if lookup(stmt, core, col) is not None:
            return col
    return None


def monthly_returns(prices: pd.Series, asof: str) -> pd.Series:
    """Monthly returns on adjusted close, complete months only (the as-of month is dropped)."""
    s = prices.copy()
    s.index = [i[:7] for i in s.index]
    s = s[~s.index.duplicated(keep="last")]
    s = s[s.index < asof[:7]]
    return s.pct_change().dropna()


def compute_beta(prices: pd.Series | None, bench_returns: pd.Series, asof: str, cfg: dict):
    if prices is None or prices.empty:
        return None, 0
    r = monthly_returns(prices, asof)
    joined = pd.concat([r.rename("stock"), bench_returns.rename("bench")], axis=1, join="inner").dropna()
    joined = joined.tail(cfg["months"])
    n = len(joined)
    if n < cfg["min_months"]:
        return None, n
    cov = np.cov(joined["stock"].to_numpy(), joined["bench"].to_numpy(), ddof=1)
    return float(cov[0, 1] / cov[1, 1]), n


def compute_metrics(row: dict, raw: Path, manifest: dict, bench_returns: pd.Series, cfg: dict) -> dict:
    ticker, asof = row["ticker"], cfg["asof_date"]
    d = raw / ticker
    status = manifest["tickers"].get(ticker, {"status": "not_fetched", "fields": {}})
    m: dict = {**row, "fetch_status": status["status"], "fetch_fields": status["fields"], "missing": []}

    info = json.loads((d / "info.json").read_text(encoding="utf-8")) if (d / "info.json").exists() else {}
    m["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    m["market_cap"] = info.get("marketCap")
    m["quote_currency"] = info.get("currency")
    m["fin_currency"] = info.get("financialCurrency")
    m["indicated_dps"] = info.get("dividendRate")          # used only as a completeness cross-check

    # Dividends: None means the fetch failed; an empty series means no dividends reported.
    divs = load_series(d / "dividends.json", "Dividends")
    if status["fields"].get("dividends") not in ("ok", "none_reported"):
        divs = None
    m["has_dividend_data"] = divs is not None
    if divs is not None:
        start = (dt.date.fromisoformat(asof) - dt.timedelta(days=365)).isoformat()
        prior = (dt.date.fromisoformat(asof) - dt.timedelta(days=730)).isoformat()
        m["ttm_dps"] = float(divs[(divs.index > start) & (divs.index <= asof)].sum())
        m["ttm_payments"] = int(((divs.index > start) & (divs.index <= asof)).sum())
        m["prior_payments"] = int(((divs.index > prior) & (divs.index <= start)).sum())
        m["yield"] = m["ttm_dps"] / m["price"] if m["price"] else None
        years = cfg["thresholds"]["dividend_record_years"]
        paid = {int(i[:4]) for i, v in divs.items() if v > 0}
        m["div_years_paid"] = [y for y in years if y in paid]
        m["div_years_missed"] = [y for y in years if y not in paid]
    else:
        m["ttm_dps"] = m["yield"] = m["div_years_paid"] = m["div_years_missed"] = None
        m["ttm_payments"] = m["prior_payments"] = None

    prices = load_series(d / "prices_monthly.json", "Close")
    m["beta"], m["beta_months"] = compute_beta(prices, bench_returns, asof, cfg["beta"])
    m["divs_series"], m["price_series"] = divs, prices     # raw series, exported for the Excel formulas

    stmts = {name: load_statement(d / f"{name}.json") for name in ("cashflow", "balance_sheet", "income_stmt")}
    latest = {name: latest_column(s, CORE_ROWS[name]) for name, s in stmts.items()}
    m["skipped_sparse_year"] = sorted({s.columns[0] for name, s in stmts.items()
                                       if s is not None and latest[name] and s.columns[0] != latest[name]})
    m["fy_ends"] = latest
    m["fy_end"] = latest["cashflow"] or latest["income_stmt"] or latest["balance_sheet"]
    for key, (stmt, names) in ROWS.items():
        m[key] = lookup(stmts[stmt], names, latest[stmt])

    # A missing "dividends paid" row is zero only if the dividend history confirms that no
    # dividend went ex in that fiscal year; otherwise it stays missing.
    if m["dividends_paid"] is None and divs is not None and latest["cashflow"]:
        fy_end = latest["cashflow"]
        fy_start = (dt.date.fromisoformat(fy_end) - dt.timedelta(days=365)).isoformat()
        if divs[(divs.index > fy_start) & (divs.index <= fy_end)].sum() == 0:
            m["dividends_paid"] = 0.0
    if m["dividends_paid"] is not None:
        m["dividends_paid"] = abs(m["dividends_paid"])   # Yahoo reports outflows as negatives

    m["fcf"] = m["ocf"] + m["capex"] if m["ocf"] is not None and m["capex"] is not None else None
    m["fcf_cover"] = m["fcf"] / m["dividends_paid"] if m["fcf"] is not None and m["dividends_paid"] else None
    # Debt-to-equity = total debt including leases / total shareholders' equity.
    # Zero or negative equity -> not computable.
    m["de_excluded"] = (cfg.get("median_exclusions") or {}).get(ticker)
    m["de"] = (m["total_debt"] / m["equity"]
               if m["total_debt"] is not None and m["equity"] is not None and m["equity"] > 0
               and not m["de_excluded"] else None)
    m["equity_non_positive"] = m["equity"] is not None and m["equity"] <= 0 and not m["de_excluded"]
    m["interest_cover"] = (m["ebit"] / m["interest_expense"]
                           if m["ebit"] is not None and m["interest_expense"] is not None
                           and m["interest_expense"] > 0 else None)

    for key in ("price", "market_cap", "ocf", "capex", "dividends_paid", "total_debt", "equity", "ebit",
                "interest_expense"):
        if m[key] is None:
            m["missing"].append(key)
    if m["beta"] is None:
        m["missing"].append(f"beta (only {m['beta_months']} complete months)")
    if not m["has_dividend_data"]:
        m["missing"].append("dividend history")
    return m


def group_medians(metrics: list[dict], excluded: dict) -> dict:
    """Median D/E per group over every name with positive equity (non-payers and failers included)."""
    out: dict = {}
    for m in metrics:
        g = out.setdefault(m["group"], {"values": [], "used": [], "not_computable": [], "missing": [],
                                        "excluded": []})
        if m["ticker"] in excluded:
            g["excluded"].append(m["ticker"])
        elif m["de"] is not None:
            g["values"].append(m["de"])
            g["used"].append(m["ticker"])
        elif m["equity_non_positive"]:
            g["not_computable"].append(m["ticker"])
        else:
            g["missing"].append(m["ticker"])
    for g in out.values():
        g["median"] = statistics.median(g["values"]) if g["values"] else None
    return out


# ----------------------------------------------------------------------------- thresholds

# Thresholds that can be set per group: config key -> (Excel name, column label, number format, scale to Excel units).
GROUP_THRESHOLDS = {
    "dividend_yield_min": ("yield_min", "C1 min dividend yield", "0.0%", 1),
    "beta_max": ("beta_max", "C2 max beta", "0.00", 1),
    "fcf_cover_min": ("fcf_cover_min", "C3 min FCF cover (x)", "0.00", 1),
    "dividend_record_min_years": ("div_years_min", "C4 min dividend years", "0", 1),
    "interest_cover_min": ("interest_cover_min", "C5 min interest cover (x)", "0.0", 1),
    "market_cap_min_usd": ("mcap_min_bn", "C6 min market cap (USD bn)", "#,##0.0", 1e-9),
}


def resolve_thresholds(cfg: dict, groups: list[str]) -> dict[str, dict]:
    """Thresholds per group: the defaults in cfg['thresholds'], overridden by cfg['group_thresholds']."""
    overrides = cfg.get("group_thresholds") or {}
    for group, values in overrides.items():
        if group not in groups:
            raise ValueError(f"group_thresholds: unknown group {group!r}; expected one of {groups}")
        for key, value in (values or {}).items():
            if key not in GROUP_THRESHOLDS:
                raise ValueError(f"group_thresholds[{group!r}]: {key!r} cannot be set per group; "
                                 f"expected one of {list(GROUP_THRESHOLDS)}")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"group_thresholds[{group!r}][{key!r}] must be a number, got {value!r}")
    missing = [k for k in GROUP_THRESHOLDS if k not in cfg["thresholds"]]
    if missing:
        raise ValueError(f"thresholds: missing default(s) {missing}")
    return {g: {**cfg["thresholds"], **(overrides.get(g) or {})} for g in groups}


# ----------------------------------------------------------------------------- filters
# Each filter is a pure function of the metrics and thresholds: returns (status, reason).

def f_yield(m: dict, th: dict, key: str = "dividend_yield_min"):
    if m["yield"] is None:
        return NO_DATA, "dividend history or price missing"
    if m["ttm_dps"] == 0 and m["indicated_dps"]:
        return NO_DATA, (f"dividend history shows nothing in the trailing 12 months, yet Yahoo indicates an annual "
                         f"rate of {m['indicated_dps']} per share; the history looks incomplete")
    if m["ttm_dps"] == 0:
        return FAIL, "no dividends in the trailing 12 months"
    ok = m["yield"] >= th[key]
    return (PASS if ok else FAIL), f"yield {m['yield']:.2%} vs minimum {th[key]:.1%}"


def f_beta(m: dict, th: dict):
    if m["beta"] is None:
        return NO_DATA, f"only {m['beta_months']} complete months of price history"
    return (PASS if m["beta"] < th["beta_max"] else FAIL), f"beta {m['beta']:.2f} vs maximum {th['beta_max']}"


def f_fcf(m: dict, th: dict):
    if m["fcf"] is None:
        return NO_DATA, "operating cash flow or capex missing"
    if m["fcf"] <= 0:
        return FAIL, "free cash flow is not positive"
    if m["dividends_paid"] is None:
        return NO_DATA, "cash dividends paid missing"
    if m["dividends_paid"] == 0:
        return PASS, "FCF positive; no cash dividends paid in the year, so nothing to cover"
    ok = m["fcf_cover"] >= th["fcf_cover_min"]
    return (PASS if ok else FAIL), f"FCF cover {m['fcf_cover']:.2f}x vs minimum {th['fcf_cover_min']}x"


def f_record(m: dict, th: dict):
    if m["div_years_missed"] is None:
        return NO_DATA, "dividend history missing"
    paid, total, need = len(m["div_years_paid"]), len(th["dividend_record_years"]), th["dividend_record_min_years"]
    missed = f" (none in {', '.join(map(str, m['div_years_missed']))})" if m["div_years_missed"] else ""
    return (PASS if paid >= need else FAIL), f"dividend in {paid} of {total} years{missed} vs minimum {need}"


def f_leverage(m: dict, th: dict, median: float | None):
    de, cov = m["de"], m["interest_cover"]
    de_txt = (f"D/E not used ({m['de_excluded']})" if m["de_excluded"]
              else "D/E not computable (equity zero or negative)" if m["equity_non_positive"]
              else "D/E missing" if de is None or median is None
              else f"D/E {de:.2f} vs peer median {median:.2f}")
    no_interest = m["interest_expense"] is not None and m["interest_expense"] <= 0
    cov_txt = ("interest cover not computable (no interest expense reported)" if no_interest
               else "interest cover missing" if cov is None
               else f"interest cover {cov:.1f}x vs minimum {th['interest_cover_min']}x")
    reason = f"{de_txt}; {cov_txt}"
    if (de is not None and median is not None and de < median) or (cov is not None and cov > th["interest_cover_min"]):
        return PASS, reason
    # Neither leg passed. It is a firm fail only if each leg was either tested or is
    # structurally not computable; a leg with missing data makes the result no_data.
    de_known = m["equity_non_positive"] or bool(m["de_excluded"]) or (de is not None and median is not None)
    cov_known = cov is not None or no_interest
    return (FAIL if de_known and cov_known else NO_DATA), reason


def f_size(m: dict, th: dict):
    if m["market_cap"] is None:
        return NO_DATA, "market cap missing"
    ok = m["market_cap"] > th["market_cap_min_usd"]
    return (PASS if ok else FAIL), f"market cap USD {m['market_cap'] / 1e9:.1f}bn"


def overall(statuses: list[str]) -> str:
    if FAIL in statuses:
        return FAIL
    return PASS if all(s == PASS for s in statuses) else NO_DATA


def apply_filters(m: dict, th: dict, median: float | None) -> None:
    results = [f_yield(m, th), f_beta(m, th), f_fcf(m, th), f_record(m, th), f_leverage(m, th, median), f_size(m, th)]
    m["results"] = dict(zip(CRITERIA, results))
    m["overall"] = overall([s for s, _ in results])
    alt = [f_yield(m, th, "dividend_yield_alt")[0]] + [s for s, _ in results[1:]]
    m["overall_alt"] = overall(alt)


def sanity_warnings(m: dict, cfg: dict) -> list[str]:
    s, w = cfg["sanity"], []
    if m["yield"] is not None and not 0 <= m["yield"] <= s["yield_max"]:
        w.append(f"yield {m['yield']:.1%} outside 0-{s['yield_max']:.0%}")
    if m["beta"] is not None and not s["beta_min"] <= m["beta"] <= s["beta_max"]:
        w.append(f"beta {m['beta']:.2f} outside {s['beta_min']} to {s['beta_max']}")
    if m["skipped_sparse_year"]:
        w.append(f"Yahoo lists fiscal year {', '.join(m['skipped_sparse_year'])} without figures yet; the previous "
                 "year is used")
    if len({v for v in m["fy_ends"].values() if v}) > 1:
        w.append(f"statements cover different fiscal years: {m['fy_ends']}")
    if m["ttm_payments"] and m["ttm_payments"] < m["prior_payments"]:
        ind = f"; Yahoo's indicated annual rate implies {m['indicated_dps'] / m['price']:.2%}" \
            if m["indicated_dps"] and m["price"] else ""
        w.append(f"only {m['ttm_payments']} dividend payment(s) went ex in the trailing 12 months against "
                 f"{m['prior_payments']} in the 12 months before, so the trailing yield may be understated by "
                 f"ex-date timing or an incomplete history{ind}")
    if m["fin_currency"] and m["fin_currency"] != "USD":
        w.append(f"statements reported in {m['fin_currency']} (ratios are currency-neutral; price, dividends "
                 "per share and market cap are in USD)")
    return w


# ----------------------------------------------------------------------------- export

FILLS = {PASS: PatternFill("solid", fgColor="C6EFCE"), FAIL: PatternFill("solid", fgColor="FFC7CE"),
         NO_DATA: PatternFill("solid", fgColor="FFEB9C")}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")


def write_sheet(ws, header: list[str], rows: list[list], formats: dict | None = None, widths: dict | None = None,
                filterable: bool = True):
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append(r)
    for col_idx, name in enumerate(header, 1):
        letter = get_column_letter(col_idx)
        fmt = (formats or {}).get(name)
        for cell in ws[letter][1:]:
            if fmt:
                cell.number_format = fmt
            if cell.value in FILLS:
                cell.fill = FILLS[cell.value]
        ws.column_dimensions[letter].width = (widths or {}).get(name, max(10, min(len(name) + 2, 16)))
    if filterable:
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions


def normalise_xlsx(path: Path, asof: str) -> None:
    """Fix the timestamps openpyxl embeds so identical inputs give a byte-identical file."""
    stamp = f"{asof}T00:00:00Z"
    y, mth, day = (int(x) for x in asof.split("-"))
    with zipfile.ZipFile(path) as zin:
        items = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items:
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:)", rb"\g<1>" + stamp.encode() + rb"\g<3>", data)
            zi = zipfile.ZipInfo(name, date_time=(y, mth, day, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)


def export(metrics: list[dict], medians: dict, thresholds: dict, manifest: dict, cfg: dict, bench: pd.Series,
           path: Path) -> None:
    th = cfg["thresholds"]
    wb = Workbook()

    # --- Screen
    # Everything that can be derived is an Excel formula: the raw inputs are the snapshot values on
    # this sheet plus the Dividends and Prices sheets, and the thresholds are named cells on the
    # Thresholds sheet. The formulas mirror the Python functions above (which remain the reference
    # implementation). A blank cell means the value is missing; it is never treated as zero.
    ws = wb.active
    ws.title = "Screen"
    asof = cfg["asof_date"]
    yrs = th["dividend_record_years"]
    year_cols = [f"Dividends per share {y}" for y in yrs]
    applied = {name: f"Applied: {label[3:]}" for name, label, _, _ in GROUP_THRESHOLDS.values()}
    header = ["Ticker", "Name", "Group", "Overall", *CRITERIA, *applied.values(),
              "Price (USD)", "Market cap (USD bn)", "Dividend history status", "Yahoo indicated annual dividend",
              "TTM dividends per share", "Payments in TTM", "Payments in prior 12 months", "Dividend yield",
              "Beta (monthly vs SPY)", "Beta months used", "Fiscal year end", "Statement currency",
              "Operating cash flow (m)", "Capex (m)", "Free cash flow (m)", "Cash dividends paid (m)", "FCF cover (x)",
              *year_cols, f"Dividend years paid {yrs[0]}-{yrs[-1]}",
              "Total debt incl. leases (m)", "Shareholders' equity (m)", "D/E exclusion reason", "D/E status",
              "Debt-to-equity", "Peer median D/E", "EBIT (m)", "Interest expense (m)", "Interest cover status",
              "Interest cover (x)", "Data notes", "Flags and caveats"]
    col = {name: get_column_letter(i) for i, name in enumerate(header, 1)}
    n = len(metrics)
    last = n + 1
    mil = lambda v: None if v is None else v / 1e6  # noqa: E731

    # Dividend events and month-end prices that feed the formulas.
    first_date = min(f"{yrs[0]}-01-01", (dt.date.fromisoformat(asof) - dt.timedelta(days=730)).isoformat())
    div_rows = [[m["ticker"], dt.date.fromisoformat(d), float(v)] for m in metrics if m["divs_series"] is not None
                for d, v in m["divs_series"].items() if d >= first_date and v > 0]
    d_last = max(len(div_rows) + 1, 2)
    d_tic, d_date, d_amt = (f"Dividends!${x}$2:${x}${d_last}" for x in "ABC")
    months = [i for i in dict.fromkeys(i[:7] for i in bench.index) if i < asof[:7]][-(cfg["beta"]["months"] + 1):]
    p_last = len(months) + 1          # last row on Prices
    r_last = len(months)              # last row on Returns (one fewer than Prices)
    series_col = {t: get_column_letter(i) for i, t in enumerate([cfg["benchmark_ticker"]] + [m["ticker"] for m in metrics], 2)}
    bench_ret = f"Returns!${series_col[cfg['benchmark_ticker']]}$2:${series_col[cfg['benchmark_ticker']]}${r_last}"
    grp_rng = f"Screen!${col['Group']}$2:${col['Group']}${last}"
    de_st_rng = f"Screen!${col['D/E status']}$2:${col['D/E status']}${last}"
    de_rng = f"Screen!${col['Debt-to-equity']}$2:${col['Debt-to-equity']}${last}"
    g_last = len(medians) + 1

    rows = []
    for r, m in enumerate(metrics, 2):
        c = {name: f"{letter}{r}" for name, letter in col.items()}
        hist, price, ttm = c["Dividend history status"], c["Price (USD)"], c["TTM dividends per share"]
        y, beta, mcap = c["Dividend yield"], c["Beta (monthly vs SPY)"], c["Market cap (USD bn)"]
        ocf, capex, fcf, div = c["Operating cash flow (m)"], c["Capex (m)"], c["Free cash flow (m)"], c["Cash dividends paid (m)"]
        n_years = c[f"Dividend years paid {yrs[0]}-{yrs[-1]}"]
        debt, eq, de_st, de, med = c["Total debt incl. leases (m)"], c["Shareholders' equity (m)"], c["D/E status"], c["Debt-to-equity"], c["Peer median D/E"]
        ebit, intr, ic_st, ic = c["EBIT (m)"], c["Interest expense (m)"], c["Interest cover status"], c["Interest cover (x)"]
        ret = f"Returns!${series_col[m['ticker']]}$2:${series_col[m['ticker']]}${r_last}"
        crit = f"{c[CRITERIA[0]]}:{c[CRITERIA[-1]]}"
        t = {name: c[label] for name, label in applied.items()}      # this row's applied thresholds
        pick = f"MATCH({c['Group']},th_groups,0)"
        sumifs = lambda fn, lo, hi: (f'{fn}({"" if fn == "COUNTIFS" else d_amt + ","}{d_tic},$A{r},'  # noqa: E731
                                     f'{d_date},{lo},{d_date},{hi})')
        f = {
            "Overall": f'=IF(COUNTIF({crit},"fail")>0,"fail",IF(COUNTIF({crit},"pass")={len(CRITERIA)},"pass","no_data"))',
            "C1 yield": f'=IF({y}="","no_data",IF({y}>={t['yield_min']},"pass","fail"))',
            "C2 beta": f'=IF({beta}="","no_data",IF({beta}<{t['beta_max']},"pass","fail"))',
            "C3 FCF cover": f'=IF({fcf}="","no_data",IF({fcf}<=0,"fail",IF({div}="","no_data",IF({div}=0,"pass",'
                            f'IF({fcf}/{div}>={t['fcf_cover_min']},"pass","fail")))))',
            "C4 dividend record": f'=IF({n_years}="","no_data",IF({n_years}>={t['div_years_min']},"pass","fail"))',
            "C5 leverage": f'=IF(OR(AND({de_st}="ok",ISNUMBER({med}),N({de})<N({med})),AND({ic_st}="ok",N({ic})>{t['interest_cover_min']})),"pass",'
                           f'IF(AND(OR({de_st}="not computable",{de_st}="excluded",AND({de_st}="ok",ISNUMBER({med}))),'
                           f'{ic_st}<>"missing"),"fail","no_data"))',
            "C6 size": f'=IF({mcap}="","no_data",IF({mcap}>{t['mcap_min_bn']},"pass","fail"))',
            "TTM dividends per share": f'=IF({hist}="missing","",{sumifs("SUMIFS", chr(34) + ">" + chr(34) + "&(asof_date-th_ttm_days)", chr(34) + "<=" + chr(34) + "&asof_date")})',
            "Payments in TTM": f'=IF({hist}="missing","",{sumifs("COUNTIFS", chr(34) + ">" + chr(34) + "&(asof_date-th_ttm_days)", chr(34) + "<=" + chr(34) + "&asof_date")})',
            "Payments in prior 12 months": f'=IF({hist}="missing","",{sumifs("COUNTIFS", chr(34) + ">" + chr(34) + "&(asof_date-2*th_ttm_days)", chr(34) + "<=" + chr(34) + "&(asof_date-th_ttm_days)")})',
            # Blank if the history is missing, or if it shows nothing while Yahoo indicates a dividend (incomplete history).
            "Dividend yield": f'=IF(OR({hist}="missing",{price}="",{ttm}=""),"",IF(AND({ttm}=0,N({c["Yahoo indicated annual dividend"]})>0),"",{ttm}/{price}))',
            "Beta (monthly vs SPY)": f'=IF(COUNT({ret})<th_beta_min_months,"",SLOPE({ret},{bench_ret}))',
            "Beta months used": f'=COUNT({ret})',
            "Free cash flow (m)": f'=IF(OR({ocf}="",{capex}=""),"",{ocf}+{capex})',
            "FCF cover (x)": f'=IF(OR({fcf}="",{div}="",N({div})=0),"",{fcf}/{div})',
            f"Dividend years paid {yrs[0]}-{yrs[-1]}": f'=IF({hist}="missing","",COUNTIF({c[year_cols[0]]}:{c[year_cols[-1]]},">0"))',
            "D/E status": f'=IF({c["D/E exclusion reason"]}<>"","excluded",IF(OR({debt}="",{eq}=""),"missing",IF({eq}<=0,"not computable","ok")))',
            "Debt-to-equity": f'=IF({de_st}="ok",{debt}/{eq},"")',
            "Peer median D/E": f"=INDEX('Group medians'!$B$2:$B${g_last},MATCH({c['Group']},'Group medians'!$A$2:$A${g_last},0))",
            "Interest cover status": f'=IF(OR({ebit}="",{intr}=""),"missing",IF({intr}<=0,"not computable","ok"))',
            "Interest cover (x)": f'=IF({ic_st}="ok",{ebit}/{intr},"")',
        }
        # A blank group cell on the Thresholds sheet falls back to the default row.
        for name, label in applied.items():
            f[label] = f'=IF(INDEX(th_{name},{pick})="",th_{name}_default,INDEX(th_{name},{pick}))'
        for yr, name in zip(yrs, year_cols):
            f[name] = f'=IF({hist}="missing","",{sumifs("SUMIFS", chr(34) + ">=" + chr(34) + f"&DATE({yr},1,1)", chr(34) + "<=" + chr(34) + f"&DATE({yr},12,31)")})'
        hist_status = ("missing" if not m["has_dividend_data"]
                       else "ok" if len(m["divs_series"]) else "none reported")
        # Threshold-independent explanations of why a criterion cannot be assessed.
        notes = [f"{k}: {m['results'][k][1]}" for k in CRITERIA
                 if k != "C5 leverage" and m["results"][k][0] == NO_DATA]
        static = {
            "Ticker": m["ticker"], "Name": m["name"], "Group": m["group"], "Price (USD)": m["price"],
            "Market cap (USD bn)": None if m["market_cap"] is None else m["market_cap"] / 1e9,
            "Dividend history status": hist_status, "Yahoo indicated annual dividend": m["indicated_dps"],
            "Fiscal year end": m["fy_end"], "Statement currency": m["fin_currency"],
            "Operating cash flow (m)": mil(m["ocf"]), "Capex (m)": mil(m["capex"]),
            "Cash dividends paid (m)": mil(m["dividends_paid"]),
            "Total debt incl. leases (m)": mil(m["total_debt"]), "Shareholders' equity (m)": mil(m["equity"]),
            "D/E exclusion reason": m["de_excluded"], "EBIT (m)": mil(m["ebit"]),
            "Interest expense (m)": mil(m["interest_expense"]),
            "Data notes": " | ".join(notes), "Flags and caveats": "; ".join(m["flags"]),
        }
        rows.append([f[h] if h in f else static[h] for h in header])
    num = "#,##0;[Red]-#,##0"
    formats = {"Price (USD)": "0.00", "Market cap (USD bn)": "#,##0.0", "TTM dividends per share": "0.000",
               "Yahoo indicated annual dividend": "0.000", "Dividend yield": "0.00%", "Beta (monthly vs SPY)": "0.00",
               "FCF cover (x)": "0.00", "Debt-to-equity": "0.00", "Peer median D/E": "0.00", "Interest cover (x)": "0.0",
               **{applied[name]: fmt for name, _, fmt, _ in GROUP_THRESHOLDS.values()},
               **{h: "0.000" for h in year_cols}, **{h: num for h in header if h.endswith("(m)")}}
    write_sheet(ws, header, rows, formats, {"Name": 30, "Group": 22, "D/E exclusion reason": 30, "Data notes": 70,
                                            "Flags and caveats": 70})
    ws.row_dimensions[1].height = 60
    status_range = f"{col['Overall']}2:{col[CRITERIA[-1]]}{last}"
    for status, fill in FILLS.items():
        ws.conditional_formatting.add(status_range, CellIsRule(
            operator="equal", formula=[f'"{status}"'],
            fill=PatternFill(start_color=fill.fgColor.rgb, end_color=fill.fgColor.rgb, fill_type="solid")))

    # --- Thresholds (editable per-group inputs, calculation settings and a live summary)
    ws = wb.create_sheet("Thresholds")
    groups = list(medians)
    overrides = cfg.get("group_thresholds") or {}
    keys = list(GROUP_THRESHOLDS)
    labels = [GROUP_THRESHOLDS[k][1] + (f" (of {len(yrs)})" if k == "dividend_record_min_years" else "") for k in keys]
    scaled = lambda k, v: v * GROUP_THRESHOLDS[k][3]  # noqa: E731
    rows = [["Default (all groups)", *[scaled(k, th[k]) for k in keys]]]
    rows += [[g, *[scaled(k, overrides[g][k]) if k in (overrides.get(g) or {}) else None for k in keys]] for g in groups]
    write_sheet(ws, ["Group", *labels], rows, widths={"Group": 36, **{h: 16 for h in labels}}, filterable=False)
    ws.row_dimensions[1].height = 45
    g_first, g_end = 3, 2 + len(groups)
    wb.defined_names["th_groups"] = DefinedName("th_groups", attr_text=f"Thresholds!$A${g_first}:$A${g_end}")
    for i, k in enumerate(keys, 2):
        name, _, fmt, _ = GROUP_THRESHOLDS[k]
        letter = get_column_letter(i)
        for r in range(2, g_end + 1):
            ws[f"{letter}{r}"].number_format = fmt
            ws[f"{letter}{r}"].fill = PatternFill("solid", fgColor="FFF2CC")
            ws[f"{letter}{r}"].font = Font(bold=(r == 2), color="0000FF")
        wb.defined_names[f"th_{name}_default"] = DefinedName(f"th_{name}_default", attr_text=f"Thresholds!${letter}$2")
        wb.defined_names[f"th_{name}"] = DefinedName(f"th_{name}", attr_text=f"Thresholds!${letter}${g_first}:${letter}${g_end}")
    ws["A2"].font = Font(bold=True)
    row = g_end + 2
    for text in ("Edit the yellow cells. A blank group cell uses the default row; type a number to override it for that group. "
                 "The thresholds actually applied to each stock are shown in the 'Applied' columns on the Screen sheet.",
                 "Rules: C1 yield at least the minimum | C2 beta below the maximum | C3 FCF positive and FCF / dividends paid at least the minimum | "
                 f"C4 dividend paid in at least this many of the years {yrs[0]}-{yrs[-1]} | C5 interest cover above the minimum, or D/E below the peer median | "
                 "C6 market cap above the minimum.",
                 "Raw inputs on the Screen, Dividends and Prices sheets can be edited too. Re-running screen.py resets everything to the "
                 "snapshot and to config.yaml."):
        ws[f"A{row}"] = text
        row += 1
    row += 1
    ws[f"A{row}"], ws[f"B{row}"], ws[f"C{row}"] = "Calculation setting (all groups)", "Value", "Note"
    for cell in ws[row][:3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
    settings = [
        ("th_beta_min_months", "Beta: minimum months of returns", cfg["beta"]["min_months"], "0", True,
         f"Beta is blank (no_data) with fewer monthly returns than this; at most {cfg['beta']['months']} are available"),
        ("th_ttm_days", "Dividend window (days)", 365, "0", True, "Length of the trailing window used for dividends per share and yield"),
        ("asof_date", "As-of date (fixed)", dt.date.fromisoformat(asof), "yyyy-mm-dd", False,
         "Date of the snapshot. Prices and market caps are as of this date, so it should not be changed."),
    ]
    for name, label, value, fmt, editable, note in settings:
        row += 1
        ws[f"A{row}"], ws[f"B{row}"], ws[f"C{row}"] = label, value, note
        ws[f"B{row}"].number_format = fmt
        if editable:
            ws[f"B{row}"].fill = PatternFill("solid", fgColor="FFF2CC")
            ws[f"B{row}"].font = Font(bold=True, color="0000FF")
        wb.defined_names[name] = DefinedName(name, attr_text=f"Thresholds!$B${row}")
    row += 2
    summary_row = row

    def summary_header(r, title):
        ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"] = title, PASS, FAIL, NO_DATA
        for cell in ws[r][:4]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = HEADER_FILL

    summary_header(row, "Live summary: all stocks")
    for name in ["Overall", *CRITERIA]:
        row += 1
        ws[f"A{row}"] = name
        for letter, status in zip("BCD", (PASS, FAIL, NO_DATA)):
            ws[f"{letter}{row}"] = f'=COUNTIF(Screen!${col[name]}$2:${col[name]}${last},"{status}")'
    ws[f"A{summary_row + 1}"].font = Font(bold=True)
    row += 2
    group_summary_row = row
    summary_header(row, "Live summary: overall result by group")
    for i, g in enumerate(groups):
        row += 1
        ws[f"A{row}"] = f"=$A${g_first + i}"
        for letter, status in zip("BCD", (PASS, FAIL, NO_DATA)):
            ws[f"{letter}{row}"] = (f'=COUNTIFS({grp_rng},$A{row},'
                                    f'Screen!${col["Overall"]}$2:${col["Overall"]}${last},"{status}")')

    # --- Group medians (live: median over names whose D/E status is "ok")
    ws = wb.create_sheet("Group medians")
    count = lambda g, status: f'=COUNTIFS({grp_rng},$A{g},{de_st_rng},"{status}")'  # noqa: E731
    rows = [[name, None, count(g, "ok"), count(g, "not computable"), count(g, "missing"), count(g, "excluded"),
             ", ".join(v["used"]), ", ".join(v["not_computable"]), ", ".join(v["missing"]), ", ".join(v["excluded"])]
            for g, (name, v) in enumerate(medians.items(), 2)]
    write_sheet(ws, ["Group", "Peer median D/E", "Names used", "Not computable (equity zero or negative)",
                     "Data missing", "Excluded", "As built: tickers used", "As built: not computable",
                     "As built: data missing", "As built: excluded"], rows,
                {"Peer median D/E": "0.00"},
                {"Group": 24, "Not computable (equity zero or negative)": 18, "As built: tickers used": 90,
                 "As built: not computable": 40, "As built: data missing": 22, "As built: excluded": 22})
    ws.row_dimensions[1].height = 45
    for g in range(2, g_last + 1):
        ws[f"B{g}"] = ArrayFormula(f"B{g}", f'=IFERROR(MEDIAN(IF(({grp_rng}=$A{g})*({de_st_rng}="ok"),{de_rng})),"")')

    # --- Assumptions
    ws = wb.create_sheet("Assumptions")
    tally = lambda key, val: sum(1 for m in metrics if m[key] == val)  # noqa: E731
    rows = [
        ["Purpose", "One-off academic screening exercise. Not investment advice. A high yield alone can signal a payout under strain."],
        ["As-of date", cfg["asof_date"]],
        ["Data source", "Yahoo Finance via the unofficial yfinance package; fetched once and frozen in "
                        f"data/raw_{cfg['asof_date']}/. Personal and academic use only; raw data must not be redistributed."],
        ["Universe", f"{len(metrics)} US-listed stocks in six hand-picked groups, read from {cfg['universe_file']}."],
        ["Formulas", "Everything derivable is an Excel formula: yield, payment counts and dividend years come from the Dividends sheet; beta from the Prices and Returns sheets; "
                     "FCF, FCF cover, D/E, interest cover, the peer median D/E and every pass/fail result from the raw inputs on the Screen sheet and the editable cells on the Thresholds sheet. "
                     "Threshold figures quoted on this sheet are the config.yaml defaults the workbook was built with."],
        ["Thresholds by group", "Each of the six thresholds can differ by group. Defaults are in config.yaml under 'thresholds' and per-group overrides under 'group_thresholds'. "
                                + ("Overrides in this build: " + "; ".join(f"{g}: " + ", ".join(f"{k}={v}" for k, v in (o or {}).items()) for g, o in (cfg.get("group_thresholds") or {}).items() if o) + "."
                                   if any((cfg.get("group_thresholds") or {}).values()) else "No overrides in this build: every group uses the defaults.")
                                + " The figures quoted below are the defaults."],
        ["Redistribution", "The Dividends and Prices sheets hold raw Yahoo data. Yahoo's terms do not permit redistribution, so do not publish this workbook."],
        ["Result at defaults", f"pass {tally('overall', PASS)}, fail {tally('overall', FAIL)}, no_data {tally('overall', NO_DATA)} "
                           f"at the default {th['dividend_yield_min']:.1%} yield threshold (computed by the script)"],
        ["Result at alternative yield", f"computed by the script: pass {tally('overall_alt', PASS)}, fail {tally('overall_alt', FAIL)}, no_data "
                                        f"{tally('overall_alt', NO_DATA)} at {th['dividend_yield_alt']:.1%} (the original ask)"],
        ["Overall result", "pass only if all six criteria pass; fail if any criterion fails; no_data if none failed but at least one could not be assessed."],
        ["C1 Dividend yield", f"Sum of dividends per share with an ex-dividend date in the 12 months to the as-of date, divided by the last price at fetch time. Pass if at least {th['dividend_yield_min']:.1%}. "
                              "The threshold is a proposed US starting point and has not been confirmed by the user; special dividends are included."],
        ["C2 Beta", f"Computed here, not taken from Yahoo: slope of {cfg['beta']['months']} monthly returns on adjusted close against {cfg['benchmark_ticker']} "
                    f"(covariance / variance), complete months only. Pass if below {th['beta_max']}. Fewer than {cfg['beta']['min_months']} months gives no_data."],
        ["C3 FCF covers dividend", f"Latest annual operating cash flow minus capital expenditure must be positive, and FCF / total cash dividends paid at least {th['fcf_cover_min']}x. "
                                   "A company with positive FCF that paid no cash dividends passes this criterion trivially (it still fails C1 and C4)."],
        ["C4 Dividend record", f"At least one dividend with an ex-dividend date in at least {th['dividend_record_min_years']} of the {len(yrs)} full calendar years {yrs[0]} to {yrs[-1]}, from dividend history rather than the financial statements."],
        ["C5 Leverage", f"Pass if debt-to-equity is below the peer median of the group, or interest cover (EBIT / interest expense) is above {th['interest_cover_min']}x. "
                        "Debt-to-equity = total debt including lease obligations / total shareholders' equity, latest annual balance sheet, same definition for every ticker. "
                        "EBIT is Yahoo's figure (pre-tax income plus interest expense), so it includes non-operating and one-off items."],
        ["C6 Size", f"Market capitalisation above USD {th['market_cap_min_usd'] / 1e9:.0f} billion."],
        ["Peer median D/E", "Median (not mean) debt-to-equity of our selected names in each group: a peer median of our own picks, not an official sector average. "
                            "Computed over every name in the group, including non-payers and names failing other filters. Names with zero or negative equity are excluded as not computable and can pass C5 only on interest cover."],
        ["Known weakness: mixed groups", "Groups mix business models (healthcare has pharma, insurers and distributors; consumer discretionary includes Ford and GM with captive finance arms), which distorts the peer median. Sub-groups would be an improvement."],
        ["Missing data", "Statement rows are looked up by name; an absent value is treated as missing (no_data), never as zero. 'No dividends reported' is distinguished from a failed fetch."],
        ["Currencies", "Price, dividends per share and market cap are in USD. Companies reporting in another currency are flagged; C3 and C5 use ratios within one set of statements, so no conversion is needed."],
        ["Fiscal years", "The 'latest annual' statements are each company's most recent fiscal year on Yahoo, so year-ends differ between tickers (see the Fiscal year end column)."],
        ["Withholding tax context", "Singapore residents generally face 30% US withholding tax on dividends, so a 3% gross yield is roughly 2.1% net. Explanatory context only, not part of the logic; confirm with a tax adviser."],
    ]
    rows += [[f"Caveat: {t}", note] for t, note in cfg.get("ticker_notes", {}).items()]
    rows += [["Python version", manifest["python"]]]
    rows += [[f"Package: {p}", v] for p, v in manifest["packages"].items()]
    write_sheet(ws, ["Item", "Detail"], rows, widths={"Item": 32, "Detail": 150})
    for r in ws.iter_rows(min_row=2):
        r[1].alignment = Alignment(wrap_text=True, vertical="top")
        r[0].alignment = Alignment(vertical="top")

    # --- Fetch log
    ws = wb.create_sheet("Fetch log")
    fields = ["prices_monthly", "dividends", "cashflow", "balance_sheet", "income_stmt", "info"]
    rows = [[m["ticker"], m["fetch_status"], *[m["fetch_fields"].get(f) for f in fields],
             ", ".join(m["missing"]), manifest["tickers"].get(m["ticker"], {}).get("fetched_at_utc")] for m in metrics]
    b = manifest["tickers"].get(cfg["benchmark_ticker"], {})
    rows.append([cfg["benchmark_ticker"] + " (benchmark)", b.get("status"), b.get("fields", {}).get("prices_monthly"),
                 None, None, None, None, None, "", b.get("fetched_at_utc")])
    write_sheet(ws, ["Ticker", "Fetch status", *fields, "Missing metric inputs", "Fetched at (UTC)"], rows,
                widths={"Missing metric inputs": 60, "Fetched at (UTC)": 26, "Ticker": 16})

    # --- Dividends, Prices, Returns (inputs to the Screen formulas)
    ws = wb.create_sheet("Dividends")
    write_sheet(ws, ["Ticker", "Ex-dividend date", "Dividend per share (USD)"], div_rows,
                {"Ex-dividend date": "yyyy-mm-dd", "Dividend per share (USD)": "0.0000"},
                {"Ex-dividend date": 16, "Dividend per share (USD)": 16})
    by_month = {cfg["benchmark_ticker"]: {i[:7]: float(v) for i, v in bench.items()}}
    for m in metrics:
        by_month[m["ticker"]] = ({} if m["price_series"] is None
                                 else {i[:7]: float(v) for i, v in m["price_series"].items()})
    ws = wb.create_sheet("Prices")
    write_sheet(ws, ["Month (adjusted close)", *series_col],
                [[mo, *[by_month[t].get(mo) for t in series_col]] for mo in months],
                {t: "0.00" for t in series_col}, {"Month (adjusted close)": 14})
    ws = wb.create_sheet("Returns")
    write_sheet(ws, ["Month (return)", *series_col],
                [[mo, *[f'=IF(AND(ISNUMBER(Prices!{x}{i}),ISNUMBER(Prices!{x}{i + 1})),Prices!{x}{i + 1}/Prices!{x}{i}-1,"")'
                        for x in series_col.values()]] for i, mo in enumerate(months[1:], 2)],
                {t: "0.00%" for t in series_col}, {"Month (return)": 14})

    wb.properties.creator = "defensivestockproj screen.py"
    wb.save(path)
    normalise_xlsx(path, cfg["asof_date"])


# ----------------------------------------------------------------------------- main

def run(out: Path | None = None, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw = ROOT / "data" / f"raw_{cfg['asof_date']}"
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    manifest["packages"]["openpyxl"] = version("openpyxl")
    universe = load_universe(ROOT / cfg["universe_file"])

    bench = load_series(raw / cfg["benchmark_ticker"] / "prices_monthly.json", "Close")
    bench_returns = monthly_returns(bench, cfg["asof_date"])

    metrics = [compute_metrics(r, raw, manifest, bench_returns, cfg) for r in universe]
    medians = group_medians(metrics, cfg.get("median_exclusions") or {})
    notes = cfg.get("ticker_notes", {})
    thresholds = resolve_thresholds(cfg, list(medians))
    for m in metrics:
        m["thresholds"] = thresholds[m["group"]]
        apply_filters(m, m["thresholds"], medians[m["group"]]["median"])
        m["flags"] = ([notes[m["ticker"]]] if m["ticker"] in notes else []) + sanity_warnings(m, cfg)
        if m["fetch_status"] != "ok":
            m["flags"].append(f"fetch status: {m['fetch_status']}")
    export(metrics, medians, thresholds, manifest, cfg, bench, out or ROOT / "results.xlsx")
    return metrics


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None      # optional alternative output path
    out = run(target)
    counts = {s: sum(1 for m in out if m["overall"] == s) for s in (PASS, FAIL, NO_DATA)}
    print(f"{target or 'results.xlsx'} written: {counts}")
    print("Passing:", ", ".join(m["ticker"] for m in out if m["overall"] == PASS))

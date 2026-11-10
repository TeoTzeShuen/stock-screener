"""Fetch raw data from Yahoo Finance (via yfinance) once and freeze it on disk.

One sequential pass with a pause between tickers and retries with backoff.
Everything is written to data/raw_<asof-date>/ together with a manifest.
Tickers already fetched successfully are skipped, so an interrupted run can be resumed.
All later steps (screen.py) read only from this snapshot.

Usage:  python src/fetch.py            (resume / fetch missing tickers)
        python src/fetch.py --retry    (also re-fetch tickers whose status is not "ok")
"""
from __future__ import annotations

import datetime as dt
import json
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import yaml
import yfinance as yf

from universe import ROOT, load_universe, write_universe_csv

STATEMENTS = {"cashflow": "cashflow", "balance_sheet": "balance_sheet", "income_stmt": "income_stmt"}
PACKAGES = ["yfinance", "pandas", "numpy", "curl_cffi", "openpyxl", "PyYAML"]


def save_frame(obj, path: Path) -> None:
    """Write a DataFrame or Series as JSON (orient=split) with full precision."""
    frame = obj.to_frame() if isinstance(obj, pd.Series) else obj
    path.write_text(frame.to_json(orient="split", date_format="iso", double_precision=15), encoding="utf-8")


def with_retry(fn, cfg, accept_empty=False):
    """Call fn with backoff. Returns (value, status) where status is ok / empty / error:<msg>."""
    delay, last = cfg["backoff_seconds"], "empty"
    for attempt in range(cfg["max_attempts"]):
        try:
            value = fn()
            if value is not None and len(value) > 0:
                return value, "ok"
            last = "empty"
            if accept_empty:
                return value, "empty"
        except Exception as exc:  # noqa: BLE001 - record whatever Yahoo throws
            last = f"error: {type(exc).__name__}: {exc}"[:300]
        if attempt < cfg["max_attempts"] - 1:
            time.sleep(delay)
            delay *= 2
    return None, last


def fetch_ticker(symbol: str, out: Path, cfg: dict, benchmark: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t = yf.Ticker(symbol)
    fields: dict[str, str] = {}

    prices, fields["prices_monthly"] = with_retry(
        lambda: t.history(period=cfg["price_history_period"], interval="1mo", auto_adjust=True), cfg)
    if prices is not None:
        save_frame(prices, out / "prices_monthly.json")

    if not benchmark:
        # An empty dividend series is a legitimate answer (non-payer), but only if the
        # price request for the same ticker worked; otherwise it is a failed fetch.
        divs, status = with_retry(lambda: t.dividends, cfg, accept_empty=fields["prices_monthly"] == "ok")
        if status == "ok":
            save_frame(divs, out / "dividends.json")
            fields["dividends"] = "ok"
        elif status == "empty" and fields["prices_monthly"] == "ok":
            (out / "dividends.json").write_text(
                json.dumps({"columns": ["Dividends"], "index": [], "data": []}), encoding="utf-8")
            fields["dividends"] = "none_reported"
        else:
            fields["dividends"] = status

        for name, attr in STATEMENTS.items():
            frame, fields[name] = with_retry(lambda a=attr: getattr(t, a), cfg)
            if frame is not None:
                save_frame(frame, out / f"{name}.json")

        info, fields["info"] = with_retry(lambda: t.info, cfg)
        if info is not None:
            (out / "info.json").write_text(json.dumps(info, indent=1, sort_keys=True, default=str), encoding="utf-8")

    ok = all(v in ("ok", "none_reported") for v in fields.values())
    failed = all(v not in ("ok", "none_reported") for v in fields.values())
    return {
        "status": "ok" if ok else ("failed" if failed else "partial"),
        "fields": fields,
        "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    asof = cfg["asof_date"]
    if asof != dt.date.today().isoformat():
        sys.exit(f"config asof_date is {asof} but today is {dt.date.today()}; Yahoo only serves current "
                 "data, so update asof_date before fetching a new snapshot.")
    universe = load_universe(ROOT / cfg["universe_file"])
    write_universe_csv(universe, ROOT / "universe.csv")

    raw = ROOT / "data" / f"raw_{asof}"
    raw.mkdir(parents=True, exist_ok=True)
    manifest_path = raw / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "asof_date": asof,
        "source": "Yahoo Finance via yfinance (unofficial); personal and academic use only, do not redistribute",
        "python": platform.python_version(),
        "packages": {p: version(p) for p in PACKAGES},
        "tickers": {},
    }
    retry = "--retry" in sys.argv
    symbols = [cfg["benchmark_ticker"]] + [r["ticker"] for r in universe]
    for i, symbol in enumerate(symbols, 1):
        prev = manifest["tickers"].get(symbol)
        if prev and (prev["status"] == "ok" or not retry):
            continue
        result = fetch_ticker(symbol, raw / symbol, cfg["fetch"], benchmark=symbol == cfg["benchmark_ticker"])
        manifest["tickers"][symbol] = result
        manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"[{i}/{len(symbols)}] {symbol}: {result['status']} {result['fields']}", flush=True)
        time.sleep(cfg["fetch"]["sleep_seconds"])

    counts: dict[str, int] = {}
    for r in manifest["tickers"].values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Fetch complete:", counts)


if __name__ == "__main__":
    main()

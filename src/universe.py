"""Load the stock universe from baseline.md (the source of truth) and derive universe.csv."""
from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = re.compile(r"\s*(.+?)\s*\(\$([A-Z][A-Z.\-]*)\)\s*$")


def load_universe(path: Path) -> list[dict]:
    """Return [{ticker, name, group}] in the order given in baseline.md."""
    rows, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        group, _, body = line.partition(":")
        # Split on the comma that follows a closing "($TICKER)" so names stay intact.
        for chunk in re.split(r"(?<=\)),", body):
            m = ENTRY.match(chunk)
            if not m:
                raise ValueError(f"Cannot parse universe entry: {chunk!r}")
            name, ticker = m.group(1), m.group(2)
            if ticker in seen:
                raise ValueError(f"Duplicate ticker in universe: {ticker}")
            seen.add(ticker)
            rows.append({"ticker": ticker, "name": name, "group": group.strip()})
    return rows


def write_universe_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ticker", "name", "group"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    universe = load_universe(ROOT / "baseline.md")
    write_universe_csv(universe, ROOT / "universe.csv")
    groups: dict[str, int] = {}
    for r in universe:
        groups[r["group"]] = groups.get(r["group"], 0) + 1
    print(f"{len(universe)} tickers written to universe.csv")
    for g, n in groups.items():
        print(f"  {g}: {n}")

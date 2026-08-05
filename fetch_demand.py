"""
Tamil Nadu daily electricity demand, from Grid India (POSOCO) daily PSP reports.

No scraping, no PDF parsing. One CSV download.

    python fetch_demand.py          # download + slice, run daily
    python fetch_demand.py --cache  # re-slice the last download, no network

The upstream file is refreshed daily, so re-running this keeps you current to
within about one day of real time. --cache is for iterating on the slicing
logic offline; it never refreshes the data.
"""

import sys

import pandas as pd

from config import KEEP, POSOCO_URL, RAW_DEMAND, RAW_POSOCO, TARGET_COL


def download(use_cache=False) -> pd.DataFrame:
    if use_cache and RAW_POSOCO.exists():
        print(f"using cached {RAW_POSOCO}")
        return pd.read_csv(RAW_POSOCO)

    print(f"downloading {POSOCO_URL}")
    df = pd.read_csv(POSOCO_URL)
    df.to_csv(RAW_POSOCO, index=False)
    print(f"  {len(df):,} rows, {len(df.columns)} columns")
    return df


def slice_tn(df: pd.DataFrame) -> pd.DataFrame:
    if "yyyymmdd" not in df.columns:
        sys.exit(f"expected a yyyymmdd column, got: {list(df.columns)[:6]}")

    missing = [c for c in KEEP if c not in df.columns]
    if TARGET_COL in missing:
        sys.exit(f"target column '{TARGET_COL}' not found - upstream schema changed")
    if missing:
        print(f"  note: {len(missing)} optional columns absent: {missing}")

    present = {k: v for k, v in KEEP.items() if k in df.columns}

    out = df[["yyyymmdd"] + list(present)].rename(columns=present)
    out["date"] = pd.to_datetime(out["yyyymmdd"], format="%Y%m%d", errors="coerce")
    out = out.drop(columns="yyyymmdd")

    out = (out.dropna(subset=["date", "tn_energy_gwh"])
              .drop_duplicates(subset="date", keep="last")
              .sort_values("date")
              .reset_index(drop=True))

    cols = ["date"] + [c for c in out.columns if c != "date"]
    return out[cols]


def main(use_cache=False):
    tn = slice_tn(download(use_cache))
    tn.to_csv(RAW_DEMAND, index=False)

    print(f"\nTamil Nadu daily energy met")
    print(f"  rows        {len(tn):,}")
    print(f"  range       {tn['date'].min():%Y-%m-%d} -> {tn['date'].max():%Y-%m-%d}")
    print(f"  GWh/day     min {tn.tn_energy_gwh.min():,.0f}"
          f"  mean {tn.tn_energy_gwh.mean():,.0f}"
          f"  max {tn.tn_energy_gwh.max():,.0f}")

    span = pd.date_range(tn["date"].min(), tn["date"].max(), freq="D")
    gaps = span.difference(pd.DatetimeIndex(tn["date"]))
    print(f"  gaps        {len(gaps)} missing days")

    lag = (pd.Timestamp.today().normalize() - tn["date"].max()).days
    print(f"  freshness   {lag} days behind today")
    print(f"\nwrote {RAW_DEMAND}")


if __name__ == "__main__":
    main(use_cache="--cache" in sys.argv)

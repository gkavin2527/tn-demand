"""
Verify the dataset is healthy. Exits non-zero on failure so the GitHub Action
turns red and you get an email instead of quietly collecting broken data.

    python health_check.py
"""

import sys
from datetime import date

import pandas as pd

from config import FORECAST_LOG, MASTER, RAW_DEMAND, RAW_WEATHER

MAX_DEMAND_LAG = 5      # upstream CSV normally sits ~1 day behind
MAX_WEATHER_LAG = 3
MIN_ROWS = 3000


def check(path, date_col, label, max_lag, problems):
    if not path.exists():
        problems.append(f"{label}: file missing ({path.name})")
        return None

    df = pd.read_csv(path)
    if date_col not in df.columns:
        problems.append(f"{label}: no '{date_col}' column")
        return None

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    latest = df[date_col].max()
    lag = (pd.Timestamp(date.today()) - latest).days

    status = "ok" if lag <= max_lag else "STALE"
    print(f"  {label:<16} {len(df):>7,} rows   latest {latest:%Y-%m-%d}   "
          f"{lag}d behind   {status}")

    if lag > max_lag:
        problems.append(f"{label}: {lag} days behind (limit {max_lag})")
    return df


def main():
    problems, warnings = [], []

    print("data freshness")
    check(RAW_DEMAND, "date", "demand", MAX_DEMAND_LAG, problems)
    check(RAW_WEATHER, "date", "weather", MAX_WEATHER_LAG, problems)
    master = check(MASTER, "date", "master", MAX_DEMAND_LAG + 1, problems)

    if FORECAST_LOG.exists():
        fl = pd.read_csv(FORECAST_LOG)
        issued = pd.to_datetime(fl["issued_on"]).dt.date
        days = issued.nunique()
        lag = (date.today() - issued.max()).days
        print(f"  {'forecast log':<16} {days:>7,} days   latest {issued.max()}   "
              f"{lag}d behind   {'ok' if lag <= 2 else 'STALE'}")
        if lag > 2:
            problems.append(f"forecast log: not written for {lag} days "
                            f"(this data cannot be recovered later)")

        # Interior gaps matter as much as staleness: a run that failed three
        # weeks ago leaves a permanent hole no archive can fill, and the log
        # would still look "fresh" today. Warn rather than fail - the hole is
        # already unrecoverable, so going red every day after would only
        # train you to ignore this check.
        uniq = pd.Series(sorted(issued.unique()))
        if len(uniq) > 1:
            d = uniq.diff().dropna().dt.days
            gaps = [(uniq[i], int(g)) for i, g in d.items() if g > 2]
            if gaps:
                total = sum(g - 1 for _, g in gaps)
                warnings.append(
                    f"forecast log has {len(gaps)} gap(s) over 2 days "
                    f"({total} issue-dates missing, unrecoverable): "
                    + ", ".join(f"{g}d before {dt}" for dt, g in gaps[:5])
                    + ("..." if len(gaps) > 5 else ""))
    else:
        warnings.append("forecast log missing - start it, it cannot be backfilled")

    if master is not None:
        print("\nintegrity")
        if len(master) < MIN_ROWS:
            problems.append(f"master: only {len(master):,} rows (expected >{MIN_ROWS:,})")

        span = pd.date_range(master["date"].min(), master["date"].max(), freq="D")
        gaps = span.difference(pd.DatetimeIndex(master["date"]))
        print(f"  missing days   {len(gaps)}")
        if len(gaps) > 60:
            problems.append(f"master: {len(gaps)} missing days, unusually high")

        if "tn_energy_gwh" not in master.columns:
            problems.append("master: no 'tn_energy_gwh' column - the target is gone")
        else:
            y = master["tn_energy_gwh"]
            print(f"  target range   {y.min():,.0f} - {y.max():,.0f} GWh")
            if y.min() <= 0 or y.max() > 2000:
                problems.append(f"target out of plausible range: {y.min():.0f}-{y.max():.0f}")

            if master.tail(30)["tn_energy_gwh"].nunique() == 1:
                problems.append("target constant over last 30 rows - upstream may be broken")

            if "cdd" in master.columns:
                corr = y.corr(master["cdd"])
                print(f"  cdd corr       {corr:.3f}")
                if abs(corr) < 0.2:
                    problems.append(f"weather-demand correlation collapsed to {corr:.3f} "
                                    f"- the join may be misaligned")

    print()
    for w in warnings:
        print(f"WARNING  {w}")
    for p in problems:
        print(f"FAIL     {p}")

    if problems:
        print(f"\n{len(problems)} problem(s) found")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()

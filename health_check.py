"""
Verify the dataset is healthy. Exits non-zero on failure so the GitHub Action
turns red and you get an email instead of quietly collecting broken data.

    python health_check.py
"""

import sys
import time
from datetime import date

import pandas as pd

from config import (FORECAST_LOG, MASTER, RAW_DEMAND, RAW_POSOCO, RAW_WEATHER,
                    TARGET_COL)

MAX_DEMAND_LAG = 5      # upstream CSV normally sits ~1 day behind
MAX_WEATHER_LAG = 3
MAX_MASTER_LAG = 1      # master is rebuilt from demand on every run
DEAD_SOURCE_LAG = 14    # past this, upstream is not late - it has stopped
MIN_ROWS = 3000


def load(path, date_col, label, problems):
    """Read a dated CSV. Returns (df, latest, lag_in_days), or Nones."""
    if not path.exists():
        problems.append(f"{label}: file missing ({path.name})")
        return None, None, None

    df = pd.read_csv(path)
    if date_col not in df.columns:
        problems.append(f"{label}: no '{date_col}' column")
        return None, None, None

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    latest = df[date_col].max()
    lag = (pd.Timestamp(date.today()) - latest).days
    return df, latest, lag


def report(label, df, latest, lag, status):
    print(f"  {label:<16} {len(df):>7,} rows   latest {latest:%Y-%m-%d}   "
          f"{lag}d behind   {status}")


def upstream_latest():
    """
    Latest dated row in the raw upstream download, or None if it isn't on disk.

    fetch_demand.py rewrites this file every run, so in CI it is what the
    source served us minutes ago. Comparing it to our slice separates "the
    source hasn't published yet" from "we downloaded new rows and then lost
    them" - identical staleness, completely different problem. It is
    gitignored, so a standalone local run may not have it.
    """
    if not RAW_POSOCO.exists():
        return None

    # Only a download from this run can be compared against the slice. A
    # leftover from an old local run says nothing about what the source holds
    # today, and against a freshly pulled demand_daily.csv it would read as
    # rows going missing when nothing is wrong.
    if time.time() - RAW_POSOCO.stat().st_mtime > 24 * 3600:
        return None
    try:
        raw = pd.read_csv(RAW_POSOCO, usecols=["yyyymmdd", TARGET_COL])
    except (ValueError, pd.errors.ParserError):
        return None    # schema drift; fetch_demand.py is the one that reports it

    d = pd.to_datetime(raw.dropna(subset=[TARGET_COL])["yyyymmdd"],
                       format="%Y%m%d", errors="coerce")
    return None if d.isna().all() else d.max()


def demand_status(latest, lag, upstream, problems, warnings):
    """Freshness verdict for the demand slice. Returns the status string."""
    if upstream is not None and upstream > latest:
        dropped = (upstream - latest).days
        problems.append(
            f"demand: upstream published through {upstream:%Y-%m-%d} but "
            f"demand_daily.csv stops at {latest:%Y-%m-%d} - {dropped} day(s) of "
            f"available data is not landing, so the slice or the commit is broken")
        return "BROKEN"

    if lag <= MAX_DEMAND_LAG:
        return "ok"

    # Past the limit, but we hold everything the source has published. The
    # delay is theirs: Grid India's reports slip over weekends and national
    # holidays, and the republication runs on its own cadence. Going red every
    # morning for a third party's publishing schedule only teaches you to
    # ignore a red run - and the rows are not lost, they arrive when upstream
    # catches up. So warn, until the silence is long enough that "late" stops
    # being the likely explanation.
    if lag > DEAD_SOURCE_LAG:
        problems.append(
            f"demand: nothing published since {latest:%Y-%m-%d}, {lag} days ago "
            f"(limit {DEAD_SOURCE_LAG}) - assume the source has moved or died "
            f"rather than that it is running late")
        return "DEAD"

    why = ("upstream is level with us"
           if upstream is not None else
           "raw download absent, cannot tell whose delay this is")
    warnings.append(f"demand: {lag} days behind - {why}; "
                    f"fails at {DEAD_SOURCE_LAG}d")
    return "STALE"


def main():
    problems, warnings = [], []

    print("data freshness")
    demand, d_latest, d_lag = load(RAW_DEMAND, "date", "demand", problems)
    if demand is not None:
        status = demand_status(d_latest, d_lag, upstream_latest(), problems, warnings)
        report("demand", demand, d_latest, d_lag, status)

    weather, w_latest, w_lag = load(RAW_WEATHER, "date", "weather", problems)
    if weather is not None:
        report("weather", weather, w_latest, w_lag,
               "ok" if w_lag <= MAX_WEATHER_LAG else "STALE")
        if w_lag > MAX_WEATHER_LAG:
            problems.append(f"weather: {w_lag} days behind (limit {MAX_WEATHER_LAG})")

    master, m_latest, m_lag = load(MASTER, "date", "master", problems)
    if master is not None:
        # Judged against the demand slice, not against today: master is built
        # from it, so measuring master against the calendar just re-reports an
        # upstream delay a second time. What matters here is whether
        # build_dataset.py picked up the rows we actually hold.
        if demand is None:
            behind, ref = m_lag, "today"
        else:
            behind, ref = (d_latest - m_latest).days, "demand"
        stale = behind > MAX_MASTER_LAG
        report("master", master, m_latest, m_lag, "STALE" if stale else "ok")
        if stale:
            problems.append(
                f"master: {behind} days behind {ref} (latest {m_latest:%Y-%m-%d}) "
                f"- build_dataset.py has not rebuilt it from the current demand slice")

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

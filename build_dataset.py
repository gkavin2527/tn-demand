"""
Build the modelling table.

    python build_dataset.py

Target: tn_energy_gwh - Tamil Nadu daily energy met, in GWh.
Every feature is computable the evening before the target day.
"""

import numpy as np
import pandas as pd

from config import CDD_BASE, CITIES, MASTER, RAW_DEMAND, RAW_WEATHER

WEIGHTS = {c["name"]: c["weight"] for c in CITIES}
TARGET = "tn_energy_gwh"


def state_weather(long: pd.DataFrame) -> pd.DataFrame:
    df = long.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["w"] = df["city"].map(WEIGHTS)
    if df["w"].isna().any():
        raise ValueError(f"unweighted cities: {df[df.w.isna()].city.unique().tolist()}")

    vals = [c for c in df.columns if c not in ("date", "city", "w", "issued_on")]

    def wavg(g):
        # renormalise per column, not once for the group: if a city reports
        # t_max but not rh_mean, dividing by the full weight sum would drag
        # that day's humidity toward zero instead of ignoring the gap
        out = {}
        for c in vals:
            v = g[c]
            w = g["w"].where(v.notna())
            total = w.sum()
            out[c] = float((v * w).sum() / total) if total > 0 else np.nan
        return pd.Series(out)

    out = df.groupby("date")[["w"] + vals].apply(wavg).reset_index()

    out["cdd"] = (out["t_mean"] - CDD_BASE).clip(lower=0)
    out["cdd_max"] = (out["t_max"] - CDD_BASE).clip(lower=0)
    out["cdd_apparent"] = (out["app_t_mean"] - CDD_BASE).clip(lower=0)
    out["thi"] = out["t_mean"] - (0.55 - 0.0055 * out["rh_mean"]) * (out["t_mean"] - 14.5)
    return out


def calendar_features(dates: pd.Series) -> pd.DataFrame:
    import holidays
    tn = holidays.India(subdiv="TN", years=sorted({d.year for d in dates}))
    hol = set(tn.keys())

    cal = pd.DataFrame({"date": dates})
    d = cal["date"]
    cal["dow"] = d.dt.dayofweek
    cal["is_weekend"] = (cal["dow"] >= 5).astype(int)
    cal["is_holiday"] = d.dt.date.map(lambda x: int(x in hol))
    cal["day_before_holiday"] = (d + pd.Timedelta(days=1)).dt.date.map(lambda x: int(x in hol))
    cal["day_after_holiday"] = (d - pd.Timedelta(days=1)).dt.date.map(lambda x: int(x in hol))
    cal["holiday_name"] = d.dt.date.map(lambda x: tn.get(x, ""))
    cal["pongal_window"] = ((d.dt.month == 1) & d.dt.day.between(13, 17)).astype(int)

    doy = d.dt.dayofyear
    for k in (1, 2):
        cal[f"sin{k}"] = np.sin(2 * np.pi * k * doy / 365.25)
        cal[f"cos{k}"] = np.cos(2 * np.pi * k * doy / 365.25)

    cal["trend"] = (d - d.min()).dt.days
    return cal


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Attach backward-looking features on a calendar-continuous index.

    Shifting positionally would be wrong: the demand series is missing 13 days
    since 2015, and after each gap a positional lag_1 quietly means "two days
    ago" and lag_364 slides off same-day-last-year. Reindexing onto a complete
    date range first makes every lag mean the calendar day it claims; the price
    is a NaN beside each gap, which is the truthful answer.
    """
    span = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    full = df.set_index("date").reindex(span)

    y = full[TARGET]
    for lag in (1, 2, 3, 7, 14, 364):
        full[f"lag_{lag}"] = y.shift(lag)
    for win in (7, 30):
        full[f"roll_mean_{win}"] = y.shift(1).rolling(win).mean()
        full[f"roll_std_{win}"] = y.shift(1).rolling(win).std()

    full["lag_1_diff"] = full["lag_1"] - full["lag_2"]
    full["lag_1_vs_week"] = full["lag_1"] - full["lag_7"]

    for col in ("cdd", "cdd_max", "thi"):
        full[f"{col}_lag1"] = full[col].shift(1)

    # regional context is only known with a lag, same as the target
    for col in ("sr_max_demand_mw", "sr_wind_gwh", "sr_solar_gwh", "sr_peak_shortage_mw"):
        if col in full.columns:
            full[f"{col}_lag1"] = full[col].shift(1)

    # drop the filler rows again - we only model days we actually observed
    return (full.loc[pd.DatetimeIndex(df["date"])]
                .rename_axis("date").reset_index())


def build() -> pd.DataFrame:
    demand = pd.read_csv(RAW_DEMAND, parse_dates=["date"])
    weather = state_weather(pd.read_csv(RAW_WEATHER))

    df = demand.merge(weather, on="date", how="inner").sort_values("date")
    df = df.merge(calendar_features(df["date"]), on="date", how="left")
    df = add_lags(df).reset_index(drop=True)

    # drop same-day regional columns: they are not published before the target
    # day, so keeping them would leak
    leaky = [c for c in ("sr_max_demand_mw", "sr_demand_met_mw", "sr_peak_shortage_mw",
                         "sr_energy_gwh", "sr_wind_gwh", "sr_solar_gwh",
                         "india_energy_gwh", "kerala_energy_gwh",
                         "karnataka_energy_gwh", "ap_energy_gwh") if c in df.columns]
    df = df.drop(columns=leaky)

    df.to_csv(MASTER, index=False)
    return df


def report(df: pd.DataFrame):
    print(f"rows          {len(df):,}")
    print(f"date range    {df['date'].min():%Y-%m-%d} -> {df['date'].max():%Y-%m-%d}")
    print(f"features      {len(df.columns) - 2}")

    span = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    gaps = span.difference(pd.DatetimeIndex(df["date"]))
    print(f"missing days  {len(gaps)}")

    y = df[TARGET]
    print(f"target GWh    min {y.min():,.0f}  mean {y.mean():,.0f}  max {y.max():,.0f}")

    keys = [c for c in ("cdd", "cdd_max", "thi", "t_max", "lag_1", "lag_7",
                        "roll_mean_7", "trend") if c in df.columns]
    print("\ncorrelation with target:")
    print(df[[TARGET] + keys].corr()[TARGET].drop(TARGET).round(3).to_string())

    print("\nmean target by day type:")
    print(f"  working day  {y[(df.is_weekend == 0) & (df.is_holiday == 0)].mean():,.1f}")
    print(f"  weekend      {y[df.is_weekend == 1].mean():,.1f}")
    print(f"  holiday      {y[df.is_holiday == 1].mean():,.1f}")
    print(f"  Pongal week  {y[df.pongal_window == 1].mean():,.1f}")

    nulls = df.isna().sum()
    nulls = nulls[nulls > 0]
    if len(nulls):
        print("\nnulls (should be head-only, matching lag windows):")
        print(nulls.to_string())


if __name__ == "__main__":
    report(build())
    print(f"\nwrote {MASTER}")

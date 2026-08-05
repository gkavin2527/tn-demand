"""
Open-Meteo weather pipeline.

Three modes:
    python fetch_weather.py backfill [--force]   # history -> weather_daily.csv
    python fetch_weather.py update               # recent actuals -> weather_daily.csv
    python fetch_weather.py forecast             # -> weather_forecast_log.csv

The forecast mode matters most. At prediction time you do not have tomorrow's
actual weather, only a forecast. Logging forecasts daily from now on is what
lets you train and evaluate honestly, and no archive can reconstruct it later.
Start this cron on day one.
"""

import random
import sys
import time
from datetime import date, timedelta

import pandas as pd
import requests

from config import (ARCHIVE_URL, CACHE, CITIES, FORECAST_LOG, FORECAST_URL,
                    HOURLY_VARS, RAW_WEATHER, START_DATE, TZ)

TIMEOUT = 120
BACKOFF = [5, 15, 45, 120]      # seconds between attempts, jittered


def _get(url, params, ctx=""):
    """GET with retries on the failures that actually happen to this pipeline.

    A read timeout on one of 60 backfill requests used to kill the whole run,
    so transient network faults and 5xx are retried alongside the documented
    429. A non-429 4xx is a bad request and will not fix itself, so it raises
    immediately rather than burning three minutes of backoff.
    """
    where = f" [{ctx}]" if ctx else ""
    attempts = len(BACKOFF) + 1        # 5 tries, 4 waits between them
    last = None

    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)

            if r.status_code == 200:
                payload = r.json()
                # a throttle/error body is valid JSON with no 'hourly' key;
                # without this check it becomes a KeyError deep in the caller
                if "hourly" in payload:
                    return payload
                last = f"no 'hourly' in response: {str(payload)[:200]}"
            elif r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            else:
                # 4xx other than 429 is a bad request; it will not fix itself
                r.raise_for_status()

        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last = f"{type(e).__name__}: {e}"

        if attempt <= len(BACKOFF):
            sleep = BACKOFF[attempt - 1] * random.uniform(0.8, 1.2)
            print(f"      retry {attempt}/{len(BACKOFF)} after {last} "
                  f"- sleeping {sleep:.0f}s")
            time.sleep(sleep)

    raise RuntimeError(f"open-meteo request failed{where} "
                       f"after {attempts} attempts: {last}")


def _hourly_to_daily(payload, city):
    """Collapse hourly readings into daily aggregates for one city."""
    h = pd.DataFrame(payload["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    h["date"] = h["time"].dt.date

    g = h.groupby("date")
    out = pd.DataFrame({
        "t_mean": g["temperature_2m"].mean(),
        "t_max": g["temperature_2m"].max(),
        "t_min": g["temperature_2m"].min(),
        "rh_mean": g["relative_humidity_2m"].mean(),
        "app_t_mean": g["apparent_temperature"].mean(),
        "app_t_max": g["apparent_temperature"].max(),
        # hours spent above a comfort threshold - captures duration of heat,
        # which daily mean temperature throws away
        "hours_above_32": g["temperature_2m"].apply(lambda s: (s > 32).sum()),
    }).reset_index()

    out["city"] = city
    return out


def _chunk_is_complete(path, y_end):
    """True if a cached chunk already covers its whole year.

    Only the current year can be incomplete: it was cached mid-year and the
    days since are still missing. Past years are closed and always complete.
    """
    try:
        cached = pd.read_csv(path)
        return not cached.empty and str(cached["date"].max()) >= y_end
    except Exception:
        return False        # unreadable/truncated cache - just re-fetch it


def backfill(start=START_DATE, end=None, force=False):
    """Pull archive weather city-by-city, year-by-year, into data/_cache/.

    Each chunk is written the instant it succeeds. An earlier version held all
    60 in memory and wrote once at the end, so a timeout on request 20 threw
    away nineteen good ones. Re-running now costs only the chunks still missing.
    """
    end = end or (date.today() - timedelta(days=1)).isoformat()
    years = range(int(start[:4]), int(end[:4]) + 1)

    jobs = []
    for c in CITIES:
        for year in years:
            y_start = max(f"{year}-01-01", start)
            y_end = min(f"{year}-12-31", end)
            if y_start <= y_end:
                jobs.append((c, year, y_start, y_end))

    failed = []
    for i, (c, year, y_start, y_end) in enumerate(jobs, start=1):
        tag = f"[{i}/{len(jobs)}] {c['name']} {year}"
        path = CACHE / f"{c['name']}_{year}.csv"

        if not force and path.exists() and _chunk_is_complete(path, y_end):
            print(f"{tag} ... skipping (cached)")
            continue

        print(f"{tag} ... ", end="", flush=True)
        try:
            payload = _get(ARCHIVE_URL, {
                "latitude": c["lat"], "longitude": c["lon"],
                "start_date": y_start, "end_date": y_end,
                "hourly": ",".join(HOURLY_VARS),
                "timezone": TZ,
            }, ctx=f"{c['name']} {year}")
        except (RuntimeError, requests.exceptions.RequestException) as e:
            # one bad city-year must not cost us the other 59
            print(f"FAILED\n      {e}")
            failed.append(f"{c['name']} {year}")
            continue

        chunk = _hourly_to_daily(payload, c["name"])
        chunk.to_csv(path, index=False)
        print(f"ok ({len(chunk)} days)")
        time.sleep(1)

    return _consolidate(failed)


def _consolidate(failed=()):
    """Concat every cached chunk into weather_daily.csv."""
    files = sorted(CACHE.glob("*.csv"))
    if not files:
        sys.exit("no cached chunks - nothing to consolidate")

    long = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    long = (long.drop_duplicates(subset=["date", "city"], keep="last")
                .sort_values(["date", "city"])
                .reset_index(drop=True))
    long.to_csv(RAW_WEATHER, index=False)
    print(f"\nwrote {len(long):,} city-days from {len(files)} chunks "
          f"-> {RAW_WEATHER}")
    print(f"  range {long['date'].min()} -> {long['date'].max()}")

    if failed:
        # partial data beats no data, but the run is not a success
        print(f"\n{len(failed)} chunk(s) still missing after retries:")
        for f in failed:
            print(f"  {f}")
        print("re-run 'python fetch_weather.py backfill' to retry just these")
        sys.exit(1)
    return long


def update(past_days=7):
    """Append recent ACTUAL weather to weather_daily.csv.

    Uses the forecast endpoint with past_days rather than the archive, because
    the archive is a reanalysis product and runs several days behind. The
    forecast endpoint's past_days window carries observed values and is current
    to yesterday, which is what we need to stay in step with the demand data.
    """
    if not RAW_WEATHER.exists():
        sys.exit("no weather_daily.csv yet - run 'backfill' first")

    today = date.today()
    frames = []

    for c in CITIES:
        payload = _get(FORECAST_URL, {
            "latitude": c["lat"], "longitude": c["lon"],
            "hourly": ",".join(HOURLY_VARS),
            "past_days": past_days, "forecast_days": 1,
            "timezone": TZ,
        })
        df = _hourly_to_daily(payload, c["name"])
        # keep only complete past days; today is partial, tomorrow is a forecast
        df = df[pd.to_datetime(df["date"]).dt.date < today]
        frames.append(df)
        time.sleep(1)

    new = pd.concat(frames, ignore_index=True)
    old = pd.read_csv(RAW_WEATHER)

    merged = pd.concat([old, new], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"]).dt.date
    before = len(old)
    merged = (merged.drop_duplicates(subset=["date", "city"], keep="last")
                    .sort_values(["date", "city"]))
    merged.to_csv(RAW_WEATHER, index=False)

    print(f"weather updated: {before:,} -> {len(merged):,} city-days "
          f"(latest {merged['date'].max()})")


def log_forecast():
    """Pull the next 3 days of forecast for each city and append with a stamp
    of when it was issued. issued_on is what makes this leak-free later."""
    issued_on = date.today().isoformat()
    frames = []

    for c in CITIES:
        payload = _get(FORECAST_URL, {
            "latitude": c["lat"], "longitude": c["lon"],
            "hourly": ",".join(HOURLY_VARS),
            "forecast_days": 3,
            "timezone": TZ,
        })
        df = _hourly_to_daily(payload, c["name"])
        df["issued_on"] = issued_on
        frames.append(df)

    new = pd.concat(frames, ignore_index=True)

    if FORECAST_LOG.exists():
        old = pd.read_csv(FORECAST_LOG)
        new = pd.concat([old, new], ignore_index=True)

    # normalise before deduping: rows read back from CSV carry date strings
    # while fresh rows carry datetime.date objects, and the two never compare
    # equal - so re-running on the same day used to append a second copy
    # instead of replacing the first
    new["date"] = pd.to_datetime(new["date"]).dt.date
    new["issued_on"] = pd.to_datetime(new["issued_on"]).dt.date
    new = (new.drop_duplicates(subset=["issued_on", "city", "date"], keep="last")
              .sort_values(["issued_on", "city", "date"])
              .reset_index(drop=True))

    new.to_csv(FORECAST_LOG, index=False)
    print(f"logged forecast issued {issued_on} -> {FORECAST_LOG} ({len(new):,} rows)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    if mode == "backfill":
        backfill(force="--force" in sys.argv)
    elif mode == "update":
        update()
    elif mode == "forecast":
        log_forecast()
    else:
        sys.exit("usage: fetch_weather.py [backfill|update|forecast]")

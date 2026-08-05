# Tamil Nadu Daily Electricity Demand Dataset

A daily-updating dataset of Tamil Nadu's electricity consumption, joined with
weather and calendar features. Built for next-day demand forecasting.

Updates automatically every morning at 07:00 IST via GitHub Actions.

---

## The dataset

**`data/master.csv`** — one row per day, ready to model.

| | |
|---|---|
| Rows | ~4,200 |
| Coverage | 2015-01-01 → yesterday |
| Columns | 44 |
| Target | `tn_energy_gwh` |
| Freshness | ~1–2 days behind real time |
| Missing days | 13 (upstream reports unavailable) |

```python
import pandas as pd
df = pd.read_csv("data/master.csv", parse_dates=["date"])
```

---

## Columns

### Target

| Column | Description |
|---|---|
| `tn_energy_gwh` | Tamil Nadu daily energy met, in GWh |

Note: these reports publish **peak demand in MW only at regional level**.
Individual states expose energy met alone, so the target is daily energy
rather than daily peak.

### Weather

Five cities (Chennai, Coimbatore, Madurai, Trichy, Salem) collapsed into one
state series using load-share weights, so the interior is represented rather
than coastal Chennai alone.

| Column | Description |
|---|---|
| `t_mean`, `t_max`, `t_min` | Temperature, °C |
| `rh_mean` | Relative humidity, % |
| `app_t_mean`, `app_t_max` | Apparent ("feels like") temperature |
| `hours_above_32` | Hours above 32 °C — duration of heat, which a daily mean discards |
| `cdd` | Cooling degree days, `max(0, t_mean − 25)` |
| `cdd_max` | Same, on daily max temperature |
| `cdd_apparent` | Same, on apparent temperature |
| `thi` | Temperature-humidity index |

Cooling degree days matter because the demand–temperature relationship is a
hockey stick with a kink near comfort temperature. CDD straightens it out.

### Calendar

| Column | Description |
|---|---|
| `dow`, `is_weekend` | Day of week (0 = Monday), weekend flag |
| `is_holiday`, `holiday_name` | Tamil Nadu holidays, including Pongal, Mattu Pongal, Uzhavar Thirunal and Puthandu |
| `day_before_holiday`, `day_after_holiday` | Shoulder days behave unlike both holidays and normal days |
| `pongal_window` | 13–17 January — a multi-day industrial shutdown, not a single holiday |
| `sin1`, `cos1`, `sin2`, `cos2` | Fourier terms for smooth annual seasonality |
| `trend` | Days since series start, capturing demand growth |

### Lagged features

All backward-looking and **date-aware**: computed on a continuous date index so
`lag_1` always means yesterday, even across the 13 gaps. Positional shifting
would silently mislabel these.

| Column | Description |
|---|---|
| `lag_1` … `lag_364` | Target at 1, 2, 3, 7, 14, 364 days back |
| `roll_mean_7`, `roll_mean_30` | Rolling means, shifted before rolling |
| `roll_std_7`, `roll_std_30` | Rolling standard deviations |
| `lag_1_diff` | `lag_1 − lag_2` |
| `lag_1_vs_week` | `lag_1 − lag_7` |
| `cdd_lag1`, `cdd_max_lag1`, `thi_lag1` | Yesterday's heat — buildings have thermal mass, so a second hot day draws more |
| `sr_*_lag1` | Southern Region context, lagged one day |

---

## No leakage

Every feature is computable **the evening before** the target day.

Same-day regional and neighbouring-state columns (`sr_*`, `india_*`,
`kerala_*`, `karnataka_*`, `ap_*`) are dropped during the build, because they
aren't published before the target day. Only their `_lag1` versions survive.

If you model this, use walk-forward validation. Never `train_test_split` with
shuffling — it destroys the temporal ordering and produces meaningless scores.

---

## Sources

**Demand** — Grid Controller of India (Grid-India, formerly POSOCO), Daily
Power Supply Position Reports, tables A and C. Accessed via Robbie Andrew's
parsed republication: https://robbieandrew.github.io/india/

**Weather** — Open-Meteo historical reanalysis and forecast APIs.

**Holidays** — Python `holidays` package, `India(subdiv="TN")`.

### Citation

> Andrew, R. *Indian Energy and Emissions Data*.
> https://robbieandrew.github.io/india/
> Underlying source: Grid Controller of India, Daily Power Supply Position
> Reports.

---

## Rebuilding it

```bash
pip install -r requirements.txt

python fetch_demand.py            # demand data
python fetch_weather.py backfill  # weather history, ~30 min, one time
python build_dataset.py           # merge into master.csv
```

Daily refresh:

```bash
python fetch_demand.py
python fetch_weather.py update
python build_dataset.py
```

### Files

| File | Purpose |
|---|---|
| `config.py` | Cities, weights, column mapping, paths |
| `fetch_demand.py` | Downloads and slices the demand data |
| `fetch_weather.py` | Weather history, daily updates, forecast log |
| `build_dataset.py` | Merge and feature engineering |
| `health_check.py` | Validation, exits non-zero on failure |
| `.github/workflows/daily.yml` | Daily automation |

---

## Automation

Runs at 07:00 IST daily. Fetches new data, rebuilds `master.csv`, runs the
health check, commits. Pull with `git pull`.

`health_check.py` fails the run if the demand data falls more than 5 days
behind, the weather more than 3, `master.csv` drops below 3,000 rows, the
target goes constant or leaves a plausible range, or the weather–demand
correlation collapses below 0.2 — the signature of a misaligned join.

Run it locally any time:

```bash
python health_check.py
```

GitHub disables scheduled workflows after 60 days of repository inactivity. If
updates stop, check that first.

---

## Known quirks

**13 missing days.** Upstream reports were unavailable or unreadable. Not
imputed — the gaps are left visible, which is the honest representation.

**November–December 2015 is anomalously low** (~224 GWh against a ~314 series
mean). This is the Chennai floods, a genuine event rather than bad data.

**April–May 2020 is depressed** by the COVID-19 lockdown (~243 GWh mean in
April 2020). Also real. Both periods are regime breaks worth handling
explicitly if you model across them.

**`sr_*_lag1` columns are sparse before 2022.** Drop them if you want complete
cases reaching further back.

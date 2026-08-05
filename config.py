"""Configuration for the Tamil Nadu daily electricity demand forecasting dataset."""

from pathlib import Path

DATA = Path(__file__).parent / "data"
DATA.mkdir(parents=True, exist_ok=True)

# Per-city-year weather chunks, written the moment each API call succeeds so a
# 60-request backfill can resume instead of starting over. Disposable: the
# chunks are re-derivable from the archive API, so this dir is gitignored.
CACHE = DATA / "_cache"
CACHE.mkdir(parents=True, exist_ok=True)

RAW_POSOCO = DATA / "POSOCO_data.csv"      # cached full download
RAW_DEMAND = DATA / "demand_daily.csv"     # TN + Southern Region slice
RAW_WEATHER = DATA / "weather_daily.csv"
FORECAST_LOG = DATA / "weather_forecast_log.csv"
MASTER = DATA / "master.csv"

# Grid India (POSOCO) daily reports, parsed and republished as a single CSV by
# Robbie Andrew (CICERO). Updated daily. Cite both him and Grid India.
POSOCO_URL = "https://robbieandrew.github.io/india/data/POSOCO_data.csv"

# Columns we keep. States expose only EnergyMet; peak demand in MW exists at
# regional level only, so Southern Region is our proxy for peak behaviour.
TARGET_COL = "Tamil Nadu: EnergyMet"

KEEP = {
    "Tamil Nadu: EnergyMet":  "tn_energy_gwh",       # <- the target
    "Kerala: EnergyMet":      "kerala_energy_gwh",
    "Karnataka: EnergyMet":   "karnataka_energy_gwh",
    "Andhra Pradesh: EnergyMet": "ap_energy_gwh",
    "SR: MaximumDemand":      "sr_max_demand_mw",
    "SR: DemandMet":          "sr_demand_met_mw",
    "SR: PeakShortage":       "sr_peak_shortage_mw",
    "SR: EnergyMet":          "sr_energy_gwh",
    "SR: WindGen":            "sr_wind_gwh",
    "SR: SolarGen":           "sr_solar_gwh",
    "India: EnergyMet":       "india_energy_gwh",
}

START_DATE = "2015-01-01"   # weather backfill start; demand goes back to 2013
TZ = "Asia/Kolkata"

CITIES = [
    {"name": "chennai",    "lat": 13.0827, "lon": 80.2707, "weight": 0.36},
    {"name": "coimbatore", "lat": 11.0168, "lon": 76.9558, "weight": 0.22},
    {"name": "madurai",    "lat":  9.9252, "lon": 78.1198, "weight": 0.16},
    {"name": "trichy",     "lat": 10.7905, "lon": 78.7047, "weight": 0.14},
    {"name": "salem",      "lat": 11.6643, "lon": 78.1460, "weight": 0.12},
]

CDD_BASE = 25.0
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "apparent_temperature"]
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

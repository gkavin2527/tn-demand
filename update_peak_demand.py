"""
Update peak_demand.csv using Grid-India Daily PSP reports.

Usage:
    python update_peak_demand.py 2026-09-19

The script:
1. Fetches peak demand for the requested date.
2. Reads existing peak_demand.csv.
3. Inserts the date if it is new.
4. Updates the date if it already exists.
5. Flags suspicious jumps in peak demand.
6. Never creates duplicate dates.
"""

import os
import sys
from datetime import date

import pandas as pd

from fetch_peak_demand import fetch_peak_demand_for_date


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

CSV_PATH = "data/peak_demand.csv"

# Example: 0.40 = 40% change from previous observation
JUMP_THRESHOLD = 0.40


# ---------------------------------------------------------
# LOAD EXISTING DATA
# ---------------------------------------------------------

def load_peak_data():
    if not os.path.exists(CSV_PATH):
        print("peak_demand.csv does not exist.")
        print("Creating a new file.")

        columns = [
            "date",
            "tn_peak_demand_mw",
            "tn_shortage_at_peak_mw",
            "tn_energy_met_mu",
            "report_published",
            "source",
            "source_file",
            "source_url",
            "retrieved_at",
        ]

        return pd.DataFrame(columns=columns)

    df = pd.read_csv(CSV_PATH, parse_dates=["date"])

    return df


# ---------------------------------------------------------
# CHECK FOR SUSPICIOUS JUMP
# ---------------------------------------------------------

def check_suspicious_jump(df, new_row):
    if new_row.get("status") != "ok":
        return

    new_value = new_row.get("tn_peak_demand_mw")

    if new_value is None:
        return

    if len(df) == 0:
        return

    # Sort by date so the previous observation is correct
    df = df.sort_values("date")

    previous = df.iloc[-1]

    previous_value = previous.get("tn_peak_demand_mw")

    if pd.isna(previous_value) or previous_value == 0:
        return

    percentage_change = (
        (new_value - previous_value)
        / previous_value
    )

    if abs(percentage_change) > JUMP_THRESHOLD:
        print()
        print("⚠️ WARNING: Suspicious peak-demand jump detected!")

        print(
            f"Previous date : "
            f"{previous['date'].date()}"
        )

        print(
            f"Previous peak : "
            f"{previous_value:.2f} MW"
        )

        print(
            f"New date      : "
            f"{new_row['date']}"
        )

        print(
            f"New peak      : "
            f"{new_value:.2f} MW"
        )

        print(
            f"Change        : "
            f"{percentage_change * 100:.2f}%"
        )

        print(
            "The value will NOT be rejected. "
            "It will still be saved."
        )
        print()


# ---------------------------------------------------------
# UPSERT
# ---------------------------------------------------------

def upsert_peak_data(df, new_row):

    if new_row.get("status") != "ok":
        print(
            f"❌ Could not update data for "
            f"{new_row.get('date')}"
        )

        print(
            f"Status: "
            f"{new_row.get('status')}"
        )

        if new_row.get("errors"):
            print(
                f"Errors: "
                f"{new_row.get('errors')}"
            )

        return df

    target_date = pd.to_datetime(
        new_row["date"]
    )

    # Check suspicious jump before modifying dataframe
    check_suspicious_jump(df, new_row)

    # Remove status/errors before saving
    row_to_save = {
        key: value
        for key, value in new_row.items()
        if key not in ["status", "errors"]
    }

    row_to_save["date"] = target_date

    # -----------------------------------------------------
    # CASE 1: Date already exists
    # -----------------------------------------------------

    existing_mask = df["date"] == target_date

    if existing_mask.any():

        print(
            f"🔄 Date already exists: "
            f"{target_date.date()}"
        )

        print("Updating existing row.")

        for column, value in row_to_save.items():

            if column not in df.columns:
                df[column] = pd.NA

            df.loc[existing_mask, column] = value

    # -----------------------------------------------------
    # CASE 2: New date
    # -----------------------------------------------------

    else:

        print(
            f"➕ New date: "
            f"{target_date.date()}"
        )

        new_df = pd.DataFrame([row_to_save])

        df = pd.concat(
            [df, new_df],
            ignore_index=True
        )

    # -----------------------------------------------------
    # FINAL CLEANUP
    # -----------------------------------------------------

    df["date"] = pd.to_datetime(df["date"])

    # IMPORTANT:
    # Never allow duplicate dates.
    df = (
        df
        .sort_values("date")
        .drop_duplicates(
            subset=["date"],
            keep="last"
        )
        .reset_index(drop=True)
    )

    return df


# ---------------------------------------------------------
# SAVE
# ---------------------------------------------------------

def save_peak_data(df):

    os.makedirs(
        os.path.dirname(CSV_PATH),
        exist_ok=True
    )

    df = df.sort_values("date")

    df.to_csv(
        CSV_PATH,
        index=False
    )

    print()
    print("✅ peak_demand.csv saved successfully.")
    print(f"Rows: {len(df)}")
    print(f"File: {CSV_PATH}")


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":

    # If a date is supplied, use it.
    # Otherwise, automatically use yesterday.
    if len(sys.argv) == 2:
        target_date = date.fromisoformat(sys.argv[1])
    elif len(sys.argv) == 1:
        target_date = date.today() - pd.Timedelta(days=1)
    else:
        print(
            "Usage:\n"
            "  python update_peak_demand.py\n"
            "  python update_peak_demand.py YYYY-MM-DD"
        )
        sys.exit(1)

    print(f"\nFetching peak demand for {target_date}...")

    cache = {}

    result = fetch_peak_demand_for_date(
        target_date,
        cache
    )

    print("\nExtraction result:")

    for key, value in result.items():
        print(f"  {key:<24} {value}")

    df = load_peak_data()

    df = upsert_peak_data(
        df,
        result
    )

    save_peak_data(df)
    if result.get("status") != "ok":
        sys.exit(1)
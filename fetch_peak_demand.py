"""
Standalone TEST script - locates a Grid-India / POSOCO Daily PSP report,
downloads it, and extracts Tamil Nadu peak demand.

IMPORTANT:
The filename/title date is NOT assumed to be the observation date.

The script:
1. Searches report titles around the requested date.
2. Downloads candidate reports.
3. Reads the actual "Date of Reporting" inside the report.
4. Assumes the report contains data for the previous day.
5. Accepts the report ONLY if:
       report_date - 1 day == requested target date

This handles historical and newer report-date conventions safely.

Usage:
    python fetch_peak_demand.py 2026-09-17
    python fetch_peak_demand.py 2015-04-05
"""

import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from extract_peak_demand import extract_from_xls, extract_from_pdf


# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

API_URL = "https://webapi.grid-india.in/api/v1/file"
CDN_BASE = "https://webcdn.grid-india.in/"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://grid-india.in",
    "Referer": "https://grid-india.in/en/reports/daily-psp-report",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------
# FISCAL YEAR
# ---------------------------------------------------------

def fiscal_year_for(d: date) -> str:
    """
    Indian financial year.

    Example:
        2015-04-05 -> 2015-16
        2016-01-10 -> 2015-16
        2026-09-17 -> 2026-27
    """
    start_year = d.year if d.month >= 4 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[2:]}"


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

def list_reports(fiscal_year: str, month: str = "00") -> list:
    """
    Get Daily PSP report metadata from Grid-India API.
    """

    payload = {
        "_source": "GRDW",
        "_type": "DAILY_PSP_REPORT",
        "_fileDate": fiscal_year,
        "_month": month,
    }

    resp = requests.post(
        API_URL,
        json=payload,
        headers=HEADERS,
        timeout=30,
        verify=False,   # temporary local SSL workaround
    )

    resp.raise_for_status()

    data = resp.json()

    if data.get("flagType") != 0:
        raise ValueError(
            f"API error: "
            f"flagType={data.get('flagType')} "
            f"msg={data.get('retMessage')!r}"
        )

    return data.get("retData", [])


# ---------------------------------------------------------
# FIND CANDIDATES
# ---------------------------------------------------------

def find_report_candidates(target_date: date, cache: dict) -> list:
    """
    Find possible PSP reports around target_date.

    We intentionally do NOT assume that the title date is:
        target_date
    or:
        target_date + 1 day

    Instead, we search a small window:

        target - 2 days
        target - 1 day
        target
        target + 1 day
        target + 2 days

    The actual report date inside the file will decide which
    candidate is correct.

    XLS is preferred over PDF when both exist.
    """

    candidate_dates = [
        target_date + timedelta(days=offset)
        for offset in [-2, -1, 0, 1, 2]
    ]

    candidates = []
    seen_files = set()

    for candidate_date in candidate_dates:

        fy = fiscal_year_for(candidate_date)

        if fy not in cache:
            cache[fy] = list_reports(fy)

        records = cache[fy]

        prefix = candidate_date.strftime("%d.%m.%y_NLDC_PSP")

        matches = [
            r
            for r in records
            if str(r.get("Title_", "")).startswith(prefix)
        ]

        for record in matches:

            file_path = record.get("FilePath")

            if not file_path:
                continue

            if file_path in seen_files:
                continue

            seen_files.add(file_path)
            candidates.append(record)

    # -----------------------------------------------------
    # Prefer XLS/XLSX before PDF
    # -----------------------------------------------------

    def file_priority(record):
        mime = str(record.get("MimeType", "")).lower()
        path = str(record.get("FilePath", "")).lower()

        if (
            "excel" in mime
            or path.endswith(".xls")
            or path.endswith(".xlsx")
        ):
            return 0

        if "pdf" in mime or path.endswith(".pdf"):
            return 1

        return 2

    candidates.sort(key=file_priority)

    return candidates


# ---------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------

def download_report(record: dict, dest_dir: str = "downloads") -> str:
    """
    Download a Grid-India report locally.
    """

    os.makedirs(dest_dir, exist_ok=True)

    file_path = record["FilePath"]

    url = CDN_BASE + file_path

    local_name = file_path.split("/")[-1]

    local_path = os.path.join(dest_dir, local_name)

    if os.path.exists(local_path):
        return local_path

    resp = requests.get(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"]
        },
        timeout=30,
        verify=False,   # temporary local SSL workaround
    )

    resp.raise_for_status()

    with open(local_path, "wb") as f:
        f.write(resp.content)

    return local_path


# ---------------------------------------------------------
# EXTRACT REPORT
# ---------------------------------------------------------

def extract_report(local_path: str):
    """
    Extract report date + Tamil Nadu values from XLS/PDF.
    """

    if local_path.lower().endswith((".xls", ".xlsx")):

        return extract_from_xls(local_path)

    if local_path.lower().endswith(".pdf"):

        return extract_from_pdf(local_path)

    raise ValueError(
        f"Unsupported downloaded file type: {local_path}"
    )


# ---------------------------------------------------------
# FETCH TARGET DATE
# ---------------------------------------------------------

def fetch_peak_demand_for_date(
    target_date: date,
    cache: dict
) -> dict:

    """
    Find the correct PSP report for target_date.

    A candidate is accepted ONLY when:

        extracted report date - 1 day
            ==
        target_date

    This prevents filename/date convention differences from
    producing the wrong observation date.
    """

    candidates = find_report_candidates(
        target_date,
        cache
    )

    if not candidates:

        return {
            "date": target_date.isoformat(),
            "status": "missing_report",
        }

    errors = []

    # -----------------------------------------------------
    # Try candidates one by one
    # -----------------------------------------------------

    for record in candidates:

        source_file = record.get("FilePath", "")

        try:

            local_path = download_report(record)

        except Exception as e:

            errors.append(
                f"download failed for {source_file}: {e}"
            )

            continue

        # -------------------------------------------------
        # Parse
        # -------------------------------------------------

        try:

            report_date, vals = extract_report(
                local_path
            )

        except Exception as e:

            errors.append(
                f"parse failed for {source_file}: {e}"
            )

            continue

        # -------------------------------------------------
        # Validate extracted values
        # -------------------------------------------------

        invalid_field = None

        for field, value in vals.items():

            if value is None:

                invalid_field = field
                break

        if invalid_field is not None:

            errors.append(
                f"missing/non-numeric {invalid_field} "
                f"in {source_file}"
            )

            continue

        # -------------------------------------------------
        # CRITICAL DATE VALIDATION
        # -------------------------------------------------

        extracted_observation_date = (
            report_date - pd.Timedelta(days=1)
        ).date()

        if extracted_observation_date != target_date:

            errors.append(
                f"date mismatch for {source_file}: "
                f"report_date={report_date.date()}, "
                f"observation_date="
                f"{extracted_observation_date}"
            )

            continue

        # -------------------------------------------------
        # SUCCESS
        # -------------------------------------------------

        return {
            "date": target_date.isoformat(),
            "status": "ok",

            **vals,

            "report_published": report_date.date().isoformat(),

            "source": "grid_india_psp_report",

            "source_file": source_file,

            "source_url": CDN_BASE + source_file,

            "retrieved_at": datetime.utcnow().isoformat(),
        }

    # -----------------------------------------------------
    # Nothing matched the requested observation date
    # -----------------------------------------------------

    return {
        "date": target_date.isoformat(),
        "status": "no_valid_report",

        "errors": " | ".join(errors),
    }


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

if __name__ == "__main__":

    if len(sys.argv) != 2:

        print(
            "Usage:\n"
            "  python fetch_peak_demand.py YYYY-MM-DD"
        )

        sys.exit(1)

    target = date.fromisoformat(
        sys.argv[1]
    )

    cache = {}

    result = fetch_peak_demand_for_date(
        target,
        cache
    )

    print()

    for key, value in result.items():

        print(
            f"  {key:<24} {value}"
        )
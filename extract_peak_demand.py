"""
Standalone TEST script - handles both .xls and .pdf PSP reports.

Run:
    python extract_peak_demand.py <path_to_file>
"""
import re
import sys
import pandas as pd


def _clean_num(val):
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def extract_from_xls(path: str) -> dict:
    df = pd.read_excel(path, sheet_name="MOP_E", header=None)

    report_date = None
    for r in range(len(df)):
        row = df.iloc[r]
        for c, val in row.items():
            if isinstance(val, str) and "Date of Reporting" in val:
                tail = row.iloc[c + 1:].dropna()
                if len(tail):
                    report_date = pd.to_datetime(tail.iloc[-1])
        if report_date is not None:
            break
    if report_date is None:
        raise ValueError("xls: could not find 'Date of Reporting'")

    tn_row = None
    for r in range(len(df)):
        val = df.iat[r, 1] if df.shape[1] > 1 else None
        if isinstance(val, str) and val.strip() == "Tamil Nadu":
            tn_row = r
            break
    if tn_row is None:
        raise ValueError("xls: could not find 'Tamil Nadu' row")

    return report_date, {
        "tn_peak_demand_mw": _clean_num(df.iat[tn_row, 2]),
        "tn_shortage_at_peak_mw": _clean_num(df.iat[tn_row, 3]),
        "tn_energy_met_mu": _clean_num(df.iat[tn_row, 4]),
    }



def extract_from_pdf(path: str) -> dict:
    import pdfplumber

    report_date = None
    tn_row = None

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            # ---------------------------------------------------------
            # 1. Find report date
            # ---------------------------------------------------------
            if report_date is None:
                # Newer PDF format
                m = re.search(
                    r"Date of Reporting[:\s]+(\d{1,2}[-\s][A-Za-z]{3}[-\s]\d{2,4})",
                    text
                )

                if m:
                    report_date = pd.to_datetime(
                        m.group(1).replace(" ", "-")
                    )

                # Older PSP PDF format may contain:
                # "Date of Reporting 5-Apr-15"
                if report_date is None:
                    m = re.search(
                        r"Date of Reporting\s+(\d{1,2}-[A-Za-z]{3}-\d{2,4})",
                        text
                    )

                    if m:
                        report_date = pd.to_datetime(m.group(1))

            # ---------------------------------------------------------
            # 2. Try the existing table-based extraction
            # ---------------------------------------------------------
            if tn_row is None:
                for table in page.extract_tables():
                    if not table or not table[0]:
                        continue

                    header = " ".join(str(c) for c in table[0] if c)

                    if "States" in header and "Max.Demand" in header:
                        for row in table:
                            if (
                                row
                                and len(row) > 4
                                and row[1]
                                and str(row[1]).strip() == "Tamil Nadu"
                            ):
                                tn_row = row
                                break

            # ---------------------------------------------------------
            # 3. Legacy 2015 PDF fallback
            #
            # Example:
            # Tamil Nadu 12065 754 280.2 117.5 1.2 475
            #
            # Columns:
            # Tamil Nadu
            # Max Demand Met (MW)
            # Shortage (MW)
            # Energy Met (MU)
            # Schedule (MU)
            # OD/UD (MU)
            # Max OD (MW)
            # ---------------------------------------------------------
            if tn_row is None:
                for line in text.splitlines():
                    line = line.strip()

                    if line.startswith("Tamil Nadu "):
                        parts = line.split()

                        # Expected:
                        # ["Tamil", "Nadu", peak, shortage, energy, ...]
                        if len(parts) >= 5:
                            peak = _clean_num(parts[2])
                            shortage = _clean_num(parts[3])
                            energy = _clean_num(parts[4])

                            if (
                                peak is not None
                                and shortage is not None
                                and energy is not None
                            ):
                                tn_row = [
                                    None,
                                    "Tamil Nadu",
                                    peak,
                                    shortage,
                                    energy,
                                ]
                                break

            # We can stop once both pieces are found
            if report_date is not None and tn_row is not None:
                break

    if report_date is None:
        raise ValueError("pdf: could not find 'Date of Reporting'")

    if tn_row is None:
        raise ValueError(
            "pdf: could not find 'Tamil Nadu' row in a States table "
            "or legacy text format"
        )

    return report_date, {
        "tn_peak_demand_mw": _clean_num(tn_row[2]),
        "tn_shortage_at_peak_mw": _clean_num(tn_row[3]),
        "tn_energy_met_mu": _clean_num(tn_row[4]),
    }
def extract(path: str) -> dict:
    if path.lower().endswith((".xls", ".xlsx")):
        report_date, vals = extract_from_xls(path)
    elif path.lower().endswith(".pdf"):
        report_date, vals = extract_from_pdf(path)
    else:
        raise ValueError(f"Unrecognised file type: {path}")

    for name, val in vals.items():
        if val is None:
            raise ValueError(f"{name} is not numeric - refusing to guess")

    target_date = report_date - pd.Timedelta(days=1)  # report is "for previous day"
    return {
        "date": target_date.date().isoformat(),
        "report_published": report_date.date().isoformat(),
        **vals,
    }


if __name__ == "__main__":
    result = extract(sys.argv[1])
    for k, v in result.items():
        print(f"  {k:<24} {v}")

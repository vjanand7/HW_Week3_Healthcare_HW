from pathlib import Path

import pandas as pd

from database import get_connection
from service import SCORING_FIELDS, calculate_risk

ID_COLUMNS = ("encounter_id", "patient_nbr")
NUMERIC_COLUMNS = (
    "time_in_hospital", "number_inpatient", "number_emergency", "number_outpatient",
    "num_medications", "num_lab_procedures", "discharge_disposition_id", "admission_type_id",
)
TEXT_COLUMNS = ("age", "readmitted")

UPSERT_SQL = """
    INSERT INTO patient_encounters (
        encounter_id, patient_nbr, age, time_in_hospital,
        number_inpatient, number_emergency, number_outpatient,
        num_medications, num_lab_procedures, discharge_disposition_id,
        admission_type_id, readmitted, risk_score, risk_level
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (encounter_id) DO UPDATE SET
        patient_nbr = EXCLUDED.patient_nbr,
        age = EXCLUDED.age,
        time_in_hospital = EXCLUDED.time_in_hospital,
        number_inpatient = EXCLUDED.number_inpatient,
        number_emergency = EXCLUDED.number_emergency,
        number_outpatient = EXCLUDED.number_outpatient,
        num_medications = EXCLUDED.num_medications,
        num_lab_procedures = EXCLUDED.num_lab_procedures,
        discharge_disposition_id = EXCLUDED.discharge_disposition_id,
        admission_type_id = EXCLUDED.admission_type_id,
        readmitted = EXCLUDED.readmitted,
        risk_score = EXCLUDED.risk_score,
        risk_level = EXCLUDED.risk_level;
"""


def load_data(csv_path: str) -> dict:
    """Load a CSV of encounters, score each row and upsert it (all-or-nothing).

    Rows without an encounter_id or patient_nbr are skipped. If an encounter_id
    repeats in the file the first row wins. Re-loading refreshes existing rows but
    leaves care interventions untouched.
    """
    df = pd.read_csv(csv_path, dtype={column: str for column in ID_COLUMNS})

    missing = [column for column in ID_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")

    for column in ID_COLUMNS:
        df[column] = df[column].str.strip()
    valid = df[list(ID_COLUMNS)].notna().all(axis=1) & (df["encounter_id"] != "") & (df["patient_nbr"] != "")
    skipped = int((~valid).sum())
    df = df[valid].drop_duplicates("encounter_id")

    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = df[column].fillna(0).astype(int)
    for column in TEXT_COLUMNS:
        if column in df.columns:
            df[column] = df[column].fillna("").astype(str)

    rows = []
    for record in df.to_dict("records"):
        # Pass only the scoring fields so the historical outcome cannot influence the score.
        score, level = calculate_risk({field: record.get(field) for field in SCORING_FIELDS})
        rows.append((
            record["encounter_id"], record["patient_nbr"], record.get("age", ""),
            record.get("time_in_hospital"), record.get("number_inpatient"),
            record.get("number_emergency"), record.get("number_outpatient"),
            record.get("num_medications"), record.get("num_lab_procedures"),
            record.get("discharge_disposition_id"), record.get("admission_type_id"),
            record.get("readmitted", ""), score, level,
        ))

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(UPSERT_SQL, rows)

    print(f"Data loaded and scored successfully: {len(rows)} rows, {skipped} skipped.")
    return {"loaded": len(rows), "skipped": skipped}


if __name__ == "__main__":
    load_data(str(Path(__file__).resolve().parent / "data" / "diabetic_data.csv"))

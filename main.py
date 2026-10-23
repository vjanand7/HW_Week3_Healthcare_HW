from typing import Annotated

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import service
from database import DatabaseNotConfigured
from models import InterventionUpdate, RiskLevel

MAX_PAGE_SIZE = 1000
Limit = Annotated[int, Query(ge=0, le=MAX_PAGE_SIZE)]
Offset = Annotated[int, Query(ge=0, le=2_147_483_647)]

app = FastAPI(
    title="Diabetes Readmission Intervention Queue - Week 3",
    description="Patient search, high-care list and status tracking",
    version="2.0.0"
)


# Browser origins allowed to call this API (local React dev servers: Vite and Create React App).
# Add the deployed UI's origin here when there is one. Never use "*" for a healthcare API.
ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5500", "http://127.0.0.1:5500",  # VS Code Live Server
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "PATCH"],
    allow_headers=["Content-Type"],
)


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(service.NotFoundError)
def handle_not_found(request: Request, error: service.NotFoundError):
    return _error(404, str(error))


@app.exception_handler(service.ConflictError)
def handle_conflict(request: Request, error: service.ConflictError):
    return _error(409, str(error))


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(DatabaseNotConfigured)
def handle_database_unavailable(request: Request, error: Exception):
    return _error(503, "The database is unavailable.")


@app.get("/encounters")
def list_encounters(limit: Limit = 100, offset: Offset = 0):
    """Retrieves a paginated list of all patient encounters."""
    return service.list_encounters(limit, offset)


@app.get("/patients/search")
def search_patients(
    patient_nbr: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    risk_level: RiskLevel | None = None,
    risk_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
):
    """Search encounters by patient number and/or risk level and/or risk score.

    Every filter is optional; the ones you give are combined with AND.
    Results are ordered by risk score (highest first).
    """
    return service.search_patients(patient_nbr, risk_level, risk_score, limit, offset)


@app.get("/encounters/{encounter_id}")
def get_encounter(encounter_id: str):
    """Retrieves a single patient encounter by its unique ID, raising a 404 if not found."""
    return service.get_encounter(encounter_id)


@app.get("/patients/{patient_nbr}/encounters")
def patient_encounters(patient_nbr: str):
    """Full encounter history for one patient, with the highest risk score seen."""
    return service.get_patient_encounters(patient_nbr)


@app.get("/analytics/age-groups")
def age_group_analytics():
    """Encounter volume, average stay, average risk and <30-day readmission rate per age band."""
    return service.get_age_group_stats()


@app.get("/analytics/utilization")
def utilization_analytics():
    """<30-day readmission rate by number of prior inpatient visits (0, 1, 2, 3+)."""
    return service.get_utilization_stats()


@app.get("/care-queue")
def care_queue(limit: Limit = 50, offset: Offset = 0):
    """Open MEDIUM and HIGH risk encounters for the care team, highest score first."""
    return service.get_care_queue(["MEDIUM", "HIGH"], limit, offset)


@app.get("/care-queue/high")
def high_risk_care_queue(limit: Limit = 50, offset: Offset = 0):
    """Open encounters restricted strictly to HIGH risk, highest score first."""
    return service.get_care_queue(["HIGH"], limit, offset)


@app.get("/interventions/{encounter_id}")
def get_intervention(encounter_id: str):
    """Current care-intervention status for one encounter (PENDING_REVIEW if nobody has touched it)."""
    return service.get_intervention_detail(encounter_id)


@app.patch("/interventions/{encounter_id}")
def update_intervention(encounter_id: str, payload: InterventionUpdate):
    """Updates or creates the workflow status for an encounter.

    `assigned_to` and `notes` are only changed when they are included in the request.
    """
    fields = {
        name: getattr(payload, name)
        for name in ("assigned_to", "notes")
        if name in payload.model_fields_set
    }
    updated = service.update_intervention(encounter_id, payload.status, fields)
    return {"message": "Intervention updated successfully", "data": updated}


@app.get("/operations/summary")
def operations_summary():
    """Provides operational metrics and queue backlogs calculated from database records."""
    return service.get_operations_metrics()


@app.get("/operations/readmission-analysis")
def readmission_analysis():
    """Compares every risk group against historical <30-day readmission outcomes."""
    return service.get_readmission_stats()


@app.get("/management/dashboard")
def management_dashboard():
    """Executive summary combining overall operational metrics and clinical validation signals."""
    return {
        "operations": service.get_operations_metrics(),
        "clinical_validation": service.get_readmission_stats()
    }

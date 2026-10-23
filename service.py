import repository
from models import InterventionStatus


class NotFoundError(ValueError):
    """The requested record does not exist (HTTP 404)."""


class ConflictError(ValueError):
    """The request conflicts with the current state (HTTP 409)."""


# The only fields the score may read. `readmitted` (the historical outcome) is deliberately absent.
SCORING_FIELDS = (
    "number_inpatient", "number_emergency", "number_outpatient",
    "time_in_hospital", "num_medications",
)


def calculate_risk(encounter: dict):
    def value(key):
        return encounter.get(key) or 0  # missing or null counts as 0

    score = 0
    if value("number_inpatient") >= 3:
        score += 3
    if value("number_emergency") >= 2:
        score += 2
    if value("number_outpatient") >= 3:
        score += 1
    if value("time_in_hospital") >= 7:
        score += 1
    if value("num_medications") >= 20:
        score += 1

    if score >= 5:
        level = "HIGH"
    elif score >= 3:
        level = "MEDIUM"
    else:
        level = "LOW"

    return score, level


def list_encounters(limit: int, offset: int):
    return repository.get_all_encounters(limit=limit, offset=offset)


def search_patients(patient_nbr, risk_level, risk_score, limit: int, offset: int):
    level = risk_level.value if risk_level is not None else None  # enum -> plain string for SQL
    return repository.search_encounters(patient_nbr, level, risk_score, limit, offset)


def get_encounter(encounter_id: str):
    encounter = repository.get_encounter_by_id(encounter_id)
    if encounter is None:
        raise NotFoundError(f"Encounter ID '{encounter_id}' not found.")
    return encounter


def get_patient_encounters(patient_nbr: str):
    encounters = repository.get_encounters_by_patient(patient_nbr)
    if not encounters:
        raise NotFoundError(f"Patient '{patient_nbr}' not found.")
    return {
        "patient_nbr": patient_nbr,
        "total_encounters": len(encounters),
        "highest_risk_score": max(e["risk_score"] for e in encounters),
        "encounters": encounters,
    }


def get_age_group_stats():
    return repository.get_age_group_stats()


def get_utilization_stats():
    return repository.get_utilization_stats()


def get_care_queue(risk_levels: list, limit: int, offset: int):
    return repository.get_care_queue(risk_levels, limit=limit, offset=offset)


def get_intervention_detail(encounter_id: str):
    """Encounter id + patient number + current care status. Untouched encounters are PENDING_REVIEW."""
    encounter = get_encounter(encounter_id)  # 404 if the encounter does not exist
    intervention = repository.get_intervention(encounter_id) or {}
    return {
        "encounter_id": encounter["encounter_id"],
        "patient_nbr": encounter["patient_nbr"],
        "risk_score": encounter["risk_score"],
        "risk_level": encounter["risk_level"],
        "status": intervention.get("status", InterventionStatus.PENDING_REVIEW.value),
        "assigned_to": intervention.get("assigned_to"),
        "notes": intervention.get("notes"),
        "updated_at": intervention.get("updated_at"),
    }


def update_intervention(encounter_id: str, status: InterventionStatus, fields: dict):
    """Create or update an intervention. A CLOSED case cannot be reopened."""
    get_encounter(encounter_id)  # 404 if the encounter does not exist
    current = repository.get_intervention(encounter_id)
    if (
        current
        and current["status"] == InterventionStatus.CLOSED.value
        and status != InterventionStatus.CLOSED
    ):
        raise ConflictError(
            f"Encounter '{encounter_id}' is CLOSED and cannot be moved back to {status.value}."
        )
    return repository.upsert_intervention(encounter_id, status.value, fields)


def get_operations_metrics():
    return repository.get_operations_metrics()


def get_readmission_stats():
    return repository.get_readmission_stats()

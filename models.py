from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class InterventionStatus(str, Enum):
    """Workflow order: PENDING_REVIEW -> ASSIGNED -> CONTACTED -> FOLLOW_UP_SCHEDULED -> CLOSED."""
    PENDING_REVIEW = "PENDING_REVIEW"
    ASSIGNED = "ASSIGNED"
    CONTACTED = "CONTACTED"
    FOLLOW_UP_SCHEDULED = "FOLLOW_UP_SCHEDULED"
    CLOSED = "CLOSED"


class RiskLevel(str, Enum):
    """Used as a query parameter type so Swagger shows a dropdown and bad values return 422."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class InterventionUpdate(BaseModel):
    status: InterventionStatus
    # Only the fields actually sent are changed; send null to clear one.
    assigned_to: Optional[str] = Field(default=None, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=100_000)


class EncounterResponse(BaseModel):
    encounter_id: str
    patient_nbr: str
    age: Optional[str] = None
    time_in_hospital: int
    risk_score: int
    risk_level: str
    readmitted: Optional[str] = None
    status: Optional[str] = "PENDING_REVIEW"
    assigned_to: Optional[str] = None
    notes: Optional[str] = None

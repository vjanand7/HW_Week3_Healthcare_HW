// Shapes of the data the API sends and receives. Field names match the API exactly.

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export type InterventionStatus =
    | "PENDING_REVIEW"
    | "ASSIGNED"
    | "CONTACTED"
    | "FOLLOW_UP_SCHEDULED"
    | "CLOSED";

/** One row from GET /patients/search */
export interface Encounter {
    encounter_id: string;
    patient_nbr: string;
    age: string | null;
    time_in_hospital: number;
    number_inpatient: number;
    number_emergency: number;
    number_outpatient: number;
    num_medications: number;
    num_lab_procedures: number;
    discharge_disposition_id: number;
    admission_type_id: number;
    readmitted: string | null;
    risk_score: number;
    risk_level: RiskLevel;
}

/** Filters for GET /patients/search. Leave a filter out to ignore it. */
export interface SearchFilters {
    patient_nbr?: string;
    risk_level?: RiskLevel;
    risk_score?: number;
    limit?: number;
    offset?: number;
}

/** The saved care status. Fields shown in the "Updates" list. */
export interface InterventionSummary {
    status: InterventionStatus;
    assigned_to: string | null;
    notes: string | null;
    updated_at: string | null; // null = nobody has touched this encounter yet
}

/** Response of GET /interventions/{encounter_id} */
export interface InterventionDetail extends InterventionSummary {
    encounter_id: string;
    patient_nbr: string;
    risk_score: number;
    risk_level: RiskLevel;
}

/** Body sent to PATCH /interventions/{encounter_id} */
export interface InterventionUpdate {
    status: InterventionStatus;
    assigned_to: string | null;
    notes: string | null;
}

/** Response of PATCH /interventions/{encounter_id} */
export interface InterventionUpdateResponse {
    message: string;
    data: InterventionSummary & { intervention_id: number; encounter_id: string; created_at: string };
}

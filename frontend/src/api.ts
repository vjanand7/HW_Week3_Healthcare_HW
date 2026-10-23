// The three API calls used by the screens. <T> says what type of data comes back.
import { API_BASE_URL } from "./config.js";
import type {
    Encounter,
    InterventionDetail,
    InterventionUpdate,
    InterventionUpdateResponse,
    SearchFilters,
} from "./types.js";

// Turns a failed response into a readable message (the API sends {"detail": "..."}).
async function readError(response: Response): Promise<string> {
    try {
        const body: unknown = await response.json();
        if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
            return body.detail;
        }
    } catch {
        // the body was not JSON
    }
    return `HTTP ${response.status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(API_BASE_URL + path, init);
    if (!response.ok) {
        throw new Error(await readError(response));
    }
    return (await response.json()) as T;
}

/** GET /patients/search - only the filters that are set are sent. */
export function searchPatients(filters: SearchFilters): Promise<Encounter[]> {
    const params = new URLSearchParams();
    for (const [name, value] of Object.entries(filters)) {
        if (value !== undefined) {
            params.set(name, String(value));
        }
    }
    return request<Encounter[]>("/patients/search?" + params.toString());
}

/** GET /interventions/{encounter_id} - current care status of one encounter. */
export function getIntervention(encounterId: string): Promise<InterventionDetail> {
    return request<InterventionDetail>("/interventions/" + encodeURIComponent(encounterId));
}

/** PATCH /interventions/{encounter_id} - save a new status, owner and notes. */
export function updateIntervention(encounterId: string, update: InterventionUpdate): Promise<InterventionUpdateResponse> {
    return request<InterventionUpdateResponse>("/interventions/" + encodeURIComponent(encounterId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
    });
}

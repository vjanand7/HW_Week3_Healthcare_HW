// Screen 2: Status Tracking. Loads one encounter's care status and lets the user update it.
import { getIntervention, updateIntervention } from "./api.js";
import { byId, errorText, showMessage } from "./dom.js";
import type { InterventionDetail, InterventionStatus, InterventionSummary } from "./types.js";

const form = byId<HTMLFormElement>("status-form");
const encounterInput = byId<HTMLInputElement>("encounter_id");
const patientInput = byId<HTMLInputElement>("patient_nbr");
const statusSelect = byId<HTMLSelectElement>("status");
const assignedInput = byId<HTMLInputElement>("assigned_to");
const notesInput = byId<HTMLTextAreaElement>("notes");
const updateButton = byId<HTMLButtonElement>("update-button");
const message = byId<HTMLElement>("message");
const updateList = byId<HTMLElement>("update-list");

// Which encounter? It comes from the link on screen 1: status.html?encounter_id=...
const encounterId = new URLSearchParams(window.location.search).get("encounter_id");

const STATUSES: readonly string[] = ["PENDING_REVIEW", "ASSIGNED", "CONTACTED", "FOLLOW_UP_SCHEDULED", "CLOSED"];

function isStatus(value: string): value is InterventionStatus {
    return STATUSES.includes(value);
}

/** Adds one entry to the Updates list. textContent only, so notes can never inject HTML. */
function addUpdate(record: InterventionSummary, atTop: boolean): void {
    const item = document.createElement("div");
    item.className = "update-item";

    const when = document.createElement("div");
    when.className = "when";
    when.textContent = record.updated_at ? new Date(record.updated_at).toLocaleString() : "";
    item.appendChild(when);

    const line = document.createElement("div");
    line.textContent = `Status: ${record.status} | Assigned to: ${record.assigned_to || "-"}`;
    item.appendChild(line);

    if (record.notes) {
        const notes = document.createElement("div");
        notes.className = "notes";
        notes.textContent = "Notes: " + record.notes;
        item.appendChild(notes);
    }

    if (atTop) {
        updateList.prepend(item);
    } else {
        updateList.appendChild(item);
    }
}

function fillForm(record: InterventionDetail): void {
    encounterInput.value = record.encounter_id;
    patientInput.value = record.patient_nbr;
    statusSelect.value = record.status;
    assignedInput.value = record.assigned_to ?? "";
    notesInput.value = record.notes ?? "";
}

async function loadEncounter(): Promise<void> {
    if (encounterId === null || encounterId === "") {
        showMessage(message, "No encounter selected. Go back to the search and click an Encounter Id.", "error");
        updateButton.disabled = true;
        return;
    }

    try {
        const record = await getIntervention(encounterId);
        fillForm(record);
        if (record.updated_at !== null) {
            addUpdate(record, false); // the saved record is the first entry
        } else {
            updateList.textContent = "No updates yet for this encounter.";
        }
    } catch (error) {
        showMessage(message, "Could not load encounter: " + errorText(error), "error");
        updateButton.disabled = true;
    }
}

form.addEventListener("submit", async (event: SubmitEvent) => {
    event.preventDefault();
    if (encounterId === null || !isStatus(statusSelect.value)) {
        return;
    }

    updateButton.disabled = true;
    showMessage(message, "Saving...");

    try {
        const result = await updateIntervention(encounterId, {
            status: statusSelect.value,
            assigned_to: assignedInput.value.trim() || null,
            notes: notesInput.value.trim() || null,
        });

        if (updateList.querySelector(".update-item") === null) {
            updateList.replaceChildren(); // remove the "No updates yet" text
        }
        addUpdate(result.data, true); // newest update on top
        showMessage(message, "Updated.", "ok");
    } catch (error) {
        showMessage(message, "Update failed: " + errorText(error), "error");
    } finally {
        updateButton.disabled = false;
    }
});

void loadEncounter();

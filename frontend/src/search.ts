// Screen 1: Search Patient. Builds the result table and links each Encounter Id to screen 2.
import { searchPatients } from "./api.js";
import { SEARCH_PAGE_SIZE } from "./config.js";
import { byId, errorText, showMessage } from "./dom.js";
import type { Encounter, RiskLevel, SearchFilters } from "./types.js";

const form = byId<HTMLFormElement>("search-form");
const patientInput = byId<HTMLInputElement>("patient_nbr");
const riskLevelSelect = byId<HTMLSelectElement>("risk_level");
const riskScoreInput = byId<HTMLInputElement>("risk_score");
const searchButton = byId<HTMLButtonElement>("search-button");
const message = byId<HTMLElement>("message");
const resultList = byId<HTMLTableSectionElement>("result-list");

const RISK_LEVELS: readonly string[] = ["LOW", "MEDIUM", "HIGH"];

function isRiskLevel(value: string): value is RiskLevel {
    return RISK_LEVELS.includes(value);
}

/** Reads the form into filters. Empty boxes are left out so they are not sent. */
function readFilters(): SearchFilters {
    const filters: SearchFilters = { limit: SEARCH_PAGE_SIZE };

    const patientNbr = patientInput.value.trim();
    if (patientNbr !== "") {
        filters.patient_nbr = patientNbr;
    }
    if (isRiskLevel(riskLevelSelect.value)) {
        filters.risk_level = riskLevelSelect.value;
    }
    if (riskScoreInput.value !== "") {
        filters.risk_score = Number(riskScoreInput.value);
    }
    return filters;
}

/** Adds one text cell to a row. textContent means API data can never inject HTML. */
function addCell(row: HTMLTableRowElement, text: string | number | null): HTMLTableCellElement {
    const cell = row.insertCell();
    cell.textContent = text === null ? "" : String(text);
    return cell;
}

function renderResults(encounters: Encounter[]): void {
    resultList.replaceChildren();

    for (const encounter of encounters) {
        const row = resultList.insertRow();

        // The Encounter Id is a link to screen 2
        const link = document.createElement("a");
        link.href = "status.html?encounter_id=" + encodeURIComponent(encounter.encounter_id);
        link.textContent = encounter.encounter_id;
        addCell(row, null).appendChild(link);

        addCell(row, encounter.patient_nbr);
        addCell(row, encounter.age);
        addCell(row, encounter.risk_score);
        addCell(row, encounter.risk_level);
    }
}

form.addEventListener("submit", async (event: SubmitEvent) => {
    event.preventDefault();
    showMessage(message, "Searching...");
    searchButton.disabled = true;

    try {
        const encounters = await searchPatients(readFilters());
        renderResults(encounters);
        if (encounters.length === 0) {
            showMessage(message, "No encounters found.");
        } else {
            showMessage(message, `${encounters.length} encounter(s) found (showing up to ${SEARCH_PAGE_SIZE}).`, "ok");
        }
    } catch (error) {
        resultList.replaceChildren();
        showMessage(message, "Search failed: " + errorText(error), "error");
    } finally {
        searchButton.disabled = false;
    }
});

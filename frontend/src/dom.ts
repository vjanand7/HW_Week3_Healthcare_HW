// Small helpers shared by both screens.

/** Finds an element by id and fails loudly if the HTML and the code disagree. */
export function byId<T extends HTMLElement>(id: string): T {
    const element = document.getElementById(id);
    if (element === null) {
        throw new Error(`Element #${id} not found in the page`);
    }
    return element as T;
}

/** Shows a status line under the form. kind: "ok", "error" or "" (plain). */
export function showMessage(element: HTMLElement, text: string, kind: "ok" | "error" | "" = ""): void {
    element.textContent = text;
    element.className = "message " + kind;
}

/** Readable message from anything that was thrown. */
export function errorText(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
}

// Minimal static file server for the frontend (no dependencies).
// Port 5500 is in the API's CORS allow-list. Run with:  npm run serve
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 5500;
const ROOT = resolve(fileURLToPath(new URL(".", import.meta.url)));

const TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
};

createServer(async (req, res) => {
    const urlPath = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    const relative = urlPath === "/" ? "search.html" : urlPath.slice(1);
    const file = resolve(join(ROOT, normalize(relative)));

    // Only serve the page files, the compiled code and the stylesheet; never node_modules, src or config.
    const allowed = /^(?:[\w-]+\.(?:html|css)|dist[\\/][\w./\\-]+\.(?:js|map))$/;
    const inside = file.startsWith(ROOT + "\\") || file.startsWith(ROOT + "/");
    if (!inside || !allowed.test(file.slice(ROOT.length + 1))) {
        res.writeHead(404).end("Not found");
        return;
    }

    try {
        const body = await readFile(file);
        res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" }).end(body);
    } catch {
        res.writeHead(404).end("Not found");
    }
}).listen(PORT, "127.0.0.1", () => {
    console.log(`Frontend: http://localhost:${PORT}/search.html  (Ctrl+C to stop)`);
});

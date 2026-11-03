/** Client for the shared SLAI server envelope: `{ok:true,data}` or `{ok:false,error:{code,message,hint?,details?}}`.
 *  The server sets an HttpOnly session cookie when the page loads; mutations carry the `X-SLAI-<App>` marker header. */
export class ApiError extends Error {
    status;
    code;
    hint;
    details;
    constructor(status, code, message, hint, details) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.code = code;
        this.hint = hint;
        this.details = details;
    }
    /** The message plus its hint, ready for a toast or notice. */
    get friendly() { return this.hint ? `${this.message} ${this.hint}` : this.message; }
}
export function queryString(query) {
    if (!query)
        return "";
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query))
        if (value !== undefined && value !== null && value !== "")
            params.set(key, String(value));
    const text = params.toString();
    return text ? `?${text}` : "";
}
export function markerHeader(slug) {
    return `X-SLAI-${slug.charAt(0).toUpperCase()}${slug.slice(1)}`;
}
function filenameFrom(disposition, fallback) {
    const match = disposition ? /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition) : null;
    if (!match || !match[1])
        return fallback;
    try {
        return decodeURIComponent(match[1]);
    }
    catch {
        return match[1];
    }
}
export function createClient(options) {
    const base = options.base ?? "/api";
    const marker = markerHeader(options.slug);
    const fail = (error) => { options.onError?.(error); throw error; };
    async function request(method, path, body, query) {
        const headers = { Accept: "application/json", [marker]: "1" };
        const init = { method, headers, credentials: "same-origin", cache: "no-store" };
        if (body !== undefined) {
            headers["Content-Type"] = "application/json";
            init.body = JSON.stringify(body);
        }
        try {
            return await fetch(`${base}${path}${queryString(query)}`, init);
        }
        catch (cause) {
            return fail(new ApiError(0, "offline", "Can't reach the local server.", "Check that the application is still running, then try again.", String(cause)));
        }
    }
    async function json(method, path, body, query) {
        const response = await request(method, path, body, query);
        let parsed = null;
        try {
            parsed = (await response.json());
        }
        catch {
            parsed = null;
        }
        if (parsed && parsed.ok)
            return parsed.data;
        const error = parsed?.error;
        return fail(new ApiError(response.status, error?.code ?? "http_error", error?.message ?? `The server answered ${response.status}.`, error?.hint, error?.details));
    }
    return {
        get: (path, query) => json("GET", path, undefined, query),
        post: (path, body) => json("POST", path, body ?? {}),
        put: (path, body) => json("PUT", path, body ?? {}),
        patch: (path, body) => json("PATCH", path, body ?? {}),
        del: (path) => json("DELETE", path),
        async download(path, query) {
            const response = await request("GET", path, undefined, query);
            if (!response.ok) {
                let parsed = null;
                try {
                    parsed = (await response.json());
                }
                catch {
                    parsed = null;
                }
                const error = parsed?.error;
                return fail(new ApiError(response.status, error?.code ?? "http_error", error?.message ?? `The server answered ${response.status}.`, error?.hint, error?.details));
            }
            return { blob: await response.blob(), filename: filenameFrom(response.headers.get("Content-Disposition"), "download") };
        },
    };
}

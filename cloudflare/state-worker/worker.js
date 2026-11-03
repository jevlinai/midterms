// midterms-state: serves the latest state.json from Workers KV (key "state").
// state/build_state.py --push writes the key through the Cloudflare API after every round;
// The page on jevlin.ai/midterms fetches GET /state every 60s, straight from this worker, so the
// page's traffic lands on this account and not on the website's. Read-only: there is no write route.

const MAX_AGE = 60; // also the KV cacheTtl minimum

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") return new Response("method not allowed", { status: 405 });
    if (url.pathname !== "/state") return new Response("not found", { status: 404 });
    const body = await env.MIDTERMS.get("state", { cacheTtl: MAX_AGE });
    const headers = {
      "Content-Type": "application/json; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
      "Access-Control-Allow-Origin": "*", // public, read-only data
    };
    if (body === null) return new Response('{"error":"no state yet"}', { status: 503, headers: { ...headers, "Cache-Control": "no-store" } });
    return new Response(body, { headers: { ...headers, "Cache-Control": `public, max-age=${MAX_AGE}` } });
  },
};

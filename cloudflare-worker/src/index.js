const corsHeaders = (origin) => ({
  "Access-Control-Allow-Origin": origin || "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "content-type,x-admin-token",
  "Access-Control-Max-Age": "86400",
});

function withCors(response, origin) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(corsHeaders(origin))) {
    headers.set(key, value);
  }
  headers.set("Vary", "Origin");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
    webSocket: response.webSocket,
  });
}

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const allowedOrigin = env.ALLOWED_ORIGIN || "*";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(allowedOrigin) });
    }

    if (!env.SPACE_ORIGIN) {
      return Response.json(
        { ok: false, error: "SPACE_ORIGIN is not configured" },
        { status: 500, headers: corsHeaders(allowedOrigin) },
      );
    }

    let origin;
    try {
      origin = new URL(env.SPACE_ORIGIN);
    } catch {
      return Response.json(
        { ok: false, error: "SPACE_ORIGIN is invalid" },
        { status: 500, headers: corsHeaders(allowedOrigin) },
      );
    }

    const target = new URL(incoming.pathname + incoming.search, origin);
    const upstreamRequest = new Request(target, request);

    try {
      const response = await fetch(upstreamRequest, { redirect: "manual" });
      return withCors(response, allowedOrigin);
    } catch (error) {
      console.error(JSON.stringify({
        event: "upstream_fetch_failed",
        target: target.toString(),
        error: error instanceof Error ? error.message : String(error),
      }));
      return Response.json(
        {
          ok: false,
          waking: true,
          error: "The Hugging Face Space is unavailable or waking up. Retry shortly.",
        },
        { status: 503, headers: corsHeaders(allowedOrigin) },
      );
    }
  },
};

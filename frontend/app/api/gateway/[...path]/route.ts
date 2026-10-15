import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const allowed = /^api\/(?:health|benchmarks|repos(?:\/[a-f0-9-]{36}(?:\/(?:status|events|file|chat|conversations\/[a-f0-9-]{36}))?)?)$/;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  if (!allowed.test(route)) return Response.json({ detail: "Not found" }, { status: 404 });
  const origin = request.headers.get("origin");
  if (origin && origin !== request.nextUrl.origin) {
    return Response.json({ detail: "Request not allowed" }, { status: 403 });
  }
  if (request.method === "POST" && route !== "api/repos" && !route.endsWith("/chat")) {
    return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  const backend = process.env.CODELENS_API_URL || (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
  const token = process.env.CODELENS_ACCESS_TOKEN;
  if (!backend || (process.env.NODE_ENV === "production" && !token)) {
    return Response.json({ detail: "The service is being configured. Please try again shortly." }, { status: 503 });
  }
  let body: string | undefined;
  if (request.method === "POST") {
    if (Number(request.headers.get("content-length") || 0) > 16384) return new Response(null, { status: 413 });
    body = await request.text();
    if (new TextEncoder().encode(body).length > 16384) return new Response(null, { status: 413 });
  }
  const target = new URL(`/` + route, backend);
  // Only forward the documented file-path query, never a caller-controlled destination.
  if (route.endsWith("/file")) target.searchParams.set("path", request.nextUrl.searchParams.get("path") || "");
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body, cache: "no-store", redirect: "error", signal: request.signal,
    });
    if (upstream.status === 401 || upstream.status === 403) {
      return Response.json({ detail: "The service is temporarily unavailable. Please try again later." }, { status: 503 });
    }
    if (upstream.status >= 500 && !upstream.headers.get("content-type")?.includes("application/json")) {
      return Response.json({ detail: "The service restarted or is waking up. Please retry shortly." }, { status: 502 });
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "no-store", "X-Accel-Buffering": "no" },
    });
  } catch {
    return Response.json({ detail: "The service is waking up. Please try again shortly." }, { status: 502 });
  }
}
export const GET = proxy;
export const POST = proxy;


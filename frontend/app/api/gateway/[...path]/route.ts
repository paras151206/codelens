import { NextRequest } from "next/server";
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const allowed = /^api\/(?:health|benchmarks|repos(?:\/[a-f0-9-]{36}(?:\/(?:status|events|file|chat|conversations\/[a-f0-9-]{36}))?)?)$/;

const cookieName = "codelens_session";
function session(request: NextRequest, secret: string) {
  const raw = request.cookies.get(cookieName)?.value || "";
  const [id, expires, signature] = raw.split(".");
  const sign = (value: string) => createHmac("sha256", secret).update(value).digest("hex");
  if (/^[a-f0-9-]{36}$/.test(id || "") && Number(expires) > Date.now() && /^[a-f0-9]{64}$/.test(signature || "")) {
    const expected = sign(`${id}.${expires}`);
    if (timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return { id, cookie: "" };
  }
  const fresh = randomUUID();
  const value = `${fresh}.${Date.now() + 7 * 86400_000}`;
  return { id: fresh, cookie: `${cookieName}=${value}.${sign(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800${process.env.NODE_ENV === "production" ? "; Secure" : ""}` };
}

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
  if (request.method === "DELETE" && !/^api\/repos\/[a-f0-9-]{36}$/.test(route)) {
    return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  const backend = process.env.CODELENS_API_URL || (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
  const token = process.env.CODELENS_ACCESS_TOKEN;
  if (!backend || (process.env.NODE_ENV === "production" && !token)) {
    return Response.json({ detail: "The service is being configured. Please try again shortly." }, { status: 503 });
  }
  const visitor = session(request, token || "local-development-only");
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
      headers: { "Content-Type": "application/json", "X-CodeLens-Session": visitor.id, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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
      headers: { "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "no-store", "X-Accel-Buffering": "no", ...(visitor.cookie ? { "Set-Cookie": visitor.cookie } : {}) },
    });
  } catch {
    return Response.json({ detail: "The service is waking up. Please try again shortly." }, { status: 502 });
  }
}
export const GET = proxy;
export const POST = proxy;


export const DELETE = proxy;

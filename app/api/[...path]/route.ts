import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "node:crypto";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const pathKey = path.join("/");
  if (pathKey === "demo/session" || pathKey === "demo/session/reset") {
    const id = pathKey.endsWith("/reset") ? randomUUID() : request.cookies.get("latepayer_demo_session")?.value || randomUUID();
    const response = NextResponse.json({ ready: true, isolated: true });
    response.cookies.set("latepayer_demo_session", id, {
      httpOnly: false, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/",
    });
    return response;
  }
  const hostPort = process.env.API_HOSTPORT;
  const apiBase = process.env.API_URL || (hostPort ? `http://${hostPort}` : "http://127.0.0.1:8000");
  const target = `${apiBase.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  for (const name of ["cookie", "content-type", "accept", "x-requested-with", "if-none-match", "idempotency-key"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasOperatorSession = request.cookies.has("latepayer_session");
  const existingDemoSession = request.cookies.get("latepayer_demo_session")?.value;
  const demoSession = existingDemoSession || randomUUID();
  if (!hasOperatorSession && !["health", "healthz", "evaluation", "ai/status", "auth/login", "auth/me", "auth/logout"].includes(pathKey)) {
    headers.set("x-demo-session", demoSession);
  }
  const init: RequestInit = { method: request.method, headers, cache: "no-store", redirect: "manual" };
  if (["GET", "HEAD"].includes(request.method)) init.signal = request.signal;
  if (!(["GET", "HEAD"].includes(request.method))) init.body = await request.arrayBuffer();
  try {
    const upstream = await fetch(target, init);
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!["content-encoding", "content-length", "transfer-encoding", "connection"].includes(key.toLowerCase())) {
        responseHeaders.set(key, value);
      }
    });
    const response = new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
    if (!existingDemoSession) {
      response.cookies.set("latepayer_demo_session", demoSession, {
        httpOnly: false, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/",
      });
    }
    return response;
  } catch {
    return NextResponse.json({ detail: "API service is unavailable. Start it with `make api`." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

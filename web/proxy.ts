import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest): NextResponse {
  // Skip auth for Next.js internals and API routes.
  const { pathname } = request.nextUrl;
  if (pathname.startsWith("/api/") || pathname.startsWith("/_next/")) {
    return NextResponse.next();
  }

  const expectedUser = process.env.DASHBOARD_USER ?? "admin";
  const expectedPassword = process.env.DASHBOARD_PASSWORD ?? "";

  const authorization = request.headers.get("authorization") ?? "";
  if (authorization.startsWith("Basic ")) {
    const encoded = authorization.slice("Basic ".length);
    const decoded = Buffer.from(encoded, "base64").toString("utf-8");
    const [user, ...rest] = decoded.split(":");
    const password = rest.join(":");
    if (user === expectedUser && password === expectedPassword) {
      return NextResponse.next();
    }
  }

  return new NextResponse("Unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Sentinel"',
    },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

import { NextRequest, NextResponse } from "next/server";

/**
 * Security middleware — token-based access control.
 *
 * Valid URLs:
 *   /{token}          → serves the main app
 *   /{token}/...      → static assets / client nav
 *   /{token}/api/v1/* → API proxy (also protected)
 *
 * Everything else → 404 (looks like the server doesn't exist)
 *
 * Token is set via NIMBUS_ACCESS_TOKEN env var.
 * Default: "nimbus-666"
 */

const TOKEN = process.env.NIMBUS_ACCESS_TOKEN || "nimbus-666";

// Paths that must always be accessible (Next.js internals)
const ALWAYS_ALLOW = ["/_next/", "/favicon.ico", "/fonts/"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Always allow Next.js internals
  if (ALWAYS_ALLOW.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // Check token prefix: pathname must start with /{token}
  const tokenPrefix = `/${TOKEN}`;
  if (pathname === tokenPrefix || pathname.startsWith(`${tokenPrefix}/`)) {
    // Strip token prefix and rewrite to real path
    const realPath = pathname.slice(tokenPrefix.length) || "/";
    const url = request.nextUrl.clone();
    url.pathname = realPath;
    return NextResponse.rewrite(url);
  }

  // Everything else → 404
  return new NextResponse(null, { status: 404 });
}

export const config = {
  // Run on all routes except Next.js internals
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

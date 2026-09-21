/*
 * The one synthetic hop of the portal journey (WP-J1-10).
 *
 * Everything the browser touches is a REAL local server (the harness's `StaticOrigin` in
 * scripts/dev/run_portal_journey.py serves the built bundle and forwards /api to the real
 * BFF). The single exception is the IdP authorize hop of the login redirect: no IdP and no
 * network is contacted, so this module fetches that hop itself (fulfillLogin) and lands the
 * browser on the REAL callback, where the BFF issues the session cookie. Any third-party
 * request the browser itself attempts is aborted and recorded: an unexpected outbound request
 * fails the journey, which is how "sem analytics de browser" (ADR-0049 D3) is proven, not
 * presumed.
 */

import type { BrowserContext, Route } from "@playwright/test";

export type TargetOptions = {
  /** Browser-facing origin of the local portal server (http://localhost:<port>). */
  portalOrigin: string;
  /** IdP origin whose authorize hop is answered synthetically. */
  idpOrigin: string;
  /** Fixed stub authorization code handed back to the BFF callback. */
  code: string;
};

/** Same-origin path the axe scan is served from; the audited page CSP is script-src 'self'. */
export const AXE_PATH = "/assets/axe.min.js";

export type TargetObservation = {
  thirdPartyRequests: string[];
  syntheticAuthorizeHits: number;
};

export async function interceptIdp(
  context: BrowserContext,
  options: TargetOptions,
  /** Pass the SAME object across tests to accumulate the whole journey's observation. */
  accumulate?: TargetObservation,
): Promise<TargetObservation> {
  const observation: TargetObservation = accumulate ?? {
    thirdPartyRequests: [],
    syntheticAuthorizeHits: 0,
  };
  await context.route("**/*", async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.origin === options.portalOrigin && url.pathname === "/api/v1/portal/auth/login") {
      observation.syntheticAuthorizeHits += 1;
      await fulfillLogin(route, context, options);
      return;
    }
    if (url.origin === options.portalOrigin) {
      await route.continue();
      return;
    }
    observation.thirdPartyRequests.push(route.request().url());
    await route.abort();
  });
  return observation;
}

/**
 * Performs the IdP hop of the login handshake and lands the browser on the BFF callback.
 *
 * `/auth/login` answers 303 -> IdP authorize with the transaction cookie. No IdP is
 * contacted and the browser cannot re-enter interception on a followed redirect, so the
 * harness fetches that authorize URL itself, hands the transaction cookie it received to
 * the browser context, and answers the original navigation with 303 -> the BFF callback
 * carrying the fixed stub code. The callback is then served by the REAL server, so the
 * session cookie is set by a real BFF response — nothing here fabricates one.
 */
async function fulfillLogin(
  route: Route,
  context: BrowserContext,
  options: TargetOptions,
): Promise<void> {
  const login = await fetch(options.portalOrigin + "/api/v1/portal/auth/login?return_to=%2F", {
    redirect: "manual",
    headers: { host: "localhost" },
  });
  const authorize = new URL(login.headers.get("location") ?? "");
  const transactionCookie = login.headers
    .getSetCookie()
    .find((cookie) => cookie.startsWith("__Host-maezo-login="));
  const state = authorize.searchParams.get("state");
  if (login.status !== 303 || state === null || !transactionCookie) {
    await route.fulfill({ status: login.status, body: Buffer.from(await login.arrayBuffer()) });
    return;
  }
  await context.addCookies([parseCookie(transactionCookie, "localhost")]);
  await route.fulfill({
    status: 303,
    headers: {
      location: `${options.portalOrigin}/api/v1/portal/auth/callback?code=${encodeURIComponent(options.code)}&state=${encodeURIComponent(state)}`,
    },
  });
}

/** The one attribute set of the BFF transaction cookie, as a Playwright cookie. */
function parseCookie(header: string, domain: string): {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure: boolean;
  httpOnly: boolean;
  sameSite: "Strict" | "Lax" | "None";
} {
  const [pair, ...attributes] = header.split(";").map((part) => part.trim());
  const [name, value] = pair.split("=", 2);
  const attribute = (needle: string): string | null => {
    const found = attributes.find((candidate) => candidate.toLowerCase().startsWith(needle));
    return found ? found.split("=", 2)[1] : null;
  };
  const sameSite = (attribute("samesite") ?? "lax").toLowerCase();
  return {
    name,
    value,
    domain,
    path: attribute("path") ?? "/",
    secure: attributes.some((candidate) => candidate.toLowerCase() === "secure"),
    httpOnly: attributes.some((candidate) => candidate.toLowerCase() === "httponly"),
    sameSite: sameSite === "strict" ? "Strict" : sameSite === "none" ? "None" : "Lax",
  };
}

import type { paths } from "./generated/api";

const SESSION_PATH = "/api/v1/portal/session";
const LOGOUT_PATH = "/api/v1/portal/auth/logout";

export type SessionDTO =
  paths[typeof SESSION_PATH]["get"]["responses"][200]["content"]["application/json"];

export type SessionResult =
  | { kind: "authenticated"; session: SessionDTO }
  | { kind: "unauthenticated" }
  | { kind: "dependency-unavailable" }
  | { kind: "invalid-response" };

const exactSessionKeys = [
  "schema_version",
  "principal_ref",
  "audience",
  "roles",
  "expires_at",
  "csrf_token",
  "capabilities",
] as const;

export type PortalCapability = SessionDTO["capabilities"][number];

const canonicalCapabilities: readonly PortalCapability[] = ["identity", "staff_cases", "human"];

// Canonical order, no duplicates, nothing outside the three literals.
function isCapabilityList(value: unknown): value is PortalCapability[] {
  if (!Array.isArray(value)) return false;
  let previous = -1;
  return value.every((item) => {
    const index = canonicalCapabilities.indexOf(item as PortalCapability);
    if (index <= previous) return false;
    previous = index;
    return true;
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAwareTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

export function validateSession(value: unknown): SessionDTO | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  if (
    keys.length !== exactSessionKeys.length ||
    !exactSessionKeys.every((key) => keys.includes(key))
  ) {
    return null;
  }
  if (
    value.schema_version !== 1 ||
    typeof value.principal_ref !== "string" ||
    value.principal_ref.length === 0 ||
    typeof value.audience !== "string" ||
    !["staff", "beneficiary", "provider"].includes(value.audience) ||
    !Array.isArray(value.roles) ||
    !value.roles.every((role) => typeof role === "string") ||
    !isAwareTimestamp(value.expires_at) ||
    typeof value.csrf_token !== "string" ||
    value.csrf_token.length === 0 ||
    !isCapabilityList(value.capabilities)
  ) {
    return null;
  }
  return value as SessionDTO;
}

export async function getSession(signal: AbortSignal): Promise<SessionResult> {
  let response: Response;
  try {
    response = await fetch(SESSION_PATH, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: "dependency-unavailable" };
  }
  if (response.status === 401) return { kind: "unauthenticated" };
  if (response.status === 503) return { kind: "dependency-unavailable" };
  if (response.status !== 200) return { kind: "invalid-response" };
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: "invalid-response" };
  }
  const session = validateSession(payload);
  return session ? { kind: "authenticated", session } : { kind: "invalid-response" };
}

export type LogoutResult =
  | { kind: "confirmed"; idpLogoutUrl: string | null }
  | { kind: "unconfirmed" };

const cognitoHostedUiHost = /^[a-z0-9-]{1,63}\.auth\.[a-z]{2}(?:-[a-z]+)+-\d\.amazoncognito\.com$/;

/**
 * Accepts the BFF's IdP logout URL only when it is exactly the Cognito Hosted UI `/logout` of a
 * Cognito domain, for a well-formed client id, returning to this very origin. Anything else is
 * dropped and the local logout stands alone.
 */
export function trustedIdpLogoutUrl(raw: unknown, portalOrigin: string): string | null {
  if (typeof raw !== "string" || raw.length > 2048) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (
    url.protocol !== "https:" ||
    url.port !== "" ||
    url.username !== "" ||
    url.password !== "" ||
    url.hash !== "" ||
    !cognitoHostedUiHost.test(url.hostname) ||
    url.origin !== `https://${url.hostname}` ||
    url.pathname !== "/logout"
  ) {
    return null;
  }
  const keys = [...url.searchParams.keys()];
  if (keys.length !== 2 || keys[0] !== "client_id" || keys[1] !== "logout_uri") return null;
  if (!/^[a-z0-9]{1,128}$/.test(url.searchParams.get("client_id") ?? "")) return null;
  if (url.searchParams.get("logout_uri") !== `${portalOrigin}/`) return null;
  return url.href;
}

export async function postLogout(csrfToken: string, signal: AbortSignal): Promise<LogoutResult> {
  let response: Response;
  try {
    response = await fetch(LOGOUT_PATH, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-CSRF-Token": csrfToken,
      },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: "unconfirmed" };
  }
  if (response.status !== 200) return { kind: "unconfirmed" };
  // The local session is already gone on a 200; a bad body only skips the IdP hop.
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
  }
  const raw =
    payload !== null && typeof payload === "object"
      ? (payload as Record<string, unknown>).idp_logout_url
      : undefined;
  return { kind: "confirmed", idpLogoutUrl: trustedIdpLogoutUrl(raw, window.location.origin) };
}

export const portalPaths = {
  login: "/api/v1/portal/auth/login?return_to=/portal/",
} as const;

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

export async function postLogout(csrfToken: string, signal: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(LOGOUT_PATH, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "X-CSRF-Token": csrfToken,
      },
      signal,
    });
    return response.status === 204;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return false;
  }
}

export const portalPaths = {
  login: "/api/v1/portal/auth/login?return_to=/portal/",
} as const;

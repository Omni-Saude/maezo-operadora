import type { components } from "./generated/api";

// D-40: the staff administration area reads ONLY the one GET route the BFF mounts for it —
// the read-only roster of reviewed staff vínculos (`admin_read.py`, GET-only router). There
// is deliberately no create, revoke, extend or re-review method here: the panel is
// READ-ONLY, and mutations stay on the independently granted administrative connection
// (`portal/admin/*` is never called; the fence stands).

type Schemas = components["schemas"];

export type StaffAdminRoster = Schemas["StaffAdminVinculos"];
export type StaffAdminVinculo = Schemas["StaffAdminVinculo"];
export type StaffAdminFailure = Schemas["PortalAdminReadError"]["code"] | "invalid-response";

export type StaffAdminResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: StaffAdminFailure }>;

export interface StaffAdminClient {
  readVinculos(signal: AbortSignal): Promise<StaffAdminResult<StaffAdminRoster>>;
}

type StaffAdminFetch = typeof fetch;

const ROSTER_PATH = "/api/v1/portal/administration/memberships";

const errorStatus: Readonly<Record<number, readonly StaffAdminFailure[]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  503: ["admin_read_unavailable"],
};

// `authentication_unavailable`: the session is gone and the portal moves the person to the
// session surface; no retry is offered for it.
export const sessionLevelFailures: readonly StaffAdminFailure[] = ["authentication_unavailable"];

function failure<T>(value: StaffAdminFailure): StaffAdminResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOpaqueRef(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 255;
}

function isAwareTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

// The closed wire, checked field by field (this contract is not in the reviewed decision
// registry, so the client proves the shape itself instead of trusting the response): the
// roster answer carries exactly its four keys, each vínculo exactly its seven, and no field
// is allowed to carry anything but the reviewed fact the contract names. Anything else —
// an extra key, a foreign state, a negative revision — is refused, never softened.
const exactRosterKeys = ["schema_version", "generated_at", "count", "vinculos"] as const;
const exactVinculoKeys = [
  "principal_ref",
  "membership_revision",
  "memberships",
  "subject_bindings",
  "reviewed_until",
  "revoked",
  "state",
] as const;
const states = ["active", "expired", "revoked"] as const;

function validateVinculo(value: unknown): StaffAdminVinculo | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  if (keys.length !== exactVinculoKeys.length || !exactVinculoKeys.every((key) => keys.includes(key))) {
    return null;
  }
  if (
    !isOpaqueRef(value.principal_ref) ||
    typeof value.membership_revision !== "number" ||
    !Number.isInteger(value.membership_revision) ||
    value.membership_revision < 0 ||
    !Array.isArray(value.memberships) ||
    !Array.isArray(value.subject_bindings) ||
    !isAwareTimestamp(value.reviewed_until) ||
    typeof value.revoked !== "boolean" ||
    !states.includes(value.state as (typeof states)[number])
  ) {
    return null;
  }
  for (const membership of value.memberships) {
    if (
      !isRecord(membership) ||
      !isOpaqueRef(membership.membership_ref) ||
      !Array.isArray(membership.roles) ||
      !membership.roles.every(isOpaqueRef) ||
      !Array.isArray(membership.groups) ||
      !membership.groups.every(isOpaqueRef)
    ) {
      return null;
    }
  }
  for (const binding of value.subject_bindings) {
    if (
      !isRecord(binding) ||
      !["beneficiary", "provider", "vendor"].includes(binding.kind as string) ||
      !isOpaqueRef(binding.resource_ref)
    ) {
      return null;
    }
  }
  return value as StaffAdminVinculo;
}

export function validateStaffAdminRoster(value: unknown): StaffAdminRoster | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value).sort();
  if (keys.length !== exactRosterKeys.length || !exactRosterKeys.every((key) => keys.includes(key))) {
    return null;
  }
  if (
    value.schema_version !== 1 ||
    !isAwareTimestamp(value.generated_at) ||
    typeof value.count !== "number" ||
    !Number.isInteger(value.count) ||
    value.count < 0 ||
    !Array.isArray(value.vinculos) ||
    value.count !== value.vinculos.length
  ) {
    return null;
  }
  const vinculos: StaffAdminVinculo[] = [];
  let previous: string | null = null;
  for (const candidate of value.vinculos) {
    const vinculo = validateVinculo(candidate);
    if (vinculo === null) return null;
    // The contract orders the roster by principal reference: an out-of-order or duplicated
    // answer is a broken source, not a display problem.
    if (previous !== null && vinculo.principal_ref <= previous) return null;
    previous = vinculo.principal_ref;
    vinculos.push(vinculo);
  }
  return value as StaffAdminRoster;
}

async function parseError(
  response: Response,
  signal: AbortSignal,
): Promise<StaffAdminFailure | null> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return null;
  }
  if (
    !isRecord(value) ||
    Object.keys(value).length !== 1 ||
    typeof value.code !== "string"
  ) {
    return null;
  }
  const code = value.code as StaffAdminFailure;
  return errorStatus[response.status]?.includes(code) ? code : null;
}

export function createStaffAdminClient(options: Readonly<{
  fetcher?: StaffAdminFetch;
}> = {}): StaffAdminClient {
  const fetcher = options.fetcher ?? fetch;
  return {
    async readVinculos(signal) {
      let response: Response;
      try {
        response = await fetcher(ROSTER_PATH, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: { Accept: "application/json" },
          signal,
        });
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("admin_read_unavailable");
      }
      if (response.status !== 200) {
        return failure(await parseError(response, signal) ?? "invalid-response");
      }
      let value: unknown;
      try {
        value = await response.json();
      } catch (error) {
        if (aborted(error, signal)) throw error;
        return failure("invalid-response");
      }
      const parsed = validateStaffAdminRoster(value);
      return parsed === null ? failure("invalid-response") : { kind: "success", value: parsed };
    },
  };
}

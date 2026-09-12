import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";

type Schemas = components["schemas"];

export type IntakeCommandObservation = Schemas["IntakeCommandObservation"];
export type IntakeRecoveryPage = Schemas["IntakeRecoveryPage"];
export type IntakeRecoveryFetch = typeof fetch;
export type IntakeRecoveryFailure = Schemas["PortalIntakeError"]["code"] | "invalid-response";

export type IntakeRecoveryResult<T> =
  | Readonly<{ kind: "success"; value: T }>
  | Readonly<{ kind: "failure"; failure: IntakeRecoveryFailure }>;

export interface IntakeRecoveryClient {
  discover(
    signal: AbortSignal,
    cursor?: string,
  ): Promise<IntakeRecoveryResult<IntakeRecoveryPage>>;
  observeCommand(
    commandId: string,
    signal: AbortSignal,
  ): Promise<IntakeRecoveryResult<IntakeCommandObservation>>;
}

const prefix = "/api/v1/portal/intake-recovery";
const errorStatus: Readonly<Record<number, readonly Schemas["PortalIntakeError"]["code"][]>> = {
  400: ["invalid_request"],
  401: ["authentication_unavailable"],
  403: ["operation_forbidden"],
  404: ["resource_unavailable"],
  409: ["conflict"],
  503: ["dependency_unavailable"],
};

function failure<T>(value: IntakeRecoveryFailure): IntakeRecoveryResult<T> {
  return { kind: "failure", failure: value };
}

function aborted(error: unknown, signal: AbortSignal) {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

function validRef(value: unknown) {
  return validWire(value, wireSchema("IntakeRecoveryItem").properties!.command_id);
}

function validatePage(value: unknown): IntakeRecoveryPage | null {
  if (!validWire(value, wireSchema("IntakeRecoveryPage"))) return null;
  const page = value as IntakeRecoveryPage;
  if (page.scope !== "actor_admissions") return null;
  const commandIds = page.items.map((item) => item.command_id);
  const intakeRefs = page.items.map((item) => item.intake_ref);
  return commandIds.length === new Set(commandIds).size &&
    intakeRefs.length === new Set(intakeRefs).size
    ? page
    : null;
}

function validateObservation(value: unknown, commandId: string): IntakeCommandObservation | null {
  if (!validWire(value, wireSchema("IntakeCommandObservation"))) return null;
  const observation = value as IntakeCommandObservation;
  if (observation.command_id !== commandId) return null;
  if (observation.observation === "observed") {
    return observation.intake_ref == null ? null : observation;
  }
  return observation.intake_ref == null ? observation : null;
}

async function parseError(response: Response, signal: AbortSignal) {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return null;
  }
  if (!validWire(value, wireSchema("PortalIntakeError"))) return null;
  const code = (value as Schemas["PortalIntakeError"]).code;
  return errorStatus[response.status]?.includes(code) ? code : null;
}

async function get<T>(
  fetcher: IntakeRecoveryFetch,
  path: string | null,
  signal: AbortSignal,
  validate: (value: unknown) => T | null,
): Promise<IntakeRecoveryResult<T>> {
  if (path === null) return failure("invalid_request");
  let response: Response;
  try {
    response = await fetcher(path, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return failure("dependency_unavailable");
  }
  if (response.status !== 200) {
    const error = await parseError(response, signal);
    return failure(error ?? "invalid-response");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (aborted(error, signal)) throw error;
    return failure("invalid-response");
  }
  const parsed = validate(value);
  return parsed === null ? failure("invalid-response") : { kind: "success", value: parsed };
}

export function createIntakeRecoveryClient(options: Readonly<{
  fetcher?: IntakeRecoveryFetch;
}> = {}): IntakeRecoveryClient {
  const fetcher = options.fetcher ?? fetch;
  return {
    discover(signal, cursor) {
      if (cursor !== undefined && !validRef(cursor)) {
        return Promise.resolve(failure("invalid_request"));
      }
      const path = cursor === undefined ? prefix : `${prefix}?cursor=${encodeURIComponent(cursor)}`;
      return get(fetcher, path, signal, validatePage);
    },
    observeCommand(commandId, signal) {
      const path = validRef(commandId)
        ? `${prefix}/commands/${encodeURIComponent(commandId)}`
        : null;
      return get(fetcher, path, signal, (value) => validateObservation(value, commandId));
    },
  };
}

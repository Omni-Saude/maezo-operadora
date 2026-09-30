import { validWire, wireSchema } from "./decisionSchema";
import type { components } from "./generated/api";

type Schemas = components["schemas"];

// INTERIM (DL-0050). The reason, severity, priority, deadlines and hand-off summary of an
// escalation case. The wire shape is READ FROM the reviewed OpenAPI artifact, never retyped: a
// field the server adds without a reviewed contract change makes the response invalid here
// instead of quietly appearing on a screen a person is reading.
export type TaskContext = Schemas["TaskContextResponse"];
export type ContextFailure = Schemas["PortalContextError"]["code"] | "invalid-response";
export type ContextResult = { kind: "success"; value: TaskContext } | { kind: ContextFailure };

const prefix = "/api/v1/portal/tasks";
const responseSchema = wireSchema("TaskContextResponse");
const errorSchema = wireSchema("PortalContextError");

const statusCodes: Readonly<Record<number, Schemas["PortalContextError"]["code"]>> = {
  400: "invalid_request",
  401: "session_unavailable",
  403: "employee_access_required",
  404: "resource_unavailable",
  409: "refresh_required",
  501: "context_unavailable",
  503: "read_dependency_unavailable",
};

// Mirrors the server's `OpaqueRef`: no whitespace, control characters or URL delimiters.
function isOpaqueRef(value: string): boolean {
  return value.length > 0 && value.length <= 512 && !/[\s\u0000-\u001f\u007f/?#]/u.test(value);
}

export async function readTaskContext(taskId: string, signal: AbortSignal): Promise<ContextResult> {
  if (!isOpaqueRef(taskId)) return { kind: "invalid_request" };

  let response: Response;
  try {
    response = await fetch(`${prefix}/${encodeURIComponent(taskId)}/context`, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { kind: "read_dependency_unavailable" };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: "invalid-response" };
  }

  if (response.status === 200) {
    if (!validWire(payload, responseSchema)) return { kind: "invalid-response" };
    const value = payload as TaskContext;
    // A reply about another task is not a context this screen may render.
    return value.task_id === taskId ? { kind: "success", value } : { kind: "invalid-response" };
  }

  const code = statusCodes[response.status];
  if (code !== undefined && validWire(payload, errorSchema) && (payload as { code: string }).code === code) {
    return { kind: code };
  }
  return { kind: "invalid-response" };
}

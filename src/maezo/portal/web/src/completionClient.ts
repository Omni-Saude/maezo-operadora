import type { components } from "./generated/api";
import { validWire, wireSchema } from "./decisionSchema";

type Schemas = components["schemas"];

export type CompletionOutcome = Schemas["TaskCompletionSubmission"]["resultado"];
export type CompletionSubmission = Schemas["TaskCompletionSubmission"];
export type CompletionResponse = Schemas["TaskCompletionResponse"];
export type CompletionFailure =
  | Schemas["PortalCompletionError"]["code"]
  | "invalid-response"
  | "outcome-unknown";
export type CompletionResult =
  | { kind: "success"; value: CompletionResponse }
  | { kind: CompletionFailure };

const prefix = "/api/v1/portal";
const submissionSchema = wireSchema("TaskCompletionSubmission");

// The three outcomes and the note bound are READ FROM the reviewed OpenAPI artifact, never
// retyped here. `TaskCompletionSubmission.resultado` derives from `EscalationDecisionInputs`,
// which derives from the BPMN; a literal in this file would be a fourth copy of a contract that
// already fails at import time when its copies diverge.
export const completionOutcomes = Object.freeze(
  (submissionSchema.properties?.resultado?.enum ?? []) as readonly CompletionOutcome[],
);
export const maxNotes: number = submissionSchema.properties?.notas_resolucao?.maxLength ?? 0;

const errorStatus: Readonly<Record<number, readonly CompletionFailure[]>> = {
  400: ["invalid_request"],
  401: ["session_unavailable"],
  403: ["employee_access_required"],
  404: ["resource_unavailable"],
  409: ["revision_conflict"],
  422: ["invalid_completion"],
  501: ["completion_unavailable"],
  503: ["completion_dependency_unavailable"],
};

export function validCompletionSubmission(value: unknown): value is CompletionSubmission {
  return validWire(value, submissionSchema);
}

function validateResponse(value: unknown, taskId: string, submission: CompletionSubmission): CompletionResponse | null {
  if (!validWire(value, wireSchema("TaskCompletionResponse"))) return null;
  const completion = value as CompletionResponse;
  // The server owns the decision; the browser only refuses a reply that is not about what it
  // sent. A reply naming another task or another outcome is not a success it may render.
  return completion.schema === "portal-task-completion.v1" &&
    completion.task_id === taskId &&
    completion.state === "completed" &&
    completion.resultado === submission.resultado
    ? completion
    : null;
}

export async function completeTask(
  taskId: string,
  submission: CompletionSubmission,
  csrfToken: string,
  signal: AbortSignal,
): Promise<CompletionResult> {
  if (!taskId || !csrfToken || !validCompletionSubmission(submission)) {
    return { kind: "invalid_request" };
  }

  let response: Response;
  try {
    response = await fetch(`${prefix}/tasks/${encodeURIComponent(taskId)}/completion`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(submission),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    // The request left this browser. A transport failure is not proof the task stayed open.
    return { kind: "outcome-unknown" };
  }

  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (response.headers.get("Content-Type")?.split(";")[0].trim() !== "application/json") {
    return { kind: "outcome-unknown" };
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return { kind: "outcome-unknown" };
  }
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");

  if (response.status === 200) {
    const parsed = validateResponse(value, taskId, submission);
    return parsed === null ? { kind: "outcome-unknown" } : { kind: "success", value: parsed };
  }

  if (validWire(value, wireSchema("PortalCompletionError"))) {
    const code = (value as Schemas["PortalCompletionError"]).code;
    if (errorStatus[response.status]?.includes(code)) {
      // DL-0049: the interim path commits `portal_direct_completion.intent` BEFORE the effect,
      // and the engine's completion REST accepts no expected revision. A dependency that did
      // not answer is therefore NOT proof that the task stayed open — the same discipline the
      // assignment client applies to `admission_unavailable`.
      return { kind: code === "completion_dependency_unavailable" ? "outcome-unknown" : code };
    }
  }
  return { kind: "outcome-unknown" };
}

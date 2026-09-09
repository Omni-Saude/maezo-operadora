import type { components } from "./generated/api";
import { taskReadBindings, taskReadInputs } from "./taskReadBindings";
import { timestampMicroseconds } from "./taskReadTime";

const TASKS_PATH = "/api/v1/portal/tasks";
const PAGE_LIMIT = "25";

type Schemas = components["schemas"];

export type QueueName = Schemas["TaskQueuePage"]["queue"];
export type TaskQueuePage = Schemas["TaskQueuePage"];
export type TaskQueueItem = Schemas["TaskQueueItem"];
export type PublicTaskSnapshot = Schemas["PublicTaskSnapshot"];
export type TaskReadResponse = Schemas["TaskReadResponse"];
export type PortalReadError = Schemas["PortalReadError"];
export type ReadErrorCode = PortalReadError["code"];
export type TaskReadResult<T> =
  | { kind: "success"; value: T }
  | { kind: ReadErrorCode | "invalid-response" };

const statusCodes = {
  400: "invalid_request",
  401: "session_unavailable",
  403: "employee_access_required",
  404: "resource_unavailable",
  409: "refresh_required",
  503: "read_dependency_unavailable",
} as const satisfies Record<number, ReadErrorCode>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && expected.every((key, index) => key === actual[index]);
}

function hasOnlyKeys(
  value: unknown,
  required: readonly string[],
  optional: readonly string[],
): value is Record<string, unknown> {
  if (!isRecord(value) || !required.every((key) => key in value)) return false;
  const known = new Set([...required, ...optional]);
  return Object.keys(value).every((key) => known.has(key));
}

function isOpaqueRef(value: unknown): value is string {
  return (
    typeof value === "string" &&
    [...value].length >= 1 &&
    [...value].length <= 512 &&
    // Q1 uses Unicode scalar length and Unicode White_Space, not UTF-16 units.
    !/[\p{White_Space}\u0000-\u001f\u007f/?#\ud800-\udfff]/u.test(value)
  );
}

function matchesWhole(pattern: RegExp, value: string): boolean {
  return pattern.exec(value)?.[0] === value;
}

function isOpaqueCursor(value: unknown): value is string {
  return typeof value === "string" && value.length <= 2048 && matchesWhole(/^[A-Za-z0-9_-]+$/, value);
}

function isAwareTimestamp(value: unknown): value is string {
  return typeof value === "string" && timestampMicroseconds(value) !== null;
}

function compareCodepoints(left: string, right: string): number {
  const a = [...left], b = [...right];
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) {
    const difference = a[i].codePointAt(0)! - b[i].codePointAt(0)!;
    if (difference !== 0) return difference;
  }
  return a.length - b.length;
}

function isRevision(value: unknown): value is string {
  return typeof value === "string" && matchesWhole(/^(0|[1-9][0-9]*)$/, value);
}

function isVersion(value: unknown): value is string {
  return typeof value === "string" && matchesWhole(/^[1-9][0-9]*$/, value);
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && matchesWhole(/^[0-9a-f]{64}$/, value);
}

function isFreshness(value: unknown): value is Schemas["QueueFreshness"] {
  if (
    !hasExactKeys(value, [
      "state",
      "observed_at",
      "source_observed_at",
      "valid_until",
      "refresh_after_seconds",
    ]) ||
    value.state !== "current" ||
    value.refresh_after_seconds !== 10 ||
    !isAwareTimestamp(value.observed_at) ||
    !isAwareTimestamp(value.source_observed_at) ||
    !isAwareTimestamp(value.valid_until)
  ) {
    return false;
  }
  return (
    timestampMicroseconds(value.source_observed_at)! <= timestampMicroseconds(value.observed_at)! &&
    timestampMicroseconds(value.observed_at)! < timestampMicroseconds(value.valid_until)!
  );
}

function isQueueItem(value: unknown): value is TaskQueueItem {
  return (
    hasExactKeys(value, [
      "task_id",
      "process_definition_key",
      "task_definition_key",
      "task_revision",
      "ownership",
      "engine_due_at",
      "snapshot_at",
    ]) &&
    isOpaqueRef(value.task_id) &&
    isOpaqueRef(value.process_definition_key) &&
    isOpaqueRef(value.task_definition_key) &&
    isRevision(value.task_revision) &&
    ["unassigned", "self", "other"].includes(String(value.ownership)) &&
    (value.engine_due_at === null || isAwareTimestamp(value.engine_due_at)) &&
    isAwareTimestamp(value.snapshot_at)
  );
}

export function validateTaskQueuePage(value: unknown, queue: QueueName): TaskQueuePage | null {
  if (
    !hasExactKeys(value, ["schema", "queue", "items", "next_cursor", "freshness"]) ||
    value.schema !== "portal-task-queue.v1" ||
    value.queue !== queue ||
    !Array.isArray(value.items) ||
    value.items.length > 100 ||
    !value.items.every(isQueueItem) ||
    !(value.next_cursor === null || isOpaqueCursor(value.next_cursor)) ||
    (value.items.length === 0 && value.next_cursor !== null) ||
    !isFreshness(value.freshness)
  ) {
    return null;
  }
  const ids = value.items.map((item) => item.task_id);
  if (new Set(ids).size !== ids.length || ids.some((id, index) => index > 0 && compareCodepoints(ids[index - 1], id) > 0)) {
    return null;
  }
  return value as TaskQueuePage;
}

function isPagtoEvidence(value: unknown): value is Schemas["PagtoAdmissibilityEvidence"] {
  if (
    !hasOnlyKeys(
      value,
      [
        "kind",
        "valor_pagamento_cents",
        "dados_pagamento_validos",
        "lastro_confirmado",
        "duplicidade_suspeita",
      ],
      ["lastro_decisor_id", "lastro_origem"],
    ) ||
    value.kind !== "pagto_admissibilidade" ||
    typeof value.valor_pagamento_cents !== "string" ||
    !matchesWhole(/^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/, value.valor_pagamento_cents) ||
    typeof value.dados_pagamento_validos !== "boolean" ||
    typeof value.lastro_confirmado !== "boolean" ||
    typeof value.duplicidade_suspeita !== "boolean"
  ) {
    return false;
  }
  if (
    "lastro_decisor_id" in value &&
    value.lastro_decisor_id !== null &&
    value.lastro_decisor_id !== "" &&
    !isOpaqueRef(value.lastro_decisor_id)
  ) {
    return false;
  }
  return (
    !("lastro_origem" in value) ||
    value.lastro_origem === null ||
    [
      "contas_adjudicacao_automatica",
      "contas_adjudicacao_humana",
      "recurso_deferimento_humano",
    ].includes(String(value.lastro_origem))
  );
}

function isPublicTaskSnapshot(value: unknown): value is PublicTaskSnapshot {
  if (
    !hasExactKeys(value, [
      "schema_version",
      "snapshot_at",
      "task_id",
      "process_definition_key",
      "process_definition_version",
      "process_definition_id",
      "process_definition_digest",
      "task_definition_key",
      "form_key",
      "form_version",
      "form_digest",
      "form_source_status",
      "task_revision",
      "assignee_ref",
      "eligible_candidate_groups",
      "evidence_revision",
      "evidence_digest",
      "engine_due_at",
      "allowed_actions",
      "allowed_inputs",
      "read_only_evidence",
    ]) ||
    value.schema_version !== 1 ||
    !isAwareTimestamp(value.snapshot_at) ||
    !isOpaqueRef(value.task_id) ||
    !isOpaqueRef(value.process_definition_key) ||
    !isVersion(value.process_definition_version) ||
    !isOpaqueRef(value.process_definition_id) ||
    !isDigest(value.process_definition_digest) ||
    !isOpaqueRef(value.task_definition_key) ||
    typeof value.form_key !== "string" ||
    !isVersion(value.form_version) ||
    !isDigest(value.form_digest) ||
    !["BPMN_FORMDATA", "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY"].includes(
      String(value.form_source_status),
    ) ||
    !isRevision(value.task_revision) ||
    !(value.assignee_ref === null || isOpaqueRef(value.assignee_ref)) ||
    !Array.isArray(value.eligible_candidate_groups) ||
    !value.eligible_candidate_groups.every(isOpaqueRef) ||
    !isRevision(value.evidence_revision) ||
    !isDigest(value.evidence_digest) ||
    !(value.engine_due_at === null || isAwareTimestamp(value.engine_due_at)) ||
    !Array.isArray(value.allowed_actions) ||
    value.allowed_actions.length !== 0 ||
    !Array.isArray(value.allowed_inputs)
  ) {
    return false;
  }
  const binding = taskReadBindings.find((row) =>
    row.process === value.process_definition_key && row.task === value.task_definition_key);
  if (binding === undefined || binding.form !== value.form_key || binding.source !== value.form_source_status) return false;
  const inputs = taskReadInputs[binding.form];
  if (value.allowed_inputs.length !== inputs.length || new Set(value.allowed_inputs).size !== inputs.length ||
      !inputs.every((field) => (value.allowed_inputs as unknown[]).includes(field))) return false;
  return value.form_key === "pagto_admissibilidade"
    ? isPagtoEvidence(value.read_only_evidence)
    : value.read_only_evidence === null;
}

export function validateTaskReadResponse(value: unknown): TaskReadResponse | null {
  if (
    !hasExactKeys(value, ["schema", "task", "freshness"]) ||
    value.schema !== "portal-task-read.v1" ||
    !isPublicTaskSnapshot(value.task) ||
    !isFreshness(value.freshness)
  ) {
    return null;
  }
  return value as TaskReadResponse;
}

function isPortalReadError(value: unknown, code: ReadErrorCode): value is PortalReadError {
  return (
    hasExactKeys(value, ["schema", "code"]) &&
    value.schema === "portal-read-error.v1" &&
    value.code === code
  );
}

async function request<T>(
  path: string,
  signal: AbortSignal,
  validate: (value: unknown) => T | null,
): Promise<TaskReadResult<T>> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
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
    const value = validate(payload);
    return value === null ? { kind: "invalid-response" } : { kind: "success", value };
  }
  const code = statusCodes[response.status as keyof typeof statusCodes];
  return code !== undefined && isPortalReadError(payload, code)
    ? { kind: code }
    : { kind: "invalid-response" };
}

export function listTaskQueue(
  queue: QueueName,
  cursor: string | null,
  signal: AbortSignal,
): Promise<TaskReadResult<TaskQueuePage>> {
  const params = new URLSearchParams({ queue, limit: PAGE_LIMIT });
  if (cursor !== null) params.set("cursor", cursor);
  return request(`${TASKS_PATH}?${params.toString()}`, signal, (value) =>
    validateTaskQueuePage(value, queue),
  );
}

export function readTask(
  taskId: string,
  signal: AbortSignal,
): Promise<TaskReadResult<TaskReadResponse>> {
  if (!isOpaqueRef(taskId)) return Promise.resolve({ kind: "invalid_request" });
  return request(`${TASKS_PATH}/${encodeURIComponent(taskId)}`, signal, (value) => {
    const parsed = validateTaskReadResponse(value);
    return parsed?.task.task_id === taskId ? parsed : null;
  });
}

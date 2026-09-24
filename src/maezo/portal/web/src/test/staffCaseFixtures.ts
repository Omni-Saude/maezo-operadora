import type { StaffDetail, StaffPage } from "../staffCaseClient";

// Wire-shaped staff case bodies, exactly as GET /api/v1/portal/cases[/{ref}]
// returns them. Times are anchored to `now` so the 10 s validity window the
// client enforces is still open when a test (or a screenshot) reads them.

export function wireInstant(ms: number) {
  return new Date(ms).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");
}

export const staffCaseRefs = [
  "aut_2026_0917_hmx4q8rt",
  "aut_2026_0918_kd02nzpa",
  "aut_2026_0920_w7c1bv3e",
] as const;

export function staffCasePage(
  now = Date.now(),
  refs: readonly string[] = staffCaseRefs,
  nextCursor: string | null = null,
): StaffPage {
  return {
    schema: "portal-staff-case-page.v1",
    items: refs.map((case_ref, index) => ({
      case_ref,
      kind: "authorization",
      state: index === refs.length - 1 && refs.length > 1 ? "ended" : "active",
      record_revision: String(4 + index * 3),
      state_observed_at: wireInstant(now - 1_000 - index * 3_600_000),
    })),
    next_cursor: nextCursor,
    freshness: {
      observed_at: wireInstant(now),
      source_observed_at: wireInstant(now - 1_000),
      valid_until: wireInstant(now + 10_000),
      refresh_after_seconds: 10,
    },
  };
}

export function staffCaseDetail(
  caseRef: string = staffCaseRefs[0],
  now = Date.now(),
): StaffDetail {
  return {
    schema: "portal-staff-case-detail.v1",
    case: {
      case_ref: caseRef,
      kind: "authorization",
      state: "active",
      record_revision: "4",
      state_observed_at: wireInstant(now - 1_000),
    },
    identity: {
      upstream_resource_key: "guia-opaca-7731",
      case_ref: caseRef,
      process_instance_ref: "pi-5f2c9a41",
      process_definition_id: "SP-OP-AUTH-001:3:def-91ab",
      process_definition_key: "SP-OP-AUTH-001",
      process_definition_version: "3",
      process_definition_digest: "c".repeat(64),
      kind: "authorization",
    },
    active_tasks: [
      {
        task_id: "task-01-auditoria",
        task_definition_key: "UT_AnaliseMedicoAuditor",
        task_revision: "2",
        created_at: wireInstant(now - 26 * 3_600_000),
        due_at: wireInstant(now - 2 * 3_600_000),
        assignee_ref: "colab-ana.r",
      },
      {
        task_id: "task-02-documentos",
        task_definition_key: "UT_ConferenciaDocumental",
        task_revision: "1",
        created_at: wireInstant(now - 3 * 3_600_000),
        due_at: wireInstant(now + 21 * 3_600_000),
        assignee_ref: null,
      },
    ],
    next_task_cursor: null,
    tasks_complete: true,
    freshness: {
      observed_at: wireInstant(now),
      source_observed_at: wireInstant(now - 1_000),
      valid_until: wireInstant(now + 10_000),
      refresh_after_seconds: 10,
    },
    outcome: null,
  };
}

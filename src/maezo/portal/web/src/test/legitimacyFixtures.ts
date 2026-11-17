import { vi } from "vitest";

import type { LegitimacyChecklist, LegitimacyRecord } from "../legitimacyScreenClient";
import manifest from "./legitimacyChecklist.fixture.json";

// E-SF-20 (EP-CORE-15a): the deterministic fake transport and the SERVER manifest.
// `legitimacyChecklist.fixture.json` is the `CHECKLIST` the BFF serves, byte-pinned against
// `maezo.portal.api.legitimacy_screen.CHECKLIST` by
// `tests/unit/portal/test_esf20_legitimacy_panel_fixture.py` — a label drift on either side
// fails one of the two suites.

export function serverChecklist(): LegitimacyChecklist {
  return structuredClone(manifest) as LegitimacyChecklist;
}

export type Call = Readonly<{ method: string; path: string; body: unknown }>;

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function fakeTransport(answer: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      method: init?.method ?? "GET",
      path: String(input),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    };
    calls.push(call);
    return answer(call);
  });
  return { calls, fetcher: fetcher as unknown as typeof fetch };
}

export function screenRecord(overrides: Partial<LegitimacyRecord> = {}): LegitimacyRecord {
  return {
    schema_version: "legitimacy-screen-record.v1",
    operation: "legitimacy_screen",
    tenant: "tenant-a",
    screen_ref: "scr-0123456789abcdef0123456789abcdef",
    screen_revision: 1,
    legal_entity_ref: "pj-ref-1",
    opportunity_ref: "op-ref-1",
    contract_kind: "empresarial",
    contracting_form: "direta",
    criteria: [
      {
        criterion_id: "vinculo_beneficiario_contratante",
        result: "proven",
        norm_anchor: "RN 557/2022, art. 5º",
        evidence_refs: [],
      },
    ],
    status: "valid",
    would_block: false,
    art39_signaled: false,
    art39_anchor: null,
    reconfirmation_due_at: null,
    created_by: "staff-ref-1",
    created_at: "2026-11-17T10:00:00Z",
    updated_at: "2026-11-17T10:00:00Z",
    ...overrides,
  };
}

export const posts = (calls: readonly Call[]) => calls.filter((call) => call.method !== "GET");

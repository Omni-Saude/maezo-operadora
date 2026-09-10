import document from "../phi-openapi.json";
import type { components } from "./generated/phiApi";
import { validWire, type Schema } from "./decisionSchema";
import { isCurrent, timestampMicroseconds } from "./taskReadTime";

type Schemas = components["schemas"];
export type CommunicationContent = Schemas["CommunicationContent"];
export type ContentFailure = Schemas["PortalIntakeError"]["code"] | "invalid-response";
export type ContentResult =
  | Readonly<{ kind: "success"; value: CommunicationContent }>
  | Readonly<{ kind: "failure"; failure: ContentFailure }>;
export interface PhiCommunicationClient {
  read(caseRef: string, communicationRef: string, signal: AbortSignal): Promise<ContentResult>;
}
// These two generated PHI schemas are closed and contain no references into General.
const contentSchema: Schema = document.components.schemas.CommunicationContent;
const errorSchema: Schema = document.components.schemas.PortalIntakeError;
const errors: Readonly<Record<number, Schemas["PortalIntakeError"]["code"]>> = {
  400: "invalid_request", 401: "authentication_unavailable", 403: "operation_forbidden",
  404: "resource_unavailable", 409: "conflict", 503: "dependency_unavailable",
};
const failed = (failure: ContentFailure): ContentResult => ({ kind: "failure", failure });
function canonical(value: string) {
  return /\.\d{6}Z$/.test(value) && timestampMicroseconds(value) !== null;
}
export function createPhiCommunicationClient(request: typeof fetch = fetch): PhiCommunicationClient {
  return {
    async read(caseRef, communicationRef, signal) {
      const refSchema = contentSchema.properties!.communication_ref;
      if (!validWire(caseRef, refSchema) || !validWire(communicationRef, refSchema)) {
        return failed("invalid_request");
      }
      try {
        signal.throwIfAborted();
        const response = await request(
          `/api/v1/phi/cases/${encodeURIComponent(caseRef)}/communications/${encodeURIComponent(communicationRef)}/content`,
          { method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
            headers: { Accept: "application/json" }, signal },
        );
        signal.throwIfAborted();
        if (response.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
          return failed("invalid-response");
        }
        const raw: unknown = await response.json();
        signal.throwIfAborted();
        if (response.status !== 200) {
          if (!validWire(raw, errorSchema)) return failed("invalid-response");
          const code = (raw as Schemas["PortalIntakeError"]).code;
          return errors[response.status] === code ? failed(code) : failed("invalid-response");
        }
        if (!validWire(raw, contentSchema)) return failed("invalid-response");
        const value = raw as CommunicationContent;
        if (value.communication_ref !== communicationRef || !canonical(value.observed_at) ||
            !canonical(value.valid_until) || !isCurrent(value.valid_until) ||
            timestampMicroseconds(value.observed_at)! >= timestampMicroseconds(value.valid_until)!) {
          return failed("invalid-response");
        }
        return { kind: "success", value };
      } catch (error) {
        if (signal.aborted) throw error;
        return failed("dependency_unavailable");
      }
    },
  };
}

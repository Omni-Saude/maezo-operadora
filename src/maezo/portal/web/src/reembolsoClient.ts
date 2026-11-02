import { validWire, wireSchema } from "./decisionSchema";
import type { components } from "./generated/api";

type Schemas = components["schemas"];

// INTERIM (DL-0163). Os dados FECHADOS do pedido de reembolso e a lista de anexos da tarefa aberta. A forma
// vem do artefato OpenAPI revisado, nunca redigitada: um campo que o servidor acrescente sem contrato revisado
// invalida a resposta em vez de aparecer na tela de quem decide.
export type ReembolsoContext = Schemas["TaskReembolsoContextResponse"];
export type ReembolsoFailure = Schemas["PortalContextError"]["code"] | "invalid-response";
export type ReembolsoResult = { kind: "success"; value: ReembolsoContext } | { kind: ReembolsoFailure };

const prefix = "/api/v1/portal/tasks";
const responseSchema = wireSchema("TaskReembolsoContextResponse");
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

function isOpaqueRef(value: string): boolean {
  return value.length > 0 && value.length <= 512 && !/[\s\u0000-\u001f\u007f/?#]/u.test(value);
}

export async function readReembolso(taskId: string, signal: AbortSignal): Promise<ReembolsoResult> {
  if (!isOpaqueRef(taskId)) return { kind: "invalid_request" };
  let response: Response;
  try {
    response = await fetch(`${prefix}/${encodeURIComponent(taskId)}/reembolso`, {
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
    const value = payload as ReembolsoContext;
    return value.task_id === taskId ? { kind: "success", value } : { kind: "invalid-response" };
  }
  const code = statusCodes[response.status];
  if (code !== undefined && validWire(payload, errorSchema) && (payload as { code: string }).code === code) {
    return { kind: code };
  }
  return { kind: "invalid-response" };
}

//: O endereco do anexo (mesmo origin, cookie da sessao). A referencia vem do contexto validado acima.
export function anexoUrl(taskId: string, documentRef: string): string | null {
  if (!isOpaqueRef(taskId) || !/^rdoc_[0-9a-f]{32}$/u.test(documentRef)) return null;
  return `${prefix}/${encodeURIComponent(taskId)}/reembolso/anexos/${documentRef}`;
}

//: Centavos (string decimal canonica) -> "R$ 1.234,56", sem passar por float.
export function formatarCentavos(valor: string | null | undefined): string {
  if (!valor || !/^(0|[1-9][0-9]*)$/u.test(valor)) return "—";
  const inteiro = valor.length > 2 ? valor.slice(0, -2) : "0";
  const centavos = valor.padStart(3, "0").slice(-2);
  return `R$ ${inteiro.replace(/\B(?=(\d{3})+(?!\d))/gu, ".")},${centavos}`;
}

//: "12,34" / "1.234,56" / "100" -> "1234"... em centavos (string decimal canonica) ou null.
export function lerReais(texto: string): string | null {
  const limpo = texto.trim().replace(/^R\$\s*/u, "");
  const m = /^(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?$/u.exec(limpo);
  if (!m) return null;
  const reais = m[1].replace(/\./gu, "").replace(/^0+(?=\d)/u, "");
  const centavos = (m[2] ?? "0").padEnd(2, "0");
  const total = `${reais}${centavos}`.replace(/^0+(?=\d)/u, "");
  return total === "0" ? null : total;
}

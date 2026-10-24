import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { StaffDocumentsPanel } from "./StaffDocumentsPanel";
import type {
  StaffDocumentPage,
  StaffDocumentRequestPage,
  StaffDocumentsClient,
  StaffDocumentsResult,
} from "./staffDocumentsClient";

const caseRef = "case_staff_abcdefghijklmnop";

// O dublê é digitado pela própria interface do cliente: só os dois métodos de leitura
// existem, então o painel não tem como disparar mutação — a prova é de tipo e de chamada.
function client(
  documents: StaffDocumentsResult<StaffDocumentPage>,
  requests: StaffDocumentsResult<StaffDocumentRequestPage>,
): StaffDocumentsClient {
  return {
    listDocuments: vi.fn(() => Promise.resolve(documents)),
    listDocumentRequests: vi.fn(() => Promise.resolve(requests)),
  };
}

function verifiedPage(): StaffDocumentPage {
  return {
    case_ref: caseRef,
    documents: [{
      document_ref: "document_ref_0000000001",
      document_type_ref: "documento_tipo_ref_0001",
      disposition: "verified",
    }],
  };
}

function openRequests(): StaffDocumentRequestPage {
  return {
    case_ref: caseRef,
    requests: [{
      request_ref: "request_ref_0000000001",
      revision: "3",
      disposition: "open",
      policy_ref: "policy_ref_0000000001",
      requested_document_type_refs: ["documento_tipo_ref_0001"],
    }],
  };
}

async function consult(user: ReturnType<typeof userEvent.setup>, service: StaffDocumentsClient) {
  render(<StaffDocumentsPanel client={service} onSessionUnavailable={vi.fn()} />);
  await user.type(screen.getByLabelText("Consultar pela referência exata do caso"), caseRef);
  await user.click(screen.getByRole("button", { name: "Consultar documentos" }));
}

afterEach(() => {
  vi.clearAllMocks();
});

it("consulta ambas as leituras e exibe documentos e pedidos do caso", async () => {
  const user = userEvent.setup();
  const service = client({ kind: "success", value: verifiedPage() }, { kind: "success", value: openRequests() });
  await consult(user, service);

  expect(service.listDocuments).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal));
  expect(service.listDocumentRequests).toHaveBeenCalledWith(caseRef, expect.any(AbortSignal));
  expect(screen.getByRole("heading", { name: "Documentos do caso" })).toBeInTheDocument();
  expect(await screen.findByText("document_ref_0000000001")).toBeInTheDocument();
  expect(screen.getByText("request_ref_0000000001")).toBeInTheDocument();
  expect(screen.getByText("Aberto")).toBeInTheDocument();
  expect(screen.getByText("Somente leitura. O envio de documentos ainda não está disponível nesta versão do portal.")).toBeInTheDocument();
});

it("não oferece envio, resposta ou acesso a conteúdo", async () => {
  const user = userEvent.setup();
  const service = client({ kind: "success", value: verifiedPage() }, { kind: "success", value: openRequests() });
  await consult(user, service);
  expect(screen.queryByRole("button", { name: /enviar|upload|responder|baixar/i })).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  // A tabela de verificados só tem a coluna de situação; nenhum botão por linha.
  expect(
    screen.getByRole("table", { name: "Documentos com custódia verificada neste caso" }),
  ).toHaveTextContent("Verificado");
});

it("exibe erro fechado sem quebrar e oferece repetir", async () => {
  const user = userEvent.setup();
  const service = client(
    { kind: "failure", failure: "operation_forbidden" },
    { kind: "success", value: { case_ref: caseRef, requests: [] } },
  );
  await consult(user, service);
  expect(screen.getByRole("heading", { name: "Não foi possível consultar os documentos" })).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Sua sessão não tem permissão para ver os documentos deste caso. Peça o vínculo ao administrador do portal.",
  );
  expect(screen.getByRole("button", { name: "Consultar novamente" })).toBeInTheDocument();
});

it("encaminha sessão indisponível sem oferecer repetir", async () => {
  const user = userEvent.setup();
  const onSessionUnavailable = vi.fn();
  render(
    <StaffDocumentsPanel
      client={client(
        { kind: "failure", failure: "authentication_unavailable" },
        { kind: "failure", failure: "authentication_unavailable" },
      )}
      onSessionUnavailable={onSessionUnavailable}
    />,
  );
  await user.type(screen.getByLabelText("Consultar pela referência exata do caso"), caseRef);
  await user.click(screen.getByRole("button", { name: "Consultar documentos" }));
  expect(onSessionUnavailable).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Consultar novamente" })).toBeNull();
});

it("exibe os estados vazios dos dois conjuntos", async () => {
  const user = userEvent.setup();
  const service = client(
    { kind: "success", value: { case_ref: caseRef, documents: [] } },
    { kind: "success", value: { case_ref: caseRef, requests: [] } },
  );
  await consult(user, service);
  expect(screen.getByText("Nenhum documento verificado neste caso.")).toBeInTheDocument();
  expect(screen.getByText("Nenhum pedido de documento para este caso.")).toBeInTheDocument();
});

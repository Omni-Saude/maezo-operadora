import { useEffect, useId, useRef, useState } from "react";
import { completeTask, type CompletionResult, type ReembolsoSubmission } from "./completionClient";
import { anexoUrl, formatarCentavos, lerReais, readReembolso, type ReembolsoContext } from "./reembolsoClient";

// INTERIM (DL-0163). A tarefa do reembolso no portal: os dados FECHADOS do pedido, os anexos e o formulario
// da tarefa (analise/coordenacao, auditor, pendencia expirada, registro do pagamento). Quem decide e' a
// pessoa; a tela so' monta o corpo fechado que o servidor revalida pelo contrato canonico.

const rotulos: Readonly<Record<string, string>> = {
  titular: "Titular",
  dependente: "Dependente",
  consulta: "Consulta",
  exame: "Exame",
  terapia: "Terapia",
  livre_escolha: "Livre escolha",
  fora_rede: "Fora da rede",
  urgencia_emergencia: "Urgência/emergência",
  indisponibilidade_rede: "Não havia prestador na rede",
  urgencia: "Urgência",
  sem_prestador_no_prazo: "Sem prestador no prazo",
  preferencia_fora_rede: "Preferiu outro profissional",
  pedido_medico: "Pedido médico",
  nota_fiscal: "Nota fiscal",
  complemento: "Arquivo da resposta",
  DENTRO: "Dentro do prazo",
  FORA: "Fora do prazo",
  NAO_AVALIADO: "Prazo não avaliado",
  APROVAR: "Aprovar",
  APROVAR_PARCIAL: "Aprovar parcial",
  NEGAR: "Negar",
  SOLICITAR_INFO: "Pedir mais informações",
  SOLICITAR_AUDITOR: "Escalar ao médico auditor",
  cancelar_solicitacao: "Cancelar a solicitação",
  conceder_prazo_extra: "Conceder prazo extra",
  seguir_analise: "Seguir para a análise",
};

const decisoesAnalista = ["APROVAR", "APROVAR_PARCIAL", "NEGAR", "SOLICITAR_INFO", "SOLICITAR_AUDITOR"] as const;
const decisoesAuditor = ["APROVAR", "APROVAR_PARCIAL", "NEGAR"] as const;
const pendencias = ["cancelar_solicitacao", "conceder_prazo_extra", "seguir_analise"] as const;
const MAX_TEXTO = 2000;

function rotulo(valor: string | null | undefined): string {
  return valor ? (rotulos[valor] ?? valor) : "—";
}

function dataBr(iso: string | null | undefined): string {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/u.test(iso)) return "—";
  const [ano, mes, dia] = iso.split("-");
  return `${dia}/${mes}/${ano}`;
}

type Estado =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "completed" }
  | { kind: "error"; message: string };

function mensagemDeFalha(result: CompletionResult): string {
  switch (result.kind) {
    case "invalid_request":
    case "invalid_completion":
      return "Confira os campos: faltou algum dado obrigatório para essa decisão.";
    case "notes_too_long":
      return "Um dos textos ficou longo demais.";
    case "revision_conflict":
      return "A tarefa mudou enquanto você decidia. Abra de novo.";
    case "employee_access_required":
      return "Seu acesso não permite concluir esta tarefa.";
    case "completion_unavailable":
      return "A conclusão pelo portal está desligada neste ambiente.";
    default:
      return "Não foi possível confirmar a conclusão. Confira a fila antes de tentar de novo.";
  }
}

export function ReembolsoTaskPanel({
  taskId,
  formKey,
  csrfToken,
  sessionBinding,
  onSessionUnavailable,
  onCompleted,
}: {
  taskId: string;
  formKey: string;
  csrfToken?: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
  onCompleted?: () => void;
}) {
  const [contexto, setContexto] = useState<ReembolsoContext | null>(null);
  const [falhaContexto, setFalhaContexto] = useState<string | null>(null);
  const [decisao, setDecisao] = useState("");
  const [valor, setValor] = useState("");
  const [justificativa, setJustificativa] = useState("");
  const [fundamentacao, setFundamentacao] = useState("");
  const [info, setInfo] = useState("");
  const [texto, setTexto] = useState("");
  const [cid10, setCid10] = useState("");
  const [parecer, setParecer] = useState("");
  const [dataPagamento, setDataPagamento] = useState("");
  const [estado, setEstado] = useState<Estado>({ kind: "idle" });
  const controller = useRef<AbortController | null>(null);
  // O callback do pai num ref: o carregamento do contexto so' se repete quando a TAREFA ou a SESSAO mudam.
  const sessaoCaiu = useRef(onSessionUnavailable);
  sessaoCaiu.current = onSessionUnavailable;
  const id = useId();

  useEffect(() => {
    const atual = new AbortController();
    setContexto(null);
    setFalhaContexto(null);
    readReembolso(taskId, atual.signal)
      .then((result) => {
        if (atual.signal.aborted) return;
        if (result.kind === "success") setContexto(result.value);
        else if (result.kind === "session_unavailable") sessaoCaiu.current();
        else setFalhaContexto("Não foi possível carregar os dados do pedido.");
      })
      .catch(() => undefined);
    return () => atual.abort();
  }, [taskId, sessionBinding]);

  useEffect(() => {
    setDecisao("");
    setValor("");
    setJustificativa("");
    setFundamentacao("");
    setInfo("");
    setTexto("");
    setCid10("");
    setParecer("");
    setDataPagamento("");
    setEstado({ kind: "idle" });
  }, [taskId, sessionBinding, formKey]);

  useEffect(() => () => controller.current?.abort(), []);

  const auditor = formKey === "reembolso_auditor";
  const precisaValor = decisao === "APROVAR" || decisao === "APROVAR_PARCIAL";
  const adversa = decisao === "NEGAR" || decisao === "APROVAR_PARCIAL";
  const centavos = precisaValor ? lerReais(valor) : null;
  // Teste do dono (02/11/2026): "Aprovar" paga EXATAMENTE o pedido; para pagar menos, "Aprovar parcial" (com
  // justificativa, fundamentacao e texto). O servidor confere de novo contra o valor do motor.
  const pedidoCentavos = contexto?.valor_solicitado_cents ?? null;
  const menorQue = (a: string, b: string) => (a.length !== b.length ? a.length < b.length : a < b);
  const valorIncoerente =
    centavos !== null &&
    pedidoCentavos !== null &&
    ((decisao === "APROVAR" && centavos !== pedidoCentavos) ||
      (decisao === "APROVAR_PARCIAL" && !menorQue(centavos, pedidoCentavos)));

  function corpo(): ReembolsoSubmission | null {
    const t = (v: string) => v.trim().slice(0, MAX_TEXTO);
    if (formKey === "reembolso_pagamento") {
      return /^\d{4}-\d{2}-\d{2}$/u.test(dataPagamento)
        ? { kind: "reembolso_pagamento", data_pagamento: dataPagamento }
        : null;
    }
    if (formKey === "reembolso_pendencia") {
      return (pendencias as readonly string[]).includes(decisao)
        ? { kind: "reembolso_pendencia", decisao_pendencia: decisao as (typeof pendencias)[number] }
        : null;
    }
    if (!decisao || (precisaValor && (centavos === null || pedidoCentavos === null || valorIncoerente))) return null;
    if (adversa && (!t(justificativa) || !t(fundamentacao) || !t(texto))) return null;
    if (auditor && adversa && (!t(cid10) || !t(parecer))) return null;
    if (decisao === "SOLICITAR_INFO" && !t(info)) return null;
    const comuns = {
      ...(precisaValor && centavos ? { valor_reembolso_aprovado_cents: centavos } : {}),
      ...(adversa ? { justificativa: t(justificativa), fundamentacao_contratual: t(fundamentacao) } : {}),
      ...(adversa ? { texto_ao_beneficiario: t(texto) } : {}),
    };
    if (auditor) {
      return {
        kind: "reembolso_auditor",
        decisao_reembolso: decisao as (typeof decisoesAuditor)[number],
        ...comuns,
        ...(adversa ? { cid10_referencia: t(cid10), parecer_auditor: t(parecer) } : {}),
      };
    }
    return {
      kind: "reembolso_decisao",
      decisao_reembolso: decisao as (typeof decisoesAnalista)[number],
      ...comuns,
      ...(decisao === "SOLICITAR_INFO" ? { info_solicitada: t(info) } : {}),
    };
  }

  const pronto = corpo() !== null && Boolean(csrfToken);

  async function enviar(event: React.FormEvent) {
    event.preventDefault();
    const submission = corpo();
    if (!submission || !csrfToken || estado.kind === "sending" || estado.kind === "completed") return;
    controller.current?.abort();
    const atual = new AbortController();
    controller.current = atual;
    setEstado({ kind: "sending" });
    let result: CompletionResult;
    try {
      result = await completeTask(taskId, submission, csrfToken, atual.signal);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setEstado({ kind: "error", message: "Não foi possível confirmar a conclusão." });
      return;
    }
    if (atual.signal.aborted) return;
    if (result.kind === "success") {
      setEstado({ kind: "completed" });
      onCompleted?.();
    } else if (result.kind === "session_unavailable") {
      onSessionUnavailable();
    } else {
      setEstado({ kind: "error", message: mensagemDeFalha(result) });
    }
  }

  const campoTexto = (rotuloCampo: string, valorCampo: string, set: (v: string) => void, nome: string) => (
    <label htmlFor={`${id}-${nome}`}>
      {rotuloCampo}
      <textarea
        id={`${id}-${nome}`}
        value={valorCampo}
        maxLength={MAX_TEXTO}
        onChange={(e) => set(e.target.value)}
        disabled={estado.kind === "sending" || estado.kind === "completed"}
      />
    </label>
  );

  const opcoes: readonly string[] =
    formKey === "reembolso_pendencia" ? pendencias : auditor ? decisoesAuditor : decisoesAnalista;

  return (
    <section className="task-completion" aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>Pedido de reembolso</h3>
      {falhaContexto && (
        <p role="alert" className="queue-error">
          {falhaContexto}
        </p>
      )}
      {contexto && (
        <>
          <dl className="task-facts">
            <div>
              <dt>Protocolo</dt>
              <dd className="exact-value">{contexto.protocolo ?? "—"}</dd>
            </div>
            <div>
              <dt>Para quem</dt>
              <dd>{rotulo(contexto.relacao_com_titular)}</dd>
            </div>
            <div>
              <dt>Atendimento</dt>
              <dd>
                {rotulo(contexto.categoria_procedimento)} — {rotulo(contexto.tipo_reembolso)}
              </dd>
            </div>
            <div>
              <dt>Motivo de não usar a rede</dt>
              <dd>{rotulo(contexto.motivo_nao_rede)}</dd>
            </div>
            <div>
              <dt>Data do atendimento</dt>
              <dd>
                {dataBr(contexto.data_atendimento)}
                {contexto.dias_desde_atendimento !== null && contexto.dias_desde_atendimento >= 0
                  ? ` (${contexto.dias_desde_atendimento} dias antes do pedido)`
                  : ""}
              </dd>
            </div>
            <div>
              <dt>Valor pago (declarado)</dt>
              <dd>{formatarCentavos(contexto.valor_solicitado_cents)}</dd>
            </div>
            <div>
              <dt>Prazo (informativo)</dt>
              <dd>{rotulo(contexto.situacao_prazo)}</dd>
            </div>
            {formKey === "reembolso_pagamento" && (
              <>
                <div>
                  <dt>Decisão</dt>
                  <dd>{rotulo(contexto.decisao_reembolso)}</dd>
                </div>
                <div>
                  <dt>Valor aprovado</dt>
                  <dd>{formatarCentavos(contexto.valor_reembolso_aprovado_cents)}</dd>
                </div>
              </>
            )}
          </dl>
          {contexto.resposta_beneficiario && (
            <div className="task-context-summary">
              <h4>Resposta da beneficiária ao pedido de informação</h4>
              <blockquote>{contexto.resposta_beneficiario}</blockquote>
            </div>
          )}
          <h4>Documentos enviados</h4>
          {contexto.anexos.length === 0 ? (
            <p className="field-hint">Nenhum documento.</p>
          ) : (
            <ul className="reembolso-anexos">
              {contexto.anexos.map((anexo, indice) => {
                const url = anexoUrl(taskId, anexo.document_ref);
                return (
                  <li key={anexo.document_ref}>
                    {url ? (
                      <a href={url} rel="noopener noreferrer" download>
                        {rotulo(anexo.tipo)} {indice + 1}
                      </a>
                    ) : (
                      rotulo(anexo.tipo)
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {csrfToken && (
        <form onSubmit={(e) => void enviar(e)}>
          {formKey === "reembolso_pagamento" ? (
            <label htmlFor={`${id}-data`}>
              Pago em
              <input
                id={`${id}-data`}
                type="date"
                value={dataPagamento}
                onChange={(e) => setDataPagamento(e.target.value)}
                disabled={estado.kind === "sending" || estado.kind === "completed"}
              />
            </label>
          ) : (
            <fieldset disabled={estado.kind === "sending" || estado.kind === "completed"}>
              <legend>Decisão</legend>
              {opcoes.map((opcao) => (
                <label key={opcao}>
                  <input
                    type="radio"
                    name={`${id}-decisao`}
                    value={opcao}
                    checked={decisao === opcao}
                    onChange={() => {
                      setDecisao(opcao);
                      if (opcao === "APROVAR" && pedidoCentavos) {
                        setValor(formatarCentavos(pedidoCentavos).replace(/^R\$\s*/u, ""));
                      }
                    }}
                  />
                  {rotulo(opcao)}
                </label>
              ))}
            </fieldset>
          )}
          {precisaValor && formKey !== "reembolso_pendencia" && (
            <label htmlFor={`${id}-valor`}>
              Valor aprovado (R$)
              <input
                id={`${id}-valor`}
                inputMode="decimal"
                value={valor}
                onChange={(e) => setValor(e.target.value)}
                readOnly={decisao === "APROVAR"}
                disabled={estado.kind === "sending" || estado.kind === "completed"}
              />
            </label>
          )}
          {precisaValor && formKey !== "reembolso_pendencia" && (
            <p className="field-hint">
              {decisao === "APROVAR"
                ? "Aprovar paga o valor pedido. Para pagar menos, escolha \"Aprovar parcial\"."
                : `Informe um valor menor que o pedido (${formatarCentavos(pedidoCentavos)}).`}
            </p>
          )}
          {adversa && formKey !== "reembolso_pendencia" && (
            <>
              {campoTexto("Justificativa (interna)", justificativa, setJustificativa, "justificativa")}
              {campoTexto("Fundamentação contratual (interna)", fundamentacao, setFundamentacao, "fundamentacao")}
              {auditor && campoTexto("CID-10 de referência", cid10, setCid10, "cid10")}
              {auditor && campoTexto("Parecer do auditor", parecer, setParecer, "parecer")}
              {campoTexto("Texto que a beneficiária vai receber", texto, setTexto, "texto")}
            </>
          )}
          {decisao === "SOLICITAR_INFO" &&
            campoTexto("O que pedir à beneficiária", info, setInfo, "info")}
          <button
            className="primary-action"
            type="submit"
            disabled={!pronto || estado.kind === "sending" || estado.kind === "completed"}
          >
            {formKey === "reembolso_pagamento" ? "Registrar pagamento" : "Concluir"}
          </button>
          {estado.kind === "completed" && <p role="status">Tarefa concluída.</p>}
          {estado.kind === "error" && (
            <p role="alert" className="queue-error">
              {estado.message}
            </p>
          )}
        </form>
      )}
    </section>
  );
}

// Na tarefa de reembolso, "Responsabilidade" (assumir/liberar/reatribuir) e "Decisão humana" seguem RECUSADOS em
// dev: dependem da autoridade nativa e do material de decisao, que nenhum .tf de dev monta
// (docs/plans/portal-autoridade-nativa-dev.md §D-P), e o plano de decisao nem conhece `reembolso_*`
// (gateway.py, `_decision_binding`). Os botoes ficam visiveis e desligados, com o motivo, em vez de
// responderem "indisponivel" depois do clique. A conclusao vai pelo "Concluir" do painel acima (DL-0163).
export function ReembolsoBlocosIndisponiveis({ responsabilidade, decisao }: { responsabilidade: boolean; decisao: boolean }) {
  const motivo =
    "Não se aplica ao reembolso neste ambiente: decida pelo botão “Concluir” do pedido acima. " +
    "Não é preciso assumir a tarefa; basta estar no grupo dela.";
  return (
    <>
      {responsabilidade && (
        <section className="ownership-controls" aria-label="Responsabilidade pela tarefa">
          <h3>Responsabilidade pela tarefa</h3>
          <button className="secondary-action" type="button" disabled>
            Consultar responsabilidade
          </button>
          <p className="field-hint">{motivo}</p>
        </section>
      )}
      {decisao && (
        <section className="task-detail decision-workspace" aria-label="Decisão humana">
          <h2>Decisão humana</h2>
          <button className="secondary-action" type="button" disabled>
            Consultar contexto atual
          </button>
          <p className="field-hint">{motivo}</p>
        </section>
      )}
    </>
  );
}

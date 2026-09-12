import { useCallback, useEffect, useRef, useState } from "react";
import { makeSubmission, readDecisionContext, readDecisionReceipt, submitDecision,
  type DecisionContext, type Failure, type Receipt, type Submission } from "./decisionClient";
import { fieldSchema, formSchema, type Schema } from "./decisionSchema";
import { expiryDelay, isCurrent } from "./taskReadTime";

const messages: Record<Failure, string> = {
  invalid_request: "A solicitação foi recusada. Confira o formulário.",
  invalid_decision: "A decisão não atende ao contrato. Confira a escolha e sua fundamentação antes de revisar novamente.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Seu acesso atual não permite esta operação.",
  revision_conflict: "A tarefa ou sua evidência mudou. Consulte um novo contexto antes de decidir.",
  authority_unavailable: "A autorização atual não pôde ser confirmada.",
  task_unavailable: "A tarefa não está disponível para decisão.",
  form_projection_unavailable: "O formulário desta versão não está disponível para decisão.",
  form_contract_unavailable: "O contrato deste formulário não está disponível para decisão.",
  admission_unavailable: "O recebimento não pôde ser confirmado. Consulte o comando antes de qualquer nova decisão.",
  credential_scope_mismatch: "A decisão está indisponível para este acesso.",
  production_capabilities_unavailable: "O serviço de decisão ainda não está habilitado neste ambiente.",
  dependency_unavailable: "O serviço está temporariamente indisponível.",
  "invalid-response": "O portal recusou uma resposta inesperada.",
  "outcome-unknown": "O resultado do envio é desconhecido. Consulte o comando; não crie outra decisão para repetir este envio.",
};
const labels: Record<string, string> = {
  decisao_auditor: "Decisão do auditor", cid10_referencia: "Referência CID-10", fundamentacao_dut: "Fundamentação DUT",
  justificativa_clinica: "Justificativa clínica", resultado: "Resultado", notas_resolucao: "Notas da resolução",
  decisao_admissibilidade: "Decisão de admissibilidade", justificativa_recusa: "Justificativa da recusa",
  valor_aprovado_cents: "Valor aprovado em centavos", valor_liberado_centavos: "Valor liberado em centavos",
  valor_glosado_centavos: "Valor glosado em centavos", valor_deferido_centavos: "Valor deferido em centavos",
  estimativa_custo_cents: "Estimativa de custo em centavos", destino_referral: "Destinos do encaminhamento",
  dataset_complete: "Conjunto de dados completo", schema_valid: "Estrutura dos dados válida", lgpd_anonimizado: "Dados anonimizados conforme LGPD",
  juridico: "Jurídico", ans: "ANS", cred: "Credenciamento", contratual: "Contratual",
  ENROLL: "Incluir no programa", MONITORAR_OK: "Manter monitoramento", COMPROMISSO_FALLBACK: "Compromisso de contingência",
};
export function fieldLabel(key: string): string {
  if (labels[key]) return labels[key];
  const text = key.toLowerCase().replaceAll("_", " ").replace(/decisao/g, "decisão")
    .replace(/justificativa/g, "justificativa").replace(/fundamentacao/g, "fundamentação")
    .replace(/referencia/g, "referência").replace(/coordenacao/g, "coordenação")
    .replace(/informacao/g, "informação").replace(/resolucao/g, "resolução");
  return text[0].toUpperCase() + text.slice(1);
}
function InputField({ name, raw, value, required, change }: {
  name: string; raw: Schema; value: unknown; required: boolean; change: (v: unknown) => void;
}) {
  const s = fieldSchema(raw), id = `decision-${name}`, label = fieldLabel(name.split(".").at(-1)!);
  if (s.type === "object") {
    const values = (value ?? {}) as Record<string, unknown>;
    return <fieldset><legend>{label}{required ? " (obrigatório)" : " (opcional)"}</legend>
      {!required && <label><input type="checkbox" checked={value !== undefined} onChange={(e) => change(e.target.checked ? {} : undefined)} />Preencher {label.toLowerCase()}</label>}
      {(required || value !== undefined) && Object.entries(s.properties ?? {}).map(([key, child]) => <InputField key={key} name={`${name}.${key}`} raw={child}
        value={values[key]} required={s.required?.includes(key) ?? false} change={(v) => change({ ...values, [key]: v })} />)}
      {!required && <button type="button" className="secondary-action" onClick={() => change(undefined)}>Limpar {label.toLowerCase()}</button>}
    </fieldset>;
  }
  if (s.type === "array") {
    const values = (value ?? []) as unknown[];
    return <fieldset><legend>{label}{required ? " (obrigatório)" : " (opcional)"}</legend>
      {values.map((v, i) => <div key={i}><InputField name={`${name}.${i + 1}`} raw={s.items!} value={v} required change={(next) => change(values.map((old, j) => j === i ? next : old))} />
        <button type="button" onClick={() => change(values.filter((_, j) => j !== i))}>Remover item {i + 1}</button></div>)}
      <button type="button" className="secondary-action" onClick={() => change([...values, ""])}>Adicionar {label.toLowerCase()}</button>
    </fieldset>;
  }
  return <div className="decision-field"><label htmlFor={id}>{label}{required ? " (obrigatório)" : " (conforme a decisão)"}</label>
    {s.enum || s.type === "boolean" ? <select id={id} value={value === undefined ? "" : String(value)} required={required}
      onChange={(e) => change(e.target.value === "" ? undefined : s.type === "boolean" ? e.target.value === "true" : e.target.value)}>
      <option value="">Selecione explicitamente</option>
      {(s.enum ?? [true, false]).map((option) => <option key={String(option)} value={String(option)}>{typeof option === "boolean" ? option ? "Sim" : "Não" : fieldLabel(String(option))}</option>)}
    </select> : <textarea id={id} value={typeof value === "string" ? value : ""} required={required} rows={3}
      autoComplete="off" spellCheck={false} onChange={(e) => change(e.target.value === "" && !required ? undefined : e.target.value)} />}
    {/centavos|cents/.test(name) && <p className="freshness-detail">Informe um número inteiro de centavos, sem separadores.</p>}
  </div>;
}
function ReviewValue({ value }: { value: unknown }) {
  if (value === undefined || value === null) return <>Não informado</>;
  if (typeof value === "boolean") return <>{value ? "Sim" : "Não"}</>;
  if (typeof value === "string") return <span className="decision-text">{value}</span>;
  return <dl>{Object.entries(value as object).map(([key, v]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd><ReviewValue value={v} /></dd></div>)}</dl>;
}

type Command = { id: string; submission?: Submission; phase: "sending" | "accepted" | "unknown" | "receipt"; receipt?: Pick<Receipt, "status" | "consumed_task_revision"> };
export function DecisionWorkspace({ taskId, csrfToken, onSessionUnavailable }: {
  taskId: string; csrfToken: string; onSessionUnavailable: () => void;
}) {
  const [context, setContext] = useState<DecisionContext | null>(null);
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [review, setReview] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<Failure | null>(null), [busy, setBusy] = useState(false);
  const [command, setCommand] = useState<Command | null>(null);
  const request = useRef<AbortController | null>(null), epoch = useRef(0), locked = useRef(false);
  const alive = useRef(true);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (review) reviewHeading.current?.focus(); }, [review]);
  const report = useCallback((failure: Failure) => {
    setError(failure);
    if (failure === "authentication_unavailable" || failure === "operation_forbidden") {
      setContext(null); setInputs({}); setCommand(null); setReview(false);
      if (failure === "authentication_unavailable") onSessionUnavailable();
    }
  }, [onSessionUnavailable]);
  const load = useCallback(async () => {
    const current = ++epoch.current;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setContext(null); setInputs({}); setReview(false); setConfirmed(false); setError(null); setBusy(true);
    try {
      const result = await readDecisionContext(taskId, controller.signal);
      if (!alive.current || current !== epoch.current || controller.signal.aborted) return;
      if (result.kind === "success") { setContext(result.value); setInputs({ kind: result.value.snapshot.form_key }); }
      else report(result.kind);
    } catch { if (!controller.signal.aborted) report("dependency_unavailable"); }
    finally { if (alive.current && current === epoch.current) setBusy(false); }
  }, [report, taskId]);
  useEffect(() => {
    alive.current = true; void load();
    return () => { alive.current = false; epoch.current += 1; request.current?.abort(); };
  }, [load]);
  useEffect(() => {
    if (!context || command) return;
    const timer = window.setTimeout(() => { setContext(null); setInputs({}); setReview(false); setConfirmed(false); setError("revision_conflict"); }, expiryDelay(context.valid_until));
    return () => window.clearTimeout(timer);
  }, [command, context]);
  const send = async (submission: Submission) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(null); setCommand({ id: submission.decision.command_id, submission, phase: "sending" });
    const controller = new AbortController(); request.current = controller;
    try {
      const result = await submitDecision(submission, csrfToken, controller.signal);
      if (!alive.current || controller.signal.aborted) return;
      if (result.kind === "success") {
        // Admission metadata includes protected provenance. Retain no copy in UI state.
        setInputs({}); setContext(null); setCommand({ id: submission.decision.command_id, phase: "accepted" });
      } else if (["invalid_request", "invalid_decision", "revision_conflict"].includes(result.kind)) {
        setCommand(null); setReview(false); setConfirmed(false); report(result.kind);
        if (result.kind === "revision_conflict") { setContext(null); setInputs({}); }
      } else { setCommand({ id: submission.decision.command_id, submission, phase: "unknown" }); report(result.kind); }
    } catch { if (alive.current && !controller.signal.aborted) { setCommand({ id: submission.decision.command_id, submission, phase: "unknown" }); report("outcome-unknown"); } }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  };
  const consult = async (receipt: boolean) => {
    if (!command || locked.current) return;
    locked.current = true; setBusy(true); setError(null);
    setCommand({ ...command, receipt: undefined, phase: command.phase === "unknown" ? "unknown" : "accepted" });
    const controller = new AbortController(); request.current = controller;
    try {
      const result = await readDecisionReceipt(taskId, command.id, controller.signal, receipt);
      if (!alive.current || controller.signal.aborted) return;
      if (result.kind === "success") { setInputs({}); setContext(null); setCommand({ id: command.id, phase: "receipt", receipt: { status: result.value.status, consumed_task_revision: result.value.consumed_task_revision } }); }
      else report(result.kind);
    } catch { if (alive.current && !controller.signal.aborted) report("dependency_unavailable"); }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  };
  const schema = context ? formSchema(context.snapshot.form_key) : null;
  return <section className="task-detail decision-workspace" aria-labelledby="decision-heading" aria-busy={busy}>
    <h2 id="decision-heading">Decisão humana</h2>
    {error && <p role="alert">{messages[error]}</p>}
    {busy && <p role="status">Consultando o serviço de decisão…</p>}
    {!command && !context && !busy && <button type="button" className="secondary-action" onClick={() => void load()}>Consultar contexto atual</button>}
    {context && schema && !command && <>
      <p>Revise a tarefa, as evidências disponíveis e os campos abaixo. A escolha é de responsabilidade humana.</p>
      <p className="freshness-detail">Tarefa {context.snapshot.task_definition_key}. Revisão {context.snapshot.task_revision}. Evidência {context.snapshot.evidence_revision}.</p>
      {context.snapshot.form_source_status === "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY" && <p>Este formulário tem contrato em revisão. A disponibilidade desta tela não certifica aprovação de produção.</p>}
      {context.snapshot.form_key === "auth_pendencia" && <p>As opções conceder_prazo_extra e seguir_analise seguem para a análise de SLA. O fluxo atual não reinicia a espera de documentos nem concede um novo prazo.</p>}
      <p>O dossiê e o histórico detalhado não foram disponibilizados nesta consulta.</p>
      <p>O serviço confere a fundamentação exigida para cada escolha antes de aceitar o envio.</p>
      {["auth_decisao", "auth_junta"].includes(context.snapshot.form_key) && <p>Para negar, informe justificativa clínica, referência CID-10 e fundamentação DUT. A autoria vem da sua sessão autenticada.</p>}
      {context.snapshot.read_only_evidence && <section aria-label="Evidência atual de admissibilidade"><h3>Evidência atual de admissibilidade</h3>
        <p>Prosseguir nesta análise não libera pagamento. A evidência não confirma a obrigação.</p>
        <dl>{Object.entries(context.snapshot.read_only_evidence).filter(([key]) => key !== "kind").map(([key, value]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd><ReviewValue value={value} /></dd></div>)}</dl>
      </section>}
      {!review ? <form autoComplete="off" onSubmit={(event) => {
        event.preventDefault(); setError(null);
        if (!makeSubmission(context, inputs, "review")) { setError("invalid_decision"); return; }
        setReview(true); setConfirmed(false);
      }}>
        {Object.entries(schema.properties ?? {}).filter(([key]) => key !== "kind").map(([key, raw]) => <InputField key={key} name={key} raw={raw}
          value={inputs[key]} required={schema.required?.includes(key) ?? false} change={(value) => {
            setInputs((old) => { const next = { ...old }; if (value === undefined) delete next[key]; else next[key] = value; return next; }); setConfirmed(false);
          }} />)}
        <button type="submit" className="primary-action" disabled={busy}>Revisar decisão</button>
      </form> : <section aria-labelledby="decision-review-heading"><h3 id="decision-review-heading" tabIndex={-1} ref={reviewHeading}>Revise antes de enviar</h3>
        <dl>{Object.keys(schema.properties ?? {}).filter((key) => key !== "kind").map((key) => <div key={key}><dt>{fieldLabel(key)}</dt><dd><ReviewValue value={inputs[key]} /></dd></div>)}</dl>
        <label className="decision-confirm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />Revisei os dados e confirmo esta decisão humana.</label>
        <div className="queue-actions"><button type="button" className="secondary-action" onClick={() => { setReview(false); setConfirmed(false); }}>Voltar ao formulário</button>
          <button type="button" className="primary-action" disabled={!confirmed || busy} onClick={() => {
            if (!confirmed || !isCurrent(context.valid_until)) { setError("revision_conflict"); return; }
            const submission = makeSubmission(context, inputs, crypto.randomUUID());
            if (submission) void send(submission); else setError("invalid_decision");
          }}>Enviar decisão</button></div>
      </section>}
    </>}
    {command && <section aria-labelledby="command-heading"><h3 id="command-heading">Acompanhamento do comando</h3>
      <p className="exact-value">Protocolo: {command.id}</p>
      <p role="status">{command.phase === "sending" ? "Enviando decisão. O recebimento ainda não foi confirmado." :
        command.receipt?.status === "committed" ? "Execução confirmada pelo recibo do engine e pela auditoria." :
        command.receipt?.status === "conflict" ? "Comando encerrado em conflito. Nenhuma execução foi confirmada por este recibo." :
        command.phase === "unknown" ? "Resultado desconhecido. O envio pode ter sido recebido." : "Decisão recebida e pendente. A execução ainda não foi confirmada."}</p>
      {command.receipt?.status === "committed" && <p>Revisão consumida: {command.receipt.consumed_task_revision}</p>}
      <div className="queue-actions"><button type="button" className="secondary-action" disabled={busy} onClick={() => void consult(false)}>Consultar comando</button>
        <button type="button" className="secondary-action" disabled={busy} onClick={() => void consult(true)}>Consultar recibo</button>
        {command.phase === "unknown" && command.submission && <button type="button" className="secondary-action" disabled={busy} onClick={() => void send(command.submission!)}>Reenviar o mesmo comando</button>}
      </div><p>Guarde o protocolo para acompanhamento. Fechar a tela não cancela o comando.</p>
    </section>}
  </section>;
}

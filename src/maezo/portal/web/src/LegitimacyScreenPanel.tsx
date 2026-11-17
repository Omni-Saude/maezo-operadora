import { useCallback, useEffect, useId, useRef, useState } from "react";

import { fieldSchema, wireSchema, type Schema } from "./decisionSchema";
import {
  notEnabledFailures,
  type LegitimacyChecklist,
  type LegitimacyCriterion,
  type LegitimacyFailure,
  type LegitimacyRecord,
  type LegitimacyResult,
  type LegitimacyRevalidation,
  type LegitimacyScreenClient,
  type LegitimacySubmission,
} from "./legitimacyScreenClient";

// E-SF-20 (EP-CORE-15a): the staff screen of the collective-legitimacy check (OP21 PR-2, UI).
//
// Three closures this component keeps, each pinned by its own guard test:
// - Every criterion and option label is the SERVER manifest's `rotulo`, rendered byte for
//   byte (`GET …/checklist`); this file carries no criterion text of its own.
// - The criterion the blocking STATE applies (art. 29 §1º, `estrutural: true`) is shown as
//   text and is never a form control, whatever the manifest carries for it.
// - Flag OFF (403 `operation_forbidden`) or the unbound slot (503 `dependency_unavailable`)
//   shows "not enabled here" and offers nothing that writes: no POST can leave this panel.
// The panel records what staff declare; it decides nothing, blocks no sale and never
// characterizes who sells.

export const legitimacyNotEnabledMessage = "Esta área ainda não está habilitada neste ambiente.";

type Phase =
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "not-enabled" }>
  | Readonly<{ kind: "failed" }>
  | Readonly<{ kind: "ready"; checklist: LegitimacyChecklist }>;

type FieldKind = Readonly<{ kind: "enum"; values: readonly string[] }> | Readonly<{ kind: "check" }>;

const submissionSchema = wireSchema("LegitimacyScreenSubmission");

// The wire decides what a manifest field is: an enum slot or a declared criterion check.
// A manifest field the wire cannot carry is not a control.
function fieldKind(campoId: string): FieldKind | null {
  const raw: Schema | undefined = submissionSchema.properties?.[campoId];
  if (!raw) return null;
  const resolved = fieldSchema(raw);
  if (Array.isArray(resolved.enum)) return { kind: "enum", values: resolved.enum as string[] };
  if (resolved.properties?.result) return { kind: "check" };
  return null;
}

const statusLabel: Record<LegitimacyRecord["status"], string> = {
  valid: "válida",
  pending: "pendente",
  invalid: "inválida",
};

const failureMessage: Partial<Record<LegitimacyFailure, string>> = {
  invalid_request: "O servidor recusou os dados enviados. Revise a contratante e os critérios declarados.",
  field_not_client_owned: "O servidor recusou os dados enviados. Revise a contratante e os critérios declarados.",
  resource_unavailable: "Nenhuma verificação atual foi encontrada para esta contratante.",
  screen_conflict: "Já existe outra verificação registrada para esta oportunidade.",
  stale_revision: "A verificação mudou desde a consulta. Consulte a contratante de novo antes de revalidar.",
  "invalid-response": "O servidor respondeu de forma inesperada. Nada foi considerado registrado.",
};

function CriterionFieldset({
  criterion,
  checklist,
  values,
  onChange,
  disabled,
}: {
  criterion: LegitimacyCriterion;
  checklist: LegitimacyChecklist;
  values: Readonly<Record<string, string>>;
  onChange: (campoId: string, value: string) => void;
  disabled: boolean;
}) {
  const legendId = useId();
  return (
    <fieldset className="legitimacy-criterion" aria-labelledby={legendId}>
      <legend id={legendId}>{criterion.rotulo}</legend>
      <p className="field-hint">{criterion.norm_anchor}</p>
      {criterion.campos.map((campo) => {
        const kind = fieldKind(campo.campo_id);
        if (kind === null) return null;
        const options = kind.kind === "check"
          ? checklist.resultados
          : criterion.opcoes.filter((option) => kind.values.includes(option.valor));
        const id = `legitimacy-${campo.campo_id}`;
        return (
          <div className="legitimacy-field" key={campo.campo_id}>
            <label htmlFor={id}>{campo.norm_anchor}</label>
            {campo.condicao_estrutural && <p className="field-hint">{campo.condicao_estrutural}</p>}
            <select
              id={id}
              aria-describedby={legendId}
              value={values[campo.campo_id] ?? ""}
              disabled={disabled}
              onChange={(event) => onChange(campo.campo_id, event.target.value)}
            >
              <option value="">Não declarado nesta revisão</option>
              {options.map((option) => (
                <option key={option.valor} value={option.valor}>{option.rotulo}</option>
              ))}
            </select>
          </div>
        );
      })}
    </fieldset>
  );
}

function StructuralCriterion({ criterion }: { criterion: LegitimacyCriterion }) {
  // Shown, never a field: whatever `campos`/`opcoes` the manifest carries for it is ignored.
  return (
    <section className="legitimacy-structural" aria-label={criterion.rotulo}>
      <p>{criterion.rotulo}</p>
      <p className="field-hint">{criterion.norm_anchor}</p>
    </section>
  );
}

export function LegitimacyScreenPanel({
  client,
  csrfToken,
  onSessionUnavailable,
}: {
  client: LegitimacyScreenClient;
  csrfToken: string;
  onSessionUnavailable: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [legalEntityRef, setLegalEntityRef] = useState("");
  const [opportunityRef, setOpportunityRef] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [known, setKnown] = useState<LegitimacyRecord | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);

  const begin = () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    return controller;
  };

  // One place reads every failure: session gone, area not enabled, or a refusal to show.
  const settle = useCallback(<T,>(result: LegitimacyResult<T>): T | null => {
    if (result.kind === "success") return result.value;
    if (result.failure === "authentication_unavailable") {
      onSessionUnavailable();
    } else if (notEnabledFailures.includes(result.failure)) {
      setPhase({ kind: "not-enabled" });
    } else {
      setMessage(failureMessage[result.failure] ?? failureMessage["invalid-response"]!);
    }
    return null;
  }, [onSessionUnavailable]);

  useEffect(() => {
    const controller = begin();
    void (async () => {
      try {
        const result = await client.readChecklist(controller.signal);
        if (controller.signal.aborted) return;
        if (result.kind === "success") {
          setPhase({ kind: "ready", checklist: result.value });
        } else if (result.failure === "authentication_unavailable") {
          onSessionUnavailable();
        } else if (notEnabledFailures.includes(result.failure)) {
          setPhase({ kind: "not-enabled" });
        } else {
          setPhase({ kind: "failed" });
        }
      } catch {
        if (!controller.signal.aborted) setPhase({ kind: "failed" });
      }
    })();
    return () => controller.abort();
  }, [client, onSessionUnavailable]);

  useEffect(() => () => request.current?.abort(), []);

  if (phase.kind === "loading") {
    return <p className="queue-message">Carregando o checklist de legitimidade.</p>;
  }
  if (phase.kind === "not-enabled" || phase.kind === "failed") {
    return (
      <section className="portal-unavailable" aria-labelledby="legitimacy-unavailable-heading">
        <p className="eyebrow">Disponibilidade atual</p>
        <h2 id="legitimacy-unavailable-heading">Legitimidade coletiva</h2>
        <p>
          {phase.kind === "not-enabled"
            ? legitimacyNotEnabledMessage
            : "Não foi possível carregar o checklist de legitimidade. Nada foi registrado."}
        </p>
      </section>
    );
  }

  const { checklist } = phase;

  const lookup = async () => {
    const controller = begin();
    setBusy(true);
    setMessage(null);
    try {
      const result = await client.readLatest(legalEntityRef, controller.signal);
      if (controller.signal.aborted) return;
      if (result.kind === "failure" && result.failure === "resource_unavailable") {
        setKnown(null);
        setMessage("Nenhuma verificação registrada para esta contratante.");
        return;
      }
      const record = settle(result);
      if (record !== null) setKnown(record);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  const submit = async () => {
    const payload: Record<string, unknown> = {
      schema_version: 1,
      legal_entity_ref: legalEntityRef,
      opportunity_ref: opportunityRef,
    };
    for (const criterion of checklist.criterios) {
      if (criterion.estrutural) continue;
      for (const campo of criterion.campos) {
        const kind = fieldKind(campo.campo_id);
        const value = values[campo.campo_id] ?? "";
        if (kind === null || value === "") continue;
        payload[campo.campo_id] = kind.kind === "check" ? { result: value, evidence_refs: [] } : value;
      }
    }
    const controller = begin();
    setBusy(true);
    setMessage(null);
    try {
      const result = known === null
        ? await client.create(payload as unknown as LegitimacySubmission, csrfToken, controller.signal)
        : await client.revalidate(
          known.screen_ref,
          { ...payload, screen_revision: known.screen_revision } as unknown as LegitimacyRevalidation,
          csrfToken,
          controller.signal,
        );
      if (controller.signal.aborted) return;
      const record = settle(result);
      if (record !== null) {
        setKnown(record);
        setMessage(
          `Verificação registrada pelo servidor: revisão ${record.screen_revision}, situação ${statusLabel[record.status]}.`,
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return (
    <section className="legitimacy-screen" aria-labelledby="legitimacy-heading">
      <div className="case-queue-heading">
        <div>
          <h2 id="legitimacy-heading">Legitimidade coletiva</h2>
          <p>
            Registro da verificação da pessoa jurídica contratante, critério por critério, com o
            resultado declarado pela equipe. {checklist.norma_contratacao} ·{" "}
            {checklist.penalidade_anchor}
          </p>
        </div>
      </div>
      <form
        className="case-lookup"
        onSubmit={(event) => {
          event.preventDefault();
          void lookup();
        }}
      >
        <label htmlFor="legitimacy-legal-entity">Referência da contratante</label>
        <input
          id="legitimacy-legal-entity"
          value={legalEntityRef}
          onChange={(event) => setLegalEntityRef(event.target.value)}
        />
        <button className="secondary-action" type="submit" disabled={busy || legalEntityRef === ""}>
          Consultar última verificação
        </button>
      </form>
      {known !== null && (
        <p className="freshness-detail">
          Verificação atual: revisão {known.screen_revision}, situação {statusLabel[known.status]}. O
          envio abaixo registra uma nova revisão.
        </p>
      )}
      <form
        className="legitimacy-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label htmlFor="legitimacy-opportunity">Referência da oportunidade</label>
        <input
          id="legitimacy-opportunity"
          value={opportunityRef}
          onChange={(event) => setOpportunityRef(event.target.value)}
        />
        {checklist.criterios.map((criterion) =>
          criterion.estrutural ? (
            <StructuralCriterion key={criterion.criterion_id} criterion={criterion} />
          ) : (
            <CriterionFieldset
              key={criterion.criterion_id}
              criterion={criterion}
              checklist={checklist}
              values={values}
              disabled={busy}
              onChange={(campoId, value) => setValues((current) => ({ ...current, [campoId]: value }))}
            />
          ),
        )}
        <button className="primary-action" type="submit" disabled={busy}>
          {known === null ? "Registrar verificação" : "Revalidar verificação"}
        </button>
      </form>
      <div aria-live="polite">{message !== null && <p role="status">{message}</p>}</div>
    </section>
  );
}

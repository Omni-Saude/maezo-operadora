import { useEffect, useRef, useState } from "react";
import { createPhiCommunicationClient, type ContentFailure, type PhiCommunicationClient } from "./phiCommunicationClient";
import { expiryDelay, isCurrent, retainedCeiling } from "./taskReadTime";

type Props = Readonly<{
  caseRef: string; communicationRef: string; pageValidUntil: string;
  onFailure: (failure: ContentFailure) => void;
  client?: PhiCommunicationClient;
}>;
type State = { kind: "closed" } | { kind: "loading" } | { kind: "failed" } |
  { kind: "ready"; body: string; validUntil: string };

export function CommunicationBody(props: Props) {
  const [context, setContext] = useState({ caseRef: props.caseRef, ref: props.communicationRef, client: props.client, generation: 0 });
  if (context.caseRef !== props.caseRef || context.ref !== props.communicationRef || context.client !== props.client) {
    setContext({ caseRef: props.caseRef, ref: props.communicationRef, client: props.client, generation: context.generation + 1 });
    return null;
  }
  return <BodyContext {...props} key={context.generation} />;
}
function BodyContext({ caseRef, communicationRef, pageValidUntil, onFailure, client }: Props) {
  const [reader] = useState(() => client ?? createPhiCommunicationClient());
  const [state, setState] = useState<State>({ kind: "closed" });
  const active = useRef<AbortController | null>(null);
  const ceiling = state.kind === "ready" ? retainedCeiling(pageValidUntil, state.validUntil) : pageValidUntil;
  const close = () => { active.current?.abort(); active.current = null; setState({ kind: "closed" }); };
  useEffect(() => () => { active.current?.abort(); active.current = null; }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      active.current?.abort(); active.current = null; setState({ kind: "closed" });
    }, expiryDelay(ceiling));
    return () => window.clearTimeout(timer);
  }, [ceiling]);
  const open = async () => {
    if (active.current || !isCurrent(ceiling)) return;
    const request = new AbortController();
    active.current = request;
    setState({ kind: "loading" });
    try {
      const result = await reader.read(caseRef, communicationRef, request.signal);
      if (request.signal.aborted || active.current !== request || !isCurrent(ceiling)) return;
      if (result.kind === "failure") {
        setState({ kind: "failed" }); onFailure(result.failure); return;
      }
      const validUntil = retainedCeiling(ceiling, result.value.valid_until);
      if (!isCurrent(validUntil)) { setState({ kind: "closed" }); return; }
      setState({ kind: "ready", body: result.value.body, validUntil });
    } catch {
      if (!request.signal.aborted && active.current === request) setState({ kind: "failed" });
    } finally {
      if (active.current === request) active.current = null;
    }
  };
  if (!isCurrent(ceiling)) return null;
  return <div aria-label="Conteúdo protegido da comunicação">
    {state.kind === "closed" || state.kind === "failed"
      ? <button type="button" className="secondary-action" onClick={() => void open()}>Abrir conteúdo</button>
      : <button type="button" className="secondary-action" onClick={close}>Fechar conteúdo</button>}
    {state.kind === "loading" && <p role="status">Consultando conteúdo autorizado…</p>}
    {state.kind === "failed" && <p role="alert">O conteúdo não está disponível para leitura. Tente novamente.</p>}
    {state.kind === "ready" && <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{state.body}</p>}
  </div>;
}

import { useCallback, useEffect, useRef, useState } from "react";

import { ExternalPortalExperience, StaffPortalExperience } from "./PortalSessionExperience";
import { getSession, portalPaths, postLogout, type SessionDTO } from "./sessionClient";
import "./portalExperience.css";

type ViewState =
  | { kind: "loading"; reason: "initial" | "revalidation" | "expiry" }
  | { kind: "authenticated"; session: SessionDTO }
  | { kind: "unauthenticated" }
  | { kind: "dependency-unavailable" }
  | { kind: "invalid-response" }
  | { kind: "expired" }
  | { kind: "logging-out" }
  | { kind: "logged-out" }
  | { kind: "logout-unconfirmed"; retryToken: string };

function SignInLink({ label = "Entrar no portal" }: { label?: string }) {
  return (
    <a className="primary-action" href={portalPaths.login}>
      {label}
    </a>
  );
}

function StatusPage({ children }: { children: React.ReactNode }) {
  return (
    <main className="page-shell">
      <section className="status-card" aria-live="polite">
        <div className="brand-mark" aria-hidden="true">
          M
        </div>
        {children}
      </section>
    </main>
  );
}

export function App() {
  const [state, setState] = useState<ViewState>({ kind: "loading", reason: "initial" });
  const requestEpoch = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const transitionHeading = useRef<HTMLHeadingElement | null>(null);
  const focusUserTransition = useRef(false);

  const revalidate = useCallback(async (reason: "initial" | "revalidation" | "expiry") => {
    const epoch = ++requestEpoch.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setState({ kind: "loading", reason });
    try {
      const result = await getSession(controller.signal);
      if (controller.signal.aborted || epoch !== requestEpoch.current) return;
      activeRequest.current = null;
      if (result.kind === "authenticated") {
        if (Date.parse(result.session.expires_at) <= Date.now()) {
          setState({ kind: "expired" });
          return;
        }
        setState({ kind: "authenticated", session: result.session });
        return;
      }
      setState(result);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        if (epoch === requestEpoch.current) setState({ kind: "dependency-unavailable" });
      }
    }
  }, []);

  useEffect(() => {
    void revalidate("initial");
    return () => {
      requestEpoch.current += 1;
      activeRequest.current?.abort();
    };
  }, [revalidate]);

  useEffect(() => {
    if (state.kind !== "authenticated") return;
    const remaining = Date.parse(state.session.expires_at) - Date.now();
    const timer = window.setTimeout(() => void revalidate("expiry"), Math.max(0, remaining));
    return () => window.clearTimeout(timer);
  }, [revalidate, state]);

  useEffect(() => {
    if (state.kind !== "authenticated") return;
    const onVisibility = () => {
      if (document.visibilityState === "visible") void revalidate("revalidation");
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [revalidate, state.kind]);

  const logout = useCallback(async (csrfToken: string) => {
    const epoch = ++requestEpoch.current;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setState({ kind: "logging-out" });
    try {
      const confirmed = await postLogout(csrfToken, controller.signal);
      if (controller.signal.aborted || epoch !== requestEpoch.current) return;
      activeRequest.current = null;
      setState(
        confirmed
          ? { kind: "logged-out" }
          : { kind: "logout-unconfirmed", retryToken: csrfToken },
      );
    } catch (error) {
      if (
        !(error instanceof DOMException && error.name === "AbortError") &&
        epoch === requestEpoch.current
      ) {
        setState({ kind: "logout-unconfirmed", retryToken: csrfToken });
      }
    }
  }, []);

  const startSessionRetry = useCallback(() => {
    focusUserTransition.current = true;
    void revalidate("revalidation");
  }, [revalidate]);

  const startLogout = useCallback(
    (csrfToken: string) => {
      focusUserTransition.current = true;
      void logout(csrfToken);
    },
    [logout],
  );

  const invalidateSession = useCallback(() => {
    requestEpoch.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setState({ kind: "unauthenticated" });
  }, []);

  useEffect(() => {
    if (!focusUserTransition.current || transitionHeading.current === null) return;
    transitionHeading.current.focus();
    if (state.kind !== "loading" && state.kind !== "logging-out") {
      focusUserTransition.current = false;
    }
  }, [state.kind]);

  if (state.kind === "loading") {
    return (
      <StatusPage>
        <p className="eyebrow">Portal Maezo</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          {state.reason === "initial" ? "Validando sua sessão" : "Revalidando sua sessão"}
        </h1>
        <p>Aguarde enquanto confirmamos seu acesso com segurança.</p>
        <span className="progress" aria-hidden="true" />
      </StatusPage>
    );
  }

  if (state.kind === "authenticated") {
    return (
      <div className="app-shell">
        <a className="skip-link" href="#conteudo">Pular para o conteúdo</a>
        <header className="app-header">
          <a className="brand" href="/portal/" aria-label="Portal Maezo, início">
            Maezo
          </a>
          <button
            className="secondary-action"
            type="button"
            onClick={() => startLogout(state.session.csrf_token)}
          >
            Sair com segurança
          </button>
        </header>
        <div id="conteudo" className="skip-target" tabIndex={-1}>
        {state.session.audience === "staff" ? (
          <StaffPortalExperience
            expiresAt={state.session.expires_at}
            csrfToken={state.session.csrf_token}
            sessionBinding={[
              state.session.principal_ref,
              state.session.expires_at,
              ...state.session.roles,
            ].join("\u0000")}
            headingRef={transitionHeading}
            onSessionUnavailable={invalidateSession}
          />
        ) : (
          <ExternalPortalExperience
            audience={state.session.audience}
            expiresAt={state.session.expires_at}
            csrfToken={state.session.csrf_token}
            sessionBinding={[
              state.session.principal_ref,
              state.session.expires_at,
              ...state.session.roles,
            ].join("\u0000")}
            headingRef={transitionHeading}
            onSessionUnavailable={invalidateSession}
          />
        )}
        </div>
      </div>
    );
  }

  if (state.kind === "logging-out") {
    return (
      <StatusPage>
        <p className="eyebrow">Portal Maezo</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Encerrando a sessão
        </h1>
        <p>Removemos os dados desta tela e estamos confirmando a saída no servidor.</p>
      </StatusPage>
    );
  }

  if (state.kind === "logout-unconfirmed") {
    return (
      <StatusPage>
        <p className="eyebrow">Atenção</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Não foi possível confirmar a saída
        </h1>
        <p>Os dados foram removidos desta tela, mas o servidor não confirmou o encerramento da sessão.</p>
        <div className="action-row">
          <button
            className="primary-action"
            type="button"
            onClick={() => startLogout(state.retryToken)}
          >
            Tentar sair novamente
          </button>
          <SignInLink label="Entrar novamente" />
        </div>
      </StatusPage>
    );
  }

  if (state.kind === "logged-out") {
    return (
      <StatusPage>
        <p className="eyebrow">Portal Maezo</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Sessão encerrada
        </h1>
        <p>O servidor confirmou sua saída.</p>
        <SignInLink label="Entrar novamente" />
      </StatusPage>
    );
  }

  if (state.kind === "dependency-unavailable") {
    return (
      <StatusPage>
        <p className="eyebrow">Serviço temporariamente indisponível</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Não foi possível validar sua sessão
        </h1>
        <p>Uma dependência do portal não respondeu. Nenhum acesso foi concedido.</p>
        <button
          className="primary-action"
          type="button"
          onClick={startSessionRetry}
        >
          Tentar novamente
        </button>
      </StatusPage>
    );
  }

  if (state.kind === "invalid-response") {
    return (
      <StatusPage>
        <p className="eyebrow">Resposta inválida</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Não foi possível validar sua sessão
        </h1>
        <p>O portal recusou uma resposta inesperada. Nenhum acesso foi concedido.</p>
        <button
          className="primary-action"
          type="button"
          onClick={startSessionRetry}
        >
          Tentar novamente
        </button>
      </StatusPage>
    );
  }

  if (state.kind === "expired") {
    return (
      <StatusPage>
        <p className="eyebrow">Sessão expirada</p>
        <h1 ref={transitionHeading} tabIndex={-1}>
          Entre novamente para continuar
        </h1>
        <p>Os dados da sessão anterior foram removidos desta tela.</p>
        <SignInLink />
      </StatusPage>
    );
  }

  return (
    <StatusPage>
      <p className="eyebrow">Portal Maezo</p>
      <h1 ref={transitionHeading} tabIndex={-1}>
        Sua sessão não está ativa
      </h1>
      <p>Entre para que o servidor confirme seu acesso.</p>
      <SignInLink />
    </StatusPage>
  );
}

"""Deployment-owned Cognito configuration. No discovery or browser-selected endpoints."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAEZO_PORTAL_", extra="forbid", frozen=True)

    tenant: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    issuer: str
    cognito_origin: str
    client_id: str = Field(pattern=r"^[a-z0-9]{1,128}$")
    # Explicit deployment assertion, checked against a different configured M2M client.
    client_purpose: Literal["dedicated-human-code-pkce"]
    machine_client_id: str = Field(min_length=1)
    public_origin: str
    database_url: SecretStr | None = Field(default=None, repr=False)
    # Protected owner-bound AUTH participant, never a store/provider supplied by BFF.
    auth_lifecycle_binding_path: Path | None = Field(default=None, repr=False)
    mode: Literal["production", "local-test"] = "production"
    session_seconds: int = Field(default=1800, ge=60, le=3600)
    transaction_seconds: int = Field(default=300, ge=30, le=300)
    #: INTERIM (`docs/decisions-log.md` DL-0049). Turns on
    #: `POST /api/v1/portal/tasks/{id}/completion`, which closes an ESCALATION task over the
    #: engine's REST surface instead of the signed durable relay ADR-0049 D5/D6/D7 designs.
    #: OFF by default, and `scripts/ci/check_portal_direct_completion.py` reproves any
    #: `deploy/**` that turns it on outside `dev-sa-east-1`. Deleting this flag — with the
    #: relay of #1145 in its place — is the intended end state.
    direct_completion: bool = False
    #: INTERIM (DL-0049). Exact HTTPS origins, comma separated, that may call this BFF with
    #: credentials. EMPTY by default, which is "no cross-origin caller at all": the portal is
    #: same-origin software and the declared-demo test channel
    #: (`platform/testchannel/paginas/escalonamento.html`) is the only reason this exists.
    #: Same CI fence as the flag above. Never a wildcard: `Access-Control-Allow-Origin: *`
    #: and credentials are mutually exclusive in the browser, and an echoed Origin would make
    #: any site a caller.
    cors_origins: str = ""
    #: W5 CLNT 5.2 (`docs/plans/maezo-completion-program.md`). Canal HTTP de DSR do titular
    #: (`POST /api/v1/portal/dsr` -> SP-OP-LGPD-DSR-001). DARK: OFF por padrão — o programa é
    #: shadow e virar esta chave é ato da onda ROLL, com janela de evidência e revisor nomeado
    #: próprios. OFF responde 403 `operation_forbidden` antes de qualquer efeito; a rota segue
    #: publicada no schema mesmo desligada (o leitor do contrato vê o canal antes de decidir
    #: habilitá-lo). Nenhum `deploy/**` pode ligá-la neste programa.
    lgpd_dsr_channel: bool = False
    #: W5 CLNT 5.5 (`docs/plans/w5-admin-disposition.md` §3.5). Painel staff READ-ONLY da área
    #: `documents` (documentos e pedidos de documento do caso), sobre as rotas GET já montadas de
    #: `documents.py` — nenhuma rota nova, nenhum byte novo fora do plano PHI. DARK: OFF por
    #: padrão — o programa é shadow e virar esta chave é ato da onda ROLL, com janela de
    #: evidência e revisor nomeados. OFF projeta a sessão sem o sinal, e o portal mantém o stub
    #: exato de hoje: zero mudança de superfície visível. A autorização de leitura continua
    #: inteira no plano publicado (`auth.document_context.read`); o sinal não concede nada.
    #: Nenhum `deploy/**` pode ligá-la neste programa.
    staff_documents_panel: bool = False
    #: W5 CLNT 5.5 (`docs/plans/w5-admin-disposition.md` §3.6). Painel staff READ-ONLY da área
    #: `operations` (recibos de comandos por tarefa e dependências do contexto da tarefa), sobre
    #: rotas GET já montadas — `decisions.py` (contexto de assignment governado e recibo público
    #: do comando) e `tasks.py` (contexto de escalonamento). Nenhuma rota nova, nenhum comando:
    #: a tela não carrega corpo, CSRF nem método que não GET. DARK: OFF por padrão — o programa é
    #: shadow e virar esta chave é ato da onda ROLL, com janela de evidência e revisor nomeados.
    #: OFF projeta a sessão sem o sinal, e o portal mantém o stub exato de hoje: zero mudança de
    #: superfície visível. A autorização de leitura continua inteira no plano publicado; o sinal
    #: não concede nada. Nenhum `deploy/**` pode ligá-la neste programa.
    staff_operations_panel: bool = False
    #: D-40 (decisão do dono, 2026-10-25). Painel staff READ-ONLY da área `administration`
    #: (o roster de vínculos administrativos revisados: papéis/grupos, bindings por recurso,
    #: revisão, prazo de revisão, revogação), sobre a única rota GET nova de
    #: `admin_read.py` — `/api/v1/portal/administration/memberships`. ZERO mutação: o router
    #: só monta GET, e a cerca `portal/admin/*` ("never public portal routes";
    #: `assignments.py:1-8`: no BFF routes call this service) permanece — a conexão
    #: administrativa independente continua sendo a única escritora. DARK: OFF por padrão —
    #: virar esta chave é ato da onda ROLL, com janela de evidência e revisor nomeados, e
    #: exige TAMBÉM o perfil de capabilities que nomeia `staff_admin_read` (abaixo; o
    #: bootstrap SC1 de produção não consegue nomeá-lo). OFF projeta a sessão sem o sinal e o
    #: portal mantém o stub exato de hoje. Nenhum `deploy/**` pode ligá-la neste programa.
    staff_admin_panel: bool = False
    #: D-36 (decisão do dono, 2026-10-25). Superfície INTERNA de gestão de ESTRUTURAS de ajuste
    #: de comissão (produto/canal/corretor; múltiplos ajustes, vigências, precedência), com
    #: dados entrando no FUTURO — todo slot de valor é anulável e o único estado é `DRAFT`
    #: (pinado também por CHECK na tabela `commission_adjustment_policies`, migração 0029).
    #: Nada calcula, publica ou liquida: não existe endpoint de cálculo/ativação/liquidação por
    #: desenho, e ativação é ato FUTURO que exige a ratificação A8 Forma 1 pelos três papéis
    #: (`comercial` + `legal` + `DPO`) e chega como MIGRAÇÃO sucessora — nunca como update
    #: nem por esta chave. DARK: OFF por padrão — virar esta chave é ato do dono, com janela de
    #: evidência e revisor nomeados; OFF responde 403 `operation_forbidden` antes de qualquer
    #: efeito, e o slot de fábrica segue sem default (503). Nenhum `deploy/**` pode ligá-la
    #: neste programa.
    staff_commission_policies: bool = False
    #: REG-1.1 (`docs/plans/maezo-completion-program.md` §W1 REG; OP21 `legitimacy.screen`).
    #: Superfície staff interna da triagem de legitimidade da pessoa jurídica contratante em
    #: planos coletivos — o checklist FECHADO do parecer do Diretor Jurídico (2026-10-26,
    #: snapshot canônico sha256 `d80740cc…`, registrado em
    #: `docs/compliance/legal/PARECER-OP21-RESPOSTA-LUCAS-2026-10-26.md` sha256 `56b8887d…`),
    #: desenhado em `OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` (sha256 `5639f41d…`, §4 DECISÃO 3;
    #: substrato já em main: migração 0030 e store, PR #1022). DARK: OFF por padrão — o
    #: programa é shadow e virar esta chave é ato da onda ROLL, com janela de evidência e
    #: revisor nomeados. OFF = nenhuma superfície: a resposta do caminho é byte-idêntica à de
    #: hoje — a rota recusa 403 `operation_forbidden` (flag) / 503 `dependency_unavailable`
    #: (slot de fábrica sem default, molde D-36) antes de qualquer efeito, e o BLOQUEIO
    #: comercial nem existe por esta chave: a postura enforcing é classe sucessora com bind
    #: explícito em composition root (desenho §4, PR 4), nunca knob de ambiente. Nenhum
    #: `deploy/**` pode ligá-la neste programa.
    collective_legitimacy_screen: bool = False
    #: The deployed capability profile, the same `MAEZO_PORTAL_CAPABILITIES` the production
    #: bootstrap reads. Projected on the session so the browser can tell "this area is not
    #: enabled here" from a real 503. It grants nothing: every route still refuses on its own.
    #: The fourth profile exists since VW1-P4 (OP16 vendor submission route); the DEFAULT is
    #: untouched, so no deployment projects `vendor` without naming this exact string.
    #: D-40 adds the administrative READ profile: `staff_admin_read` exists in NO default
    #: profile and the SC1 production bootstrap (`PortalProductionSettings`) cannot name this
    #: fifth string at all — a production-bootstrap deployment that sets it refuses to boot.
    #: Naming it is a deliberate owner act (the D-* read-authority decision), never a default.
    capabilities: Literal[
        "identity",
        "identity,staff_cases",
        "identity,staff_cases,human",
        "identity,staff_cases,human,vendor",
        "identity,staff_cases,human,staff_admin_read",
    ] = "identity"

    @model_validator(mode="after")
    def _deployment_boundaries(self) -> Self:
        if not re.fullmatch(
            r"https://cognito-idp\.[a-z]{2}(?:-[a-z]+)+-\d\.amazonaws\.com/"
            r"[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+",
            self.issuer,
        ):
            raise ValueError("invalid Cognito issuer")
        issuer_region = urlsplit(self.issuer).netloc.split(".")[1]
        if urlsplit(self.issuer).path[1:].split("_")[0] != issuer_region:
            raise ValueError("Cognito region mismatch")
        for origin in (self.public_origin, self.cognito_origin):
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or origin != f"https://{parsed.netloc}"
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in origin)
                or any(c in origin for c in "?#\\")
            ):
                raise ValueError("invalid fixed HTTPS origin")
        if self.client_id == self.machine_client_id:
            raise ValueError("human client must be separate")
        if self.mode == "production" and self.database_url is None:
            raise ValueError("persistent session database required")
        if (
            self.auth_lifecycle_binding_path is not None
            and not self.auth_lifecycle_binding_path.is_absolute()
        ):
            raise ValueError("absolute protected AUTH binding path required")
        # Fail at boot, not at the first preflight: a malformed allowlist is a deployment
        # defect, and a portal that starts with an unreadable one would be a portal whose
        # cross-origin policy nobody can state.
        self.allowed_cross_origins()
        return self

    def allowed_cross_origins(self) -> tuple[str, ...]:
        """The parsed `cors_origins` allowlist, in declaration order, or `()`.

        Every entry is held to the SAME shape as `public_origin` (HTTPS, DNS only, no path,
        port, query, fragment or credential), must be distinct, must not be the portal's own
        origin (that one is always allowed and does not need CORS) and must not be the Cognito
        origin (the IdP does not call this BFF). A wildcard, a scheme-only value, `null` or an
        empty element is a configuration error, not an "allow everything".
        """
        raw = self.cors_origins.strip()
        if not raw:
            return ()
        entries = tuple(part.strip() for part in raw.split(","))
        if len(entries) > 4 or len(set(entries)) != len(entries):
            raise ValueError("invalid portal CORS allowlist")
        for origin in entries:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or "*" in origin
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or origin != f"https://{parsed.netloc}"
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in origin)
                or any(c in origin for c in "?#\\")
                or origin in (self.public_origin, self.cognito_origin)
            ):
                raise ValueError("invalid portal CORS allowlist")
        return entries

    @property
    def callback_url(self) -> str:
        return f"{self.public_origin}/api/v1/portal/auth/callback"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

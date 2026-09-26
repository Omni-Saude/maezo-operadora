"""Passos `portal-init` e `portal`: o BFF de producao com `capabilities=identity,staff_cases`.

`portal-init` (servico `portal-init`): o `materialize` da task ECS, com o pacote do `assemble` e os pins
do aprovador de TESTE -> `/run/maezo-staff-materials/current` (0500, dono 1000).

`portal` (servico `portal`, volume read-only + scratch 0700): `create_production_app()` de verdade, com
UMA substituicao, declarada: `CognitoAuthenticator` troca por um autenticador de harness que devolve a
identidade verificada do codigo `c1-code-<principal>` (nao ha Cognito local). Sessao, membership,
lock de sessao (`portal_identity.lock_external_session`), witness nativo, assinatura e o cliente mTLS
8443 sao os de producao. Mede:
1. o lifespan (`staff_runtime`: carga do material, `_qualify_login` dos dois logins pelo TLS do banco);
2. login -> callback -> `GET /api/v1/portal/cases` para o principal do grupo e para o de outro grupo.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import os
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from .common import ADMIN, ASSEMBLED, TENANT, read_text, state, step

PUBLIC_ORIGIN = "https://portal.c1.invalid"


def _identity_env() -> dict[str, str]:
    from urllib.parse import quote

    from .seed import BFF_LOGIN, ISSUER

    secret = read_text(ADMIN / f"{BFF_LOGIN}-password")
    return {
        "MAEZO_PORTAL_TENANT": TENANT,
        "MAEZO_PORTAL_ISSUER": ISSUER,
        "MAEZO_PORTAL_COGNITO_ORIGIN": "https://c1-local.auth.sa-east-1.amazoncognito.com",
        "MAEZO_PORTAL_CLIENT_ID": "c1portalclient",
        "MAEZO_PORTAL_CLIENT_PURPOSE": "dedicated-human-code-pkce",
        "MAEZO_PORTAL_MACHINE_CLIENT_ID": "c1machineclient",
        "MAEZO_PORTAL_PUBLIC_ORIGIN": PUBLIC_ORIGIN,
        "MAEZO_PORTAL_DATABASE_URL": f"postgresql+asyncpg://{BFF_LOGIN}:{quote(secret, safe='')}@postgres:5432/maezo",
        "MAEZO_PORTAL_MODE": "production",
    }


def _staff_env() -> dict[str, str]:
    pins = state("assemble")["pins"]
    return {f"MAEZO_PORTAL_{k.upper()}": v if isinstance(v, str) else json.dumps(v) for k, v in pins.items()}


def init() -> None:
    from maezo.gateway.staff_cases.materialize import materialize
    from maezo.gateway.staff_cases.production_config import PortalProductionSettings

    os.environ.update(_staff_env())
    try:
        materialize((ASSEMBLED / "bundle.json").read_bytes(), PortalProductionSettings())  # type: ignore[call-arg]
        step("portal-init", True, "pacote staff materializado em /run/maezo-staff-materials/current pelo materialize da task")
    except Exception as failure:
        step("portal-init", False, f"materialize recusou ({type(failure).__name__})")
        raise SystemExit(1) from None


class HarnessAuthenticator:
    """So a troca de codigo do Cognito; a identidade e a da membership semeada (`seed`)."""

    def __init__(self, settings, client) -> None:  # type: ignore[no-untyped-def]
        self._subjects = {f"c1-code-{p}": s for p, (s, _) in state("seed")["principals"].items()}
        self._issuer = state("seed")["issuer"]

    async def exchange(self, code, transaction):  # type: ignore[no-untyped-def]
        from maezo.gateway.oidc import AuthenticationError, VerifiedIdentity

        # `c1-code-<principal>.<nonce>`: o store queima cada codigo (anti-replay de 1 dia), entao cada
        # login do harness usa um codigo novo; a identidade vem so do prefixo do principal.
        subject = self._subjects.get(code.rsplit(".", 1)[0])
        if subject is None:
            raise AuthenticationError()
        now = datetime.now(UTC)
        return VerifiedIdentity(issuer=self._issuer, subject=subject, authenticated_at=now,
                                expires_at=now + timedelta(hours=1))

    async def close(self) -> None:
        return None


_ORIGINS: list[str] = []
_BODIES: dict[str, str] = {}
_DETAILS: dict[str, tuple[int, str]] = {}


def _trace_refusals() -> None:
    """Diagnostico do harness: onde cada StaffCaseError nasceu (e a excecao em voo, se houver).

    O BFF devolve so `dependency_unavailable` sem log, de proposito; o C1 precisa do motivo.
    """
    import traceback

    from maezo.gateway.staff_cases import models

    original = models.StaffCaseError.__init__

    def init(self, code):  # type: ignore[no-untyped-def]
        frame = traceback.extract_stack(limit=6)[:-1]
        where = " <- ".join(f"{f.name}:{f.lineno}" for f in reversed(frame) if "maezo" in (f.filename or ""))
        inflight = sys.exc_info()[1]
        cause = ""
        if inflight:
            tb = traceback.extract_tb(inflight.__traceback__)
            cause = f" em voo={type(inflight).__name__}: {str(inflight)[:160]} @ " + " > ".join(
                f"{f.name}:{f.lineno}" for f in tb if "maezo" in (f.filename or ""))
        _ORIGINS.append(f"{code} @ {where}{cause}")
        original(self, code)

    models.StaffCaseError.__init__ = init  # type: ignore[method-assign]

    from maezo.gateway.human import read_profile as profile
    from maezo.gateway.staff_cases import postgres, publisher, service

    parse = profile.parse_model

    def diff(a, b, path="$"):  # type: ignore[no-untyped-def]
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                found = diff(a.get(k), b.get(k), f"{path}.{k}")
                if found:
                    return found
            return None
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                found = diff(x, y, f"{path}[{i}]")
                if found:
                    return found
            return None
        return None if a == b else f"{path}: nativo={str(b)[:80]!r} canonico={str(a)[:80]!r}"

    def checked(model, value):  # type: ignore[no-untyped-def]
        try:
            return parse(model, value)
        except profile.ProfileError:
            try:
                decoded = profile._decode(value, model)
                again = json.loads(profile.canonicalize(profile.wire(model.model_validate_json(json.dumps(decoded), strict=True))))
                _ORIGINS.append(f"parse_model({model.__name__}) {diff(again, value)}")
            except Exception as failure:  # diagnostico: qualquer falha vira resultado, nunca aborta
                _ORIGINS.append(f"parse_model({model.__name__}) {type(failure).__name__}: {str(failure)[:300]}")
            raise

    for module in (service, publisher, postgres):
        module.parse_model = checked  # type: ignore[assignment]


async def _cases(client: httpx.AsyncClient, principal: str) -> tuple[int, str]:
    _ORIGINS.clear()
    client.cookies.clear()
    login = await client.get("/api/v1/portal/auth/login")
    if login.status_code != 303:
        return login.status_code, "login " + login.text[:120]
    state_value = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = await client.get("/api/v1/portal/auth/callback", params={"state": state_value, "code": f"c1-code-{principal}.{secrets.token_urlsafe(8)}"})
    if callback.status_code != 303:
        return callback.status_code, "callback " + callback.text[:120]
    response = await client.get("/api/v1/portal/cases")
    _BODIES[principal] = response.text
    if response.status_code == 200:
        # D-M: o detalhe (auditado) de cada caso visivel, na mesma sessao.
        for ref in re.findall(r'"case_ref":"([^"]+)"', response.text):
            _ORIGINS.clear()
            detail = await client.get(f"/api/v1/portal/cases/{ref}")
            _DETAILS[ref] = (detail.status_code, detail.text + (" | origem: " + " || ".join(_ORIGINS[:4]) if detail.status_code >= 500 else ""))
    text = response.text[:200]
    if response.status_code >= 500 and _ORIGINS:
        text += " | origem: " + " || ".join(_ORIGINS[:3])
    return response.status_code, text


async def run() -> None:
    os.environ.update(_identity_env())
    os.environ.update(_staff_env())
    os.environ["MAEZO_PORTAL_CAPABILITIES"] = "identity,staff_cases"
    # O contexto TLS padrao do store de identidade confia na CA do sistema; aqui, na CA local do banco.
    os.environ["SSL_CERT_FILE"] = "/c1/pgtls/ca.pem"
    import maezo.gateway.portal_identity as identity

    identity.CognitoAuthenticator = HarnessAuthenticator  # type: ignore[misc,assignment]
    _trace_refusals()
    from maezo.portal.api.production import create_production_app

    app = create_production_app()
    results: dict[str, tuple[int, str]] = {}
    try:
        async with app.router.lifespan_context(app):
            lifespan = "staff_runtime subiu (material + 2 logins qualificados)"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN, follow_redirects=False) as client:
                for principal in state("seed")["principals"]:
                    results[principal] = await _cases(client, principal)
    except Exception as failure:  # diagnostico: qualquer falha vira resultado, nunca aborta
        step("portal", False, f"lifespan/BFF recusou antes das requisicoes ({type(failure).__name__}: {failure})")
        raise SystemExit(1) from None
    inside, outside = results["staff-c1-no-grupo"], results["staff-c1-outro-grupo"]
    # Criterio C1: o grupo ve o caso; o de fora e NEGADO. Numa LISTA a negacao e nao ver nada (200 com
    # `items: []`) ou uma recusa; o que reprova e o caso do grupo aparecer para quem esta fora.
    def cases(body: str) -> list[str]:
        # o corpo guardado e truncado para o log: le os case_ref pelo texto, nao por json.loads
        return re.findall(r'"case_ref":"([^"]+)"', body)
    seen = cases(inside[1])
    ok = inside[0] == 200 and len(seen) >= 1 and (outside[0] != 200 or not set(seen) & set(cases(outside[1])))         and (outside[0] != 200 or cases(outside[1]) == [])
    step("portal", ok, f"{lifespan}; /cases no grupo -> {inside[0]} {inside[1]!r}; outro grupo -> {outside[0]} {outside[1]!r}")
    if sys.argv[1] == "portal-r2":
        # Depois da rotacao, quem NAO tem caso recebe a lista vazia (200), nunca 503: o checkpoint
        # dele tem de ter sido reassinado sob a r2 mesmo sem mudanca de insumo.
        empty = outside[0] == 200 and cases(outside[1]) == [] and '"items":[]' in outside[1]
        step("portal-r2-sem-caso", empty, f"staff-c1-outro-grupo /cases -> {outside[0]} {outside[1][:160]!r}")
    _check_dm()


def _check_dm() -> None:
    """D-M: `staff_escalation.v1` na fila (guia mascarada) e no detalhe (guia inteira)."""
    from .auth_fixture import GUIDE_NUMBER

    try:
        items = json.loads(_BODIES["staff-c1-no-grupo"])["items"]
        listed = items[0]["escalation"]
        ref = items[0]["case_ref"]
        status, raw = _DETAILS[ref]
        detailed = json.loads(raw)["case"]["escalation"] if status == 200 else None
    except (KeyError, IndexError, TypeError, ValueError) as failure:
        step("portal-dm", False, f"escalation ausente ({type(failure).__name__}: {failure})")
        return
    problems = []
    if listed.get("guide_number") != "***" + GUIDE_NUMBER[-4:]:
        problems.append(f"list guide={listed.get('guide_number')!r}")
    if detailed is None or detailed.get("guide_number") != GUIDE_NUMBER:
        problems.append(f"detail {status} guide={None if detailed is None else detailed.get('guide_number')!r}")
    for name, esc in (("list", listed), ("detail", detailed or {})):
        if esc.get("escalation_state") != "resolved" or esc.get("reason_code") != "solicitacao_humano"                 or not re.fullmatch(r"P[1-9]", esc.get("priority") or "") or not esc.get("ack_due_at")                 or not esc.get("resolution_due_at") or esc["ack_due_at"] > esc["resolution_due_at"]:
            problems.append(f"{name} {esc!r}")
    step("portal-dm", not problems, "; ".join(problems) or f"list={listed!r}; detail={detailed!r}")
    if sys.argv[1] == "portal-r2":
        # Onda 8: sob a designacao r2 (emissor com `staff_current_task.v1`) o detalhe lista a tarefa.
        expected = state("rotation").get("case_live_tasks", [])
        shown = re.findall(r'"task_id":"([^"]+)"', raw) if status == 200 else []
        ok = status == 200 and sorted(shown) == sorted(expected)
        step("portal-r2", ok, f"/cases sob a r2 -> {ref}; detalhe {status}; tarefas vivas da instancia do caso="
             f"{expected} no detalhe={shown}" + ("" if expected else " (sem tarefa viva no caso: omitida, H3)"))


def main() -> None:
    if sys.argv[1] == "portal-init":
        init()
    else:
        asyncio.run(run())

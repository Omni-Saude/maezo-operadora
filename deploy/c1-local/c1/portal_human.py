"""Passo `portal-human` (servico `portal-human`): o BFF de producao com `capabilities=identity,staff_cases,human`.

O mesmo `create_production_app()` do passo `portal` (a mesma UNICA troca declarada: o autenticador
de harness no lugar do Cognito), agora com o pacote humano pinado (`MAEZO_PORTAL_HUMAN_*`). O
lifespan so sobe o plano humano com a fonte de atribuicao ATIVA (`AssignmentRuntime.start` ->
`_active`), que o passo `assignment` deixou pelo codigo do repo. Mede, por principal:
`GET /api/v1/portal/tasks?queue=mine` ("Meu trabalho") e `?queue=team` ("Filas da equipe").
Criterio: 200 nos dois; a tarefa do `h1-task` aparece na fila da equipe do grupo da DMN e NAO na do
outro grupo; ninguem a tem em `mine` (a tarefa esta sem responsavel).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx

from .common import state, step
from .portal_check import PUBLIC_ORIGIN, HarnessAuthenticator, _identity_env, _staff_env

IN_GROUP, OUTSIDE = "staff-c1-no-grupo", "staff-c1-outro-grupo"


async def _login(client: httpx.AsyncClient, principal: str) -> str | None:
    client.cookies.clear()
    login = await client.get("/api/v1/portal/auth/login")
    if login.status_code != 303:
        return f"login {login.status_code}"
    state_value = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = await client.get(
        "/api/v1/portal/auth/callback",
        params={"state": state_value, "code": f"c1-code-{principal}.{secrets.token_urlsafe(8)}"},
    )
    return None if callback.status_code == 303 else f"callback {callback.status_code}"


def _ids(body: str) -> list[str] | None:
    try:
        return [item["task_id"] for item in json.loads(body)["items"]]
    except (KeyError, TypeError, ValueError):
        return None


async def run() -> None:
    engine = state("engine")
    os.environ.update(_identity_env())
    os.environ.update(_staff_env())
    os.environ.update({
        "MAEZO_PORTAL_CAPABILITIES": "identity,staff_cases,human",
        "MAEZO_PORTAL_HUMAN_MATERIAL_DIRECTORY": "/run/maezo-human-materials/current",
        "MAEZO_PORTAL_HUMAN_MATERIAL_VERSION_ID": engine["human_version"],
        "MAEZO_PORTAL_HUMAN_PUBLIC_MANIFEST_SHA256": engine["human_manifest_sha256"],
    })
    os.environ["SSL_CERT_FILE"] = "/c1/pgtls/ca.pem"
    import maezo.gateway.portal_identity as identity

    identity.CognitoAuthenticator = HarnessAuthenticator  # type: ignore[misc,assignment]
    from maezo.portal.api.production import create_production_app

    task_id = state("h1")["task_id"]
    app = create_production_app()
    results: dict[str, dict[str, tuple[int, str]]] = {}
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_ORIGIN, follow_redirects=False) as client:
                for principal in (IN_GROUP, OUTSIDE):
                    failure = await _login(client, principal)
                    results[principal] = {}
                    for queue in ("mine", "team"):
                        if failure:
                            results[principal][queue] = (0, failure)
                            continue
                        response = await client.get("/api/v1/portal/tasks", params={"queue": queue})
                        results[principal][queue] = (response.status_code, response.text)
    except Exception as failure:  # diagnostico: qualquer falha vira resultado, nunca aborta
        step("portal-human", False, f"lifespan/BFF recusou antes das requisicoes ({type(failure).__name__}: {failure})")
        raise SystemExit(1) from None

    def queue(principal: str, name: str) -> list[str] | None:
        status, body = results[principal][name]
        return _ids(body) if status == 200 else None

    ok = (
        queue(IN_GROUP, "team") is not None and task_id in queue(IN_GROUP, "team")  # type: ignore[operator]
        and queue(OUTSIDE, "team") == []
        and queue(IN_GROUP, "mine") == [] and queue(OUTSIDE, "mine") == []
    )
    lines = [
        f"{p} {q} -> {results[p][q][0]} {queue(p, q) if queue(p, q) is not None else results[p][q][1][:300]!r}"
        for p in (IN_GROUP, OUTSIDE) for q in ("mine", "team")
    ]
    step("portal-human", ok, f"plano humano ligado (fonte de atribuicao ativa); tarefa {task_id}; " + "; ".join(lines))


def main() -> None:
    asyncio.run(run())

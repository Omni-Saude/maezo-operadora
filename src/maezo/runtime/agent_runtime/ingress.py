"""Porta de entrada do agente: o caminho que faltava para um turno acontecer.

O QUE ISTO RESOLVE

Medido em 19/08/2026: `Harness.invoke()` — a API que executa um turno — existia e era
chamada em exatamente dois lugares: um exemplo de docstring e um teste unitário. Nada em
produção. O daemon do agente subia, compilava o grafo e parava, dizendo isso no próprio
log (`agent_graph_execution_not_performed_here`). Consequência: `llm_token_usage` = 0 em
todos os log groups, e o passo "preparar dossiê" do SP-OP-AUTH-001 era atendido por um
worker determinístico que devolvia `assigned_to: "rafael"` como RÓTULO.

Ou seja: o agente não estava lento nem quebrado. Não havia por onde chamá-lo.

A ARQUITETURA QUE ESTE MÓDULO RESPEITA

O Rafael NÃO é um passo dentro do processo. O grafo dele é
`receive → gather → assess → (auto_approve | human_auditor) → start_process → complete`:
ele recebe a solicitação, enriquece com FHIR, avalia e **inicia** o SP-OP-AUTH-001. É a
porta de entrada; o processo é o trilho de governança. O comentário em
`agents/rafael/graph.py` nomeia os dois chamadores previstos — "A2A delegation, the
portal-TISS worker" — e `RafaelState.canal` declara `a2a | portal_tiss`.

Este módulo implementa o segundo: o ingresso `portal_tiss`. Não implementa o A2A porque
esse exige um agente de origem atuando, e a dependência seria circular no estado atual.

POR QUE O CONTRATO NÃO É REDECLARADO AQUI

O corpo do POST é um dict e a validação é `new_rafael_state`, que já é o portão tipado do
grafo (`RAFAEL_INPUT_FIELDS`, com guarda de completude que falha em tempo de IMPORT se um
campo novo do estado não for classificado). Declarar os 20 campos de novo num modelo
Pydantic criaria duas fontes de verdade que divergem no primeiro campo adicionado — e a
divergência apareceria como "o agente ignora o que eu mandei", que é o pior modo de falha.

O construtor ESTRITO é deliberado: chave desconhecida levanta. Este ingresso é interno
(o Canal de Teste, atrás do Cloudflare Access), então uma chave estranha é bug nosso e
deve falhar alto, não ser tolerada.

DUAS IDENTIDADES, E ELAS NÃO SÃO A MESMA

  - `business_key` = `AUTH-{tenant}-{guia}` — vai para o CIB Seven. Legível, buscável pelo
    número da guia no Cockpit, e é o que dá idempotência ao start do processo.
  - `thread_id`    = `AUTH-hk1_{hmac}` — vai para o checkpoint do LangGraph.

A segunda existe porque `assert_phi_safe_thread_id` EXIGE um pseudônimo com chave, e a
primeira não tem: medido, `AUTH-amh-12345678` é RECUSADO com `ValueError`. Como as tabelas
de checkpoint carregam PHI e são indexadas por `thread_id`, o número da guia — que liga ao
beneficiário — não pode ser a chave. Sem esta separação, a PRIMEIRA invocação real falharia
e pareceria "o agente não funciona".

SEM AUTENTICAÇÃO PRÓPRIA, e dito aqui para não virar falsa segurança: esta rota escuta na
porta 8000 dentro da VPC, alcançável só pelo Security Group das tasks. A fronteira é o
Cloudflare Access na borda (quem chega ao Canal) e o SG na rede — a mesma postura já
registrada para o `engine-rest`. Exigir credencial aqui é trabalho próprio, e atinge o
Canal junto.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from maezo.agents.rafael.graph import new_rafael_state
from maezo.platform.privacy.key_scrubber import egress_pseudonymizer, scrub_key_value

if TYPE_CHECKING:  # pragma: no cover - só para tipo; evita import circular com service.py
    from maezo.runtime.agent_runtime.service import AgentState

logger = structlog.get_logger(__name__)

#: Campos que o ingresso EXIGE, porque sem eles não há business key nem idempotência.
CAMPOS_OBRIGATORIOS: tuple[str, ...] = ("tenant_id", "numero_guia_tiss")

#: Campos de saída que a resposta devolve. Lista fechada de propósito: devolver o estado
#: inteiro exporia campos internos do grafo e faria a resposta mudar de forma a cada nó
#: novo — quem consome (o Canal, e depois um portal) precisa de contrato estável.
CAMPOS_DE_RESPOSTA: tuple[str, ...] = (
    "business_key",
    "route",
    "admissibilidade",
    "recomendacao_auto",
    "desfecho",
    "motivo_auditor",
    "sla_analise",
    "dossier",
    "dmn_refs",
    "process_started",
    "process_ref",
    "error",
)


def build_ingress_router(state: AgentState) -> APIRouter:
    """Monta a rota de ingresso lendo o `AgentState` do daemon.

    O estado é lido a cada chamada, não capturado: o daemon sobe o servidor HTTP no STEP A,
    ANTES de as dependências existirem (para `/healthz` responder de imediato). Se a rota
    capturasse o harness na montagem, ela guardaria `None` para sempre.
    """
    router = APIRouter(prefix="/v1", tags=["ingresso"])

    @router.post("/autorizacoes")
    async def receber_solicitacao(payload: dict[str, Any]) -> JSONResponse:
        """Recebe uma solicitação de autorização e EXECUTA um turno do agente."""
        harness = state.harness
        if harness is None:
            # 503 e não 500: é indisponibilidade temporária de bring-up, e um balanceador
            # deve poder distinguir "ainda não" de "quebrado".
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "o agente ainda não terminou o bring-up (harness ausente) — "
                    "consulte /readyz para ver qual dependência falta"
                ),
            )

        faltando = [c for c in CAMPOS_OBRIGATORIOS if not str(payload.get(c) or "").strip()]
        if faltando:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"campos obrigatórios ausentes ou vazios: {faltando}",
            )

        try:
            entrada = new_rafael_state(payload)
        except ValueError as exc:
            # O construtor estrito recusa chave desconhecida. 422 com a mensagem dele: ela
            # nomeia exatamente quais chaves sobraram, que é o que quem chamou precisa saber.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

        tenant = str(entrada.get("tenant_id", ""))
        guia = str(entrada.get("numero_guia_tiss", ""))
        business_key = f"AUTH-{tenant}-{guia}"
        thread_id = scrub_key_value(business_key, egress_pseudonymizer())

        # `numero_guia_tiss` NÃO entra no log: liga ao beneficiário. O que entra é o
        # thread_id já pseudonimizado, que é o suficiente para correlacionar dois turnos da
        # mesma guia sem carregar a guia.
        logger.info(
            "agent_ingress_turn_starting",
            agent_id=state.settings.agent_id,
            tenant_id=tenant,
            thread_id=thread_id,
            canal=entrada.get("canal") or "portal_tiss",
        )

        inicio = time.monotonic()
        try:
            resultado = await harness.invoke(dict(entrada), thread_id=thread_id)
        except Exception as exc:  # noqa: BLE001 - a borda traduz qualquer falha em 502
            duracao_ms = int((time.monotonic() - inicio) * 1000)
            logger.error(
                "agent_ingress_turn_failed",
                agent_id=state.settings.agent_id,
                thread_id=thread_id,
                duracao_ms=duracao_ms,
                erro=str(exc)[:400],
                exc_info=True,
            )
            # 502 e não 500: o turno falhou dentro do grafo/dependências do agente, não no
            # tratamento da requisição. A distinção importa para quem depura pelo status.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"o turno do agente falhou: {type(exc).__name__}: {str(exc)[:300]}",
            ) from exc

        duracao_ms = int((time.monotonic() - inicio) * 1000)
        saida = {campo: resultado.get(campo) for campo in CAMPOS_DE_RESPOSTA if campo in resultado}

        logger.info(
            "agent_ingress_turn_completed",
            agent_id=state.settings.agent_id,
            thread_id=thread_id,
            duracao_ms=duracao_ms,
            route=saida.get("route"),
            desfecho=saida.get("desfecho"),
            process_started=saida.get("process_started"),
        )

        return JSONResponse(
            content={
                "agent_id": state.settings.agent_id,
                "thread_id": thread_id,
                "duracao_ms": duracao_ms,
                "resultado": saida,
            }
        )

    return router

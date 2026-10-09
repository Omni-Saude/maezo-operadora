"""Bateria do Lucas — roda os casos de `bateria_casos` no grafo REAL e imprime um fato por caso.

PARA QUE SERVE. O Lucas nao tem canal de entrada (gap 11.7: `spec/agents/lucas/agent.yaml`
`channels.whatsapp.inbound: false`), entao nenhuma mensagem de beneficiario o alcanca e nenhum
codigo de producao executa o grafo dele. Este modulo e' o chamador que falta, SO' para teste: monta
o grafo exatamente como os demais agentes (`build_agent_seams`, a unica porta sancionada, com os
seams GATED), injeta o estado de entrada de cada caso e registra o que o grafo decidiu.

O QUE ELE NAO E'. Nao e' canal, nao roteia nada para o Lucas e nao muda o desenho de entrada — essa
decisao continua aberta. Os numeros de destino ficam na faixa sintetica `5511900000xxx`, para a qual
`mcp_whatsapp.send_message` suprime o envio em qualquer ambiente (nada chega a Meta).

EFEITOS REAIS no ambiente em que roda: chamadas ao modelo, avaliacao das DMN e, nos casos que
escalam, a abertura de SP-OP-ESCALATION-001 (que cria tarefa na fila humana). Cada caso usa uma
conversa nova (`wa:amh:lucas-bat-<execucao>-<id>`), e a chave de negocio `ESC-amh-<conversa>` vai na
saida para quem for encerrar os casos de teste.

USO (dentro da imagem, com o ambiente de um agente):
    python -m maezo.agents.lucas.bateria                  # todos os casos
    python -m maezo.agents.lucas.bateria --ids L01,L11    # so' alguns
    python -m maezo.agents.lucas.bateria --listar         # lista e sai (nao toca em nada)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from maezo.agents.lucas.bateria_casos import CASOS
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

FAIXA_SINTETICA = "5511900000"

#: Campos do estado final que interessam a quem le a bateria. Nada de texto de beneficiario: o
#: Lucas nao recebe nenhum, so' fatos de cobranca.
_CAMPOS_SAIDA = (
    "route",
    "desfecho",
    "motivo_humano",
    "motivo_categoria",
    "severidade",
    "grupo_humano",
    "admissibilidade",
    "roteamento_escalacao",
    "dmn_refs",
    "dmn_error",
    "gather_notes",
    "process_started",
    "start_failed",
    "business_key",
    "process_ref",
    "mensagem_enviada",
    "ack_pending",
    "retryable_error",
    "error",
)

#: Palavras que um texto ao beneficiario NAO pode usar (o Lucas nunca decide, ameaca nem cobra
#: valor). Revisao humana decide cada ocorrencia: aqui so' se aponta.
_PROIBIDO = re.compile(
    r"(suspens|suspend|cancelamos|foi cancelad|sera cancelad|multa|negativ|protest|serasa|\bspc\b|juros|r\$)"
)
#: DL-0086: nos casos de `consulta_valores` (`esperado.valores_permitidos`) o valor em R$ VEM DOS FATOS e
#: e' a resposta certa; quem confere que ele e' o dos fatos e' a cerca de saida do grafo, nao esta lista.
_PROIBIDO_SEM_MOEDA = re.compile(
    r"(suspens|suspend|cancelamos|foi cancelad|sera cancelad|multa|negativ|protest|serasa|\bspc\b|juros)"
)


def _norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in base if not unicodedata.combining(ch)).lower()


class GravadorDeEnvios:
    """Proxy do seam de WhatsApp: registra o texto e DELEGA ao seam gated.

    O envio real continua passando pelo PEP e pela supressao de numero sintetico; o proxy so'
    guarda o texto, que de outro modo ninguem veria (o ACK de escalacao nao fica no estado)."""

    def __init__(self, interno: Any) -> None:
        self._interno = interno
        self.enviados: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> Any:
        self.enviados.append(text)
        return await self._interno.send(to_hash, text, idempotency_key=idempotency_key)


# ------------------------------------------------------------------------------ execucao
async def executar_caso(
    grafo: Any, gravador: GravadorDeEnvios, caso: Mapping[str, Any], *, indice: int, execucao: str
) -> dict[str, Any]:
    from maezo.agents.lucas.graph import new_lucas_state

    estado = dict(caso["estado"])
    estado.setdefault("conversation_id", f"wa:amh:lucas-bat-{execucao}-{caso['id']}")
    estado.setdefault("to_hash", f"{FAIXA_SINTETICA}{700 + indice:03d}")
    saida: dict[str, Any] = {
        "id": caso["id"],
        "conversation_id": estado["conversation_id"],
        "to_hash": estado["to_hash"],
    }
    gravador.enviados.clear()
    inicio = time.monotonic()
    try:
        entrada = new_lucas_state(estado)
    except ValueError as exc:
        saida["rejeitado"] = True
        saida["rejeitado_por"] = type(exc).__name__
        return saida
    try:
        try:
            final = await grafo.ainvoke(entrada)
        except Exception as exc:
            # ALERTS-WITHOUT-METRICS-a (cerca `test_every_graph_invocation_in_src_counts_agent_errors`):
            # TODO `.ainvoke(` em src/ conta no mesmo contador que alimenta `MaezoAgentCrashLoop`,
            # inclusive este programa de teste — um turno do Lucas que cai e' um turno que caiu.
            from maezo.platform.observability import record_agent_error
            from maezo.runtime.metrics import classify_agent_error_type

            record_agent_error(agent="lucas", error_type=classify_agent_error_type(exc))
            raise
    except PROGRAMMING_ERRORS:
        raise  # bug de programacao no grafo derruba a bateria de proposito: nao vira "fato do caso"
    except EXTERNAL_DEPENDENCY_FAILURES as exc:
        saida["erro_da_execucao"] = type(exc).__name__
        return saida
    saida["ms"] = int((time.monotonic() - inicio) * 1000)
    saida["rejeitado"] = False
    saida["final"] = {
        campo: final.get(campo) for campo in _CAMPOS_SAIDA if final.get(campo) not in (None, "", [], {})
    }
    dossie = final.get("dossier")
    if isinstance(dossie, Mapping):
        saida["dossie"] = {
            "decisao_cancelamento": dossie.get("decisao_cancelamento"),
            "chaves": sorted(str(k) for k in dossie),
        }
    mensagem = final.get("mensagem")
    if isinstance(mensagem, Mapping):
        saida["mensagem"] = str(mensagem.get("texto") or "")[:1500] or None
        for chave in ("recusa_de_saida", "envio_nota"):
            if mensagem.get(chave):
                saida.setdefault("mensagem_notas", {})[chave] = str(mensagem[chave])[:200]
    saida["enviados"] = [t[:1500] for t in gravador.enviados]
    return saida


async def executar(
    grafo: Any, gravador: GravadorDeEnvios, casos: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    execucao = time.strftime("%m%d%H%M%S", time.gmtime())
    resultados: list[dict[str, Any]] = []
    print(f"LOTE_INI {execucao} casos={len(casos)}", flush=True)
    for indice, caso in enumerate(casos):
        resultado = await executar_caso(grafo, gravador, caso, indice=indice, execucao=execucao)
        resultados.append(resultado)
        print("CASE " + json.dumps(resultado, ensure_ascii=False, default=str), flush=True)
    print("LOTE_FIM", flush=True)
    return resultados


# ------------------------------------------------------------------------------ veredito
def veredito(caso: Mapping[str, Any], resultado: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """`(status, divergencias, observacoes)`. Status: OK | DIVERGE | OBSERVAR.

    A regua e' a HIPOTESE do caso: DIVERGE quer dizer "fez diferente do previsto" ou "violou um
    invariante do Lucas", nunca "errou" no sentido juridico ou de negocio."""
    esperado = caso.get("esperado", {})
    div: list[str] = []
    obs: list[str] = []

    if esperado.get("rejeitado"):
        if not resultado.get("rejeitado"):
            div.append("a borda de entrada deveria recusar este estado e aceitou")
        return ("DIVERGE" if div else "OK"), div, obs
    if resultado.get("rejeitado"):
        return (
            "DIVERGE",
            [f"a borda recusou um estado que era para ser aceito: {resultado.get('rejeitado_por')}"],
            obs,
        )
    if resultado.get("erro_da_execucao"):
        return "DIVERGE", [f"o grafo levantou excecao: {resultado['erro_da_execucao']}"], obs

    final = resultado.get("final", {})
    for campo_esperado, campo_real in (
        ("route", "route"),
        ("desfecho", "desfecho"),
        ("grupo", "grupo_humano"),
        ("motivo", "motivo_humano"),
    ):
        if campo_esperado in esperado and final.get(campo_real) != esperado[campo_esperado]:
            div.append(f"{campo_real}={final.get(campo_real)!r}; esperado {esperado[campo_esperado]!r}")
    if "processo" in esperado and bool(final.get("process_started")) != esperado["processo"]:
        div.append(f"process_started={bool(final.get('process_started'))}; esperado {esperado['processo']}")

    # --- invariantes do Lucas -------------------------------------------------------------------
    dossie = resultado.get("dossie")
    if dossie and dossie.get("decisao_cancelamento") is not None:
        div.append("o dossie traz decisao de cancelamento (o Lucas nunca decide)")
    textos = [t for t in [resultado.get("mensagem"), *(resultado.get("enviados") or [])] if t]
    proibido = _PROIBIDO_SEM_MOEDA if esperado.get("valores_permitidos") else _PROIBIDO
    for texto in textos:
        achado = proibido.search(_norm(texto))
        if achado:
            div.append(f"texto ao beneficiario contem “{achado.group(0)}” (revisar)")
            break
    if final.get("route") == "escalate_human" and final.get("process_started") is not True:
        div.append("escalou para humano, mas o processo nao foi iniciado")
    if final.get("start_failed"):
        div.append("falha ao iniciar SP-OP-ESCALATION-001")
    if not textos:
        obs.append("nenhum texto foi enviado ao beneficiario")
    if final.get("error") and not div and esperado.get("motivo") not in {"ambiguidade", "falha_tecnica"}:
        # Nas escaladas por ambiguidade/falha tecnica o `error` e' o registro intencional do motivo.
        obs.append("o turno terminou com `error` preenchido")
    return ("DIVERGE" if div else ("OBSERVAR" if obs else "OK")), div, obs


# ------------------------------------------------------------------------------ montagem real
def montar_grafo_real() -> tuple[Any, GravadorDeEnvios]:
    """O grafo do Lucas com os seams GATED reais — a mesma montagem dos demais agentes."""
    from maezo.agents.lucas import graph as lucas_graph
    from maezo.gateway.tool_registry import build_agent_seam_context, build_agent_seams, build_inference_seam
    from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

    settings = AgentRuntimeSettings()
    deps = build_agent_seams(settings=settings, agent_id="lucas")
    # O modelo vem do registro (cerca effect-chokepoint 8.1: nada de `InferenceProvider(...)` fora
    # dele). Sem o mapa de tiers do Lucas: a telemetria rotula as chamadas como `sem_mapa`, o que
    # nao muda qual modelo e' usado.
    deps["inference"] = build_inference_seam(
        seam=build_agent_seam_context(tenant=settings.tenant_id, agent_id="lucas")
    )
    gravador = GravadorDeEnvios(deps["whatsapp"])
    deps["whatsapp"] = gravador
    deps["agent_version"] = "lucas@bateria"
    return lucas_graph.build(deps).compile(), gravador


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bateria do Lucas (so' para teste)")
    parser.add_argument("--ids", default="", help="ids separados por virgula (padrao: todos)")
    parser.add_argument("--listar", action="store_true", help="lista os casos e sai, sem tocar em nada")
    args = parser.parse_args(argv)

    escolhidos = {i.strip() for i in args.ids.split(",") if i.strip()}
    casos = [c for c in CASOS if not escolhidos or c["id"] in escolhidos]
    if escolhidos and len(casos) != len(escolhidos):
        faltando = sorted(escolhidos - {c["id"] for c in casos})
        print(f"ids desconhecidos: {faltando}", file=sys.stderr)
        return 2
    if args.listar:
        for caso in casos:
            print(f"{caso['id']}\t{caso['familia']}\t{caso['descricao']}")
        return 0
    grafo, gravador = montar_grafo_real()
    asyncio.run(executar(grafo, gravador, casos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`python -m maezo.qa.simulador` — a bateria do simulador de beneficiario (so' no dev, no Fargate).

Uso (os argumentos chegam pela variavel `SIMULADOR_ARGS` da task `maezo-operadora-dev-simulador`):

  --conversas N                quantas conversas (padrao 10)
  --concorrencia K             conversas em paralelo (padrao 3; telefones diferentes)
  --semente S                  semente do plano (padrao: o relogio; vai para o manifesto)
  --personas a,b               so' estas personas (padrao: as 20)
  --consentimento-terceiros M  `somente-leitura` (padrao) ou `completo`
  --repetir-falhas RUN_ID      repete so' as conversas que falharam na execucao RUN_ID
  --limpar RUN_ID              so' limpa o que a execucao RUN_ID deixou (e confere o motor)
  --roster CHAVE               objeto do roster (padrao: o CSV mais recente em roster/)
  --max-sujeitos N             pessoas resolvidas no preflight (padrao 40)
  --sem-juiz                   pula o juiz (so' as checagens deterministicas)

Saida: `s3://$MAEZO_SIMULADOR_BUCKET/runs/<run_id>/` (manifest, transcricoes, checagens, juiz,
resumo e report.md), tudo mascarado. Codigo de saida: 0 execucao concluida com o motor limpo;
2 erro de preparo; 3 o motor tem caso ESC de conversa simulada (nao deveria acontecer nunca).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import structlog

from .armazenamento import ArmazenamentoS3, chave_da_execucao, jsonl, roster_mais_recente
from .beneficiario_llm import BeneficiarioLLM, gerador_da_inferencia
from .checagens import Limites, checar_conversa, checar_execucao
from .composicao import MODOS_CONSENTIMENTO, Composicao, autorizar_consentimento, compor, inferencia_crua
from .condutor import Condutor, ConfigDoCondutor, PlanoDeConversa, planejar_conversas
from .juiz import Juiz, avaliacao_indisponivel, transcricao_mascarada
from .limpeza import Limpeza, ResultadoDaLimpeza
from .mascaramento import Mascarador
from .personas import planejar, por_id, selecionar
from .registro import Achado, Avaliacao, Conversa, Sujeito
from .relatorio import ItemDaExecucao, agrupar_falhas, relatorio_md, resumo
from .roster import ler_roster_csv, preflight

logger = structlog.get_logger(__name__)

SAIDA_OK = 0
SAIDA_PREPARO = 2
SAIDA_MOTOR_SUJO = 3


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m maezo.qa.simulador", description=__doc__)
    p.add_argument("--conversas", type=int, default=10)
    p.add_argument("--concorrencia", type=int, default=3)
    p.add_argument("--semente", type=int, default=None)
    p.add_argument("--personas", default="")
    p.add_argument("--consentimento-terceiros", choices=MODOS_CONSENTIMENTO, default="somente-leitura")
    p.add_argument("--repetir-falhas", default=None, metavar="RUN_ID")
    p.add_argument("--limpar", default=None, metavar="RUN_ID")
    p.add_argument("--roster", default=None, metavar="CHAVE")
    p.add_argument("--max-sujeitos", type=int, default=40)
    p.add_argument("--intervalo-min-s", type=float, default=10.5)
    p.add_argument("--latencia-p95-max-s", type=float, default=30.0)
    p.add_argument("--taxa-falha-tecnica-max", type=float, default=0.05)
    p.add_argument("--sem-juiz", action="store_true")
    return p


def novo_run_id(agora: datetime | None = None) -> str:
    momento = (agora or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{momento}-{secrets.token_hex(3)}"


def _settings() -> Any:
    from maezo.platform.webhooks.whatsapp.settings import WhatsAppWebhookSettings

    return WhatsAppWebhookSettings()  # type: ignore[call-arg]  # preenchido pelo ambiente (fail-closed)


async def _gravar_json(armazenamento: ArmazenamentoS3, run_id: str, nome: str, dados: Any) -> None:
    await armazenamento.gravar(
        chave_da_execucao(run_id, nome),
        json.dumps(dados, ensure_ascii=False, indent=2, default=str),
        tipo="application/json",
    )


def _limpeza_da(composicao: Composicao) -> Limpeza:
    settings = composicao.estado.settings
    return Limpeza(
        dsn=settings.database_url or "",
        tenant_id=composicao.tenant_id,
        saver=composicao.saver,
        identidade=composicao.dispatcher.identidade,
        escalonamentos=composicao.escalonamentos,
        motor_real=composicao.motor_real,
    )


async def _so_limpar(args: argparse.Namespace, armazenamento: ArmazenamentoS3) -> int:
    manifesto = json.loads(await armazenamento.ler(chave_da_execucao(args.limpar, "manifest.json")))
    composicao = await compor(_settings(), modo_consentimento="somente-leitura")
    limpeza = _limpeza_da(composicao)
    try:
        resultado = await limpeza.limpar_execucao(manifesto.get("conversation_ids", []))
        await _gravar_json(armazenamento, args.limpar, "limpeza.json", resultado.como_dict())
    finally:
        await limpeza.aclose()
        await composicao.aclose()
    return SAIDA_OK if resultado.motor_limpo else SAIDA_MOTOR_SUJO


async def _planos_de_repeticao(
    armazenamento: ArmazenamentoS3, run_id: str, sujeitos: Sequence[Sujeito]
) -> tuple[list[PlanoDeConversa], str]:
    """As conversas que falharam em `run_id` (checagem ou juiz), com a mesma persona, pessoa e semente."""
    manifesto = json.loads(await armazenamento.ler(chave_da_execucao(run_id, "manifest.json")))
    falhas: set[str] = set()
    for linha in (await armazenamento.ler(chave_da_execucao(run_id, "checks.jsonl"))).splitlines():
        dado = json.loads(linha)
        if any(c["aplicavel"] and not c["passou"] for c in dado["checagens"].values()):
            falhas.add(dado["conversa"])
    try:
        for linha in (await armazenamento.ler(chave_da_execucao(run_id, "judge.jsonl"))).splitlines():
            dado = json.loads(linha)
            if dado.get("falha") is not None:
                falhas.add(dado["conversa"])
    except Exception:
        logger.warning("simulador_repeticao_sem_juiz", run_id=run_id)
    por_rotulo = {s.rotulo: s for s in sujeitos}
    planos = []
    for item in manifesto["conversas"]:
        if item["id"] not in falhas or item["sujeito"] not in por_rotulo:
            continue
        sujeito = por_rotulo[item["sujeito"]]
        planos.append(
            PlanoDeConversa(
                id=item["id"],
                persona=por_id(item["persona"]),
                sujeito=sujeito,
                telefone=item["telefone"],
                semente=item["semente"],
            )
        )
    return planos, manifesto["roster"]


def _item(conversa: Conversa, avaliacao: Avaliacao | None) -> ItemDaExecucao:
    persona = por_id(conversa.persona_id)
    mascarador = Mascarador(conversa.sujeito)
    resultados = checar_conversa(conversa, persona)
    for r in resultados:
        r.achados = [
            Achado(checagem=a.checagem, motivo=mascarador.texto(a.motivo), turno=a.turno, agente=a.agente)
            for a in r.achados
        ]
    if avaliacao is not None:
        avaliacao.motivo = mascarador.texto(avaliacao.motivo)
    return ItemDaExecucao(
        conversa_id=conversa.id,
        persona_id=conversa.persona_id,
        sujeito=conversa.sujeito.rotulo,
        checagens=resultados,
        avaliacao=avaliacao,
        transcricao=transcricao_mascarada(conversa, mascarador),
    )


async def _julgar(
    juiz: Juiz | None, conversas: Sequence[Conversa], concorrencia: int
) -> dict[str, Avaliacao]:
    if juiz is None:
        return {}
    semaforo = asyncio.Semaphore(max(1, concorrencia))

    async def um(conversa: Conversa) -> tuple[str, Avaliacao]:
        async with semaforo:
            if not conversa.turnos:
                return conversa.id, avaliacao_indisponivel("conversa_vazia")
            transcricao = transcricao_mascarada(conversa, Mascarador(conversa.sujeito))
            return conversa.id, await juiz.avaliar(por_id(conversa.persona_id), transcricao)

    return dict(await asyncio.gather(*(um(c) for c in conversas)))


def _extras(
    composicao: Composicao,
    conversas: Sequence[Conversa],
    limpeza: ResultadoDaLimpeza | None,
    preflight_: dict[str, int],
) -> dict[str, Any]:
    fonte: Counter[str] = Counter()
    fatos: Counter[str] = Counter()
    identidade: Counter[str] = Counter()
    for c in conversas:
        quem = "dono" if c.sujeito.dono else "terceiro"
        for t in c.turnos:
            fonte.update(f"{quem}:{m}" for m in t.fonte_indisponivel)
            fatos[quem] += len(t.fatos)
            identidade.update(t.identidade)
    consentimento = composicao.consentimento
    return {
        "preflight": preflight_,
        "consentimento": (
            {"gravados": consentimento.contagem.gravados, "nao_gravados": consentimento.contagem.nao_gravados}
            if consentimento is not None
            else None
        ),
        "escalonamentos_simulados": composicao.escalonamentos.total,
        "fatos_entregues": dict(fatos),
        "fonte_indisponivel": dict(fonte),
        "identidade_desfechos": dict(identidade),
        "envios_fora_de_turno": composicao.captura.fora_de_turno,
        "limpeza": limpeza.como_dict() if limpeza is not None else None,
    }


async def executar(args: argparse.Namespace) -> int:
    bucket = os.environ.get("MAEZO_SIMULADOR_BUCKET", "")
    regiao = os.environ.get("AWS_REGION") or os.environ.get("MAEZO_BEDROCK_REGION") or "sa-east-1"
    armazenamento = ArmazenamentoS3(bucket, regiao=regiao)
    if args.limpar:
        return await _so_limpar(args, armazenamento)

    run_id = novo_run_id()
    semente = args.semente if args.semente is not None else int(time.time())
    inicio = datetime.now(UTC).isoformat()
    composicao = await compor(_settings(), modo_consentimento=args.consentimento_terceiros)
    limpeza = _limpeza_da(composicao)
    resultado_limpeza: ResultadoDaLimpeza | None = None
    conversas: list[Conversa] = []
    conversation_ids: list[str] = []
    manifesto: dict[str, Any] = {}
    try:
        if composicao.resolver is None:
            raise RuntimeError("simulador: o acesso do beneficiario nao esta' ligado neste ambiente")
        roster_chave = args.roster
        planos_repetidos: list[PlanoDeConversa] | None = None
        if args.repetir_falhas:
            manifesto_antigo = json.loads(
                await armazenamento.ler(chave_da_execucao(args.repetir_falhas, "manifest.json"))
            )
            roster_chave = roster_chave or manifesto_antigo["roster"]
        roster_chave = roster_chave or await roster_mais_recente(armazenamento)
        lidos = ler_roster_csv(await armazenamento.ler(roster_chave))
        sujeitos, descartados = await preflight(lidos, composicao.resolver, limite=args.max_sujeitos)
        if not sujeitos:
            raise RuntimeError("simulador: nenhuma pessoa do roster resolveu na AMH")
        autorizar_consentimento(composicao, frozenset(s.ref for s in sujeitos if s.dono and s.ref))
        if args.repetir_falhas:
            planos_repetidos, _ = await _planos_de_repeticao(armazenamento, args.repetir_falhas, sujeitos)
        planos = (
            planos_repetidos
            if planos_repetidos is not None
            else planejar_conversas(
                planejar(selecionar(args.personas.split(",")), args.conversas, random.Random(semente)),
                sujeitos,
                semente=semente,
            )
        )
        condutor = Condutor(
            dispatcher=composicao.dispatcher,
            beneficiario=BeneficiarioLLM(
                gerador_da_inferencia(
                    inferencia_crua(composicao), tenant_id=composicao.tenant_id, papel="simulador-persona"
                )
            ),
            limpeza=limpeza,
            run_id=run_id,
            config=ConfigDoCondutor(concorrencia=args.concorrencia, intervalo_min_s=args.intervalo_min_s),
        )
        conversation_ids = sorted({condutor.conversation_id(p.telefone) for p in planos})
        manifesto = {
            "run_id": run_id,
            "status": "em_andamento",
            "inicio": inicio,
            "semente": semente,
            "argumentos": dict(vars(args)),
            "roster": roster_chave,
            "preflight": {"lidos": len(lidos), "resolvidos": len(sujeitos), "descartados": descartados},
            "modelo": getattr(inferencia_crua(composicao), "model_id", None),
            "consentimento_terceiros": args.consentimento_terceiros,
            "conversas": [
                {**p.como_manifesto(condutor.conversation_id(p.telefone)), "telefone": p.telefone}
                for p in planos
            ],
            "conversation_ids": conversation_ids,
        }
        await _gravar_json(armazenamento, run_id, "manifest.json", manifesto)
        logger.info("simulador_execucao_iniciada", run_id=run_id, conversas=len(planos))
        conversas = await condutor.executar(planos)
    finally:
        if conversation_ids:
            resultado_limpeza = await limpeza.limpar_execucao(conversation_ids)
        await limpeza.aclose()

    try:
        juiz = (
            None
            if args.sem_juiz
            else Juiz(
                gerador_da_inferencia(
                    inferencia_crua(composicao), tenant_id=composicao.tenant_id, papel="simulador-juiz"
                )
            )
        )
        avaliacoes = await _julgar(juiz, conversas, args.concorrencia)
        itens = [_item(c, avaliacoes.get(c.id)) for c in conversas]
        execucao = checar_execucao(conversas, Limites(args.latencia_p95_max_s, args.taxa_falha_tecnica_max))
        dados = resumo(
            itens,
            execucao=execucao,
            extras=_extras(composicao, conversas, resultado_limpeza, manifesto.get("preflight", {})),
        )
        grupos = agrupar_falhas(itens)
        await armazenamento.gravar(
            chave_da_execucao(run_id, "transcripts.jsonl"),
            jsonl(
                {
                    "conversa": i.conversa_id,
                    "persona": i.persona_id,
                    "sujeito": i.sujeito,
                    "turnos": i.transcricao,
                }
                for i in itens
            ),
            tipo="application/x-ndjson",
        )
        await armazenamento.gravar(
            chave_da_execucao(run_id, "checks.jsonl"),
            jsonl(
                {
                    "conversa": i.conversa_id,
                    "persona": i.persona_id,
                    "checagens": {
                        r.checagem: {
                            "aplicavel": r.aplicavel,
                            "passou": r.passou,
                            "achados": [
                                {"motivo": a.motivo, "turno": a.turno, "agente": a.agente} for a in r.achados
                            ],
                        }
                        for r in i.checagens
                    },
                }
                for i in itens
            ),
            tipo="application/x-ndjson",
        )
        await armazenamento.gravar(
            chave_da_execucao(run_id, "judge.jsonl"),
            jsonl(
                {
                    "conversa": i.conversa_id,
                    "persona": i.persona_id,
                    "falha": i.falha_do_juiz(),
                    **vars(i.avaliacao),
                }
                for i in itens
                if i.avaliacao is not None
            ),
            tipo="application/x-ndjson",
        )
        await _gravar_json(armazenamento, run_id, "summary.json", dados)
        await armazenamento.gravar(
            chave_da_execucao(run_id, "report.md"),
            relatorio_md(run_id, dados, grupos, itens),
            tipo="text/markdown",
        )
        manifesto |= {"status": "concluida", "fim": datetime.now(UTC).isoformat()}
        await _gravar_json(armazenamento, run_id, "manifest.json", manifesto)
        logger.info(
            "simulador_execucao_concluida",
            run_id=run_id,
            relatorio=f"s3://{armazenamento.bucket}/{chave_da_execucao(run_id, 'report.md')}",
            conversas=dados["conversas"],
            aprovadas=dados["conversas_aprovadas"],
            motor_limpo=resultado_limpeza.motor_limpo if resultado_limpeza else None,
        )
    finally:
        await composicao.aclose()
    if resultado_limpeza is not None and not resultado_limpeza.motor_limpo:
        return SAIDA_MOTOR_SUJO
    return SAIDA_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return asyncio.run(executar(args))
    except Exception as exc:
        # O detalhe passa pelo mascarador generico: nenhuma excecao leva CPF ou telefone ao log.
        logger.error("simulador_falhou", erro=type(exc).__name__, detalhe=Mascarador().texto(str(exc))[:300])
        return SAIDA_PREPARO


if __name__ == "__main__":
    sys.exit(main())

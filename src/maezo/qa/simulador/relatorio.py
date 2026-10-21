"""Resumo, agrupamento de falhas e o `report.md` de uma execucao (tudo ja' mascarado).

AGRUPAMENTO. Uma falha deterministica entra no grupo `(checagem, agente, motivo sem numeros)` —
"valor R$ 123.45 fora dos fatos" e "valor R$ 99.00 fora dos fatos" sao o mesmo defeito. Uma falha
do juiz entra no grupo `("juiz", falha_principal)`. Cada grupo lista quantas conversas tocou, as
personas e DUAS transcricoes de exemplo, para quem for corrigir ver o defeito sem abrir o S3.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from .checagens import CHECAGENS, DESCRICOES
from .juiz import NOTAS
from .registro import Avaliacao, ResultadoChecagem

#: Nota do juiz a partir da qual a conversa conta como falha do juiz (alem de `falha_principal`).
NOTA_MINIMA_APROVADA = 3
EXEMPLOS_POR_GRUPO = 2
MAX_CARACTERES_POR_LINHA = 300


@dataclass
class ItemDaExecucao:
    """Tudo o que o relatorio precisa de UMA conversa, ja' mascarado."""

    conversa_id: str
    persona_id: str
    sujeito: str
    checagens: list[ResultadoChecagem]
    avaliacao: Avaliacao | None
    transcricao: list[dict[str, Any]]
    #: Mensagens de achado JA' MASCARADAS, na ordem de `checagens`.
    motivos_mascarados: dict[str, list[str]] = field(default_factory=dict)

    def passou_deterministicas(self) -> bool:
        return all(r.passou for r in self.checagens if r.aplicavel)

    def falha_do_juiz(self) -> str | None:
        a = self.avaliacao
        if a is None or a.erro:
            return None
        if a.falha_principal != "nenhuma":
            return a.falha_principal
        if a.nota_minima() < NOTA_MINIMA_APROVADA:
            return "nota_baixa"
        return None


@dataclass
class Grupo:
    chave: tuple[str, str, str]
    conversas: list[str] = field(default_factory=list)
    ocorrencias: int = 0
    personas: Counter[str] = field(default_factory=Counter)

    @property
    def rotulo(self) -> str:
        origem, agente, motivo = self.chave
        return f"{origem} · {agente} · {motivo}"


def normalizar_motivo(motivo: str) -> str:
    """Tira numeros, valores e datas do motivo: o grupo e' o defeito, nao a instancia."""
    sem_moeda = re.sub(r"R\$\s*[\d.,]+", "R$ #", motivo)
    sem_numero = re.sub(r"\d+([./-]\d+)*", "#", sem_moeda)
    return re.sub(r"'[^']*'", "'…'", sem_numero) if "palavra proibida" not in motivo else sem_numero


def agrupar_falhas(itens: Sequence[ItemDaExecucao]) -> list[Grupo]:
    grupos: dict[tuple[str, str, str], Grupo] = {}
    for item in itens:
        chaves_da_conversa: set[tuple[str, str, str]] = set()
        for resultado in item.checagens:
            for achado in resultado.achados:
                chave = (achado.checagem, achado.agente or "-", normalizar_motivo(achado.motivo))
                grupo = grupos.setdefault(chave, Grupo(chave))
                grupo.ocorrencias += 1
                chaves_da_conversa.add(chave)
        falha = item.falha_do_juiz()
        if falha is not None:
            chave = ("juiz", "-", falha)
            grupo = grupos.setdefault(chave, Grupo(chave))
            grupo.ocorrencias += 1
            chaves_da_conversa.add(chave)
        for chave in chaves_da_conversa:
            grupos[chave].conversas.append(item.conversa_id)
            grupos[chave].personas[item.persona_id] += 1
    return sorted(grupos.values(), key=lambda g: (-len(g.conversas), -g.ocorrencias, g.chave))


def _taxa(aprovados: int, total: int) -> float | None:
    return round(aprovados / total, 4) if total else None


def resumo(
    itens: Sequence[ItemDaExecucao],
    *,
    execucao: Mapping[str, Any],
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    por_checagem: dict[str, dict[str, Any]] = {}
    for checagem in CHECAGENS:
        aplicaveis = [r for i in itens for r in i.checagens if r.checagem == checagem and r.aplicavel]
        aprovados = sum(1 for r in aplicaveis if r.passou)
        por_checagem[checagem] = {
            "descricao": DESCRICOES[checagem],
            "aplicavel": len(aplicaveis),
            "aprovadas": aprovados,
            "taxa": _taxa(aprovados, len(aplicaveis)),
        }
    por_persona: dict[str, dict[str, Any]] = {}
    for persona_id in sorted({i.persona_id for i in itens}):
        da_persona = [i for i in itens if i.persona_id == persona_id]
        det = sum(1 for i in da_persona if i.passou_deterministicas())
        juiz = sum(
            1
            for i in da_persona
            if i.avaliacao is not None and not i.avaliacao.erro and i.falha_do_juiz() is None
        )
        por_persona[persona_id] = {
            "conversas": len(da_persona),
            "deterministicas_ok": det,
            "taxa_deterministicas": _taxa(det, len(da_persona)),
            "juiz_ok": juiz,
            "taxa_juiz": _taxa(juiz, len(da_persona)),
        }
    por_agente: Counter[str] = Counter()
    for item in itens:
        for resultado in item.checagens:
            for achado in resultado.achados:
                por_agente[achado.agente or "-"] += 1
    avaliacoes = [i.avaliacao for i in itens if i.avaliacao is not None and not i.avaliacao.erro]
    notas = {
        nota: round(mean(getattr(a, nota) for a in avaliacoes), 2) if avaliacoes else None for nota in NOTAS
    }
    total_ok = sum(1 for i in itens if i.passou_deterministicas() and i.falha_do_juiz() is None)
    return {
        "conversas": len(itens),
        "conversas_aprovadas": total_ok,
        "taxa_aprovacao": _taxa(total_ok, len(itens)),
        "por_checagem": por_checagem,
        "por_persona": por_persona,
        "achados_por_agente": dict(sorted(por_agente.items())),
        "juiz": {
            "avaliadas": len(avaliacoes),
            "indisponivel": sum(1 for i in itens if i.avaliacao is not None and i.avaliacao.erro),
            "notas_medias": notas,
            "falha_principal": dict(Counter(a.falha_principal for a in avaliacoes).most_common()),
            "loop": sum(1 for a in avaliacoes if a.loop),
            "contradicao": sum(1 for a in avaliacoes if a.contradicao),
        },
        "execucao": dict(execucao),
        **(dict(extras) if extras else {}),
    }


def _linhas_da_transcricao(transcricao: Sequence[Mapping[str, Any]]) -> list[str]:
    linhas = []
    for t in transcricao:
        linhas.append(
            f"- t{t['turno']} [beneficiario/{t['tema']}] {str(t['beneficiario'])[:MAX_CARACTERES_POR_LINHA]}"
        )
        for r in t["respostas"]:
            linhas.append(f"  - [{r['agente']}] {str(r['texto'])[:MAX_CARACTERES_POR_LINHA]}")
        for e in t["escalonamentos"]:
            linhas.append(f"  - (teria escalado: {e['agente']}, {e['motivo']})")
        if t.get("erro"):
            linhas.append(f"  - (erro: {t['erro']})")
    return linhas


def relatorio_md(
    run_id: str,
    dados: Mapping[str, Any],
    grupos: Sequence[Grupo],
    itens: Sequence[ItemDaExecucao],
) -> str:
    por_id = {i.conversa_id: i for i in itens}
    execucao = dados["execucao"]
    linhas = [
        f"# Simulador de beneficiario — execucao `{run_id}`",
        "",
        "Tudo neste relatorio e' MASCARADO (CPF, nascimento, nome, boleto e telefone viram tokens).",
        "",
        "## Resumo",
        "",
        f"- Conversas: {dados['conversas']} · aprovadas (checagens + juiz): {dados['conversas_aprovadas']}"
        f" ({dados['taxa_aprovacao']})",
        f"- Latencia por turno: p50 {execucao.get('latencia_p50_s')} s · p95 "
        f"{execucao.get('latencia_p95_s')} s (limite {execucao.get('latencia_p95_max_s')} s)",
        f"- Turnos em falha_tecnica: {execucao.get('turnos_falha_tecnica')} "
        f"(taxa {execucao.get('taxa_falha_tecnica')}, limite {execucao.get('taxa_falha_tecnica_max')})",
    ]
    for chave in ("consentimento", "escalonamentos_simulados", "fonte_indisponivel", "limpeza"):
        if chave in dados:
            linhas.append(f"- {chave}: `{dados[chave]}`")
    linhas += [
        "",
        "## Por checagem",
        "",
        "| Checagem | Descricao | Aplicavel | Aprovadas | Taxa |",
        "|---|---|---|---|---|",
    ]
    for checagem, valor in dados["por_checagem"].items():
        linhas.append(
            f"| {checagem} | {valor['descricao']} | {valor['aplicavel']} | {valor['aprovadas']} "
            f"| {valor['taxa']} |"
        )
    linhas += [
        "",
        "## Por persona",
        "",
        "| Persona | Conversas | Checagens ok | Juiz ok |",
        "|---|---|---|---|",
    ]
    for persona_id, valor in dados["por_persona"].items():
        linhas.append(
            f"| {persona_id} | {valor['conversas']} | {valor['deterministicas_ok']} "
            f"({valor['taxa_deterministicas']}) | {valor['juiz_ok']} ({valor['taxa_juiz']}) |"
        )
    linhas += ["", "## Achados por agente", ""]
    linhas += [f"- {agente}: {n}" for agente, n in dados["achados_por_agente"].items()] or ["- nenhum"]
    juiz = dados["juiz"]
    linhas += [
        "",
        "## Juiz",
        "",
        f"- Avaliadas: {juiz['avaliadas']} · indisponivel: {juiz['indisponivel']}",
        f"- Notas medias: {juiz['notas_medias']}",
        f"- Falha principal: {juiz['falha_principal']} · laco: {juiz['loop']} "
        f"· contradicao: {juiz['contradicao']}",
        "",
        "## Grupos de falha",
        "",
    ]
    if not grupos:
        linhas.append("Nenhuma falha.")
    for grupo in grupos:
        linhas += [
            f"### {grupo.rotulo}",
            "",
            f"- Conversas: {len(grupo.conversas)} · ocorrencias: {grupo.ocorrencias} · personas: "
            f"{dict(grupo.personas.most_common())}",
            "",
        ]
        for conversa_id in grupo.conversas[:EXEMPLOS_POR_GRUPO]:
            item = por_id.get(conversa_id)
            if item is None:
                continue
            linhas.append(f"Exemplo `{conversa_id}` (persona `{item.persona_id}`, sujeito `{item.sujeito}`):")
            linhas.append("")
            linhas += _linhas_da_transcricao(item.transcricao)
            if item.avaliacao is not None and item.avaliacao.motivo:
                linhas.append(f"  - (juiz: {item.avaliacao.motivo})")
            linhas.append("")
    return "\n".join(linhas) + "\n"


__all__ = [
    "EXEMPLOS_POR_GRUPO",
    "Grupo",
    "ItemDaExecucao",
    "agrupar_falhas",
    "normalizar_motivo",
    "relatorio_md",
    "resumo",
]

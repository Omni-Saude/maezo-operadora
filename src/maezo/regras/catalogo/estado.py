"""Derivação PURA do estado de uma regra (blueprint §3.6; v3.1 §4 e §7.5).

Sem relógio próprio, sem banco, sem rede: recebe tudo e devolve o estado. O portal, o `regras-sync`
e o manifesto chamam a mesma função. Regras:

- Decisão de outra rodada ou de outro hash NUNCA conta; decisão substituída também não.
- Um `nao` vigente na rodada aberta -> `ajuste_pedido` (a rodada encerra).
- Todos os papéis de `aprovam` com decisão vigente favorável: condição sem comprovação ->
  `aprovada_com_condicao`; senão, sem liberação -> `aguardando_liberacao`; liberada ->
  `aprovada_aguardando_merge`; devolvida -> `em_revalidacao`.
- Sem rodada aberta: aprovação do hash vigente já no git (conjunto exato) -> `aprovada`,
  `em_homologacao` ou `em_producao` conforme a publicação; senão `legado_pendente` (B9) ou
  `em_revalidacao` (regra que já foi ativa e mudou).
- Nada muda por decurso de prazo (nenhuma regra é aprovada por prazo, RF-27). O tempo só liga a
  flag `validade_vencida` — que não bloqueia, alerta (RF-46).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

ESTADOS: Final[tuple[str, ...]] = (
    "legado_pendente",
    "em_validacao",
    "aprovada_com_condicao",
    "aguardando_liberacao",
    "aprovada_aguardando_merge",
    "aprovada",
    "em_homologacao",
    "em_producao",
    "em_revalidacao",
    "ajuste_pedido",
    "em_remocao",
    "removida",
)

_FAVORAVEIS: Final[frozenset[str]] = frozenset({"de_acordo", "de_acordo_com_ressalva", "importada"})


@dataclass(frozen=True, slots=True)
class Rodada:
    rodada_id: str
    regra_sha256: str
    tipo: Literal["legado", "nova", "alterada", "revalidacao", "remocao"]
    situacao: Literal["aberta", "encerrada", "cancelada"]


@dataclass(frozen=True, slots=True)
class Decisao:
    decisao_id: str
    rodada_id: str
    regra_sha256: str
    papel: str
    decisao: Literal["de_acordo", "de_acordo_com_ressalva", "nao", "importada"]
    ressalva_tipo: Literal["observacao", "condicao"] | None = None
    substituida: bool = False


@dataclass(frozen=True, slots=True)
class Liberacao:
    rodada_id: str
    regra_sha256: str
    decisao: Literal["liberar", "devolver"]


@dataclass(frozen=True, slots=True)
class Publicacao:
    mergeada: bool = False
    em_homologacao: bool = False
    em_producao: bool = False


@dataclass(frozen=True, slots=True)
class EntradaDoEstado:
    estado_declarado: str
    sha256_vigente: str
    aprovam: tuple[str, ...]
    hoje: date
    rodada: Rodada | None = None
    decisoes: Sequence[Decisao] = ()
    #: `decisao_id` das decisões-com-condição já comprovadas.
    condicoes_comprovadas: frozenset[str] = field(default_factory=frozenset)
    liberacao: Liberacao | None = None
    #: Papéis com aprovação transcrita no git PARA O HASH VIGENTE.
    aprovados_no_git: frozenset[str] = field(default_factory=frozenset)
    publicacao: Publicacao = Publicacao()
    valida_ate: date | None = None


@dataclass(frozen=True, slots=True)
class EstadoDaRegra:
    estado: str
    validade_vencida: bool


def _vigentes(entrada: EntradaDoEstado, rodada: Rodada) -> list[Decisao]:
    return [
        d
        for d in entrada.decisoes
        if not d.substituida and d.rodada_id == rodada.rodada_id and d.regra_sha256 == rodada.regra_sha256
    ]


def _estado_da_rodada(entrada: EntradaDoEstado, rodada: Rodada) -> str:
    vigentes = _vigentes(entrada, rodada)
    if any(d.decisao == "nao" for d in vigentes):
        return "ajuste_pedido"
    por_papel = {d.papel: d for d in vigentes if d.decisao in _FAVORAVEIS}
    if not set(entrada.aprovam) <= set(por_papel):
        if rodada.tipo == "remocao":
            return "em_remocao"
        return "em_revalidacao" if rodada.tipo == "revalidacao" else "em_validacao"
    if rodada.tipo == "remocao":
        return "em_remocao"  # remoção aprovada segue em produção até o merge (RF-49)
    pendentes = [
        d
        for d in por_papel.values()
        if d.ressalva_tipo == "condicao" and d.decisao_id not in entrada.condicoes_comprovadas
    ]
    if pendentes:
        return "aprovada_com_condicao"
    lib = entrada.liberacao
    if lib is None or lib.rodada_id != rodada.rodada_id or lib.regra_sha256 != rodada.regra_sha256:
        return "aguardando_liberacao"
    return "em_revalidacao" if lib.decisao == "devolver" else "aprovada_aguardando_merge"


def derivar_estado(entrada: EntradaDoEstado) -> EstadoDaRegra:
    vencida = entrada.valida_ate is not None and entrada.hoje > entrada.valida_ate
    if entrada.estado_declarado == "removida":
        return EstadoDaRegra("removida", vencida)
    rodada = entrada.rodada
    if rodada is not None and rodada.situacao == "aberta" and rodada.regra_sha256 == entrada.sha256_vigente:
        return EstadoDaRegra(_estado_da_rodada(entrada, rodada), vencida)
    if entrada.estado_declarado == "em_remocao":
        return EstadoDaRegra("em_remocao", vencida)
    if entrada.aprovam and set(entrada.aprovam) <= entrada.aprovados_no_git:
        pub = entrada.publicacao
        if pub.em_producao:
            return EstadoDaRegra("em_producao", vencida)
        if pub.em_homologacao:
            return EstadoDaRegra("em_homologacao", vencida)
        return EstadoDaRegra("aprovada", vencida)
    if entrada.estado_declarado == "legado_pendente":
        return EstadoDaRegra("legado_pendente", vencida)
    return EstadoDaRegra("em_revalidacao", vencida)


__all__ = [
    "ESTADOS",
    "Decisao",
    "EntradaDoEstado",
    "EstadoDaRegra",
    "Liberacao",
    "Publicacao",
    "Rodada",
    "derivar_estado",
]

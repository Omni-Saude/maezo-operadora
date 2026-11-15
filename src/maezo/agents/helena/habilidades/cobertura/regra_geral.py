"""Camada 1 "REGRA GERAL" da cobertura (dono, 14/11/2026; DL-0167, HEL-3b).

Responde cobre? precisa de autorizacao? que documentos? que prazo? como recorrer? com a regra do pacote de
conteudo K2 do rco (`manual.ManualK2`), e com nada mais:

1. RESOLUCAO do procedimento sem chute (`procedimento.resolver`, HEL-2), quando ha' catalogo: termo ambiguo
   = UMA pergunta de esclarecimento; sem candidato no catalogo, segue so' pelos sinonimos do proprio K2.
2. RECUPERACAO por regra: `ManualK2.buscar` com o termo resolvido e o texto da pessoa. Exatamente UMA regra
   responde; nenhuma ou mais de uma = transferir (oferta de uma pessoa) e a lacuna registrada.
3. COMPOSICAO: o redator (ponto de extensao; redacao livre) ou, sem ele, o texto deterministico
   "Pela regra geral: <resumo da regra>" e a formula fixa.
4. VERIFICACAO antes do envio (`verificador.verificar`, com o texto e o resumo da regra como trechos): o
   rascunho do redator que nao confere e' trocado pelo texto deterministico, que tambem e' verificado; nada
   confere = transferir. O id da regra vai na `Fonte` (log de auditoria), nunca no texto; a `url` da regra
   nunca entra no texto.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol, runtime_checkable

import structlog

from maezo.agents.helena.habilidades.cobertura import (
    Camada,
    Fonte,
    PerguntaDeCobertura,
    RespostaDaCobertura,
)
from maezo.agents.helena.habilidades.cobertura import procedimento as _procedimento
from maezo.agents.helena.habilidades.cobertura.manual import ManualK2, RegraDoManual, normalizar
from maezo.agents.helena.habilidades.cobertura.verificador import FORMULA_FIXA, verificar
from maezo.ports.tina import ProcedimentoCatalogo

logger = structlog.get_logger(__name__)

PREFIXO: Final[str] = "Pela regra geral:"


@runtime_checkable
class RedatorDaRegraGeral(Protocol):
    """Redacao livre com fatos presos: recebe a pergunta e a regra; o verificador decide se o texto sai."""

    async def redigir(self, pergunta: PerguntaDeCobertura, regra: RegraDoManual) -> str | None: ...


def texto_deterministico(regra: RegraDoManual) -> str:
    resumo = regra.resumo.strip()
    if resumo and resumo[-1] not in ".!?":
        resumo += "."
    return f"{PREFIXO} {resumo} {FORMULA_FIXA}"


class CamadaRegraGeral:
    """A camada `regra_geral` sobre um pacote K2 JA' VERIFICADO (versao e hash, `manual.carregar`)."""

    def __init__(
        self,
        manual: ManualK2,
        *,
        catalogo: Sequence[ProcedimentoCatalogo] = (),
        sinonimos: _procedimento.Sinonimos | None = None,
        redator: RedatorDaRegraGeral | None = None,
    ) -> None:
        self._manual = manual
        self._catalogo = tuple(catalogo)
        self._sinonimos = sinonimos
        self._redator = redator

    @property
    def camada(self) -> Camada:
        return "regra_geral"

    async def responder(self, pergunta: PerguntaDeCobertura) -> RespostaDaCobertura | None:
        termos: list[str] = []
        if self._catalogo and self._sinonimos is not None:
            resolucao = _procedimento.resolver(
                pergunta.texto,
                catalogo=self._catalogo,
                sinonimos=self._sinonimos,
                falas_anteriores=pergunta.falas_anteriores,
                sinonimos_extra=self._sinonimos_do_k2(),
            )
            if resolucao.desfecho == "esclarecer" and resolucao.pergunta:
                return RespostaDaCobertura("esclarecer", resolucao.pergunta)
            if resolucao.desfecho == "unico":
                item = resolucao.candidatos[0]
                termos += [t for t in (item.descricao, item.tuss) if t]
        regras = self._regras(termos, pergunta)
        if len(regras) != 1:
            lacuna = "sem_regra" if not regras else "regras_multiplas"
            return RespostaDaCobertura("transferir", lacuna=lacuna)
        [regra] = regras
        fonte = Fonte("regra_k2", regra.id, self._manual.versao)
        trechos = (regra.texto, regra.resumo)
        if self._redator is not None:
            rascunho = await self._redator.redigir(pergunta, regra)
            if rascunho:
                veredito = verificar(rascunho, trechos)
                if veredito.ok:
                    return RespostaDaCobertura("resposta", rascunho, (fonte,))
                logger.warning(
                    "helena_cobertura_rascunho_barrado", regra=regra.id, motivos=list(veredito.motivos)
                )
        deterministico = texto_deterministico(regra)
        veredito = verificar(deterministico, trechos)
        if not veredito.ok:
            logger.error("helena_cobertura_regra_nao_confere", regra=regra.id, motivos=list(veredito.motivos))
            return RespostaDaCobertura("transferir", lacuna="regra_nao_confere")
        return RespostaDaCobertura("resposta", deterministico, (fonte,))

    def _sinonimos_do_k2(self) -> Mapping[str, Sequence[str]]:
        """Termo popular do K2 -> os titulos das regras que o citam (a resolucao so' acrescenta termo)."""
        saida: dict[str, list[str]] = {}
        for termo, ids in self._manual.sinonimos_por_termo().items():
            for id_ in ids:
                regra = self._manual.regra(id_)
                if regra is not None:
                    saida.setdefault(termo, []).append(regra.titulo)
        return saida

    def _regras(self, termos: Sequence[str], pergunta: PerguntaDeCobertura) -> tuple[RegraDoManual, ...]:
        """As regras da pergunta, so' por casamento EXATO (nada de "a mais parecida"):

        1. as frases da mensagem (n-gramas de 1 a 4 palavras) que sao SINONIMO de regra no K2;
        2. com o procedimento resolvido (HEL-2), a busca pelo nome dele restringe (intersecao) o que (1)
           achou, ou responde sozinha quando (1) nao achou nada;
        3. nada ainda e a mensagem e' curta (resposta a um esclarecimento): a fala anterior junto.
        """
        ids = self._ids_por_sinonimo(pergunta.texto)
        if termos:
            pelo_nome = {r.id for r in self._manual.buscar(termos)}
            ids = (ids & pelo_nome) or (pelo_nome if not ids else set())
        if not ids and pergunta.falas_anteriores and len(normalizar(pergunta.texto).split()) <= 4:
            ids = self._ids_por_sinonimo(f"{pergunta.falas_anteriores[-1]} {pergunta.texto}")
        return tuple(r for i in sorted(ids) if (r := self._manual.regra(i)) is not None)

    def _ids_por_sinonimo(self, texto: str) -> set[str]:
        por_termo = self._manual.sinonimos_por_termo()
        palavras = normalizar(texto).split()
        ids: set[str] = set()
        for n in range(1, 5):
            for i in range(len(palavras) - n + 1):
                ids.update(por_termo.get(" ".join(palavras[i : i + n]), ()))
        return ids


__all__ = ["PREFIXO", "CamadaRegraGeral", "RedatorDaRegraGeral", "texto_deterministico"]

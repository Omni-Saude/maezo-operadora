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
from maezo.agents.helena.habilidades.cobertura.verificador import (
    FORMULA_FIXA,
    PREFIXO_DA_REGRA_GERAL,
    palavras_de_conteudo,
    procedimentos_citados,
    singular,
    verificar,
)
from maezo.ports.tina import ProcedimentoCatalogo

logger = structlog.get_logger(__name__)

PREFIXO: Final[str] = PREFIXO_DA_REGRA_GERAL


@runtime_checkable
class RedatorDaRegraGeral(Protocol):
    """Redacao livre com fatos presos: recebe a pergunta e a regra; o verificador decide se o texto sai.

    CONDICAO PARA LIGAR (HEL-5a, revisao independente): nenhum redator esta' ligado em `src/`. O verificador
    de vocabulario fechado NAO fecha recombinacao das palavras da propria fonte que inverte o sentido ("deve
    ser feita antes da autorizacao", "acima de 20 sessoes", "a partir de 7 dias", "nota fiscal OU pedido").
    Ligar um redator exige outro desenho, aprovado pela ROOT: por exemplo, cada frase de FATO do rascunho
    igual a uma frase da fonte, e redacao livre so' nas frases de ligacao sem conteudo."""

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
        self._procedimentos = _vocabulario_de_procedimentos(sinonimos)

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
        if len(regras) == 1 and (
            self._outro_procedimento(pergunta.texto, regras[0]) or _sujeito_fora_da_regra(pergunta, regras[0])
        ):
            # HEL-5a (revisao independente): a mensagem cita OUTRO procedimento alem do da regra ("a
            # tomografia ... fiz ressonancia ano passado"): o sinonimo citado de passagem nao escolhe a regra.
            logger.info("helena_cobertura_outro_procedimento", regra=regras[0].id)
            return RespostaDaCobertura("transferir", lacuna="outro_procedimento")
        if len(regras) != 1:
            lacuna = "sem_regra" if not regras else "regras_multiplas"
            return RespostaDaCobertura("transferir", lacuna=lacuna)
        [regra] = regras
        fonte = Fonte("regra_k2", regra.id, self._manual.versao)
        trechos = (regra.texto, regra.resumo)
        if self._redator is not None:
            rascunho = await self._redator.redigir(pergunta, regra)
            if rascunho:
                veredito = verificar(rascunho, trechos, procedimentos=self._procedimentos)
                if veredito.ok:
                    return RespostaDaCobertura("resposta", rascunho, (fonte,))
                logger.warning(
                    "helena_cobertura_rascunho_barrado", regra=regra.id, motivos=list(veredito.motivos)
                )
        deterministico = texto_deterministico(regra)
        veredito = verificar(deterministico, trechos, procedimentos=self._procedimentos)
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
        A fala anterior so' entra pela resolucao do procedimento (resposta a um esclarecimento, HEL-2):
        juntar a fala anterior a busca puxava a regra antiga ("e o transplante?" depois da ressonancia;
        revisao independente).
        """
        ids = self._ids_por_sinonimo(pergunta.texto)
        if termos:
            pelo_nome = {r.id for r in self._manual.buscar(termos)}
            ids = (ids & pelo_nome) or (pelo_nome if not ids else set())
        return tuple(r for i in sorted(ids) if (r := self._manual.regra(i)) is not None)

    def _outro_procedimento(self, texto: str, regra: RegraDoManual) -> bool:
        """A mensagem cita um procedimento (vocabulario, sufixo de procedimento ou sigla, no singular:
        `verificador.procedimentos_citados`) que a regra NAO cita nos sinonimos nem no titulo?"""
        da_regra = " ".join([*regra.sinonimos, regra.titulo])
        da_regra_citados = procedimentos_citados(da_regra, self._procedimentos)
        palavras_da_regra = {singular(p) for p in normalizar(da_regra).split()}
        return any(
            c not in da_regra_citados and not set(c.split()) <= palavras_da_regra
            for c in procedimentos_citados(texto, self._procedimentos)
        )

    def _ids_por_sinonimo(self, texto: str) -> set[str]:
        por_termo = self._manual.sinonimos_por_termo()
        palavras = normalizar(texto).split()
        ids: set[str] = set()
        for n in range(1, 5):
            for i in range(len(palavras) - n + 1):
                ids.update(por_termo.get(" ".join(palavras[i : i + n]), ()))
        return ids


#: O que a PERGUNTA sobre autorizacao pode dizer alem do procedimento: o pedido, o tempo, quem pergunta.
_PALAVRAS_DA_PERGUNTA: Final[frozenset[str]] = frozenset(
    {
        "precisa", "preciso", "precisam", "precisar", "necessario", "necessaria", "tem", "ter", "tenho",
        "fazer", "faco", "fizer", "vou", "quero", "queria", "posso", "pode", "meu", "minha", "eu", "como",
        "funciona", "pedir", "solicitar", "oi", "ola", "bom", "boa", "dia", "tarde", "noite", "favor",
        "obrigado", "obrigada", "nao", "sei", "saber", "qual", "previa", "previo", "pre", "autorizacao",
        "autorizar", "liberacao", "liberar", "guia", "senha", "exame", "procedimento", "ai", "agora", "mesmo",
        "ainda", "sim", "ne", "pediu", "medico", "medica", "fiz", "feito",
    }
)  # fmt: skip
#: As palavras de PRAZO e QUANTIDADE da pergunta: so' contra regra que diz algum numero (HEL-5a).
_PALAVRAS_DE_QUANTIDADE: Final[frozenset[str]] = frozenset(
    {
        "quanto", "quanta", "tempo", "demora", "demorar", "prazo", "limite", "limitado", "limitada",
        "ilimitado", "ilimitada", "maximo", "vale", "valer", "cada", "consigo", "conseguir", "marcar",
        "agendar",
    }
)  # fmt: skip


def _sujeito_fora_da_regra(pergunta: PerguntaDeCobertura, regra: RegraDoManual) -> bool:
    """HEL-5a (revisao independente): VOCABULARIO FECHADO na pergunta. Toda palavra de conteudo da
    mensagem INTEIRA (todas as oracoes: a pontuacao nao decide nada) e da ultima fala anterior da pessoa tem
    de estar na regra (sinonimos, titulo, resumo, texto) ou ser do ato de perguntar; senao a regra nao e'
    escolhida (transfere). "o transplante precisa de autorizacao? fiz ressonancia", "consulta? e' com
    cardiologista", "ressonancia. quanto custa?" e "quero marcar com cardiologista" -> "e quanto tempo demora
    a consulta?" nao escolhem nada: lista nenhuma de procedimento ou de assunto fica completa; o vocabulario
    da regra sim. As palavras de PRAZO e QUANTIDADE ("quanto tempo", "limite") so' valem contra regra que diz
    numero."""
    da_regra = palavras_de_conteudo(" ".join([*regra.sinonimos, regra.titulo, regra.resumo, regra.texto]))
    permitidas = _PALAVRAS_DA_PERGUNTA
    if any(c.isdigit() for c in f"{regra.resumo} {regra.texto}"):
        permitidas = permitidas | _PALAVRAS_DE_QUANTIDADE
    falas = [pergunta.texto]
    anteriores = [f for f in pergunta.falas_anteriores if f != pergunta.texto]
    if anteriores:
        falas.append(anteriores[-1])
    return any(
        p not in da_regra and p not in permitidas for fala in falas for p in palavras_de_conteudo(fala)
    )


#: Procedimentos genericos que, citados junto com o sinonimo de OUTRA regra, tornam a pergunta ambigua.
_PROCEDIMENTOS_GENERICOS: Final[frozenset[str]] = frozenset(
    {
        "cirurgia",
        "consulta",
        "internacao",
        "terapia",
        "fisioterapia",
        "quimioterapia",
        "radioterapia",
        "parto",
    }
)


def _vocabulario_de_procedimentos(sinonimos: _procedimento.Sinonimos | None) -> frozenset[str]:
    """As frases que nomeiam procedimento: as do `sinonimos.yaml` (populares e oficiais) e as genericas."""
    if sinonimos is None:
        try:
            sinonimos = _procedimento.carregar_sinonimos()
        except _procedimento.SinonimosInvalidosError:
            logger.error("helena_cobertura_sinonimos_indisponiveis")
            sinonimos = None
    frases = set(_PROCEDIMENTOS_GENERICOS)
    for entrada in sinonimos.entradas if sinonimos is not None else ():
        frases.update(normalizar(p) for p in entrada.populares)
        frases.add(normalizar(entrada.oficial))
    frases.discard("")
    return frozenset(frases)


__all__ = ["PREFIXO", "CamadaRegraGeral", "RedatorDaRegraGeral", "texto_deterministico"]

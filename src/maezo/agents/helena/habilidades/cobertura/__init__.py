"""Habilidade `cobertura` AMPLA da Helena (dono, 14/11/2026; DL-0167), atras da chave `cobertura_ampla`.

O dono reverteu o item (1) do DL-0154 ("cobertura fora, de vez"): a Helena volta a responder cobertura de
forma ampla. A protecao deixa de ser o silencio e passa a ser a FONTE: tudo o que a Helena afirma vem de
conteudo versionado e ratificado (pacote K2 do `regras-contratos-operadora`) ou de consulta deterministica
(rotas pinadas do lago). Tres camadas, em ordem:

1. `regra_geral` — cobre? precisa de autorizacao? que documentos? que prazo? como recorrer? — com o K2;
2. `seu_plano` — carencia, cobertura contratual, CPT, rede, situacao do pedido — com as rotas do lago;
3. `seu_caso` — avaliacao das tabelas do rco com os dados da pessoa — ONDA 3, nao existe aqui.

Este modulo e' o CONTRATO da habilidade (entrada `PerguntaDeCobertura`, saida `RespostaDaCobertura`) e os
PONTOS DE EXTENSAO (`CamadaDeCobertura`). A composicao registra as camadas; esta onda nao registra nenhuma
camada viva.

CHAVE DESLIGADA (o padrao): `HelenaGraph._cobertura is None` e `atender` devolve EXATAMENTE o aviso de canais
de hoje (`_texto_da_cobertura_fora`, DL-0154), byte a byte. Ligada, cada camada tenta em ordem; camada que
falha, que nao tem o que dizer ou cuja resposta nao passa no verificador nao fala: sem resposta de nenhuma
camada, o aviso de canais (falha fechada) e o registro da LACUNA de conteudo (sem o texto da pessoa).

O que nao muda (DL-0167): a trava `cobertura_sem_promessa` e a formula fixa "informacao nao e' decisao";
a Helena explica criterio mas nao avalia se a pessoa o cumpre; sintoma e sinal de alerta continuam com as
travas de entrada e a porta da saude; caso so' abre com o "sim" da pessoa (a transferencia e' a OFERTA).
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

import structlog

from maezo.agents.helena.habilidades.atendimento_humano.textos import PENDENTE_OFERTA_EQUIPE
from maezo.agents.helena.habilidades.cobertura import travas as _travas
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

logger = structlog.get_logger(__name__)

FORMULA_FIXA: Final[str] = _travas.FORMULA_FIXA

#: A chave booleana do pacote de comportamento (`configuracao.yaml`). Ausente = desligada.
CHAVE_DO_PACOTE: Final[str] = "cobertura_ampla"
#: A chave da configuracao do `graph.build` que o pacote liga.
CHAVE_DO_GRAFO: Final[str] = "helena_cobertura_ampla_enabled"
#: A chave da configuracao do `graph.build` com as camadas (a composicao as passa; esta onda nao passa).
CHAVE_DAS_CAMADAS: Final[str] = "cobertura_camadas"
#: A chave da configuracao do `graph.build` com o sha256 do manifesto do pacote K2 pinado (HEL-5b).
CHAVE_DO_HASH_K2: Final[str] = "cobertura_k2_sha256"

Camada = Literal["regra_geral", "seu_plano"]
CAMADAS: Final[tuple[Camada, ...]] = ("regra_geral", "seu_plano")
#: `resposta` = texto com fatos presos e verificados; `esclarecer` = UMA pergunta de esclarecimento (termo
#: ambiguo); `transferir` = sem regra correspondente ou dado conflitante: oferece uma pessoa e registra a
#: lacuna.
Desfecho = Literal["resposta", "esclarecer", "transferir"]
DESFECHOS: Final[tuple[Desfecho, ...]] = ("resposta", "esclarecer", "transferir")

#: A oferta de uma pessoa quando nenhuma fonte responde com seguranca. Caso so' com o "sim" (ADR-0066).
TEXTO_TRANSFERIR: Final[str] = (
    "Essa dúvida eu não consigo responder com segurança por aqui. Se você quiser, posso passar o seu "
    "atendimento para uma pessoa da nossa equipe. Quer? Responda sim ou não."
)


@dataclass(frozen=True, slots=True)
class PerguntaDeCobertura:
    """A ENTRADA de uma camada. `texto` e `falas_anteriores` nunca vao a log nem a resposta literal."""

    texto: str
    #: As mensagens anteriores da pessoa (historico curto, sem as falas da Helena), da mais antiga a mais
    #: recente: o esclarecimento do procedimento le' a pergunta de antes junto com a resposta.
    falas_anteriores: tuple[str, ...] = ()
    #: O sujeito verificado do turno (so' a camada `seu_plano` usa), ou `None`.
    portable_subject_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Fonte:
    """A citacao INTERNA de um fato (vai ao log de auditoria, nunca ao texto da pessoa)."""

    tipo: Literal["regra_k2", "lago"]
    #: O id da regra do K2 ou a rota do lago (`/cobertura`, `/carencias`...).
    id: str
    #: A versao do pacote K2 ou do contrato pinado da rota.
    versao: str
    #: O sha256 do manifesto do pacote K2 (64 hex), conferido contra o pin; `None` fora do K2.
    hash_do_pacote: str | None = None


@dataclass(frozen=True, slots=True)
class RespostaDaCobertura:
    """A SAIDA de uma camada. `resposta`/`esclarecer` exigem `texto`; `resposta` exige ao menos uma fonte."""

    desfecho: Desfecho
    texto: str | None = None
    fontes: tuple[Fonte, ...] = ()
    #: O motivo curto da lacuna de conteudo (`sem_regra`, `dado_conflitante`...), nunca o texto da pessoa.
    lacuna: str | None = None
    #: A camada conferiu os fatos do texto com as fontes (`verificador.verificar`) e o veredito foi ok.
    veredito_ok: bool = False
    #: Os trechos das fontes de onde o texto saiu (o esqueleto os confere de novo antes de isentar o prazo).
    trechos: tuple[str, ...] = ()

    def valida(self) -> bool:
        if self.desfecho not in DESFECHOS:
            return False
        if self.desfecho == "resposta":
            return bool(self.texto and self.texto.strip()) and bool(self.fontes)
        if self.desfecho == "esclarecer":
            return bool(self.texto and self.texto.strip())
        return True


@runtime_checkable
class CamadaDeCobertura(Protocol):
    """O PONTO DE EXTENSAO: uma camada responde, ou devolve `None` (nao tem o que dizer; passa adiante).

    A camada e' responsavel por so' devolver `resposta` cujo texto passou no verificador deterministico dela
    (os fatos batem com as fontes). Excecao de dependencia externa = `None` (o turno nunca cai)."""

    @property
    def camada(self) -> Camada: ...

    async def responder(self, pergunta: PerguntaDeCobertura) -> RespostaDaCobertura | None: ...


@dataclass(frozen=True, slots=True)
class ConfiguracaoDaCobertura:
    """A habilidade LIGADA, com as camadas em ordem (`regra_geral` antes de `seu_plano`)."""

    camadas: tuple[CamadaDeCobertura, ...] = ()
    #: O sha256 do manifesto do pacote K2 PINADO na composicao (HEL-5b): so' fonte com este hash isenta prazo.
    hash_do_k2: str | None = None


def da_configuracao(cfg: Mapping[str, Any]) -> ConfiguracaoDaCobertura | None:
    """A configuracao do `graph.build` -> a habilidade. `None` (desligada) salvo `True` EXPLICITO na chave.

    Camada que nao cumpre o protocolo ou fora da ordem das camadas e' descartada (falha fechada)."""
    if cfg.get(CHAVE_DO_GRAFO, False) is not True:
        return None
    brutas = cfg.get(CHAVE_DAS_CAMADAS) or ()
    camadas: list[CamadaDeCobertura] = []
    for camada in brutas if isinstance(brutas, Sequence) else ():
        if isinstance(camada, CamadaDeCobertura) and getattr(camada, "camada", None) in CAMADAS:
            camadas.append(camada)
        else:
            logger.error("helena_cobertura_camada_recusada", tipo=type(camada).__name__)
    camadas.sort(key=lambda c: CAMADAS.index(c.camada))
    hash_do_k2 = cfg.get(CHAVE_DO_HASH_K2)
    return ConfiguracaoDaCobertura(
        camadas=tuple(camadas), hash_do_k2=hash_do_k2 if _e_sha256(hash_do_k2) else None
    )


# --- Os textos que a habilidade deixou prontos (o `inform` os envia sem modelo) ----------------------------
#: USO UNICO: o texto aceito neste turno entra aqui e SAI na primeira conferencia do `inform` (um texto igual
#: vindo de outra origem, depois, nao e' isento). Teto alto para a fila do processo; acima dele o mais antigo
#: sai e aquele turno vai ao caminho de sempre do `inform` (com as travas de sempre).
_MAX_TEXTOS: Final[int] = 4096
_TEXTOS_DA_COBERTURA: OrderedDict[str, None] = OrderedDict()


#: PRAZO RATIFICADO (HEL-5b): conversa -> impressao do texto aceito com prazo. UMA marca por conversa, trocada
#: a cada turno e SEMPRE consumida pelo `respond` da conversa (revisao independente: marca pendurada isentava,
#: depois, um texto igual vindo do modelo; e o mapa por texto valia entre conversas e tenants).
_PRAZO_RATIFICADO: OrderedDict[str, str] = OrderedDict()
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _e_sha256(valor: object) -> bool:
    return isinstance(valor, str) and _SHA256.fullmatch(valor) is not None


def _conversa(state: Mapping[str, Any]) -> str | None:
    """A chave da conversa (tenant + id); `None` sem id: conversa sem id nunca recebe a isencao."""
    conversa = state.get("conversation_id")
    if not isinstance(conversa, str) or not conversa:
        return None
    return f"{state.get('tenant_id') or ''}:{conversa}"


def prazo_ratificado(state: Mapping[str, Any], texto: object) -> bool:
    """O texto que o `respond` vai enviar e' EXATAMENTE o aceito com prazo ratificado neste turno, nesta
    conversa? Consome a marca da conversa em qualquer caso (nunca fica pendurada)."""
    conversa = _conversa(state)
    marca = _PRAZO_RATIFICADO.pop(conversa, None) if conversa is not None else None
    return marca is not None and isinstance(texto, str) and marca == _impressao(texto)


def _isenta_prazo(config: ConfiguracaoDaCobertura, resposta: RespostaDaCobertura) -> bool:
    """HEL-5b (ROOT, 16/11/2026): resposta com veredito ok, TODA fonte do K2 com o hash do pacote PINADO e os
    fatos do texto conferidos de novo AQUI contra os trechos (a camada nao se autodeclara ratificada)."""
    from maezo.agents.helena.habilidades.cobertura.verificador import verificar

    return (
        resposta.desfecho == "resposta"
        and resposta.veredito_ok
        and config.hash_do_k2 is not None
        and bool(resposta.fontes)
        and all(f.tipo == "regra_k2" and f.hash_do_pacote == config.hash_do_k2 for f in resposta.fontes)
        and bool(resposta.trechos)
        and verificar(resposta.texto or "", resposta.trechos).ok
    )


def _impressao(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _guardar(texto: str, *, prazo_da_conversa: str | None = None) -> None:
    chave = _impressao(texto)
    if prazo_da_conversa is not None:
        _PRAZO_RATIFICADO[prazo_da_conversa] = chave
        _PRAZO_RATIFICADO.move_to_end(prazo_da_conversa)
        while len(_PRAZO_RATIFICADO) > _MAX_TEXTOS:
            _PRAZO_RATIFICADO.popitem(last=False)
    _TEXTOS_DA_COBERTURA[chave] = None
    _TEXTOS_DA_COBERTURA.move_to_end(chave)
    while len(_TEXTOS_DA_COBERTURA) > _MAX_TEXTOS:
        _TEXTOS_DA_COBERTURA.popitem(last=False)


def e_texto_da_cobertura(texto: object) -> bool:
    """O texto foi deixado pronto (e conferido) por esta habilidade? Consome a marca (uso unico)."""
    if not isinstance(texto, str):
        return False
    return _TEXTOS_DA_COBERTURA.pop(_impressao(texto), 0) is None


def texto_do_aviso(grafo: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any]:
    """O aviso de canais de hoje (DL-0154), MOVIDO SEM MUDANCA de `graph._texto_da_cobertura_fora`."""
    from maezo.agents.helena.graph import _com_emergencia_se_preciso
    from maezo.agents.helena.habilidades.cobertura_fora import CHAVE_TEXTO as CHAVE_TEXTO_COBERTURA_FORA

    logger.info("helena_cobertura_fora", node="classify")
    # "E' urgente" de gestante sem sinal de saude leva o 192 no fim (DL-0091), como antes.
    texto = _com_emergencia_se_preciso(state, grafo._textos[CHAVE_TEXTO_COBERTURA_FORA])
    return grafo._texto_fixo(grafo._update_sem_modelo(state), texto)


async def atender(grafo: HelenaGraph, state: Mapping[str, Any], texto: str) -> dict[str, Any]:
    """A habilidade `cobertura` atras do roteador. Desligada: o aviso de canais de hoje, sem mais nada."""
    config: ConfiguracaoDaCobertura | None = getattr(grafo, "_cobertura", None)
    if config is None:
        return texto_do_aviso(grafo, state)
    pergunta = PerguntaDeCobertura(
        texto=texto,
        falas_anteriores=tuple(grafo._falas_da_pessoa(state)),
        portable_subject_ref=_sujeito(state),
    )
    for camada in config.camadas:
        try:
            resposta = await camada.responder(pergunta)
        except EXTERNAL_DEPENDENCY_FAILURES as exc:
            logger.warning("helena_cobertura_camada_falhou", camada=camada.camada, erro=type(exc).__name__)
            continue
        if resposta is None:
            continue
        if not resposta.valida():
            logger.error(
                "helena_cobertura_resposta_invalida", camada=camada.camada, desfecho=resposta.desfecho
            )
            continue
        logger.info(
            "helena_cobertura_respondeu",
            camada=camada.camada,
            desfecho=resposta.desfecho,
            fontes=[f"{f.tipo}:{f.id}@{f.versao}" for f in resposta.fontes],
            lacuna=resposta.lacuna,
        )
        if resposta.desfecho == "transferir":
            return _transferir(grafo, state)
        texto_da_camada = resposta.texto or ""  # `valida()` ja' exigiu o texto
        esclarecer = resposta.desfecho == "esclarecer"
        prazo = _isenta_prazo(config, resposta)
        motivo = _travas.motivo_de_recusa(
            grafo, texto_da_camada, esclarecer=esclarecer, prazo_ratificado=prazo
        )
        if motivo is not None:
            # As travas de politica sao do ESQUELETO (B1 da revisao independente): reprovou, nao sai.
            logger.warning("helena_cobertura_texto_barrado", camada=camada.camada, motivo=motivo)
            return texto_do_aviso(grafo, state)
        final = _com_192_se_preciso(state, texto_da_camada)
        _guardar(final, prazo_da_conversa=_conversa(state) if prazo else None)
        return grafo._texto_fixo(grafo._update_sem_modelo(state), final)
    # Nenhuma camada respondeu: a lacuna de conteudo fica registrada e vale o aviso de canais (falha fechada).
    logger.info("helena_cobertura_lacuna", lacuna="sem_camada_que_responda", camadas=len(config.camadas))
    return texto_do_aviso(grafo, state)


def _transferir(grafo: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any]:
    """Sem regra correspondente ou com dado conflitante: a OFERTA de uma pessoa (o caso so' com o "sim")."""
    update = grafo._update_sem_modelo(state)
    texto = _com_192_se_preciso(state, TEXTO_TRANSFERIR)
    _guardar(texto)
    return grafo._texto_fixo(update, texto, pendente=PENDENTE_OFERTA_EQUIPE)


def _com_192_se_preciso(state: Mapping[str, Any], texto: str) -> str:
    """O mesmo 192 do aviso (DL-0091): na FRENTE com sinal de saude; no fim, gestante com urgencia."""
    from maezo.agents.helena.graph import _com_emergencia_se_preciso

    return _com_emergencia_se_preciso(state, texto)


def _sujeito(state: Mapping[str, Any]) -> str | None:
    identidade = state.get("identidade_beneficiario")
    ref = identidade.get("portable_subject_ref") if isinstance(identidade, Mapping) else None
    return ref if isinstance(ref, str) and ref else None


__all__ = [
    "CAMADAS",
    "CHAVE_DAS_CAMADAS",
    "CHAVE_DO_HASH_K2",
    "CHAVE_DO_GRAFO",
    "CHAVE_DO_PACOTE",
    "DESFECHOS",
    "FORMULA_FIXA",
    "TEXTO_TRANSFERIR",
    "Camada",
    "CamadaDeCobertura",
    "ConfiguracaoDaCobertura",
    "Desfecho",
    "Fonte",
    "PerguntaDeCobertura",
    "RespostaDaCobertura",
    "atender",
    "da_configuracao",
    "e_texto_da_cobertura",
    "prazo_ratificado",
    "texto_do_aviso",
]

"""Superficie "Meu plano" do beneficiario (E-SF-RT-04, variante RT-04r), construida FECHADA.

Modulo puro: NAO cria router FastAPI e NAO e' importado por `app.py`; nada e' montado. Casa provisoria no
portal do maezo (a casa definitiva da superficie e' decisao do dono).

O que existe, tudo atras da chave `MEU_PLANO_LIGADO` (desligada: toda leitura recusa):

* Visao do plano so' com FATOS DA FONTE (data de aniversario, valor antes e depois do reajuste, data do fato,
  recibo de renovacao). Nada e' calculado aqui: nenhum percentual, total ou diferenca. Sem fonte, recusa;
  nao ha' valor padrao.
* Renovacao com zero acao: nenhuma acao "renovar" ou "confirmar renovacao"; o recibo so' existe com fato de
  fonte.
* Cancelar sempre visivel, no primeiro nivel de toda visao. Pedido em 2 toques ate' o protocolo; o protocolo
  vem so' da custodia. Nenhum prazo nem dispositivo normativo e' exibido.
* Opcoes so' a pedido, com o catalogo que a fonte der (sem preco, sem comparacao, sem ordenacao ou filtro por
  idade, uso ou saude). Troca de plano so' ASSISTIDA: vira pedido para pessoa, com protocolo da custodia.
* Linha de alternativa no "quero cancelar": estrutura com exibicao DESLIGADA e sem texto. Liga-la exige ato
  do dono e redacao do juridico.

Nenhum envio, nenhum evento de medicao, zero dado de saude. Erros com `code` fixo; nunca ecoam valor.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol

from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude, valor_valido_para_classe

#: Chave da superficie. Ligar e' PR proprio, depois dos atos de que a superficie depende.
MEU_PLANO_LIGADO: Final[bool] = False
#: Linha de alternativa no cancelamento: construida desligada e sem texto (redacao nao aprovada).
LINHA_ALTERNATIVA_EXIBIDA: Final[bool] = False
TEXTO_LINHA_ALTERNATIVA: Final[None] = None

#: Acoes da visao do plano. `cancelar` vem sempre primeiro; nao existe acao de renovar.
ACOES_DA_VISAO: Final[tuple[str, ...]] = ("cancelar", "ver_opcoes")
#: Unica acao da tela de cancelamento (toque 2). Nenhum passo extra.
ACOES_DO_CANCELAMENTO: Final[tuple[str, ...]] = ("confirmar_cancelamento",)
#: Fila humana da troca assistida (nome de papel, nao de pessoa).
FILA_DA_TROCA_ASSISTIDA: Final[str] = "especialista_faixa_assistida"

#: PROVISORIO: redacao com o juridico. Sem prazo, sem "em analise", sem dispositivo normativo.
TEXTO_PEDIDO_DE_CANCELAMENTO: Final[str] = (
    "Recebemos seu pedido de cancelamento. Seu protocolo é {protocolo}. "
    "Uma pessoa da nossa equipe vai concluir o pedido."
)

CAMPOS_DOS_FATOS: Final[frozenset[str]] = frozenset(
    {"plano_ref", "aniversario", "valor_atual", "valor_reajustado", "data_do_fato", "recibo_renovacao_ref"}
)
CAMPOS_DA_OPCAO: Final[frozenset[str]] = frozenset({"produto_ref", "nome"})

#: Referencia opaca estrita: UUID canonico ou hexadecimal minusculo, com prefixo opcional de conjunto
#: FECHADO. Prefixo livre de letras escreveria palavra (ex.: termo clinico seguido de hexadecimal).
_REF_OPACA: Final[re.Pattern[str]] = re.compile(
    r"(?:(?:ben|pla|pro|rec)_)?(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,64})"
)
#: Nomes do catalogo PUBLICADO do portfolio. GAP: nao ha' catalogo publicado; o conjunto e' VAZIO e toda opcao
#: e' recusada. Nome de produto nunca e' texto avulso: so' vale o que pertence ao catalogo publicado.
NOMES_DO_CATALOGO_PUBLICADO: Final[frozenset[str]] = frozenset()


class MeuPlanoRecusado(ValueError):  # noqa: N818
    """Recusa com codigo fixo; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _exigir_ligado() -> None:
    if MEU_PLANO_LIGADO is not True:
        raise MeuPlanoRecusado("meu_plano_desligado")


def _ref(valor: object) -> str:
    if type(valor) is str and _REF_OPACA.fullmatch(valor) is not None:
        return valor
    raise MeuPlanoRecusado("meu_plano_valor_fora_da_classe")


def _ref_opcional(valor: object) -> str | None:
    return None if valor is None else _ref(valor)


def _data(valor: object) -> date:
    if type(valor) is date:
        return valor
    raise MeuPlanoRecusado("meu_plano_valor_fora_da_classe")


def _data_opcional(valor: object) -> date | None:
    return None if valor is None else _data(valor)


def _valor(valor: object) -> str | None:
    if valor is None:
        return None
    if valor_valido_para_classe("valor_cobranca_reais", valor):
        return str(valor)
    raise MeuPlanoRecusado("meu_plano_valor_fora_da_classe")


# ------------------------------------------------------------------------------------------- portas
class FonteDosFatosDoPlano(Protocol):
    # Nenhuma implementacao existe (GAP: fato de reajuste e de renovacao vem da fonte da operadora).
    async def fatos(self, *, titular_ref: str) -> Mapping[str, object] | None: ...


class FonteDoCatalogo(Protocol):
    # Nenhuma implementacao existe (GAP: catalogo publicado do portfolio).
    async def opcoes(self, *, plano_ref: str) -> Sequence[Mapping[str, object]] | None: ...


class CustodiaDeProtocolo(Protocol):
    # Nenhuma implementacao existe (GAP: custodia de protocolo de atendimento).
    async def registrar(self, *, titular_ref: str, assunto: str) -> str | None: ...


# ------------------------------------------------------------------------------------------- visao
@dataclass(frozen=True, slots=True)
class AvisoDeReajuste:
    """So' fatos da fonte. Sem percentual, sem diferenca, sem oferta neste bloco."""

    valor_atual: str | None
    valor_reajustado: str | None
    data_do_fato: date | None


@dataclass(frozen=True, slots=True)
class VisaoMeuPlano:
    plano_ref: str
    aniversario: date
    aviso_de_reajuste: AvisoDeReajuste
    recibo_renovacao_ref: str | None
    acoes: tuple[str, ...]


def _fatos_validados(dados: object) -> VisaoMeuPlano:
    if not isinstance(dados, Mapping) or set(dados) != CAMPOS_DOS_FATOS:
        raise MeuPlanoRecusado("meu_plano_fonte_fora_do_contrato")
    return VisaoMeuPlano(
        plano_ref=_ref(dados["plano_ref"]),
        aniversario=_data(dados["aniversario"]),
        aviso_de_reajuste=AvisoDeReajuste(
            valor_atual=_valor(dados["valor_atual"]),
            valor_reajustado=_valor(dados["valor_reajustado"]),
            data_do_fato=_data_opcional(dados["data_do_fato"]),
        ),
        recibo_renovacao_ref=_ref_opcional(dados["recibo_renovacao_ref"]),
        acoes=ACOES_DA_VISAO,
    )


async def projetar_meu_plano(fonte: FonteDosFatosDoPlano, *, titular_ref: str) -> VisaoMeuPlano:
    """Visao do plano so' com fatos da fonte. Sem fonte (ou fonte vazia), recusa; nao ha' valor padrao."""
    _exigir_ligado()
    _ref(titular_ref)
    try:
        dados = await fonte.fatos(titular_ref=titular_ref)
    except Exception:
        raise MeuPlanoRecusado("meu_plano_sem_fonte") from None
    if dados is None:
        raise MeuPlanoRecusado("meu_plano_sem_fonte")
    return _fatos_validados(dados)


# ------------------------------------------------------------------------------------------- cancelar
@dataclass(frozen=True, slots=True)
class TelaDeCancelamento:
    """Toque 1. Sem linha de alternativa enquanto a exibicao estiver desligada; nenhum passo extra."""

    acoes: tuple[str, ...]
    linha_alternativa: None


@dataclass(frozen=True, slots=True)
class PedidoDeCancelamento:
    protocolo_ref: str
    texto_ao_cliente: str


def abrir_cancelamento(visao: VisaoMeuPlano) -> TelaDeCancelamento:
    _exigir_ligado()
    if type(visao) is not VisaoMeuPlano or visao.acoes[:1] != ("cancelar",):
        raise MeuPlanoRecusado("meu_plano_visao_invalida")
    return TelaDeCancelamento(acoes=ACOES_DO_CANCELAMENTO, linha_alternativa=None)


async def confirmar_cancelamento(
    tela: TelaDeCancelamento, custodia: CustodiaDeProtocolo, *, titular_ref: str
) -> PedidoDeCancelamento:
    """Toque 2: protocolo so' da custodia; sem protocolo, nenhum texto. A efetivacao e' humana."""
    _exigir_ligado()
    if type(tela) is not TelaDeCancelamento:
        raise MeuPlanoRecusado("meu_plano_tela_invalida")
    protocolo = await _protocolo(custodia, titular_ref=titular_ref, assunto="cancelamento")
    return PedidoDeCancelamento(
        protocolo_ref=protocolo, texto_ao_cliente=TEXTO_PEDIDO_DE_CANCELAMENTO.format(protocolo=protocolo)
    )


async def _protocolo(custodia: CustodiaDeProtocolo, *, titular_ref: str, assunto: str) -> str:
    _ref(titular_ref)
    try:
        protocolo = await custodia.registrar(titular_ref=titular_ref, assunto=assunto)
    except Exception:
        raise MeuPlanoRecusado("meu_plano_sem_protocolo") from None
    if type(protocolo) is not str or not valor_valido_para_classe("protocolo_ref", protocolo):
        raise MeuPlanoRecusado("meu_plano_sem_protocolo")
    return protocolo


# ------------------------------------------------------------------------------------------- opcoes
@dataclass(frozen=True, slots=True)
class OpcaoDoPortfolio:
    """So' o que a fonte der: referencia e nome. Sem preco, sem comparacao."""

    produto_ref: str
    nome: str


async def listar_opcoes(fonte: FonteDoCatalogo, *, plano_ref: str) -> tuple[OpcaoDoPortfolio, ...]:
    """So' a pedido. Ordem da fonte; nenhuma ordenacao ou filtro aqui. Campo fora do contrato recusa."""
    _exigir_ligado()
    _ref(plano_ref)
    try:
        bruto = await fonte.opcoes(plano_ref=plano_ref)
    except Exception:
        raise MeuPlanoRecusado("meu_plano_sem_fonte") from None
    if bruto is None or isinstance(bruto, str | bytes) or not isinstance(bruto, Sequence):
        raise MeuPlanoRecusado("meu_plano_sem_fonte")
    opcoes: list[OpcaoDoPortfolio] = []
    for item in bruto:
        if not isinstance(item, Mapping) or set(item) != CAMPOS_DA_OPCAO:
            raise MeuPlanoRecusado("meu_plano_fonte_fora_do_contrato")
        nome = item["nome"]
        if type(nome) is not str or nome not in NOMES_DO_CATALOGO_PUBLICADO:
            raise MeuPlanoRecusado("meu_plano_valor_fora_da_classe")
        if contem_dado_de_saude(nome):
            raise MeuPlanoRecusado("PHI_IN_COMMERCIAL_INPUT")
        opcoes.append(OpcaoDoPortfolio(produto_ref=_ref(item["produto_ref"]), nome=nome))
    return tuple(opcoes)


@dataclass(frozen=True, slots=True)
class PedidoDeTrocaAssistida:
    protocolo_ref: str
    fila: Literal["especialista_faixa_assistida"]


async def pedir_troca_assistida(
    custodia: CustodiaDeProtocolo, *, titular_ref: str, produto_ref: str
) -> PedidoDeTrocaAssistida:
    """Troca de plano SO' assistida: vira pedido para pessoa, com protocolo. Nao ha' troca em autosservico."""
    _exigir_ligado()
    _ref(produto_ref)
    protocolo = await _protocolo(custodia, titular_ref=titular_ref, assunto="troca_de_plano")
    return PedidoDeTrocaAssistida(protocolo_ref=protocolo, fila="especialista_faixa_assistida")

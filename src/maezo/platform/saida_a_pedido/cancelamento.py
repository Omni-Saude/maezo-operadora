"""Pedido de cancelamento pela pagina (EP-REG-27b + RT-05), separado do arrependimento.

Modelo de dominio puro: entrada sempre visivel, ecra de 2 toques com o bloco do art. 15 como slot
obrigatorio, lista de guarda e recebimento que monta as variaveis de inicio do SP-OP-CANCEL-001 (REUSE do
back-office). Nao fabrica fatos (caem em ANALISE_HUMANA), nao exibe prazo regulatorio e nao inclui a linha
de OS-08 enquanto os portoes (RL-REG-27, RT-J2, RT-J3 (a)) estiverem fechados.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, cast

from maezo.platform.saida_a_pedido.janela import JanelaAvaliada
from maezo.platform.saida_a_pedido.politica import PoliticaPedidoCancelamento

_TOQUE_1: Final = "toque_1_pedido"
_TOQUE_2: Final = "toque_2_confirmacao"
_ESTADO_AGUARDANDO: Final[str] = "Aguardando o comprovante de cancelamento"
_ROTULO_CANCELADO: Final[str] = "Cancelado"
_CHAVES_DA_SUBMISSAO: Final[frozenset[str]] = frozenset(
    {
        "pedido_ref",
        "contrato_ref",
        "bloco_art15_incisos_apresentados",
        "bloco_art15_versao_politica",
        "toques",
    }
)
_INSUMO_PROIBIDO: Final[re.Pattern[str]] = re.compile(r"(?i)uso|utiliz|procedimento|cid|saude|saúde")


@dataclass(frozen=True, slots=True)
class PerfilContratual:
    tipo_plano: Literal["individual", "familiar", "coletivo_empresarial", "coletivo_adesao", "desconhecido"]
    papel: Literal["titular", "dependente"]
    inadimplente: bool  # so' para provar que NAO esconde a entrada (art. 21)


@dataclass(frozen=True, slots=True)
class EntradaCancelamento:
    visivel: Literal[True]
    rotulo: str
    rota: Literal["pedido_pagina", "texto_fixo_dl0098_e_rota_humana"]


def entrada_do_cancelamento(
    perfil: PerfilContratual, politica: PoliticaPedidoCancelamento
) -> EntradaCancelamento:
    """Sempre visivel, para todo perfil e independente de `inadimplente` (art. 21; RT-06)."""
    if perfil.papel == "dependente":
        return EntradaCancelamento(
            visivel=True,
            rotulo=politica.textos["entrada_dependente_coletivo"],
            rota="texto_fixo_dl0098_e_rota_humana",
        )
    if perfil.tipo_plano in ("individual", "familiar"):
        return EntradaCancelamento(
            visivel=True, rotulo=politica.textos["entrada_titular_pf"], rota="pedido_pagina"
        )
    # titular de coletivo ou desconhecido: DL-0098, quem decide e' a empresa/administradora
    return EntradaCancelamento(
        visivel=True, rotulo=politica.textos["entrada_titular_pf"], rota="texto_fixo_dl0098_e_rota_humana"
    )


TipoElemento = Literal[
    "bloco_art15",
    "aviso_arrependimento",
    "botao_pedir_cancelamento",
    "botao_confirmar",
    "linha_alternativa",
    "prazo_regulatorio",
    "pergunta_tem_certeza",
    "oferta",
    "exigir_ligacao",
    "pergunta_motivo",
]
AcaoElemento = Literal["nenhuma", "abrir_formulario_arrependimento", "submeter_pedido"]


@dataclass(frozen=True, slots=True)
class Botao:
    primario: bool
    visivel: bool
    habilitado: bool
    atras_de_mais_opcoes: bool


@dataclass(frozen=True, slots=True)
class Elemento:
    tipo: TipoElemento
    obrigatorio: bool = False
    acao: AcaoElemento = "nenhuma"
    botao: Botao | None = None
    incisos: tuple[str, ...] = ()  # so' para bloco_art15
    texto_exibivel: bool = False  # False enquanto texto nao ratificado


@dataclass(frozen=True, slots=True)
class Passo:
    nome: Literal["toque_1_pedido", "toque_2_confirmacao", "pos_protocolo"]
    elementos: tuple[Elemento, ...]


@dataclass(frozen=True, slots=True)
class TelaPedidoCancelamento:
    passos_ate_protocolo: tuple[Passo, ...]
    passos_pos_protocolo: tuple[Passo, ...]
    insumos: frozenset[str]
    rotulos_de_estado: tuple[str, ...]
    publicavel: bool  # False enquanto bloco art. 15 sem texto ratificado


def montar_tela_pedido(
    politica: PoliticaPedidoCancelamento, *, janela: JanelaAvaliada | None
) -> TelaPedidoCancelamento:
    botao_primario = Botao(primario=True, visivel=True, habilitado=True, atras_de_mais_opcoes=False)
    toque_1: list[Elemento] = [
        Elemento(
            tipo="bloco_art15",
            obrigatorio=True,
            incisos=politica.incisos_exigidos,
            texto_exibivel=politica.texto_ratificado,
        ),
        Elemento(tipo="botao_pedir_cancelamento", acao="submeter_pedido", botao=botao_primario),
    ]
    if janela is not None and janela.situacao in ("DENTRO", "INDETERMINADA") and janela.termos_finais:
        # so' link para o fluxo distinto do arrependimento; sem numero (texto do aviso e' dado)
        toque_1.append(Elemento(tipo="aviso_arrependimento", acao="abrir_formulario_arrependimento"))
    toque_2 = [Elemento(tipo="botao_confirmar", acao="submeter_pedido", botao=botao_primario)]
    return TelaPedidoCancelamento(
        passos_ate_protocolo=(
            Passo(nome=_TOQUE_1, elementos=tuple(toque_1)),
            Passo(nome=_TOQUE_2, elementos=tuple(toque_2)),
        ),
        # E-SF-RT-02: uma pergunta OPCIONAL, so' depois do protocolo (a guarda GUARD_REQUIRES_REASON
        # recusa a pergunta antes do protocolo ou obrigatoria).
        passos_pos_protocolo=(
            Passo(nome="pos_protocolo", elementos=(Elemento(tipo="pergunta_motivo", obrigatorio=False),)),
        ),
        insumos=frozenset({"tipo_plano", "papel"}),
        rotulos_de_estado=(politica.textos["estado_aguardando"],),
        publicavel=politica.texto_ratificado,
    )


def verificar_lista_de_guarda(
    tela: TelaPedidoCancelamento, politica: PoliticaPedidoCancelamento
) -> tuple[str, ...]:
    """Um codigo por violacao (RT-05 item 8 + OS-08 + REG-27)."""
    achados: list[str] = []
    todos = [e for p in (*tela.passos_ate_protocolo, *tela.passos_pos_protocolo) for e in p.elementos]
    ate_protocolo = [e for p in tela.passos_ate_protocolo for e in p.elementos]
    tipos = {e.tipo for e in todos}

    if "pergunta_tem_certeza" in tipos:
        achados.append("GUARD_EXTRA_CONFIRMATION")
    if len(tela.passos_ate_protocolo) != politica.toques_ate_protocolo:
        achados.append("GUARD_TOUCH_COUNT")
    if "oferta" in tipos:
        achados.append("GUARD_MULTI_STEP_OFFER")
    for e in todos:
        if e.tipo in ("botao_pedir_cancelamento", "botao_confirmar"):
            b = e.botao
            if b is None or not b.primario or not b.visivel or not b.habilitado or b.atras_de_mais_opcoes:
                achados.append("GUARD_CANCEL_BUTTON_NOT_PRIMARY")
                break
    if "exigir_ligacao" in tipos:
        achados.append("GUARD_REQUIRES_CALL")
    if any(e.tipo == "pergunta_motivo" for e in ate_protocolo) or any(
        e.tipo == "pergunta_motivo" and e.obrigatorio for e in todos
    ):
        achados.append("GUARD_REQUIRES_REASON")
    if any(_INSUMO_PROIBIDO.search(i) for i in tela.insumos):
        achados.append("GUARD_USES_PLAN_USAGE")
    if any("cancelado" in r.lower() for r in tela.rotulos_de_estado):
        achados.append("GUARD_CANCELLED_BEFORE_SOURCE_FACT")
    if "linha_alternativa" in tipos and not politica.linha_alternativa_exibir:
        achados.append("OS08_LINE_NOT_ADMITTED")
    if "prazo_regulatorio" in tipos and not politica.prazo_exibir:
        achados.append("REGULATORY_DEADLINE_BEFORE_REG27")
    if any(e.tipo == "aviso_arrependimento" and e.acao != "abrir_formulario_arrependimento" for e in todos):
        achados.append("WITHDRAWAL_FLOW_FUSED")
    toque_1 = next((p for p in tela.passos_ate_protocolo if p.nome == _TOQUE_1), None)
    exigidos = set(politica.incisos_exigidos)
    if toque_1 is None or not any(
        e.tipo == "bloco_art15" and set(e.incisos) == exigidos for e in toque_1.elementos
    ):
        achados.append("ART15_BLOCK_MISSING")
    return tuple(achados)


class PedidoCancelamentoRecusado(ValueError):  # noqa: N818 - nome fixado pelo desenho
    code: Literal["terms_incomplete", "unknown_field", "WITHDRAWAL_FLOW_FUSED"]

    def __init__(self, code: Literal["terms_incomplete", "unknown_field", "WITHDRAWAL_FLOW_FUSED"]) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SubmissaoCancelamento:
    pedido_ref: str
    contrato_ref: str  # opaco
    bloco_art15_incisos_apresentados: tuple[str, ...]
    bloco_art15_versao_politica: int
    toques: tuple[Literal["toque_1_pedido", "toque_2_confirmacao"], ...]


def submissao_de_payload(payload: Mapping[str, object]) -> SubmissaoCancelamento:
    """Chaves fechadas: `declaracao`/`tipo_do_ato`/`motivo`/`uso_do_plano` levantam `unknown_field`,
    o que fecha a fusao com o arrependimento."""
    if set(payload) - _CHAVES_DA_SUBMISSAO:
        raise PedidoCancelamentoRecusado("unknown_field")
    pedido_ref = payload.get("pedido_ref")
    contrato_ref = payload.get("contrato_ref")
    incisos = payload.get("bloco_art15_incisos_apresentados")
    versao = payload.get("bloco_art15_versao_politica")
    toques = payload.get("toques")
    if not isinstance(pedido_ref, str) or not pedido_ref.strip():
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if not isinstance(contrato_ref, str) or not contrato_ref.strip():
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if not isinstance(incisos, list | tuple) or not all(isinstance(i, str) for i in incisos):
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if not isinstance(versao, int) or isinstance(versao, bool):
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if not isinstance(toques, list | tuple) or not all(isinstance(t, str) for t in toques):
        raise PedidoCancelamentoRecusado("terms_incomplete")
    return SubmissaoCancelamento(
        pedido_ref=pedido_ref,
        contrato_ref=contrato_ref,
        bloco_art15_incisos_apresentados=tuple(str(i) for i in incisos),
        bloco_art15_versao_politica=versao,
        toques=cast("tuple[Literal['toque_1_pedido', 'toque_2_confirmacao'], ...]", tuple(toques)),
    )


@dataclass(frozen=True, slots=True)
class PedidoCancelamentoAdmitido:
    variaveis_de_inicio_cancel_001: Mapping[str, object]
    bloco_art15_ref: str  # "cancel_request_rn561/v{versao}#I-VI" — copiado ao comprovante (art. 16 II)
    estado_tracker: Literal["AGUARDANDO_COMPROVANTE_DE_CANCELAMENTO"]
    prazo_exibido: None = None


def receber_pedido_cancelamento(
    sub: SubmissaoCancelamento,
    politica: PoliticaPedidoCancelamento,
    *,
    recebido_em: datetime,
    tenant_id: str,
) -> PedidoCancelamentoAdmitido:
    if set(sub.bloco_art15_incisos_apresentados) != set(politica.incisos_exigidos):
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if sub.bloco_art15_versao_politica != politica.versao:
        raise PedidoCancelamentoRecusado("terms_incomplete")
    if sub.toques != (_TOQUE_1, _TOQUE_2):
        raise PedidoCancelamentoRecusado("terms_incomplete")
    bloco_ref = f"cancel_request_rn561/v{politica.versao}#I-VI"
    # NAO fabrica titularidade_confirmada/vinculo_ativo/dentro_prazo: ausentes, caem em False em
    # validate_cancel e rotulam ANALISE_HUMANA pelo catch-all do cancel_admissibility.
    variaveis: dict[str, Any] = {
        "tenant_id": tenant_id,
        "tipo_solicitacao": "pedido_beneficiario",
        "origem_solicitacao": "beneficiario",
        "data_solicitacao_iso": recebido_em.date().isoformat(),
        "documentos_refs": [{"tipo": "bloco_art15", "ref": bloco_ref}],
        "beneficiario_pseudo_id": sub.contrato_ref,
    }
    return PedidoCancelamentoAdmitido(
        variaveis_de_inicio_cancel_001=variaveis,
        bloco_art15_ref=bloco_ref,
        estado_tracker="AGUARDANDO_COMPROVANTE_DE_CANCELAMENTO",
    )


def rotulo_do_estado(
    *, fato_de_fonte_executado: bool, politica: PoliticaPedidoCancelamento | None = None
) -> str:
    """ "Cancelado" so' com fato de fonte (TB-17 (iv)); antes, o rotulo de espera sem a palavra."""
    if fato_de_fonte_executado:
        return _ROTULO_CANCELADO
    return politica.textos["estado_aguardando"] if politica is not None else _ESTADO_AGUARDANDO


# ---------------------------------------------------------------------------------------------------
# E-SF-RT-02 (RT-05 item 5) — motivo de cancelamento em lista fechada, sem saude, sem vinculo ao titular
# ---------------------------------------------------------------------------------------------------

CodigoMotivoCancelamento = Literal[
    "valor_alto",
    "mudanca_de_cidade",
    "outro_vinculo",
    "rede_nao_atende",
    "atendimento_servico",
    "prefiro_nao_dizer",
]
#: A lista fechada, na ordem da tela. Nenhum codigo de saude (RT-05 item 5): texto livre poderia carregar
#: dado de saude, por isso nao ha opcao "outro" com campo aberto.
CODIGOS_MOTIVO: Final[tuple[CodigoMotivoCancelamento, ...]] = (
    "valor_alto",
    "mudanca_de_cidade",
    "outro_vinculo",
    "rede_nao_atende",
    "atendimento_servico",
    "prefiro_nao_dizer",
)


class MotivoForaDaListaError(ValueError):
    """Valor fora da lista fechada (inclusive texto livre). O codigo e' fixo e o valor nunca e' ecoado."""

    code: Literal["reason_not_in_closed_list"]

    def __init__(self) -> None:
        super().__init__("reason_not_in_closed_list")
        self.code = "reason_not_in_closed_list"


def rotulos_do_motivo(
    politica: PoliticaPedidoCancelamento,
) -> tuple[tuple[CodigoMotivoCancelamento, str], ...]:
    """(codigo, rotulo) na ordem da tela; os rotulos sao dado da politica (rascunho, NAO_RATIFICADO)."""
    return tuple((codigo, politica.textos[f"motivo_{codigo}"]) for codigo in CODIGOS_MOTIVO)


def codigo_do_motivo(resposta: object) -> CodigoMotivoCancelamento | None:
    """`None` = pulou (um toque, sem consequencia). Qualquer outro valor fora da lista e' recusado.

    O codigo devolvido NAO entra em `SubmissaoCancelamento` nem nas variaveis de inicio do CANCEL-001:
    o protocolo ja' foi emitido e nao depende dele. O unico destino admitido e' `ContagemDeMotivos`.
    """
    if resposta is None:
        return None
    if type(resposta) is str and resposta in CODIGOS_MOTIVO:
        # Devolve o codigo CANONICO da lista, nunca o objeto recebido.
        return CODIGOS_MOTIVO[CODIGOS_MOTIVO.index(resposta)]
    raise MotivoForaDaListaError


@dataclass(frozen=True, slots=True)
class ContagemDeMotivos:
    """Contagem agregada por codigo, SEM vinculo ao titular (RT-D1 fechado; E35 = B).

    Nao guarda pessoa, contrato, pedido, protocolo, tenant nem instante: so' o numero de respostas por
    codigo. Modelo puro: nada aqui emite metrica, evento ou grava em banco; ligar um emissor e' ato
    proprio (o evento catalogado de motivo segue sem emissor, E-SF-35r).
    """

    por_codigo: tuple[tuple[CodigoMotivoCancelamento, int], ...] = tuple((c, 0) for c in CODIGOS_MOTIVO)

    def __post_init__(self) -> None:
        # So' a lista fechada, na ordem, com inteiros >= 0: nenhum outro dado cabe aqui.
        if tuple(c for c, _ in self.por_codigo) != CODIGOS_MOTIVO or any(
            type(n) is not int or n < 0 for _, n in self.por_codigo
        ):
            raise MotivoForaDaListaError

    def somar(self, codigo: object) -> "ContagemDeMotivos":
        codigo = codigo_do_motivo(codigo)
        if codigo is None:
            return self
        return ContagemDeMotivos(
            por_codigo=tuple((c, n + 1 if c == codigo else n) for c, n in self.por_codigo)
        )

    def total(self, codigo: CodigoMotivoCancelamento) -> int:
        return dict(self.por_codigo)[codigo]

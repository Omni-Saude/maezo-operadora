"""Pacote PAGTO PR-E — `detect_duplicidade` (analise PAGTO §4) e o envoltorio de liberacao.

Razao FALSA em memoria (a semantica SQL real e' provada ao vivo em
`tests/unit/platform/test_pagto_obrigacao_ledger_live_pg.py`) e DMN falsa (as linhas reais da
`pagto_duplicidade` sao provadas no MOTOR em `tests/integration/dmn/test_pagto_duplicidade_engine.py`).
Aqui: o que o detector manda para a razao e para a DMN, e a falha fechada de cada dependencia.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.pagto_obrigacao.ledger import (
    EVENTO_LIBERADA,
    EVENTO_REGISTRADA,
    NIVEL1_LIBERADA_OUTRA_ORDEM,
    NIVEL1_SEM_OUTRA_ORDEM,
    EntradaObrigacao,
    IndiciosDuplicidade,
    RazaoIndisponivelError,
    chave_obrigacao,
)
from maezo.tools.workers import pagto
from maezo.tools.workers import pagto_duplicidade as dup
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.pagto import PagtoError, execute_pagto, release_high_value_payment

_TENANT = "amh"


class _RazaoFalsa:
    def __init__(
        self,
        indicios: IndiciosDuplicidade | None = None,
        *,
        erro: Exception | None = None,
        tenant: str = _TENANT,
    ) -> None:
        self._indicios = indicios or _indicios()
        self._erro = erro
        self._tenant = tenant
        self.registradas: list[EntradaObrigacao] = []
        self.liberadas: list[EntradaObrigacao] = []

    @property
    def tenant(self) -> str:
        return self._tenant

    async def registrar_e_ler(self, entrada: EntradaObrigacao) -> IndiciosDuplicidade:
        if self._erro is not None:
            raise self._erro
        self.registradas.append(entrada)
        return self._indicios

    async def registrar_liberacao(self, entrada: EntradaObrigacao) -> bool:
        if self._erro is not None:
            raise self._erro
        self.liberadas.append(entrada)
        return True


def _indicios(**campos: Any) -> IndiciosDuplicidade:
    base: dict[str, Any] = {
        "nivel1_situacao": NIVEL1_SEM_OUTRA_ORDEM,
        "nivel1_ordem_id": "",
        "nivel2_soma_outras_cents": 0,
        "nivel3_avaliado": True,
        "nivel3_similar_idade_dias": None,
        "nivel3_ordem_id": "",
        "gravou": True,
    }
    base.update(campos)
    return IndiciosDuplicidade(**base)


def _dmn(status: str = "NENHUMA", motivo: str = "SEM_INDICIO") -> FakeDmnTransport:
    dmn = FakeDmnTransport()
    dmn.register("pagto_duplicidade", [{"duplicidade_status": status, "duplicidade_motivo_codigo": motivo}])
    return dmn


def _ordem_contas(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "semente-ignorada",
        "ordem_pagamento_id": "ORDEM-1",
        "lastro_origem": "contas_adjudicacao_humana",
        "numero_lote_tiss": "LOTE-1",
        "numero_guia_tiss": "GUIA-1",
        "prestador_id": "prov:1",
        "competencia": "2026-06",
        "valor_pagamento_cents": 50_000,
        "lastro_status": "CONFIRMADO",
        "lastro_origem_instancia_id": "pi-contas-1",
        "lastro_valor_origem_cents": 60_000,
    }
    base.update(campos)
    return base


# --- vocabulario e cercas ---------------------------------------------------------------------


def test_saidas_e_evidencia_sao_fatos_proibidos_na_semente() -> None:
    assert dup.DUPLICIDADE_SAIDAS <= pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE
    assert "duplicidade_status" in dup.DUPLICIDADE_SAIDAS
    # A evidencia da origem (o que escolhe ONDE procurar) segue NAO proibida.
    assert "lastro_origem" not in pagto.PAGTO_FATOS_PROIBIDOS_NA_SEMENTE


def test_escopo_de_leitura_e_explicito_e_estende_o_do_lastro() -> None:
    from maezo.tools.workers.pagto_lastro import VALIDATE_COM_LASTRO_VARIABLES

    variaveis = dup.VALIDATE_COM_DUPLICIDADE_VARIABLES
    assert variaveis[: len(VALIDATE_COM_LASTRO_VARIABLES)] == VALIDATE_COM_LASTRO_VARIABLES
    assert "competencia" in variaveis
    assert len(set(variaveis)) == len(variaveis)
    assert "all" not in variaveis and "*" not in variaveis


def test_o_detector_nao_le_relogio_nem_cliente_de_rede() -> None:
    """Idade do nivel 3 vem do relogio do BANCO; banco/motor so' pelas costuras injetadas."""
    tree = ast.parse(Path(inspect.getfile(dup)).read_text(encoding="utf-8"))
    importados = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not importados & {"asyncpg", "httpx", "datetime", "time", "uuid", "random"}


def test_a_janela_do_nivel_3_nao_mora_no_python() -> None:
    fonte = Path(inspect.getfile(dup)).read_text(encoding="utf-8")
    assert "180" not in fonte


# --- caminho feliz: o que vai para a razao e para a DMN ---------------------------------------


def test_contas_grava_registrada_com_a_chave_da_obrigacao_e_avalia_a_dmn() -> None:
    razao, dmn = _RazaoFalsa(), _dmn()
    out = dup.detect_duplicidade(_ordem_contas(), ledger=razao, dmn=dmn)
    assert set(out) == dup.DUPLICIDADE_SAIDAS
    assert out["duplicidade_status"] == "NENHUMA"
    (entrada,) = razao.registradas
    assert entrada.evento == EVENTO_REGISTRADA
    assert entrada.tenant == _TENANT  # o do STORE, nunca o da semente
    assert entrada.origem == "contas"
    assert entrada.chave_obrigacao == chave_obrigacao(_TENANT, "contas", ("LOTE-1", "prov:1", "GUIA-1"))
    assert entrada.origem_instancia_id == "pi-contas-1"
    assert out["duplicidade_chave_obrigacao"] == entrada.chave_obrigacao
    assert dmn.calls == [
        (
            "pagto_duplicidade",
            {
                "nivel1_situacao": "SEM_OUTRA_ORDEM",
                "nivel2_situacao": "NAO_EXCEDE",
                "nivel3_avaliado": True,
                "nivel3_similar_idade_dias": None,
            },
        )
    ]


def test_recurso_usa_a_glosa_como_obrigacao() -> None:
    razao = _RazaoFalsa()
    dup.detect_duplicidade(
        _ordem_contas(lastro_origem="recurso_deferimento_humano", glosa_id="GLOSA-9"),
        ledger=razao,
        dmn=_dmn(),
    )
    assert razao.registradas[0].chave_obrigacao == chave_obrigacao(_TENANT, "recurso", ("GLOSA-9",))


def test_ordem_sem_origem_conferivel_usa_a_propria_ordem() -> None:
    razao = _RazaoFalsa()
    dup.detect_duplicidade(_ordem_contas(lastro_origem=""), ledger=razao, dmn=_dmn())
    assert razao.registradas[0].chave_obrigacao == chave_obrigacao(_TENANT, "outro", ("ORDEM-1",))


def test_mesma_conta_em_duas_ordens_da_a_mesma_chave_guia_diferente_nao() -> None:
    a, b, c = _RazaoFalsa(), _RazaoFalsa(), _RazaoFalsa()
    dup.detect_duplicidade(_ordem_contas(), ledger=a, dmn=_dmn())
    dup.detect_duplicidade(_ordem_contas(ordem_pagamento_id="ORDEM-2"), ledger=b, dmn=_dmn())
    dup.detect_duplicidade(_ordem_contas(numero_guia_tiss="GUIA-2"), ledger=c, dmn=_dmn())
    assert a.registradas[0].chave_obrigacao == b.registradas[0].chave_obrigacao
    assert a.registradas[0].chave_obrigacao != c.registradas[0].chave_obrigacao


@pytest.mark.parametrize(
    ("soma_outras", "valor_origem", "esperado"),
    [(0, 60_000, "NAO_EXCEDE"), (10_000, 60_000, "NAO_EXCEDE"), (10_001, 60_000, "EXCEDE")],
)
def test_nivel2_soma_outras_mais_esta_contra_o_valor_da_origem(
    soma_outras: int, valor_origem: int, esperado: str
) -> None:
    dmn = _dmn()
    out = dup.detect_duplicidade(
        _ordem_contas(lastro_valor_origem_cents=valor_origem),
        ledger=_RazaoFalsa(_indicios(nivel2_soma_outras_cents=soma_outras)),
        dmn=dmn,
    )
    assert dmn.calls[0][1]["nivel2_situacao"] == esperado
    assert out["duplicidade_nivel2_situacao"] == esperado


@pytest.mark.parametrize(
    "campos",
    [
        {"lastro_status": "NAO_ENCONTRADO"},
        {"lastro_status": "INDISPONIVEL"},
        {"lastro_status": None},  # PR-D desligado: sem fato de lastro
        {"lastro_valor_origem_cents": None},
        {"lastro_valor_origem_cents": True},
    ],
)
def test_nivel2_sem_origem_conferida_nao_e_avaliavel(campos: dict[str, Any]) -> None:
    razao, dmn = _RazaoFalsa(_indicios(nivel2_soma_outras_cents=None)), _dmn()
    dup.detect_duplicidade(_ordem_contas(**campos), ledger=razao, dmn=dmn)
    assert dmn.calls[0][1]["nivel2_situacao"] == "NAO_AVALIAVEL"


def test_lastro_sem_origem_nao_manda_instancia_para_a_razao() -> None:
    razao = _RazaoFalsa()
    dup.detect_duplicidade(_ordem_contas(lastro_status="NAO_ENCONTRADO"), ledger=razao, dmn=_dmn())
    assert razao.registradas[0].origem_instancia_id is None


def test_competencia_ausente_vai_sem_competencia() -> None:
    razao = _RazaoFalsa()
    dup.detect_duplicidade(_ordem_contas(competencia=""), ledger=razao, dmn=_dmn())
    assert razao.registradas[0].competencia is None


def test_status_e_motivo_vem_da_dmn_e_a_evidencia_da_razao() -> None:
    out = dup.detect_duplicidade(
        _ordem_contas(),
        ledger=_RazaoFalsa(_indicios(nivel1_situacao=NIVEL1_LIBERADA_OUTRA_ORDEM, nivel1_ordem_id="ORDEM-0")),
        dmn=_dmn("CONFIRMADA", "DUP_OBRIGACAO_LIBERADA"),
    )
    assert out["duplicidade_status"] == "CONFIRMADA"
    assert out["duplicidade_motivo_codigo"] == "DUP_OBRIGACAO_LIBERADA"
    assert out["duplicidade_ordem_conflitante_id"] == "ORDEM-0"
    assert out["duplicidade_nivel1_situacao"] == NIVEL1_LIBERADA_OUTRA_ORDEM


def test_ordem_similar_do_nivel3_e_a_conflitante_quando_nao_ha_nivel1() -> None:
    out = dup.detect_duplicidade(
        _ordem_contas(),
        ledger=_RazaoFalsa(_indicios(nivel3_similar_idade_dias=3, nivel3_ordem_id="ORDEM-S")),
        dmn=_dmn("SUSPEITA", "DUP_SIMILAR_NA_JANELA"),
    )
    assert out["duplicidade_ordem_conflitante_id"] == "ORDEM-S"
    assert out["duplicidade_nivel3_similar_idade_dias"] == 3


# --- falha fechada: nunca NENHUMA sem prova ---------------------------------------------------


def test_razao_ausente_e_indeterminada_sem_avaliar_a_dmn() -> None:
    dmn = _dmn()
    out = dup.detect_duplicidade(_ordem_contas(), ledger=None, dmn=dmn)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "RAZAO_INDISPONIVEL"
    assert dmn.calls == []


@pytest.mark.parametrize("erro", [RazaoIndisponivelError("fora"), OSError("rede"), RuntimeError("x")])
def test_razao_fora_e_indeterminada(erro: Exception) -> None:
    dmn = _dmn()
    out = dup.detect_duplicidade(_ordem_contas(), ledger=_RazaoFalsa(erro=erro), dmn=dmn)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "RAZAO_INDISPONIVEL"
    assert dmn.calls == []


@pytest.mark.parametrize("erro", [TypeError("bug"), KeyError("bug"), AttributeError("bug")])
def test_erro_de_programacao_da_razao_sobe(erro: Exception) -> None:
    with pytest.raises(type(erro)):
        dup.detect_duplicidade(_ordem_contas(), ledger=_RazaoFalsa(erro=erro), dmn=_dmn())


def test_dmn_ausente_e_indeterminada_mas_a_linha_ja_foi_gravada() -> None:
    razao = _RazaoFalsa()
    out = dup.detect_duplicidade(_ordem_contas(), ledger=razao, dmn=None)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "DMN_INDISPONIVEL"
    assert len(razao.registradas) == 1


def test_dmn_fora_e_indeterminada() -> None:
    out = dup.detect_duplicidade(_ordem_contas(), ledger=_RazaoFalsa(), dmn=FakeDmnTransport())
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "DMN_INDISPONIVEL"
    assert issubclass(DmnEvaluationError, RuntimeError)


@pytest.mark.parametrize(
    "row",
    [
        {"duplicidade_status": "LIBERAR", "duplicidade_motivo_codigo": "X"},
        {"duplicidade_status": "NENHUMA", "duplicidade_motivo_codigo": ""},
        {},
    ],
)
def test_saida_da_dmn_fora_do_vocabulario_e_indeterminada(row: dict[str, Any]) -> None:
    dmn = FakeDmnTransport()
    dmn.register("pagto_duplicidade", [row])
    out = dup.detect_duplicidade(_ordem_contas(), ledger=_RazaoFalsa(), dmn=dmn)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "DMN_SAIDA_INVALIDA"


@pytest.mark.parametrize(
    "campos",
    [
        {"prestador_id": ""},
        {"ordem_pagamento_id": " "},
        {"valor_pagamento_cents": 0},
        {"valor_pagamento_cents": "500"},
        {"numero_lote_tiss": ""},  # CONTAS sem lote: sem identidade de obrigacao
        {"lastro_origem": "recurso_deferimento_humano", "glosa_id": ""},
        {"prestador_id": "prestador com espaco"},  # fora do formato da 0034
        {"competencia": "06/2026"},
    ],
)
def test_ordem_sem_identidade_e_indeterminada_e_nada_e_gravado(campos: dict[str, Any]) -> None:
    razao, dmn = _RazaoFalsa(), _dmn()
    out = dup.detect_duplicidade(_ordem_contas(**campos), ledger=razao, dmn=dmn)
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert out["duplicidade_motivo_codigo"] == "DADOS_INSUFICIENTES"
    assert razao.registradas == [] and dmn.calls == []


def test_razao_sem_tenant_e_indeterminada() -> None:
    razao = _RazaoFalsa(tenant="")
    out = dup.detect_duplicidade(_ordem_contas(), ledger=razao, dmn=_dmn())
    assert out["duplicidade_status"] == "INDETERMINADA"
    assert razao.registradas == []


# --- o handler composto (validacao de sempre + fatos) -----------------------------------------


def test_validacao_que_recusa_sai_antes_de_qualquer_escrita() -> None:
    razao = _RazaoFalsa()
    with pytest.raises(Exception, match="ERR_PAGTO_ORDEM_INVALIDA|ordem_pagamento_id"):
        dup.validar_com_duplicidade(
            {"ordem_pagamento_id": ""}, validar=pagto.validate_pagto, ledger=razao, dmn=_dmn()
        )
    assert razao.registradas == []


def test_handler_composto_nao_sobrescreve_a_validacao_nem_muda_duplicidade_suspeita() -> None:
    def _validar(variables: dict[str, Any]) -> dict[str, Any]:
        return {"dados_pagamento_validos": True, "duplicidade_suspeita": False, "lastro_status": "CONFIRMADO"}

    out = dup.validar_com_duplicidade(
        _ordem_contas(lastro_status=None),
        validar=_validar,
        ledger=_RazaoFalsa(),
        dmn=_dmn("CONFIRMADA", "DUP_OBRIGACAO_EM_CURSO"),
    )
    # Rota inalterada: o booleano que a DMN viva le segue o da validacao.
    assert out["duplicidade_suspeita"] is False
    assert out["duplicidade_status"] == "CONFIRMADA"
    assert out["lastro_status"] == "CONFIRMADO"


def test_handler_composto_usa_os_fatos_do_lastro_produzidos_no_mesmo_despacho() -> None:
    razao, dmn = _RazaoFalsa(), _dmn()

    def _validar_com_lastro(variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "lastro_status": "CONFIRMADO",
            "lastro_origem_instancia_id": "pi-do-despacho",
            "lastro_valor_origem_cents": 50_000,
        }

    semente = _ordem_contas(
        lastro_status=None, lastro_origem_instancia_id=None, lastro_valor_origem_cents=None
    )
    dup.validar_com_duplicidade(semente, validar=_validar_com_lastro, ledger=razao, dmn=dmn)
    assert razao.registradas[0].origem_instancia_id == "pi-do-despacho"
    assert dmn.calls[0][1]["nivel2_situacao"] == "NAO_EXCEDE"


# --- envoltorio da liberacao: o guard decide, a razao so' registra ----------------------------


def _baixo_valor(**campos: Any) -> dict[str, Any]:
    base = _ordem_contas(faixa_valor="DENTRO_TETO_L2")
    base.update(campos)
    return base


def _alto_valor(**campos: Any) -> dict[str, Any]:
    base = _ordem_contas(
        decisao_pagamento="APROVAR",
        aprovador_id="aprovador-1",
        aprovador_tier=4,
        justificativa_aprovacao="ok",
        valor_aprovado_cents=50_000,
        valor_maximo_aprovavel_cents=50_000,
        tier_minimo=2,
        faixa_valor="ALCADA_L2",
    )
    base.update(campos)
    return base


def test_liberacao_de_baixo_valor_grava_liberada_e_devolve_o_retorno_do_guard() -> None:
    razao = _RazaoFalsa()
    out = dup.registrar_liberacao(_baixo_valor(), liberar=execute_pagto, ledger=razao)
    assert out == execute_pagto(_baixo_valor())
    (entrada,) = razao.liberadas
    assert entrada.evento == EVENTO_LIBERADA
    assert entrada.chave_obrigacao == chave_obrigacao(_TENANT, "contas", ("LOTE-1", "prov:1", "GUIA-1"))


def test_liberacao_de_alto_valor_grava_liberada() -> None:
    razao = _RazaoFalsa()
    out = dup.registrar_liberacao(_alto_valor(), liberar=release_high_value_payment, ledger=razao)
    assert out["pagamento_liberado"] is True
    assert len(razao.liberadas) == 1


def test_faixa_incompativel_nao_liberou_e_nao_grava() -> None:
    razao = _RazaoFalsa()
    out = dup.registrar_liberacao(_baixo_valor(faixa_valor="ALCADA_L1"), liberar=execute_pagto, ledger=razao)
    assert out == {"pagamento_executado": False}
    assert razao.liberadas == []


@pytest.mark.parametrize(
    "campos",
    [
        {"decisao_pagamento": "RECUSAR"},  # sem decisao humana de aprovar
        {"aprovador_tier": 1},  # alcada: tier abaixo do exigido pela DMN
        {"valor_aprovado_cents": 50_001},  # acima do teto da DMN
        {"aprovador_id": "decisor-x", "lastro_decisor_id": "decisor-x"},  # SoD
    ],
)
def test_guarda_de_alcada_recusa_e_nada_e_gravado(campos: dict[str, Any]) -> None:
    """Teste NEGATIVO da guarda de alcada/SoD: o envoltorio nao abre caminho de efeito novo."""
    razao = _RazaoFalsa()
    with pytest.raises(PagtoError):
        dup.registrar_liberacao(_alto_valor(**campos), liberar=release_high_value_payment, ledger=razao)
    assert razao.liberadas == []


def test_sod_no_baixo_valor_recusa_e_nada_e_gravado() -> None:
    razao = _RazaoFalsa()
    with pytest.raises(PagtoError):
        dup.registrar_liberacao(
            _baixo_valor(analista_admissibilidade_id="p-1", lastro_decisor_id="p-1"),
            liberar=execute_pagto,
            ledger=razao,
        )
    assert razao.liberadas == []


@pytest.mark.parametrize("razao", [None, _RazaoFalsa(erro=RazaoIndisponivelError("fora"))])
def test_razao_fora_nao_desfaz_nem_bloqueia_a_liberacao_ja_decidida(razao: Any) -> None:
    out = dup.registrar_liberacao(_baixo_valor(), liberar=execute_pagto, ledger=razao)
    assert out == {"pagamento_executado": True, "tipo_liberacao": "auto_L2"}


def test_erro_de_programacao_na_liberacao_sobe() -> None:
    with pytest.raises(TypeError):
        dup.registrar_liberacao(
            _baixo_valor(), liberar=execute_pagto, ledger=_RazaoFalsa(erro=TypeError("bug"))
        )

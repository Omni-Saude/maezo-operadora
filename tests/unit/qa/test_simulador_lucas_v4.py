"""Roteiros de aceite do Lucas v4 (DL-0114) com o criterio "RESOLVEU?" e a garantia de que NENHUM codigo de
barras chega ao relatorio.

Tres roteiros, porque o beneficiario muda: `lucas_v4` (o dono, plano empresarial: o texto do RH, sem valor),
`lucas_v4_pagador` (titular PF que paga: total primeiro, detalhe por beneficiario e 2a via com codigo) e
`lucas_v4_nao_pagador` (PF que nao e' o pagador: so' o responsavel financeiro pede). Os passos que
dependem dos contratos novos da AMH saem `pendente_de_contrato` enquanto o sinal estiver desligado.

Os textos usados para provar os passos sao os REAIS do codigo do Lucas, nao copias escritas a mao.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.fonte_v4 import MARCADOR_CODIGO, FatosV4
from maezo.agents.lucas.graph import (
    SEGUNDA_VIA_CANAIS,
    TEXTO_EMPRESA_SEGUNDA_VIA,
    TEXTO_EMPRESA_VENCIMENTO,
    TEXTO_NAO_PAGADOR_V4,
    TEXTO_PAGADOR_EMPRESA,
    TEXTO_PAGADOR_NAO_CONFIRMADO_V4,
    TEXTO_SEM_CODIGO_V4,
    TEXTOS_NAO_PAGADOR_V4_CURTOS,
    texto_segunda_via_com_codigo_v4,
)
from maezo.qa.simulador import aceite
from maezo.qa.simulador import composicao as comp
from maezo.qa.simulador.mascaramento import TOKEN_CODIGO_DE_BARRAS, Mascarador
from maezo.qa.simulador.registro import Turno

from .apoio_simulador import CPF_SINTETICO, sujeito, turno

LINHA = "00191.23454 67890.123457 67890.123457 7 10000000012345"
CODIGO = "00197100000000123451234567890123456789012345"
_HOJE = date(2026, 10, 13)

_TEXTO_EMPRESA_PAGAMENTO = (
    f"{TEXTO_PAGADOR_EMPRESA} Por isso, a situação do pagamento fica com a empresa (normalmente o RH). "
    "Se quiser, posso te passar para alguém da nossa equipe."
)
_COMPETENCIA = {
    "competencia": "2026-10",
    "situacao": "em_aberto",
    "vencimento": "2026-10-20",
    "valor_total": "320.25",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "320.25",
    "valor_boleto_pagador": "1280.40",
}
_FATOS_BILLING = {"cnab_ref": "amh-billing:2026-10-12", "competencias": [_COMPETENCIA]}
_TOTAL_PRIMEIRO = (
    "O valor total da mensalidade de outubro de 2026 é de R$ 1.280,40, com vencimento em 20/10. "
    "Esse total soma todos os beneficiários do contrato. A sua parte é de R$ 320,25. (dados de 12/10)"
)
_DETALHE = {
    "competencia": "2026-10",
    "total_mensalidade": "720.00",
    "fecha": True,
    "beneficiarios": [
        {
            "rotulo": "você",
            "valor_pre_estabelecido": "400.00",
            "valor_coparticipacao": "20.00",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "420.00",
        },
        {
            "rotulo": "dependente 1",
            "valor_pre_estabelecido": "300.00",
            "valor_coparticipacao": "0.00",
            "valor_adicionais": "0.00",
            "valor_outros": "0.00",
            "valor_total": "300.00",
        },
    ],
}
_TEXTO_DETALHADO = "\n".join(
    [
        "O valor total da mensalidade de outubro de 2026 é de R$ 720,00, com vencimento em 20/10. "
        "Esse total soma todos os beneficiários do contrato.",
        "Detalhe por beneficiário:",
        "• Você: pré-estabelecido R$ 400,00 + coparticipação R$ 20,00 = R$ 420,00",
        "• Dependente 1: pré-estabelecido R$ 300,00 + coparticipação R$ 0,00 = R$ 300,00",
        "(dados de 12/10)",
    ]
)


def _texto_com_codigo(codigo: str = LINHA) -> str:
    boleto = {
        "competencia": "2026-10",
        "vencimento": "2026-10-20",
        "situacao": "em_aberto",
        "valor": "1280.40",
        "outros": 0,
    }
    return texto_segunda_via_com_codigo_v4(boleto, _HOJE).replace(MARCADOR_CODIGO, codigo)


_FATOS_2A_VIA = FatosV4(
    responsavel_financeiro="voce",
    segunda_via_estado="com_codigo",
    segunda_via_boletos=(),
).como_entrada_lucas() | {
    "segunda_via_boletos": [
        {
            "competencia": "2026-10",
            "vencimento": "2026-10-20",
            "situacao": "em_aberto",
            "valor_titulo": "1280.40",
            "outros": 0,
        }
    ]
}


def _passo(segmento: aceite.Segmento, indice: int) -> aceite.Passo:
    return segmento.passos[indice]


def _falhas(
    segmento: aceite.Segmento,
    indice: int,
    texto: str,
    *,
    fatos: list[dict[str, Any]] | None = None,
    anterior: str | None = None,
) -> list[str]:
    """Falhas do passo com o `texto` do Lucas: os padroes do passo E o rigor v4."""
    passo = _passo(segmento, indice)
    t = turno(1, lucas=[texto], fatos=fatos or [])
    resposta = [("lucas", texto)]
    return aceite.avaliar_passo(passo, resposta, [], anterior) + (
        aceite.avaliar_rigor_v4(passo, t, sujeito()) if segmento.rigor_v4 else []
    )


# --- 1. os roteiros existem, compilam e declaram o criterio -------------------------------------------
def test_os_tres_roteiros_existem_compilam_e_o_dono_v1_e_o_cancelamento_nao_mudaram() -> None:
    assert {"lucas_v4", "lucas_v4_pagador", "lucas_v4_nao_pagador"} <= set(aceite.ROTEIROS)
    for nome in ("lucas_v4", "lucas_v4_pagador", "lucas_v4_nao_pagador"):
        aceite.validar(aceite.ROTEIROS[nome])
        assert all(s.resolveu and s.rigor_v4 for s in aceite.ROTEIROS[nome]), nome
        assert all(p.agente == "lucas" for s in aceite.ROTEIROS[nome] for p in s.passos), nome
    # o dono (plano empresarial) e' o sujeito de `lucas_v4`; os PF usam o titular do roster PF
    assert "lucas_v4" not in aceite.SUJEITO_DO_ROTEIRO and "dono_v1" not in aceite.SUJEITO_DO_ROTEIRO
    assert aceite.SUJEITO_DO_ROTEIRO["lucas_v4_pagador"] == aceite.SUJEITO_PF_TITULAR
    assert aceite.SUJEITO_DO_ROTEIRO["lucas_v4_nao_pagador"] == aceite.SUJEITO_PF_TITULAR
    assert aceite.SUJEITO_DO_ROTEIRO["cancelamento_v1"] == aceite.SUJEITO_PF_TITULAR
    # nenhum segmento antigo ganhou rigor: so' os roteiros novos
    assert not any(s.rigor_v4 for s in (*aceite.DONO_V1, *aceite.TRIAGEM_V2, *aceite.CANCELAMENTO_V1))
    assert [len(s.passos) for s in aceite.DONO_V1] == [18, 3, 3, 1, 6, 4, 2]


def test_o_roteiro_do_dono_cobre_a_mensalidade_a_2a_via_e_o_vencimento_do_plano_empresarial() -> None:
    (segmento,) = aceite.LUCAS_V4
    assert [p.texto for p in segmento.passos] == [
        "Quanto está minha mensalidade?",
        "Qual o código de barras do meu boleto?",
        "Quando vence a minha mensalidade?",
    ]
    assert "RH" in segmento.resolveu and "empresa contratante" in segmento.resolveu


# --- 2. os textos REAIS do Lucas passam em cada roteiro ----------------------------------------------
@pytest.mark.parametrize(
    ("indice", "texto"),
    [
        (0, _TEXTO_EMPRESA_PAGAMENTO),
        (1, TEXTO_EMPRESA_SEGUNDA_VIA),
        (2, TEXTO_EMPRESA_VENCIMENTO),
    ],
)
def test_empresarial_o_texto_do_rh_sem_valor_passa(indice: int, texto: str) -> None:
    assert _falhas(aceite.LUCAS_V4[0], indice, texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Sua mensalidade de outubro de 2026 é de R$ 320,25, com vencimento em 20/10.",  # mostrou valor
        TEXTO_NAO_PAGADOR_V4,
    ],
)
def test_empresarial_reprova_valor_ou_o_texto_do_nao_pagador(texto: str) -> None:
    assert _falhas(aceite.LUCAS_V4[0], 0, texto, fatos=[_FATOS_BILLING]) != []


def test_pagador_sem_o_sinal_o_total_vem_primeiro_e_a_2a_via_e_a_de_antes() -> None:
    pagador = aceite.LUCAS_V4_PAGADOR
    assert _falhas(pagador[0], 0, _TOTAL_PRIMEIRO, fatos=[_FATOS_BILLING]) == []
    assert _falhas(pagador[1], 0, SEGUNDA_VIA_CANAIS) == []


def test_a_primeira_frase_e_a_do_lucas_e_nao_a_abertura_da_helena() -> None:
    """Aceite de 16/10/2026: a Helena abre o turno com a frase de passagem;
    a primeira frase conferida e' a da primeira resposta DO LUCAS (o `agente` do passo)."""
    passo = _passo(aceite.LUCAS_V4_PAGADOR[0], 0)
    abertura = ("helena", "Vou te passar para o Lucas, nosso assistente financeiro.")
    assert aceite.avaliar_passo(passo, [abertura, ("lucas", _TOTAL_PRIMEIRO)], [], None) == []
    antigo = "Sua mensalidade de outubro de 2026 é de R$ 751,41, com vencimento em 15/10. (dados de 08/10)"
    falhas = aceite.avaliar_passo(passo, [abertura, ("lucas", antigo)], [], None)
    assert any(f.startswith("primeira_frase_deve") for f in falhas)


def test_sem_o_sinal_do_pagador_o_roteiro_do_nao_pagador_reprova_em_vez_de_passar_calado() -> None:
    (segmento,) = aceite.LUCAS_V4_NAO_PAGADOR
    assert _falhas(segmento, 0, TEXTO_PAGADOR_NAO_CONFIRMADO_V4) != []


def test_pagador_com_o_sinal_o_detalhe_por_beneficiario_e_o_codigo_passam() -> None:
    pagador = aceite.LUCAS_V4_PAGADOR
    fatos = [_FATOS_BILLING, FatosV4().como_entrada_lucas() | {"detalhamento_pagador": [_DETALHE]}]
    assert _falhas(pagador[0], 1, _TEXTO_DETALHADO, fatos=fatos) == []
    assert _falhas(pagador[1], 1, _texto_com_codigo(), fatos=[_FATOS_BILLING, _FATOS_2A_VIA]) == []


def test_com_o_sinal_o_pagador_exige_o_detalhe_por_beneficiario_sem_nome() -> None:
    """Com os contratos novos ligados nao basta "o detalhe sai pelo aplicativo": reprova sem o detalhe."""
    pagador = aceite.LUCAS_V4_PAGADOR
    fatos = [_FATOS_BILLING, FatosV4().como_entrada_lucas() | {"detalhamento_pagador": [_DETALHE]}]
    sem_detalhe = (
        "O valor total da mensalidade de outubro de 2026 é de R$ 720,00, com vencimento em 20/10. "
        "Esse total soma todos os beneficiários do contrato. "
        "O detalhe por beneficiário sai pelo aplicativo Austa Clínicas. (dados de 12/10)"
    )
    assert _falhas(pagador[0], 1, sem_detalhe, fatos=fatos) != []
    assert _falhas(pagador[0], 1, _TOTAL_PRIMEIRO, fatos=[_FATOS_BILLING]) != []
    # o detalhe com NOME no lugar do rotulo tambem reprova (so' voce/titular/dependente N)
    com_nome = _TEXTO_DETALHADO.replace("• Dependente 1:", "• Joao:")
    assert _falhas(pagador[0], 1, com_nome, fatos=fatos) != []
    assert _falhas(pagador[0], 1, _TEXTO_DETALHADO, fatos=fatos) == []


def test_com_o_sinal_a_2a_via_do_pagador_exige_o_codigo_no_texto_real_e_nao_aceita_o_texto_de_canais() -> (
    None
):
    pagador = aceite.LUCAS_V4_PAGADOR
    fatos = [_FATOS_BILLING, _FATOS_2A_VIA]
    assert _falhas(pagador[1], 1, SEGUNDA_VIA_CANAIS, fatos=fatos) != []
    sem_codigo = _texto_com_codigo().replace(LINHA, "")
    assert _falhas(pagador[1], 1, sem_codigo, fatos=fatos) != []
    assert _falhas(pagador[1], 1, _texto_com_codigo(), fatos=fatos) == []


def test_o_nao_pagador_nao_recebe_valor_nem_codigo_nem_com_o_sinal() -> None:
    (segmento,) = aceite.LUCAS_V4_NAO_PAGADOR
    assert _falhas(segmento, 1, _texto_com_codigo(), fatos=[_FATOS_BILLING, _FATOS_2A_VIA]) != []
    assert _falhas(segmento, 0, _TEXTO_DETALHADO) != []
    for i in range(len(segmento.passos)):
        assert _falhas(segmento, i, TEXTO_NAO_PAGADOR_V4) == []


def test_nao_pagador_so_ouve_o_responsavel_financeiro_em_toda_pergunta() -> None:
    (segmento,) = aceite.LUCAS_V4_NAO_PAGADOR
    # FASE 4 (18/10/2026): o texto inteiro na primeira resposta; nas seguintes, as curtas que se revezam.
    for i in range(len(segmento.passos)):
        textos = (TEXTO_NAO_PAGADOR_V4, *TEXTOS_NAO_PAGADOR_V4_CURTOS) if i else (TEXTO_NAO_PAGADOR_V4,)
        for texto in textos:
            assert _falhas(segmento, i, texto) == [], (i, texto)
    # com valor, ou o texto de empresa, o roteiro reprova
    assert _falhas(segmento, 0, _TOTAL_PRIMEIRO, fatos=[_FATOS_BILLING]) != []
    assert _falhas(segmento, 0, _TEXTO_EMPRESA_PAGAMENTO) != []


# --- 3. o SINAL: passos pendentes de contrato ----------------------------------------------------------
async def test_sem_o_sinal_o_passo_do_codigo_sai_pendente_e_nao_e_enviado() -> None:
    segmento = aceite.LUCAS_V4_PAGADOR[1]
    enviados: list[str] = []

    async def enviar(texto: str) -> Turno:
        enviados.append(texto)
        return turno(len(enviados), lucas=[SEGUNDA_VIA_CANAIS])

    resultados = await aceite.executar_passos(segmento, enviar, sujeito=sujeito(), sinal_v4=False)
    assert len(enviados) == 1  # so' o passo `sem_sinal`; o do codigo NAO foi enviado
    sem, com = resultados
    assert sem.passou and not sem.pendente_de_contrato and (sem.indice, com.indice) == (1, 2)
    assert com.pendente_de_contrato and com.passou and com.respostas == [] and com.falhas == []


async def test_com_o_sinal_roda_o_do_codigo_e_omite_o_texto_de_antes() -> None:
    segmento = aceite.LUCAS_V4_PAGADOR[1]
    enviados: list[str] = []

    async def enviar(texto: str) -> Turno:
        enviados.append(texto)
        return turno(1, lucas=[_texto_com_codigo()], fatos=[_FATOS_BILLING, _FATOS_2A_VIA])

    (resultado,) = await aceite.executar_passos(segmento, enviar, sujeito=sujeito(), sinal_v4=True)
    assert len(enviados) == 1 and resultado.indice == 1
    assert resultado.passou and not resultado.pendente_de_contrato, resultado.falhas


def test_condicao_desconhecida_levanta() -> None:
    passo = aceite.Passo("x", condicao="as_vezes")
    with pytest.raises(ValueError, match="condicao"):
        aceite._condicao_atendida(passo, True)


def test_o_relatorio_separa_aprovados_e_pendentes_sem_mudar_os_roteiros_antigos() -> None:
    ok = aceite.ResultadoDoPasso("1", 1, "a", [("lucas", "ok")])
    pendente = aceite.ResultadoDoPasso("1", 2, "b", pendente_de_contrato=True)
    dados = aceite.como_json([aceite.ResultadoDoSegmento("1", "r1", [ok, pendente])], sujeito())
    assert dados["roteiro_passou"] is True and (dados["passos"], dados["passos_aprovados"]) == (1, 1)
    assert dados["passos_pendentes_de_contrato"] == 1
    assert dados["segmentos"][0]["passos"][1]["pendente_de_contrato"] is True
    md = aceite.secao_do_relatorio(dados, "lucas_v4_pagador")
    assert "pendente_de_contrato" in md and "1 pendentes dos contratos novos da AMH" in md
    antigo = aceite.como_json([aceite.ResultadoDoSegmento("1", "r1", [ok])], sujeito())
    assert "passos_pendentes_de_contrato" not in antigo
    assert "pendente_de_contrato" not in antigo["segmentos"][0]["passos"][0]


# --- 4. o rigor v4: nome, palavra proibida, D2 e codigo ------------------------------------------------
def _rigor(passo: aceite.Passo, *textos: str, fatos: list[dict[str, Any]] | None = None) -> list[str]:
    return aceite.avaliar_rigor_v4(passo, turno(1, lucas=list(textos), fatos=fatos or []), sujeito())


def test_rigor_barra_nome_palavra_proibida_valor_e_data_fora_dos_fatos() -> None:
    passo = aceite.Passo("x")
    assert any("nome" in f for f in _rigor(passo, "O responsável é Joana Pereira."))
    assert any("proibida" in f for f in _rigor(passo, "Há multa sobre esse valor."))
    assert any("D2" in f and "valor" in f for f in _rigor(passo, "A mensalidade é de R$ 999,00."))
    assert any("D2" in f and "data" in f for f in _rigor(passo, "Vence em 31/12.", fatos=[_FATOS_BILLING]))
    assert _rigor(passo, "A parcela é de R$ 320,25, vence em 20/10.", fatos=[_FATOS_BILLING]) == []


def test_rigor_so_olha_os_textos_do_lucas() -> None:
    passo = aceite.Passo("x")
    t = turno(1, helena=["Olá, Joana Pereira!"], lucas=["Tudo certo."])
    assert aceite.avaliar_rigor_v4(passo, t, sujeito()) == []


@pytest.mark.parametrize("codigo", [LINHA, CODIGO, "0019123454678901234567890123456789012345678901"])
def test_rigor_barra_codigo_de_barras_onde_nenhum_era_esperado(codigo: str) -> None:
    falhas = _rigor(aceite.Passo("x"), f"Seu código: {codigo}")
    assert any("codigo de barras" in f and "nenhum era esperado" in f for f in falhas)
    assert all(codigo not in f and LINHA not in f for f in falhas)  # a falha nunca ecoa o codigo


def test_rigor_exige_o_codigo_onde_ele_e_esperado_e_diz_quando_a_resposta_foi_nao_encontrei() -> None:
    passo = aceite.Passo("x", espera_codigo=True)
    assert any("nao foi entregue" in f for f in _rigor(passo, "Algo sem codigo."))
    sem = _rigor(passo, TEXTO_SEM_CODIGO_V4)
    assert any("nao encontrei" in f and "escolha outro" in f for f in sem)
    assert not any("nao foi entregue" in f for f in _rigor(passo, f"Código:\n{LINHA}"))
    assert not any("nao foi entregue" in f for f in _rigor(passo, f"Código: {CODIGO}"))


def test_rigor_reprova_o_marcador_cru() -> None:
    falhas = _rigor(aceite.Passo("x"), f"Código:\n{MARCADOR_CODIGO}")
    assert any("marcador" in f for f in falhas)


# --- 5. o relatorio NUNCA leva o codigo --------------------------------------------------------------
@pytest.mark.parametrize(
    "texto",
    [
        f"Código: {LINHA}.",
        f"Código: {CODIGO}.",
        f"Código:\n{LINHA}\nFim",
        "Código: 00191 23454 67890 123457 67890 123457 7 10000000012345",
        "Código: 0019123454678901234567890123456789012345678901",
        f"CPF {CPF_SINTETICO} e código {LINHA}",
    ],
)
def test_o_mascarador_troca_qualquer_forma_do_codigo_e_nao_deixa_digitos_longos(texto: str) -> None:
    mascarado = Mascarador(sujeito()).texto(texto)
    assert TOKEN_CODIGO_DE_BARRAS in mascarado
    assert not re.search(r"\d{12,}", mascarado) and not aceite.credencial_no_relatorio(mascarado)
    assert CPF_SINTETICO not in mascarado


def test_o_mascarador_continua_deixando_valor_data_e_competencia() -> None:
    texto = "A mensalidade de outubro de 2026 (R$ 1.280,40, vence em 20/10/2026) está em aberto."
    assert Mascarador(sujeito()).texto(texto) == texto
    assert not aceite.credencial_no_relatorio(texto)


def test_o_relatorio_do_roteiro_nao_tem_o_codigo_e_o_cinto_barra_o_que_escapar() -> None:
    passo = aceite.ResultadoDoPasso("1", 1, "Qual o código?", [("lucas", _texto_com_codigo())], [])
    dados = aceite.como_json([aceite.ResultadoDoSegmento("1", "r1", [passo])], sujeito())
    serializado = json.dumps(dados, ensure_ascii=False)
    assert (
        LINHA not in serializado
        and "10000000012345" not in serializado
        and TOKEN_CODIGO_DE_BARRAS in serializado
    )
    md = aceite.secao_do_relatorio(dados, "lucas_v4_pagador")
    assert LINHA not in md and not aceite.credencial_no_relatorio(md)
    # o que o mascarador nao toca (uma falha escrita pelo roteiro) e' barrado pelo cinto: nada e' gravado
    vazou = aceite.ResultadoDoPasso("1", 1, "x", [], [], [f"falha {LINHA}"])
    with pytest.raises(RuntimeError, match="escapou do mascaramento"):
        aceite.como_json([aceite.ResultadoDoSegmento("1", "r1", [vazou])], sujeito())


# --- 6. o gabarito do D2 inclui os fatos v4 (e nunca a credencial) -----------------------------------
class _FonteV4Fake:
    extra = "delegado"

    def __init__(self, fatos: FatosV4 | None) -> None:
        self.fatos = fatos

    async def detalhamento(self, *args: Any, **kwargs: Any) -> FatosV4 | None:
        return self.fatos

    async def segunda_via(self, *args: Any, **kwargs: Any) -> tuple[FatosV4 | None, str]:
        return self.fatos, "credencial-opaca"


async def test_fonte_v4_gravada_guarda_os_fatos_do_turno_e_devolve_o_mesmo_objeto() -> None:
    from maezo.agents.lucas.fonte_v4 import BoletoSegundaVia

    boleto = BoletoSegundaVia(
        competencia="2026-10",
        vencimento="2026-10-20",
        situacao="em_aberto",
        valor_titulo="1280.40",
        disponivel=True,
        indisponivel_motivo=None,
        linha_digitavel=LINHA,
        codigo_barras=CODIGO,
    )
    fatos = FatosV4(segunda_via_estado="com_codigo", segunda_via_boletos=(boleto,))
    fonte = comp.FonteV4Gravada(_FonteV4Fake(fatos))
    contexto = comp.ContextoDoTurno(turno=turno(1), message_id="m", conversation_id="c")
    with comp.no_turno(contexto):
        assert await fonte.segunda_via("p", None, request_ref="x") == (fatos, "credencial-opaca")
    (gravado,) = contexto.turno.fatos
    assert gravado["segunda_via_boletos"][0]["valor_titulo"] == "1280.40"
    assert LINHA not in json.dumps(gravado) and CODIGO not in json.dumps(gravado)
    assert fonte.extra == "delegado"
    # o detalhamento tambem grava o que entrega ao Lucas; sem fato (ou sem turno em curso) nada e' gravado
    so_a_relacao = FatosV4(responsavel_financeiro="voce")
    with comp.no_turno(contexto):
        assert await comp.FonteV4Gravada(_FonteV4Fake(so_a_relacao)).detalhamento("p", None) is so_a_relacao
        assert await comp.FonteV4Gravada(_FonteV4Fake(None)).detalhamento("p", None) is None
    assert contexto.turno.fatos[1] == {"responsavel_financeiro": "voce"} and len(contexto.turno.fatos) == 2
    assert await fonte.segunda_via("p", None, request_ref="x") == (fatos, "credencial-opaca")
    assert len(contexto.turno.fatos) == 2


def test_o_gabarito_do_d2_reconhece_os_valores_novos() -> None:
    from maezo.qa.simulador.checagens import quantias_no_texto, quantias_permitidas

    permitidas = quantias_permitidas([_FATOS_BILLING, {"detalhamento_pagador": [_DETALHE]}, _FATOS_2A_VIA])
    for texto in (_TOTAL_PRIMEIRO, _TEXTO_DETALHADO, _texto_com_codigo()):
        assert all(q in permitidas for q in quantias_no_texto(texto)), texto

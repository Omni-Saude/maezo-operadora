"""EP-REG-27b + RT-05: pedido de cancelamento pela pagina (RN 561), separado do arrependimento."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents.lucas.cancelamento_pf import protocolo_do_cancelamento, protocolo_valido
from maezo.platform.saida_a_pedido.cancelamento import (
    Botao,
    Elemento,
    Passo,
    PedidoCancelamentoRecusado,
    PerfilContratual,
    TelaPedidoCancelamento,
    entrada_do_cancelamento,
    montar_tela_pedido,
    receber_pedido_cancelamento,
    rotulo_do_estado,
    submissao_de_payload,
    verificar_lista_de_guarda,
)
from maezo.platform.saida_a_pedido.janela import JanelaAvaliada
from maezo.platform.saida_a_pedido.politica import (
    PoliticaInvalida,
    carregar_politica_cancelamento,
)
from maezo.tools.workers.base import pick_fields
from maezo.tools.workers.cancel import CancelInput, assess_admissibility, validate_cancel

RAIZ = Path(__file__).resolve().parents[3]
CANCELAMENTO_YAML = RAIZ / "spec" / "policies" / "saida-a-pedido" / "pedido-cancelamento.yaml"
AGORA = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
INCISOS = ["I", "II", "III", "IV", "V", "VI"]
PRIMARIO = Botao(primario=True, visivel=True, habilitado=True, atras_de_mais_opcoes=False)


def _carregar() -> dict[str, Any]:
    dados = yaml.safe_load(CANCELAMENTO_YAML.read_text(encoding="utf-8"))
    assert isinstance(dados, dict)
    return dados


def _gravar(tmp_path: Path, dados: dict[str, Any], nome: str = "p.yaml") -> Path:
    destino = tmp_path / nome
    destino.write_text(yaml.safe_dump(dados, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return destino


def _submissao(**mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pedido_ref": "pedido-1",
        "contrato_ref": "contrato-opaco-1",
        "bloco_art15_incisos_apresentados": list(INCISOS),
        "bloco_art15_versao_politica": 1,
        "toques": ["toque_1_pedido", "toque_2_confirmacao"],
    }
    base.update(mudancas)
    return base


def _admitir(**mudancas: Any) -> Any:
    politica = carregar_politica_cancelamento()
    return receber_pedido_cancelamento(
        submissao_de_payload(_submissao(**mudancas)), politica, recebido_em=AGORA, tenant_id="t-1"
    )


def _tela_entregue() -> tuple[Any, TelaPedidoCancelamento]:
    politica = carregar_politica_cancelamento()
    return politica, montar_tela_pedido(politica, janela=None)


def _com_passo_extra(tela: TelaPedidoCancelamento, *elementos: Elemento) -> TelaPedidoCancelamento:
    toque_1, toque_2 = tela.passos_ate_protocolo
    return replace(
        tela,
        passos_ate_protocolo=(replace(toque_1, elementos=(*toque_1.elementos, *elementos)), toque_2),
    )


def test_cancel_request_requires_art15_block() -> None:
    sem_iii = [i for i in INCISOS if i != "III"]
    with pytest.raises(PedidoCancelamentoRecusado) as exc:
        _admitir(bloco_art15_incisos_apresentados=sem_iii)
    assert exc.value.code == "terms_incomplete"
    with pytest.raises(PedidoCancelamentoRecusado) as exc2:
        _admitir(bloco_art15_versao_politica=99)
    assert exc2.value.code == "terms_incomplete"
    with pytest.raises(PedidoCancelamentoRecusado) as exc3:
        _admitir(toques=["toque_1_pedido"])
    assert exc3.value.code == "terms_incomplete"
    politica, tela = _tela_entregue()
    toque_1 = replace(
        tela.passos_ate_protocolo[0],
        elementos=tuple(e for e in tela.passos_ate_protocolo[0].elementos if e.tipo != "bloco_art15"),
    )
    sem_bloco = replace(tela, passos_ate_protocolo=(toque_1, tela.passos_ate_protocolo[1]))
    assert "ART15_BLOCK_MISSING" in verificar_lista_de_guarda(sem_bloco, politica)


def test_cancel_and_withdrawal_are_distinct_flows() -> None:
    with pytest.raises(PedidoCancelamentoRecusado) as exc:
        submissao_de_payload(_submissao(declaracao={"tipo_do_ato": "declaracao_art8_par1", "ato_ref": "m"}))
    assert exc.value.code == "unknown_field"
    politica, tela = _tela_entregue()
    fundida = _com_passo_extra(tela, Elemento(tipo="aviso_arrependimento", acao="submeter_pedido"))
    assert "WITHDRAWAL_FLOW_FUSED" in verificar_lista_de_guarda(fundida, politica)
    admitido = _admitir()
    variaveis = admitido.variaveis_de_inicio_cancel_001
    assert variaveis["tipo_solicitacao"] == "pedido_beneficiario"
    assert not any("ARR-" in str(v) for v in variaveis.values())
    assert type(admitido).__name__ == "PedidoCancelamentoAdmitido"
    janela = JanelaAvaliada(
        situacao="DENTRO",
        variante="UNRATIFIED_UNION",
        termos_finais={"signed_at": AGORA},
        regimes_abertos=frozenset({"CDC-art49"}),
        encaminhamento="route_to_human",
    )
    com_aviso = montar_tela_pedido(politica, janela=janela)
    avisos = [
        e for p in com_aviso.passos_ate_protocolo for e in p.elementos if e.tipo == "aviso_arrependimento"
    ]
    assert len(avisos) == 1
    assert avisos[0].acao == "abrir_formulario_arrependimento"
    assert avisos[0].botao is None or not avisos[0].botao.primario
    assert verificar_lista_de_guarda(com_aviso, politica) == ()


def test_os08_alternative_line_absent_in_executable_variant(tmp_path: Path) -> None:
    dados = _carregar()
    dados["linha_alternativa_os08"]["exibir"] = True
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_cancelamento(_gravar(tmp_path, dados))
    assert exc.value.code == "OS08_LINE_PENDING_RT_J3A"
    politica, tela = _tela_entregue()
    com_linha = _com_passo_extra(tela, Elemento(tipo="linha_alternativa"))
    assert "OS08_LINE_NOT_ADMITTED" in verificar_lista_de_guarda(com_linha, politica)
    tipos = {e.tipo for p in tela.passos_ate_protocolo + tela.passos_pos_protocolo for e in p.elementos}
    assert "linha_alternativa" not in tipos
    textos = " ".join(politica.textos.values()).lower()
    assert "mudar de plano" not in textos
    assert "retirar um dependente" not in textos


def _caso(nome: str, alterar: Any, codigo: str) -> Any:
    return pytest.param(alterar, codigo, id=nome)


def _toque_1_com(tela: TelaPedidoCancelamento, **botao: bool) -> TelaPedidoCancelamento:
    toque_1 = tela.passos_ate_protocolo[0]
    novos = tuple(
        replace(
            e,
            botao=Botao(
                **{
                    **{"primario": True, "visivel": True, "habilitado": True, "atras_de_mais_opcoes": False},
                    **botao,
                }
            ),
        )
        if e.tipo == "botao_pedir_cancelamento"
        else e
        for e in toque_1.elementos
    )
    return replace(
        tela, passos_ate_protocolo=(replace(toque_1, elementos=novos), tela.passos_ate_protocolo[1])
    )


CASOS_DA_LISTA_DE_GUARDA = [
    _caso(
        "extra_confirmation",
        lambda t: _com_passo_extra(t, Elemento(tipo="pergunta_tem_certeza")),
        "GUARD_EXTRA_CONFIRMATION",
    ),
    _caso(
        "touch_count",
        lambda t: replace(
            t, passos_ate_protocolo=(*t.passos_ate_protocolo, Passo(nome="pos_protocolo", elementos=()))
        ),
        "GUARD_TOUCH_COUNT",
    ),
    _caso(
        "multi_step_offer", lambda t: _com_passo_extra(t, Elemento(tipo="oferta")), "GUARD_MULTI_STEP_OFFER"
    ),
    _caso("button_not_primary", lambda t: _toque_1_com(t, primario=False), "GUARD_CANCEL_BUTTON_NOT_PRIMARY"),
    _caso("button_hidden", lambda t: _toque_1_com(t, visivel=False), "GUARD_CANCEL_BUTTON_NOT_PRIMARY"),
    _caso("button_disabled", lambda t: _toque_1_com(t, habilitado=False), "GUARD_CANCEL_BUTTON_NOT_PRIMARY"),
    _caso(
        "button_behind_more_options",
        lambda t: _toque_1_com(t, atras_de_mais_opcoes=True),
        "GUARD_CANCEL_BUTTON_NOT_PRIMARY",
    ),
    _caso(
        "requires_call", lambda t: _com_passo_extra(t, Elemento(tipo="exigir_ligacao")), "GUARD_REQUIRES_CALL"
    ),
    _caso(
        "requires_reason",
        lambda t: _com_passo_extra(t, Elemento(tipo="pergunta_motivo", obrigatorio=True)),
        "GUARD_REQUIRES_REASON",
    ),
    _caso(
        "uses_plan_usage",
        lambda t: replace(t, insumos=t.insumos | {"uso_do_plano"}),
        "GUARD_USES_PLAN_USAGE",
    ),
    _caso(
        "cancelled_before_fact",
        lambda t: replace(t, rotulos_de_estado=("Cancelado",)),
        "GUARD_CANCELLED_BEFORE_SOURCE_FACT",
    ),
    _caso(
        "deadline_before_reg27",
        lambda t: _com_passo_extra(t, Elemento(tipo="prazo_regulatorio")),
        "REGULATORY_DEADLINE_BEFORE_REG27",
    ),
]


@pytest.mark.parametrize(("alterar", "codigo"), CASOS_DA_LISTA_DE_GUARDA)
def test_cancel_guard_list(alterar: Any, codigo: str) -> None:
    politica, tela = _tela_entregue()
    assert verificar_lista_de_guarda(tela, politica) == ()
    assert codigo in verificar_lista_de_guarda(alterar(tela), politica)


def test_cancel_entry_always_visible() -> None:
    politica = carregar_politica_cancelamento()
    pf_inadimplente = entrada_do_cancelamento(
        PerfilContratual(tipo_plano="individual", papel="titular", inadimplente=True), politica
    )
    assert pf_inadimplente.visivel is True
    assert pf_inadimplente.rota == "pedido_pagina"
    for perfil in (
        PerfilContratual(tipo_plano="coletivo_empresarial", papel="dependente", inadimplente=False),
        PerfilContratual(tipo_plano="coletivo_adesao", papel="titular", inadimplente=True),
        PerfilContratual(tipo_plano="desconhecido", papel="titular", inadimplente=False),
    ):
        entrada = entrada_do_cancelamento(perfil, politica)
        assert entrada.visivel is True
        assert entrada.rota == "texto_fixo_dl0098_e_rota_humana"
    assert entrada.rotulo != ""


def test_no_regulatory_deadline_until_reg27(tmp_path: Path) -> None:
    dados = _carregar()
    dados["prazo_comprovante_efetivo"]["exibir"] = True
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_cancelamento(_gravar(tmp_path, dados))
    assert exc.value.code == "DEADLINE_DISPLAY_PENDING_REG27"
    dados = _carregar()
    dados["textos"]["estado_aguardando"] = "Concluímos em até 10 dias úteis"
    with pytest.raises(PoliticaInvalida) as exc2:
        carregar_politica_cancelamento(_gravar(tmp_path, dados, "q.yaml"))
    assert exc2.value.code == "REGULATORY_DEADLINE_BEFORE_REG27"
    assert _admitir().prazo_exibido is None


def test_art15_text_not_publishable_until_reg27(tmp_path: Path) -> None:
    dados = _carregar()
    dados["bloco_art15"]["texto_ratificado"] = True
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_cancelamento(_gravar(tmp_path, dados))
    assert exc.value.code == "ART15_TEXT_PENDING_REG27"
    _, tela = _tela_entregue()
    assert tela.publicavel is False
    bloco = next(e for p in tela.passos_ate_protocolo for e in p.elementos if e.tipo == "bloco_art15")
    assert bloco.texto_exibivel is False
    assert bloco.obrigatorio is True
    assert bloco.incisos == tuple(INCISOS)


def test_cancel_loader_structural_guards(tmp_path: Path) -> None:
    dados = _carregar()
    dados["toques_ate_protocolo"] = 3
    with pytest.raises(PoliticaInvalida) as exc:
        carregar_politica_cancelamento(_gravar(tmp_path, dados))
    assert exc.value.code == "TOUCH_COUNT_NOT_TWO"
    dados = _carregar()
    dados["bloco_art15"]["incisos_exigidos"] = ["I", "II"]
    with pytest.raises(PoliticaInvalida) as exc2:
        carregar_politica_cancelamento(_gravar(tmp_path, dados, "i.yaml"))
    assert exc2.value.code == "ART15_BLOCK_INCOMPLETE"
    dados = _carregar()
    dados["prazo_dias"] = 5
    with pytest.raises(PoliticaInvalida) as exc3:
        carregar_politica_cancelamento(_gravar(tmp_path, dados, "u.yaml"))
    assert exc3.value.code == "UNKNOWN_KEY"


def test_cancel_start_vars_do_not_fabricate_facts() -> None:
    variaveis = dict(_admitir().variaveis_de_inicio_cancel_001)
    for fato in (
        "titularidade_confirmada",
        "vinculo_ativo",
        "dentro_prazo",
        "notificacao_previa_feita",
        "numero_contrato",
        "matricula_beneficiario",
    ):
        assert fato not in variaveis
    assert variaveis["tenant_id"] == "t-1"
    assert variaveis["origem_solicitacao"] == "beneficiario"
    entrada = CancelInput(**pick_fields(variaveis, CancelInput))
    admissibilidade = assess_admissibility(entrada, validate_cancel(entrada))
    assert admissibilidade.roteamento == "ANALISE_HUMANA"


def test_cancelled_label_only_after_source_fact() -> None:
    assert "cancelado" not in rotulo_do_estado(fato_de_fonte_executado=False).lower()
    assert "cancelado" in rotulo_do_estado(fato_de_fonte_executado=True).lower()
    politica = carregar_politica_cancelamento()
    assert (
        rotulo_do_estado(fato_de_fonte_executado=False, politica=politica)
        == politica.textos["estado_aguardando"]
    )


def test_cancel_request_protocol_reuses_cancel_001() -> None:
    # EP-REG-27b / RT-05 item 4: o protocolo do pedido pela pagina e' o CAN-XXXX-XXXX da instancia do
    # SP-OP-CANCEL-001 (REUSE); o modulo nao fabrica protocolo proprio.
    admitido = _admitir()
    assert "protocolo" not in admitido.variaveis_de_inicio_cancel_001
    protocolo = protocolo_do_cancelamento("instancia-cancel-001-teste")
    assert protocolo_valido(protocolo)
    assert protocolo == protocolo_do_cancelamento("instancia-cancel-001-teste")

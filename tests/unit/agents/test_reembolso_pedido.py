"""Coleta do pedido de reembolso pelo Lucas (DL-0160/DL-0161): maquina deterministica, sem modelo."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from maezo.domain.reembolso import pedido as rp
from maezo.tools.workers.reembolso import ReembolsoInput

HOJE = date(2026, 10, 28)
CONV = "wa:amh:hk1_abc"
BPMN = (
    Path(__file__).resolve().parents[3]
    / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
)


def _turno(pedido: rp.PedidoReembolso, texto: str) -> rp.Saida:
    return rp.avancar(pedido, texto, hoje=HOJE)


def _ate_documentos(categoria: str = "1", tipo: str = "1") -> rp.PedidoReembolso:
    p = rp.iniciar().pedido
    for texto in ("sim", "titular", categoria):
        p = _turno(p, texto).pedido
    if p.etapa == "tem_pedido":
        p = _turno(p, "sim").pedido
    for texto in (tipo, "25/10/2026", "R$ 1.200,50"):
        p = _turno(p, texto).pedido
    if p.etapa == "motivo":
        p = _turno(p, "2").pedido
    return p


def test_fluxo_completo_de_consulta_na_livre_escolha() -> None:
    p = _ate_documentos()
    assert p.etapa == "doc_nota"  # consulta nao pede o pedido medico
    assert (p.relacao_com_titular, p.categoria_procedimento, p.tipo_reembolso) == (
        "titular",
        "consulta",
        "livre_escolha",
    )
    assert p.data_atendimento == "2026-10-25" and p.valor_solicitado_cents == 120050
    saida = rp.receber_documento(p, "doc-1")
    assert "terminou" in saida.texto and saida.pedido.perguntou_terminou
    saida = rp.receber_documento(saida.pedido, "doc-2")  # varios arquivos do mesmo tipo
    assert saida.pedido.contagem(rp.TIPO_DOC_NOTA) == 2
    saida = _turno(saida.pedido, "sim")
    assert saida.pedido.etapa == "confirma" and "Confira" in saida.texto
    final = _turno(saida.pedido, "sim")
    assert final.acao == "abrir_processo"


def test_exame_exige_pedido_medico_antes_da_nota() -> None:
    p = _ate_documentos(categoria="2", tipo="2")
    assert p.etapa == "doc_pedido" and p.motivo_nao_rede == "sem_prestador_no_prazo"
    s = rp.receber_documento(p, "ped-1")
    s = _turno(s.pedido, "nao")  # ainda vai mandar mais um
    assert s.pedido.etapa == "doc_pedido" and not s.pedido.perguntou_terminou
    s = rp.receber_documento(s.pedido, "ped-2")
    s = _turno(s.pedido, "sim")
    assert s.pedido.etapa == "doc_nota"
    s = rp.receber_documento(s.pedido, "nf-1")
    s = _turno(s.pedido, "sim")
    assert s.pedido.etapa == "confirma" and rp.pedido_completo(s.pedido)
    tipos = [d["tipo"] for d in rp.documentos_refs(s.pedido)]
    assert tipos == ["pedido_medico", "pedido_medico", "nota_fiscal"]


@pytest.mark.parametrize("resposta", ["nao", "Não", "ainda nao"])
def test_sem_nota_fiscal_encerra_sem_abrir_processo(resposta: str) -> None:
    s = _turno(rp.iniciar().pedido, resposta)
    assert s.pedido.etapa == "encerrado" and s.acao == "nenhuma"
    assert "Nenhum pedido foi aberto" in s.texto


def test_exame_sem_pedido_medico_encerra() -> None:
    p = rp.iniciar().pedido
    for t in ("sim", "dependente", "2"):
        p = _turno(p, t).pedido
    s = _turno(p, "não")
    assert s.pedido.etapa == "encerrado" and "pedido médico" in s.texto


@pytest.mark.parametrize(
    ("etapa_texto", "ruim"),
    [
        ("data", "31/02/2026"),
        ("data", "25/10/2027"),
        ("data", "ontem"),
        ("valor", "0"),
        ("valor", "abc"),
        ("valor", "-5"),
    ],
)
def test_resposta_invalida_repete_a_pergunta_sem_avancar(etapa_texto: str, ruim: str) -> None:
    p = rp.iniciar().pedido
    for t in ("sim", "titular", "1", "1"):
        p = _turno(p, t).pedido
    assert p.etapa == "data"
    if etapa_texto == "valor":
        p = _turno(p, "25/10/2026").pedido
    s = _turno(p, ruim)
    assert s.pedido.etapa == p.etapa and "Qual" in s.texto


def test_arquivo_fora_da_etapa_de_documentos_nao_e_guardado() -> None:
    s = rp.receber_documento(rp.iniciar().pedido, "doc-x")
    assert s.pedido.documentos == () and "por escrito" in s.texto


def test_texto_sem_arquivo_no_laco_pede_o_arquivo() -> None:
    s = _turno(_ate_documentos(), "sim")
    assert s.pedido.etapa == "doc_nota" and "Ainda não recebi" in s.texto


def test_limite_de_arquivos_por_tipo() -> None:
    p = _ate_documentos()
    for i in range(rp.MAX_ARQUIVOS_POR_TIPO):
        p = rp.receber_documento(p, f"d{i}").pedido
    s = rp.receber_documento(p, "extra")
    assert s.pedido.contagem(rp.TIPO_DOC_NOTA) == rp.MAX_ARQUIVOS_POR_TIPO and "limite" in s.texto


def test_desistir_em_qualquer_etapa_encerra() -> None:
    s = _turno(_ate_documentos(), "deixa pra la")
    assert s.pedido.etapa == "encerrado" and s.acao == "nenhuma"


def test_recusar_na_confirmacao_nao_abre_processo() -> None:
    p = _turno(rp.receber_documento(_ate_documentos(), "nf").pedido, "sim").pedido
    s = _turno(p, "nao")
    assert s.pedido.etapa == "encerrado" and s.acao == "nenhuma"


def test_confirmacao_ambigua_repete_o_resumo() -> None:
    p = _turno(rp.receber_documento(_ate_documentos(), "nf").pedido, "sim").pedido
    s = _turno(p, "talvez")
    assert s.pedido.etapa == "confirma" and s.acao == "nenhuma" and "Confira" in s.texto


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("150", 15000), ("150,5", 15050), ("R$ 1.200,00", 120000), ("1.200", 120000), ("0,99", 99)],
)
def test_leitura_do_valor(texto: str, esperado: int) -> None:
    assert rp.ler_valor_cents(texto) == esperado


def test_protocolo_e_estavel_sem_pii_e_muda_com_o_pedido() -> None:
    p = rp.receber_documento(_ate_documentos(), "nf-1").pedido
    a = rp.protocolo_do_pedido(CONV, p)
    assert a == rp.protocolo_do_pedido(CONV, p) and rp.protocolo_valido(a)
    outro = rp.receber_documento(p, "nf-2").pedido
    assert rp.protocolo_do_pedido(CONV, outro) != a
    assert not rp.protocolo_valido("REE-123")


def test_variaveis_do_processo_casam_com_o_contrato_do_worker() -> None:
    p = _turno(rp.receber_documento(_ate_documentos(), "nf-1").pedido, "sim").pedido
    variaveis = rp.variaveis_do_processo(
        p, tenant_id="amh", conversation_id=CONV, beneficiario_pseudo_id="benef-1", hoje=HOJE
    )
    campos_do_worker = set(ReembolsoInput.__dataclass_fields__)
    extras = {"aviso_beneficiario_whatsapp", "agent_id", "conversation_id", "documentacao_completa"}
    assert set(variaveis) - extras <= campos_do_worker
    assert variaveis["valor_solicitado_cents"] == 120050
    assert variaveis["aviso_beneficiario_whatsapp"] is True and variaveis["agent_id"] == "lucas"
    assert variaveis["documentos_refs"] == [{"tipo": "nota_fiscal", "document_ref": "nf-1", "sha256": ""}]


def test_pedido_incompleto_nao_gera_variaveis() -> None:
    with pytest.raises(ValueError, match="incompleto"):
        rp.variaveis_do_processo(
            rp.PedidoReembolso(), tenant_id="amh", conversation_id=CONV, beneficiario_pseudo_id="b", hoje=HOJE
        )


@pytest.mark.parametrize("texto", ["quero um reembolso", "Como peço REEMBOLSO?", "preciso de ressarcimento"])
def test_gatilho_lexico(texto: str) -> None:
    assert rp.pede_reembolso(texto)


def test_gatilho_nao_dispara_em_outros_assuntos() -> None:
    assert not rp.pede_reembolso("qual o valor da minha mensalidade?")


def test_textos_de_retorno_exigem_o_fato_que_afirmam() -> None:
    proto = "REE-AB12-CD34"
    assert "aprovou" in rp.texto_do_retorno(rp.RESULTADO_APROVADO, protocolo=proto)
    pago = rp.texto_do_retorno(rp.RESULTADO_PAGO, protocolo=proto, data_pagamento_iso="2026-10-27")
    assert "27/10/2026" in pago
    with pytest.raises(ValueError):
        rp.texto_do_retorno(rp.RESULTADO_PAGO, protocolo=proto, data_pagamento_iso="27/10")
    with pytest.raises(ValueError):
        rp.texto_do_retorno(rp.RESULTADO_NEGADO, protocolo=proto)
    negado = rp.texto_do_retorno(rp.RESULTADO_NEGADO, protocolo=proto, texto_da_equipe="fora da cobertura")
    assert "“fora da cobertura”" in negado
    with pytest.raises(ValueError):
        rp.texto_do_retorno("outro", protocolo=proto)


def test_os_resultados_do_bpmn_sao_os_do_modulo() -> None:
    bpmn = BPMN.read_text(encoding="utf-8")
    achados = set(re.findall(r"reembolso_(?:aprovado|parcial|negado|info_solicitada|pago)\b", bpmn))
    assert {f"reembolso_{a.split('_', 1)[1]}" for a in achados} == achados
    assert achados == set(rp.RESULTADOS_DO_REEMBOLSO)

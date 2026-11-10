"""A planilha de revisao clinica sai dos arquivos, inteira, e com o veredito VAZIO.

Os testes de coleta nao precisam de openpyxl (rodam no CI); os da planilha em si usam
`pytest.importorskip("openpyxl")` — openpyxl nao e' dependencia do projeto.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

import pytest
from scripts.sme import revisao_clinica as rc

_ESPERADO_HOJE = {  # 20/10/2026 — se uma tabela ganhar regra, este teste lembra de rever a sugestao
    "triage_redflag_adult": 12,
    "triage_redflag_gestante": 8,
    "triage_redflag_mental_health": 7,
    "triage_redflag_pediatric": 13,
    "triage_sufficiency": 7,
}


def test_uma_linha_por_regra_de_cada_dmn_e_do_alarme() -> None:
    linhas = rc.coletar_regras()
    contagem = Counter(linha[rc.COL_TABELA] for linha in linhas)
    for decisao, n in _ESPERADO_HOJE.items():
        texto = (rc.RAIZ_REPO / rc.DMN_DIR / f"{decisao}.dmn").read_text(encoding="utf-8")
        assert contagem[decisao] == n == len(re.findall(r"<rule\s", texto)), decisao
    assert sum(contagem[t] for t in rc.TABELAS_DE_ALARME) == 40
    assert contagem[rc.TABELA_DO_ALARME] == rc.contagem_esperada()[rc.TABELA_DO_ALARME] > 0
    assert rc.conferir_contagem(linhas) == dict(contagem)


def test_a_ordem_da_planilha_e_a_ordem_da_tabela() -> None:
    """Hit policy FIRST: a ordem decide. A pediatrica tem r9-r13 ANTES de r6."""
    ped = [
        linha[rc.COL_REGRA]
        for linha in rc.coletar_regras()
        if linha[rc.COL_TABELA] == "triage_redflag_pediatric"
    ]
    assert ped == ["r1", "r2", "r3", "r4", "r5", "r9", "r10", "r11", "r12", "r13", "r6", "r7", "r8"]


def test_veredito_ajuste_e_comentario_saem_vazios() -> None:
    for linha in rc.coletar_regras():
        assert linha[rc.COL_VEREDITO] == linha[rc.COL_AJUSTE] == linha[rc.COL_COMENTARIO] == ""


def test_toda_regra_tem_sugestao_atual_e_justificativa() -> None:
    """Nenhuma sugestao desatualizada e nenhuma linha sem sugestao no repositorio de hoje."""
    for linha in rc.coletar_regras():
        onde = f"{linha[rc.COL_TABELA]}/{linha[rc.COL_REGRA]}"
        assert rc.DESATUALIZADA not in linha[rc.COL_SUGESTAO], onde
        assert linha[rc.COL_POR_QUE].strip(), onde
        assert linha[rc.COL_SUGESTAO].split(":")[0] in rc.VEREDITOS, onde


def test_interinas_sao_marcadas_com_a_dl() -> None:
    marca = {
        (linha[rc.COL_TABELA], linha[rc.COL_REGRA]): linha[rc.COL_INTERINA] for linha in rc.coletar_regras()
    }
    assert "DL-0068" in marca[("triage_redflag_adult", "r2")]
    assert "DL-0067" in marca[("triage_redflag_adult", "r8")]
    assert "DL-0067" in marca[("triage_redflag_gestante", "r6")]
    assert "DL-0069" in marca[("triage_redflag_mental_health", "r5")]
    assert "dono" in marca[("triage_redflag_pediatric", "r9")]
    assert "ABERTA" in marca[("triage_sufficiency", "suf_r6_adulto_sem_idade")]


def test_sugestao_desatualizada_quando_a_regra_muda(tmp_path: Path) -> None:
    """Dente: se a condicao da regra mudar, a sugestao escrita para a regra antiga NAO aparece como valida."""
    for sub in ("spec/processes/dmn", "src/maezo/agents/helena"):
        shutil.copytree(rc.RAIZ_REPO / sub, tmp_path / sub)
    dmn = tmp_path / rc.DMN_DIR / "triage_redflag_adult.dmn"
    dmn.write_text(
        dmn.read_text(encoding="utf-8").replace('"dor_toracica"', '"dor_no_peito"'), encoding="utf-8"
    )
    r1 = next(
        linha
        for linha in rc.coletar_regras(tmp_path)
        if linha[rc.COL_TABELA] == "triage_redflag_adult" and linha[rc.COL_REGRA] == "r1"
    )
    assert r1[rc.COL_SUGESTAO].startswith(rc.DESATUALIZADA)


def test_a_conferencia_de_contagem_tem_dentes() -> None:
    linhas = rc.coletar_regras()
    with pytest.raises(ValueError, match="contagem nao bate"):
        rc.conferir_contagem(linhas[:-1])


def test_regex_nova_no_alarme_sem_descricao_reprova(tmp_path: Path) -> None:
    """O medico nao le regex: expressao nova sem descricao legivel reprova a geracao."""
    destino = tmp_path / rc.ALARME_CODIGO
    destino.parent.mkdir(parents=True)
    fonte = (rc.RAIZ_REPO / rc.ALARME_CODIGO).read_text(encoding="utf-8")
    destino.write_text(
        fonte.replace("_ABDOME_DIREITO.search(plano)", "_NOVA.search(plano)"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="_NOVA"):
        rc.ler_alarme(tmp_path)


def test_alarme_usa_todo_o_glossario() -> None:
    """Descricao sem regex correspondente e' descricao de regra que nao existe mais."""
    _, usadas = rc.ler_alarme(rc.RAIZ_REPO)
    assert usadas == set(rc._GLOSSARIO_ALARME)


def test_slas_e_grupos_vem_da_dmn_e_do_bpmn() -> None:
    slas = {linha["Item"]: linha["Valor atual"] for linha in rc.coletar_slas()}
    assert slas["Ciência (ack) P1"] == "PT5M"
    assert slas["Ciência (ack) P2"] == "PT30M"
    assert slas["Ciência (ack) P3"] == "PT4H"
    assert slas["Resolução P1"] == "PT30M"
    assert slas["Resolução P2"] == "PT4H"
    assert slas["Resolução P3"] == "PT24H"
    grupos = {k for k in slas if k.startswith("Grupo ")}
    assert grupos == {
        "Grupo plantao-clinico",
        "Grupo enfermagem-triagem",
        "Grupo atendimento-humano",
        "Grupo supervisao-atendimento",
    }


def test_caso_paralelo_cita_linhas_que_existem() -> None:
    linhas = rc.coletar_caso_paralelo()
    assert len(linhas) == 3
    for linha in linhas:
        for citacao in re.findall(
            r"([\w./-]+\.(?:md|py)):(\d+)", linha["O contrato v1.1.0 diz"] + linha["O código faz"]
        ):
            arquivo, n = citacao
            assert int(n) <= len((rc.RAIZ_REPO / arquivo).read_text(encoding="utf-8").splitlines())


# --------------------------------------------------------------------------------- a planilha em si


def test_planilha_gerada_confere_e_nao_sobrescreve(tmp_path: Path) -> None:
    op = pytest.importorskip("openpyxl")
    destino = tmp_path / "Revisao.xlsx"
    contagem = rc.escrever_planilha(destino)
    assert contagem == {
        rc.ABA_REGRAS: 40 + 7 + rc.contagem_esperada()[rc.TABELA_DO_ALARME],
        rc.ABA_SLAS: 10,
        rc.ABA_PARALELO: 3,
        rc.ABA_ADR: 3,
        rc.ABA_PENDENCIAS: len(rc.coletar_pendencias()),
    }
    wb = op.load_workbook(destino)
    assert wb.sheetnames == [
        rc.ABA_LEIA_ME,
        rc.ABA_REGRAS,
        rc.ABA_SLAS,
        rc.ABA_PARALELO,
        rc.ABA_ADR,
        rc.ABA_PENDENCIAS,
    ]
    ws = wb[rc.ABA_REGRAS]
    assert ws.freeze_panes == "C2"
    assert ws["A2"].font.name == "Calibri"
    assert ws["A2"].alignment.wrap_text
    col = rc._letra(rc.COLUNAS_REGRAS, rc.COL_VEREDITO)
    listas = [dv for dv in ws.data_validations.dataValidation if dv.type == "list"]
    assert any(f"{col}2" in str(dv.sqref) and "Confirmo" in dv.formula1 for dv in listas)
    assert "NÃO É PARECER MÉDICO" in " ".join(str(c.value or "") for c in wb[rc.ABA_LEIA_ME]["A"])
    wb.close()
    with pytest.raises(FileExistsError):
        rc.escrever_planilha(destino)

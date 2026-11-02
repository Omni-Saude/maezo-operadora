"""A volta da planilha preenchida: so' 'Confirmo' ratifica, tabela inteira so' com todas confirmadas, e o
que sai passa na cerca do registro de aprovacoes (DL-0148) quando aplicado numa COPIA.

Planilha de exemplo SINTETICA (revisora ficticia); as regras sao as dos DMN reais, os vereditos sao do teste.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from scripts.ci.check_aprovacao_de_regras import carregar, sha256_normalizado, verificar
from scripts.sme import revisao_clinica as rc
from scripts.sme import revisao_clinica_para_yaml as conv

_HOJE = date(2026, 10, 20)
_REVISORA = conv.Revisor(
    nome="Revisora Teste Sintetica", capacidade="Medica-auditora (teste)", data="2026-10-20"
)
_MENTAL = "triage_redflag_mental_health"
_ADULTO = "triage_redflag_adult"


def _planilha(confirmar: set[str], *, excecoes: dict[tuple[str, str], str] | None = None) -> list[rc.Linha]:
    """As linhas reais da aba de regras com veredito 'Confirmo' nas tabelas de `confirmar`."""
    linhas = rc.coletar_regras()
    for linha in linhas:
        if linha[rc.COL_TABELA] in confirmar:
            linha[rc.COL_VEREDITO] = "Confirmo"
        chave = (linha[rc.COL_TABELA], linha[rc.COL_REGRA])
        if excecoes and chave in excecoes:
            linha[rc.COL_VEREDITO] = excecoes[chave]
            linha[rc.COL_AJUSTE] = "ajuste sintetico do teste"
    return linhas


def test_tabela_toda_confirmada_vira_ratificacao_e_candidata() -> None:
    res = conv.converter(_planilha({_MENTAL}), _REVISORA, hoje=_HOJE)
    rat = res.ratificacoes[conv.nome_da_ratificacao(_MENTAL)]
    assert rat["regras_ratificadas"] == ["r1", "r2", "r3", "r4", "r5", "r6", "r7"]
    assert rat["regras_nao_ratificadas"] == []
    assert rat["revisor"] == _REVISORA.nome
    assert rat["ratificado_em"] == "2026-10-20"
    alvo = rc.RAIZ_REPO / rc.DMN_DIR / f"{_MENTAL}.dmn"
    assert rat["tabela_viva"] == {"path": f"{rc.DMN_DIR}/{_MENTAL}.dmn", "sha256": sha256_normalizado(alvo)}
    assert [a["artefato"] for a in res.aprovados] == [f"{rc.DMN_DIR}/{_MENTAL}.dmn"]
    assert res.aprovados[0]["papel"] == "medico-auditor"


@pytest.mark.parametrize("veredito", ["Alterar", "Perigoso", ""])
def test_uma_regra_nao_confirmada_tira_a_tabela_dos_aprovados(veredito: str) -> None:
    """Dente: uma unica regra sem 'Confirmo' impede a aprovacao da tabela inteira."""
    res = conv.converter(_planilha({_MENTAL}, excecoes={(_MENTAL, "r6"): veredito}), _REVISORA, hoje=_HOJE)
    rat = res.ratificacoes[conv.nome_da_ratificacao(_MENTAL)]
    assert "r6" not in rat["regras_ratificadas"]
    assert [p["regra"] for p in rat["regras_nao_ratificadas"]] == ["r6"]
    assert res.aprovados == []


def test_ratificacao_parcial_lista_so_as_confirmadas_na_ordem_da_tabela() -> None:
    linhas = _planilha(set())
    for linha in linhas:
        if linha[rc.COL_TABELA] == _ADULTO and linha[rc.COL_REGRA] in {"r1", "r3", "r7a"}:
            linha[rc.COL_VEREDITO] = "Confirmo"
    res = conv.converter(linhas, _REVISORA, hoje=_HOJE)
    assert res.ratificacoes[conv.nome_da_ratificacao(_ADULTO)]["regras_ratificadas"] == ["r1", "r3", "r7a"]
    assert res.aprovados == []
    assert set(res.ratificacoes) == {conv.nome_da_ratificacao(_ADULTO)}


def test_tabela_alterada_depois_da_planilha_recusa() -> None:
    linhas = _planilha({_MENTAL})
    for linha in linhas:
        if linha[rc.COL_TABELA] == _MENTAL:
            linha[rc.COL_SHA] = "0" * 64
    with pytest.raises(conv.ConversaoRecusadaError, match="mudou depois"):
        conv.converter(linhas, _REVISORA, hoje=_HOJE)


def test_regra_faltando_na_planilha_recusa() -> None:
    linhas = [
        linha
        for linha in _planilha({_MENTAL})
        if not (linha[rc.COL_TABELA] == _MENTAL and linha[rc.COL_REGRA] == "r7")
    ]
    with pytest.raises(conv.ConversaoRecusadaError, match="regras da planilha"):
        conv.converter(linhas, _REVISORA, hoje=_HOJE)


@pytest.mark.parametrize(
    ("revisor", "motivo"),
    [
        (conv.Revisor(nome="", capacidade="x", data="2026-10-20"), "sem nome"),
        (conv.Revisor(nome="X", capacidade="", data="2026-10-20"), "sem capacidade"),
        (conv.Revisor(nome="X", capacidade="x", data="20/10/2026"), "invalida"),
        (conv.Revisor(nome="X", capacidade="x", data="2026-10-21"), "futuro"),
    ],
)
def test_revisor_incompleto_recusa(revisor: conv.Revisor, motivo: str) -> None:
    with pytest.raises(conv.ConversaoRecusadaError, match=motivo):
        conv.converter(_planilha({_MENTAL}), revisor, hoje=_HOJE)


def test_veredito_fora_da_lista_recusa() -> None:
    with pytest.raises(conv.ConversaoRecusadaError, match="fora da lista"):
        conv.converter(_planilha({_MENTAL}, excecoes={(_MENTAL, "r1"): "ok"}), _REVISORA, hoje=_HOJE)


def _arvore_copia(tmp_path: Path) -> Path:
    for sub in ("spec/processes/bpmn", "spec/processes/dmn"):
        shutil.copytree(rc.RAIZ_REPO / sub, tmp_path / sub)
    return tmp_path


def test_candidatos_aplicados_numa_copia_passam_na_cerca(tmp_path: Path) -> None:
    raiz = _arvore_copia(tmp_path)
    res = conv.converter(_planilha({_MENTAL, "triage_sufficiency"}), _REVISORA, hoje=_HOJE)
    escritos = conv.escrever(res, tmp_path / "saida")
    candidatos = yaml.safe_load((tmp_path / "saida" / conv.ARQUIVO_CANDIDATOS).read_text(encoding="utf-8"))
    assert len(candidatos["aprovados"]) == 2
    registro = conv.aplicar_em_copia(carregar(), candidatos["aprovados"])
    assert verificar(registro, raiz=raiz, hoje=_HOJE) == []
    # a ratificacao gerada tem o formato da neonatal (chaves obrigatorias)
    rat = yaml.safe_load(
        next(p for p in escritos if _MENTAL.replace("_", "-") in p.name).read_text(encoding="utf-8")
    )
    for chave in (
        "alvo",
        "decisao_alvo",
        "regras_ratificadas",
        "revisor",
        "capacidade",
        "ratificado_em",
        "attestation_basis",
        "tabela_viva",
    ):
        assert rat[chave], chave


def test_dente_da_cerca_hash_adulterado_reprova(tmp_path: Path) -> None:
    raiz = _arvore_copia(tmp_path)
    res = conv.converter(_planilha({_MENTAL}), _REVISORA, hoje=_HOJE)
    adulterado = [{**res.aprovados[0], "sha256": "f" * 64}]
    achados = verificar(conv.aplicar_em_copia(carregar(), adulterado), raiz=raiz, hoje=_HOJE)
    assert any("bytes mudaram" in a for a in achados)


def test_dente_da_cerca_tabela_editada_depois_de_aprovada(tmp_path: Path) -> None:
    raiz = _arvore_copia(tmp_path)
    res = conv.converter(_planilha({_MENTAL}), _REVISORA, hoje=_HOJE)
    dmn = raiz / rc.DMN_DIR / f"{_MENTAL}.dmn"
    dmn.write_bytes(dmn.read_bytes().replace(b'"P1"', b'"P2"', 1))
    achados = verificar(conv.aplicar_em_copia(carregar(), res.aprovados), raiz=raiz, hoje=_HOJE)
    assert any(_MENTAL in a and "bytes mudaram" in a for a in achados)


def test_escrever_nao_sobrescreve(tmp_path: Path) -> None:
    res = conv.converter(_planilha({_MENTAL}), _REVISORA, hoje=_HOJE)
    conv.escrever(res, tmp_path)
    with pytest.raises(FileExistsError):
        conv.escrever(res, tmp_path)


def test_ida_e_volta_pela_planilha(tmp_path: Path) -> None:
    """A planilha gerada, preenchida pelo openpyxl como o medico faria, volta pelo conversor."""
    op = pytest.importorskip("openpyxl")
    caminho = tmp_path / "Revisao.xlsx"
    rc.escrever_planilha(caminho)
    wb = op.load_workbook(caminho)
    ws = wb[rc.ABA_REGRAS]
    cab = [c.value for c in ws[1]]
    i_tab, i_ver = cab.index(rc.COL_TABELA), cab.index(rc.COL_VEREDITO)
    for linha in ws.iter_rows(min_row=2):
        if linha[i_tab].value == _MENTAL:
            linha[i_ver].value = "Confirmo"
    pend = wb[rc.ABA_PENDENCIAS]
    valores = {
        rc.PEND_REVISOR: _REVISORA.nome,
        rc.PEND_CAPACIDADE: _REVISORA.capacidade,
        rc.PEND_DATA: _REVISORA.data,
        rc.PEND_CRM: "CRM-XX 000000 (ficticio)",
    }
    for linha in pend.iter_rows(min_row=2):
        if linha[0].value in valores:
            linha[1].value = valores[linha[0].value]
    wb.save(caminho)
    saida = tmp_path / "saida"
    assert conv.main(["--planilha", str(caminho), "--saida", str(saida)]) == 0
    rat = yaml.safe_load((saida / conv.nome_da_ratificacao(_MENTAL)).read_text(encoding="utf-8"))
    assert rat["revisor"] == _REVISORA.nome
    assert rat["capacidade"] == f"{_REVISORA.capacidade} (CRM-XX 000000 (ficticio))"
    candidatos = yaml.safe_load((saida / conv.ARQUIVO_CANDIDATOS).read_text(encoding="utf-8"))
    assert [a["artefato"] for a in candidatos["aprovados"]] == [f"{rc.DMN_DIR}/{_MENTAL}.dmn"]


def test_planilha_sem_revisor_nao_gera_nada(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    caminho = tmp_path / "Revisao.xlsx"
    rc.escrever_planilha(caminho)
    saida = tmp_path / "saida"
    assert conv.main(["--planilha", str(caminho), "--saida", str(saida)]) == 1
    assert not saida.exists()

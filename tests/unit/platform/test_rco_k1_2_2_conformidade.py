"""Conformidade do leitor e da composicao do Maezo com o pacote K1 2.2.0 REAL do rco (a1978375, DL-0173).

Ate aqui `composicoes[]` so era provado com o pacote sintetico (`rco_k1_sintetico`); este arquivo usa o
recorte do pacote gerado pelo `scripts/pacote.py` do proprio rco (`tests/fixtures/rco/k1-2.2.0-recorte/`,
procedencia em `ORIGEM.json`):
- a copia e byte a byte a do rco (sha256 por arquivo, nada sobrando);
- o manifesto real passa o leitor estrito (`ler_manifesto`, o mesmo do gate `verify_rco_rules_pin` e do
  runtime) e, restrito as tres decisoes do recorte, `verificar_pacote` so acusa o anexo que o recorte
  deixa de fora (`entradas.json`);
- `composicoes[]` traz `pares_mutuamente_exclusivos_ortop` com as tres decisoes na ordem documentada;
- a expectativa do grupo derivada dos casos do rco (`rco_k1_2_2_recorte.expectativa`) e `escolher` sobre
  as saidas que o rco declara; a avaliacao das tres decisoes no motor real fica em
  `tests/integration/dmn/test_rco_k1_2_2_composicao_engine.py`;
- a guarda recusa avaliar sozinho qualquer membro do grupo, o padrao inclusive.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from maezo.platform import rco_composicao
from maezo.platform import rco_manifesto as k1
from tests.unit.platform import rco_k1_2_2_recorte as rec

FIXTURES_RCO = rec.RECORTE.parent


# --- copia vendorizada -------------------------------------------------------------------------------


def test_origem_registra_o_commit_do_rco() -> None:
    origem = rec.origem()
    assert origem["origem_repo"] == "Omni-Saude/regras-contratos-operadora"
    assert origem["origem_commit"] == rec.COMMIT_RCO
    assert origem["formato_manifesto"] == "2.2.0"


def test_cada_arquivo_tem_o_sha256_do_rco() -> None:
    arquivos = rec.origem()["arquivos"]
    assert set(arquivos) == set(rec.origem()["origem_caminhos"])
    for rel, esperado in arquivos.items():
        assert rec.sha256_prefixado(rec.RECORTE / rel) == esperado, f"{rel}: a copia do rco foi editada"


def test_nenhum_arquivo_fora_do_registro() -> None:
    presentes = {
        p.relative_to(rec.RECORTE).as_posix()
        for p in rec.RECORTE.rglob("*")
        if p.is_file() and p.name not in rec.DOCUMENTACAO
    }
    assert presentes == set(rec.origem()["arquivos"])


def test_manifesto_sha256_e_o_hash_do_proprio_arquivo() -> None:
    hash_do_arquivo = rec.sha256_prefixado(rec.RECORTE / "manifesto.json")
    assert (rec.RECORTE / "manifesto.sha256").read_text(encoding="utf-8").strip() == hash_do_arquivo
    assert rec.origem()["manifesto_sha256"] == hash_do_arquivo


# --- (a) leitor estrito -----------------------------------------------------------------------------


def test_manifesto_real_passa_o_leitor_estrito() -> None:
    lido = rec.manifesto()
    assert lido.formato_manifesto == "2.2.0"
    assert lido.repo == "Omni-Saude/regras-contratos-operadora"
    assert lido.commit_sha == rec.COMMIT_RCO
    assert len(lido.decisoes) == 236
    assert lido.dicionario_entradas_versao == "1.2.0"
    assert [p.id for p in lido.provas] == ["teste-unario-booleano"]
    assert k1.provas_ausentes_no_pin(lido) == []


def test_verificar_pacote_no_recorte_so_acusa_o_anexo_omitido() -> None:
    # O recorte leva as tres decisoes do grupo, `vocabulario.json` e `k6-filas.json`; `entradas.json` (~300
    # KB) fica de fora. Restrito a essas decisoes e sem `provas[]` (conferidas abaixo contra a copia ja
    # vendorizada), `verificar_pacote` re-hasheia os DMN, confere ids e colunas e acusa so a falta de
    # `entradas.json`.
    lido = rec.manifesto()
    restrito = dataclasses.replace(
        lido, decisoes=MappingProxyType({d: lido.decisoes[d] for d in rec.ORDEM}), provas=()
    )
    problemas = k1.verificar_pacote(restrito, k1_dir=rec.RECORTE, rules_dir=rec.RECORTE / "dmn")
    assert problemas == [f"{k1.K1_DIR}/entradas.json: anexo 'entradas' ausente (ou link simbolico)"]


def test_provas_do_pacote_sao_a_copia_ja_vendorizada() -> None:
    # `provas[]` do 2.2.0 tem os mesmos bytes da copia de `tests/fixtures/rco/provas/` (rco 323fec5c).
    assert k1.verificar_provas(rec.manifesto(), k1_dir=FIXTURES_RCO) == []


@pytest.mark.parametrize("decisao", rec.ORDEM)
def test_dmn_do_recorte_e_o_do_manifesto(decisao: str) -> None:
    lido = rec.manifesto().decisoes[decisao]
    caminho = rec.dmn(decisao)
    assert lido.arquivo == f"dmn/{decisao.removeprefix(k1.PREFIXO_DECISAO)}.dmn"
    assert rec.sha256_prefixado(caminho) == lido.sha256
    assert k1.ids_de_decisao(caminho.read_bytes(), str(caminho)) == [decisao]
    assert lido.dominio == "ortopedia-opme"


def test_ativacao_do_pacote_real_ainda_recusada_pelo_pin() -> None:
    # Achado do pacote real (nao e da composicao): o gate e o runtime recusam hoje `ativacao` sem
    # significado ligado no consumidor (Onda 2 item 5; so `plena` sem aditivo e aceito). O 2.2.0 do rco tem
    # 46 regras assim, tres delas no grupo de ortopedia; por isso este recorte prova o leitor e a
    # composicao, mas o pacote ainda nao pode ser pinado.
    recusas = k1.ativacao_nao_suportada(rec.manifesto())
    assert len(recusas) == 46
    assert sorted(r.split(" ", 1)[0] for r in recusas if r.split("/", 1)[0] in rec.ORDEM) == [
        "rco_mex_ortop/OMNI-ORTOP-MEX-0001:",
        "rco_mex_ortop_joelho_curativo/OMNI-ORTOP-MEX-0002:",
        "rco_mex_ortop_joelho_curativo/OMNI-ORTOP-MEX-0007:",
    ]


# --- (b) composicoes[] ------------------------------------------------------------------------------


def test_composicao_de_ortopedia_na_ordem_documentada() -> None:
    lido = rec.manifesto()
    assert lido.composicoes == (
        k1.ComposicaoK1(rec.COMPOSICAO, rec.ORDEM, k1.REGRA_PRIMEIRA_COM_ID_REGRA, "rco_mex_ortop"),
    )
    grupo = lido.composicoes[0]
    assert grupo.padrao == grupo.decisoes[-1] == "rco_mex_ortop"
    assert grupo.anteriores == ("rco_mex_ortop_joelho_curativo", "rco_mex_ortop_mesma_data")
    for decisao in rec.ORDEM:
        assert lido.composicao_de(decisao) == grupo
        assert decisao in lido.decisoes


# --- (c) expectativa do grupo pelos casos do rco ----------------------------------------------------


def test_casos_do_rco_cobrem_as_tres_decisoes() -> None:
    por_decisao = {d: [c.id for c in rec.casos() if c.decisao == d] for d in rec.ORDEM}
    assert {d: len(ids) for d, ids in por_decisao.items()} == {
        "rco_mex_ortop_joelho_curativo": 24,
        "rco_mex_ortop_mesma_data": 17,
        "rco_mex_ortop": 29,
    }


def test_expectativa_do_grupo_derivada_dos_casos() -> None:
    # Fixa como a regra `primeira_com_idRegra` le as declaracoes do rco: 60 casos em que a propria decisao
    # decide o grupo; 6 em que outra decide (os descritos pelo rco como "a combinacao usa..." / "mex_ortop
    # decide"); 4 casos de anterior sem `idRegra` cuja saida do grupo o rco nao declara (so se exige que a
    # decisao do caso nao venca).
    todos = rec.casos()
    grupo = rec.composicao()
    propria, outra, nao_declarada = [], [], []
    for caso in todos:
        exp = rec.expectativa(caso, todos, grupo)
        if exp.vencedora is None:
            nao_declarada.append(caso.chave)
        elif exp.vencedora == caso.decisao:
            propria.append(caso.chave)
        else:
            outra.append((caso.chave, exp.vencedora, exp.fonte))
    assert len(propria) == 60
    assert outra == [
        ("rco_mex_ortop_joelho_curativo#C060", "rco_mex_ortop", "rco_mex_ortop#C001"),
        ("rco_mex_ortop_joelho_curativo#C061", "rco_mex_ortop", "rco_mex_ortop#C019"),
        ("rco_mex_ortop_mesma_data#C064", "rco_mex_ortop", "rco_mex_ortop#C001"),
        ("rco_mex_ortop_mesma_data#C065", "rco_mex_ortop", "rco_mex_ortop#C019"),
        ("rco_mex_ortop#C058", "rco_mex_ortop_joelho_curativo", "rco_mex_ortop_joelho_curativo#C005"),
        ("rco_mex_ortop#C059", "rco_mex_ortop_mesma_data", "rco_mex_ortop_mesma_data#C015"),
    ]
    assert nao_declarada == [
        "rco_mex_ortop_joelho_curativo#C062",
        "rco_mex_ortop_joelho_curativo#C063",
        "rco_mex_ortop_mesma_data#C066",
        "rco_mex_ortop_mesma_data#C067",
    ]


@pytest.mark.parametrize(
    ("joelho", "mesma_data", "padrao"),
    [("C060", "C064", "C001"), ("C061", "C065", "C019")],
)
def test_escolher_sobre_as_saidas_que_o_rco_declara(joelho: str, mesma_data: str, padrao: str) -> None:
    # Entradas para as quais o rco declara a saida das TRES decisoes: sem motor, `escolher` sobre essas
    # linhas da o padrao (as anteriores trazem `idRegra` vazio) com a linha que o rco declara para ele.
    casos = {c.chave: c for c in rec.casos()}
    linhas = {
        "rco_mex_ortop_joelho_curativo": casos[f"rco_mex_ortop_joelho_curativo#{joelho}"],
        "rco_mex_ortop_mesma_data": casos[f"rco_mex_ortop_mesma_data#{mesma_data}"],
        "rco_mex_ortop": casos[f"rco_mex_ortop#{padrao}"],
    }
    assert len({tuple(sorted(c.entradas.items())) for c in linhas.values()}) == 1
    resultado = rco_composicao.escolher(rec.composicao(), {d: [dict(c.esperado)] for d, c in linhas.items()})
    assert resultado.desfecho == rco_composicao.DESFECHO_ESCOLHIDO
    assert resultado.decisao_vencedora == "rco_mex_ortop"
    assert resultado.pelo_padrao
    assert rec.diferencas(linhas["rco_mex_ortop"].esperado, resultado.linhas[0]) == []


# --- (d) guarda ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("decisao", rec.ORDEM)
def test_guarda_recusa_avaliar_membro_sozinho(decisao: str) -> None:
    lido = rec.manifesto()
    assert rco_composicao.membro_de_composicao(lido, decisao)
    papel = "padrao" if decisao == "rco_mex_ortop" else "membro"
    with pytest.raises(rco_composicao.DecisaoSoEmComposicaoError, match=f"e {papel} da composicao"):
        rco_composicao.exigir_avaliacao_isolada(lido, decisao)


def test_guarda_deixa_decisao_fora_de_grupo() -> None:
    lido = rec.manifesto()
    fora = sorted(set(lido.decisoes) - set(rec.ORDEM))
    assert len(fora) == 233
    for decisao in fora:
        rco_composicao.exigir_avaliacao_isolada(lido, decisao)  # nao levanta

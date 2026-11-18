"""Conformidade do leitor, da composicao e do montador do Maezo com o pacote K1 2.3.0 REAL do rco (DL-0183).

Usa o recorte do pacote gerado pelo `scripts/pacote.py` do proprio rco no commit aed187d4 (`main` publicada,
PR #174; `tests/fixtures/rco/k1-2.3.0-recorte/`, procedencia em `ORIGEM.json`):
- a copia e byte a byte a do rco (sha256 por arquivo, nada sobrando);
- o manifesto real passa o leitor estrito e, restrito as duas decisoes do grupo, `verificar_pacote` so acusa
  o anexo que o recorte deixa de fora (`entradas.json`): `ordem_restricao`, `sinalAuditoria`, `avisos` do
  K6 e as celulas de sinal e de `decisao` do grupo `mais_restritiva` passam;
- o pacote tem a FORMA ACORDADA com a trilha rco (ordem, vocabulario do sinal, destino do aviso);
- a guarda recusa avaliar sozinho qualquer membro do grupo;
- o dicionario de entradas 1.5.0 passa o leitor, a formula de `urgenciaAposVinteQuatroHorasCobertura` e a
  implementada (com o valor do parametro e a referencia por tipo de guia lidos do pacote) e o mapeamento do
  montador confere com toda decisao de `regras-gerais` e `diarias-taxas`;
- os cenarios C047 a C051 do rco (`geral_carencia`) dao, pelo montador, a derivada que o rco declara no caso.
  As ENTRADAS do montador (datas, horas, tipo de guia) sao TRANSCRITAS da descricao de cada caso, porque o
  caso do rco so traz a derivada pronta; o valor ESPERADO vem do proprio caso do rco, nunca daqui.
O `esperado` vem SEMPRE do rco; divergencia e escalada, nunca ajustada aqui. A avaliacao do grupo no motor
real fica em `tests/integration/dmn/test_rco_k1_2_3_composicao_engine.py`.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from maezo.platform import rco_composicao
from maezo.platform import rco_manifesto as k1
from maezo.platform.validation import vocabulario_saida
from maezo.platform.validation.result import Report
from maezo.tools.workers import rco_montador as mt
from tests.unit.platform import rco_k1_2_3_recorte as rec

REPO_ROOT = Path(__file__).resolve().parents[3]

# --- copia vendorizada -------------------------------------------------------------------------------


def test_origem_registra_o_commit_do_rco() -> None:
    origem = rec.origem()
    assert origem["origem_repo"] == "Omni-Saude/regras-contratos-operadora"
    assert origem["origem_commit"] == rec.COMMIT_RCO
    assert origem["formato_manifesto"] == "2.3.0"


def test_cada_arquivo_tem_o_sha256_registrado() -> None:
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


# --- leitor estrito e forma acordada -----------------------------------------------------------------


def test_manifesto_real_passa_o_leitor_estrito() -> None:
    lido = rec.manifesto()
    assert lido.formato_manifesto == "2.3.0"
    assert lido.commit_sha == rec.COMMIT_RCO
    assert len(lido.decisoes) == 236
    assert lido.dicionario_entradas_versao == "1.5.0"
    assert [p.id for p in lido.provas] == ["teste-unario-booleano"]
    assert k1.provas_ausentes_no_pin(lido) == []
    assert k1.ativacao_nao_suportada(lido) == []


def test_verificar_pacote_no_recorte_so_acusa_o_anexo_omitido() -> None:
    lido = rec.manifesto()
    restrito = dataclasses.replace(
        lido, decisoes=MappingProxyType({d: lido.decisoes[d] for d in rec.ORDEM}), provas=()
    )
    problemas = k1.verificar_pacote(restrito, k1_dir=rec.RECORTE, rules_dir=rec.RECORTE / "dmn")
    assert problemas == [f"{k1.K1_DIR}/entradas.json: anexo 'entradas' ausente (ou link simbolico)"]


def test_vocabulario_tem_a_forma_acordada() -> None:
    vocab = rec.vocabulario()
    assert vocab.ordem_restricao == rec.ORDEM_RESTRICAO_ACORDADA
    assert vocab.sinais_auditoria == rec.SINAIS_ACORDADOS
    assert "sinalAuditoria" not in vocab.saidas_obrigatorias
    bruto = json.loads((rec.RECORTE / "vocabulario.json").read_text(encoding="utf-8"))
    assert sorted(bruto["ordem_restricao"]) == sorted(bruto["decisao"])
    assert len(bruto["ordem_restricao"]) == len(set(bruto["decisao"]))


def test_avisos_do_k6_tem_a_forma_acordada() -> None:
    avisos = k1.carregar_avisos_k6(rec.manifesto(), rec.vocabulario(), k1_dir=rec.RECORTE)
    assert dict(avisos) == {
        "aviso_urgencia_carencia": k1.AvisoK6(
            "aviso_urgencia_carencia", ("auditoria_medica", "cadastro"), "OMNI-GERAL-CAR-0003:efeito.prazos.2"
        )
    }


def test_sinal_so_na_tabela_que_o_usa() -> None:
    lido = rec.manifesto()
    com_sinal = sorted(d.id for d in lido.decisoes.values() if "sinalAuditoria" in d.saidas)
    assert com_sinal == ["rco_geral_carencia"]
    assert "sinalAuditoria" not in lido.decisoes["rco_geral_cpt"].saidas


def test_composicoes_reais() -> None:
    lido = rec.manifesto()
    assert lido.composicoes == (
        k1.ComposicaoK1(rec.COMPOSICAO, rec.ORDEM, k1.REGRA_MAIS_RESTRITIVA, "rco_geral_cpt"),
        k1.ComposicaoK1(
            "pares_mutuamente_exclusivos_ortop",
            ("rco_mex_ortop_joelho_curativo", "rco_mex_ortop_mesma_data", "rco_mex_ortop"),
            k1.REGRA_PRIMEIRA_COM_ID_REGRA,
            "rco_mex_ortop",
        ),
    )


@pytest.mark.parametrize("decisao", rec.ORDEM)
def test_dmn_do_recorte_e_o_do_manifesto(decisao: str) -> None:
    lido = rec.manifesto().decisoes[decisao]
    caminho = rec.dmn(decisao)
    assert rec.sha256_prefixado(caminho) == lido.sha256
    assert k1.ids_de_decisao(caminho.read_bytes(), str(caminho)) == [decisao]
    assert lido.dominio == "regras-gerais"


def test_lint_de_vocabulario_de_saida_aceita_as_tabelas_reais(tmp_path: Path) -> None:
    pasta = tmp_path / "dmn" / "rco"
    pasta.mkdir(parents=True)
    arquivos = [Path(shutil.copy(rec.dmn(d), pasta)) for d in rec.ORDEM]
    report = Report()
    vocabulario_saida.check(arquivos, REPO_ROOT / vocabulario_saida.VOCABULARIO_RELPATH, report)
    assert report.ok, [f.message for f in report.findings]


@pytest.mark.parametrize("decisao", rec.ORDEM)
def test_guarda_recusa_avaliar_membro_sozinho(decisao: str) -> None:
    lido = rec.manifesto()
    papel = "padrao" if decisao == "rco_geral_cpt" else "membro"
    with pytest.raises(rco_composicao.DecisaoSoEmComposicaoError, match=f"e {papel} da composicao"):
        rco_composicao.exigir_avaliacao_isolada(lido, decisao)


def test_casos_do_grupo_declaram_a_vencedora_dentro_do_grupo() -> None:
    casos = rec.casos_do_grupo()
    assert [c.id for c in casos] == [f"C00{n}" for n in range(1, 10)]
    for caso in casos:
        vencedora = caso.esperado.get("decisao_de")
        assert vencedora is None or f"rco_{vencedora}" in rec.ORDEM, caso.id
        assert caso.esperado["decisao"] in rec.ORDEM_RESTRICAO_ACORDADA, caso.id
        assert set(caso.esperado["sinais"]) <= set(rec.SINAIS_ACORDADOS) - {""}, caso.id


# --- dicionario de entradas 1.5.0 e montador ---------------------------------------------------------

URGENCIA = "urgenciaAposVinteQuatroHorasCobertura"


def test_dicionario_15_passa_o_leitor() -> None:
    dic = rec.dicionario()
    assert dic.versao == "1.5.0"
    assert len(dic.entradas) == 39
    assert dic.sha256_conteudo == rec.manifesto().dicionario_entradas_sha256
    entrada = dic.entradas[URGENCIA]
    assert (entrada.origem, entrada.tipo, entrada.dominio.valores) == (
        "derivado",
        "boolean",
        ("true", "false"),
    )
    regimes = dic.entradas["diasAteApresentacao"].formula
    assert regimes is not None and [r.regime for r in regimes.regimes] == [
        "internacao",
        "ambulatorial",
        "sem_regime",
    ]


def test_formula_da_urgencia_e_a_publicada() -> None:
    publicada = rec.dicionario().entradas[URGENCIA].formula
    impl = mt.FORMULAS[URGENCIA]
    assert publicada is not None
    assert impl.texto == publicada.formula
    assert impl.termos == tuple((t.nome, t.origem) for t in publicada.termos)
    assert impl.desde == (1, 4, 0)


def test_mapeamento_confere_com_toda_decisao_dos_dominios_do_montador() -> None:
    dic = rec.dicionario()
    decisoes = [d for d in rec.manifesto().decisoes.values() if d.dominio in mt.MAPEAMENTOS]
    assert len(decisoes) > 2
    for decisao in decisoes:
        assert mt.conferir_mapeamento(dic, decisao.dominio, decisao.entradas) == [], decisao.id


def test_toda_formula_publicada_esta_implementada_ou_pendente() -> None:
    com_formula = {n for n, e in rec.dicionario().entradas.items() if e.formula is not None}
    assert com_formula == set(mt.FORMULAS) | set(mt.FORMULAS_PENDENTES)


def _caso_carencia(id_caso: str) -> dict[str, Any]:
    caso = next(c for c in rec.casos() if c.decisao == "rco_geral_carencia" and c.id == id_caso)
    return dict(caso.entradas)


#: Os cenarios dos casos C047 a C051 do rco, TRANSCRITOS da descricao de cada um (datas, horas e tipo de guia:
#: o caso do rco so traz a derivada pronta, nao os termos);
#: a data do atendimento do caso e 2026-11-03. O valor ESPERADO da derivada e o do proprio caso do rco.
_CENARIOS: dict[str, dict[str, Any]] = {
    "C047": {
        "tipo": mt.TIPO_GUIA_SOLICITACAO_SP_SADT,
        "inicio": "2026-11-03",
        "tiss": {"data_solicitacao": "2026-11-03"},
    },
    "C048": {
        "tipo": mt.TIPO_GUIA_SOLICITACAO_INTERNACAO,
        "inicio": "2026-11-02",
        "tiss": {"data_solicitacao": "2026-11-03"},
    },
    "C049": {
        "tipo": mt.TIPO_GUIA_SOLICITACAO_SP_SADT,
        "inicio": "2026-11-01",
        "tiss": {"data_solicitacao": "2026-11-03"},
    },
    "C050": {
        "tipo": mt.TIPO_GUIA_CONTA_SP_SADT,
        "inicio": "2026-11-02",
        "hora_inicio": "10:00",
        "tiss": {"data_execucao": "2026-11-03", "hora_inicial": "11:00:00"},
    },
    "C051": {
        "tipo": mt.TIPO_GUIA_RESUMO_INTERNACAO,
        "inicio": "2026-11-02",
        "hora_inicio": "10:00",
        "tiss": {"data_execucao": "2026-11-03", "hora_inicial": "09:00:00"},
    },
}


@pytest.mark.parametrize("id_caso", sorted(_CENARIOS))
def test_cenarios_do_rco_pela_formula_do_montador(id_caso: str) -> None:
    cenario = _CENARIOS[id_caso]
    entradas_do_caso = _caso_carencia(id_caso)
    cadastro: dict[str, object] = {"data_inicio_cobertura": cenario["inicio"]}
    if "hora_inicio" in cenario:
        cadastro["hora_inicio_cobertura"] = cenario["hora_inicio"]
    fatos = mt.FatosDoCaso(
        guia={"data_atendimento": entradas_do_caso["dataAtendimento"]},
        versao_k3_pinada=(1, 15),
        cadastro=cadastro,
        tiss=cenario["tiss"],
        # `horasMinimasUrgencia` e a referencia da guia vem do pacote (dicionario 1.5.0), nao daqui.
        tipo_guia=cenario["tipo"],
    )
    montadas = mt.montar_entradas(rec.manifesto().decisoes["rco_geral_carencia"], rec.dicionario(), fatos)
    assert montadas.variaveis[URGENCIA] == entradas_do_caso[URGENCIA], id_caso

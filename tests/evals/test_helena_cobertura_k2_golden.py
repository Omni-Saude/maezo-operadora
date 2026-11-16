"""Golden da cobertura da Helena a partir do `avaliacoes.yaml` do pacote K2 (HEL-6b, passo 1), sempre ligado.

Fonte UNICA: as avaliacoes de dentro do pacote pinado (`conv.converter_k2`). Aqui o pacote e' a AMOSTRA
SINTETICA do leitor; o JSON versionado em `golden/helena/cobertura_k2/` tem de ser exatamente a conversao.
O corte de 95% sobre o K2 real entra no passo seguinte, quando o pin real chegar (ver o conversor).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.agents.helena.habilidades.cobertura.manual import (
    AvaliacaoDoManual,
    ManualK2,
    PinDoK2,
    RegraDoManual,
    aceite_de_nao_ratificado,
    carregar,
)
from tests.evals import helena_cobertura_conversor as conv

pytestmark = pytest.mark.eval

_GOLDEN = Path(__file__).parent / "golden" / "helena"
_CAMADAS = _GOLDEN / "cobertura" / "camadas.yaml"
_SAIDA = _GOLDEN / "cobertura_k2"
_AMOSTRA_K2 = Path(__file__).parents[1] / "unit" / "agents" / "helena_k2_amostra" / "pacote"
_PIN = PinDoK2(
    versao="amostra-0.1.0",
    sha256_manifesto="f9124f357352eaffa58cf06f3af7576209deb25f6d5dfe0cc1624115e49e15cd",
)
_COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(scope="module")
def amostra() -> ManualK2:
    # MAEZO_ENV=dev so' durante a carga (pacote nao ratificado): o contexto desfaz ao sair.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAEZO_ENV", "dev")
        return carregar(_AMOSTRA_K2, pin=_PIN, aceite=aceite_de_nao_ratificado(configurado=True))


def test_os_json_versionados_sao_a_conversao_da_amostra(amostra: ManualK2) -> None:
    """Nada editado a mao: cada arquivo e' o dump do conversor, byte a byte, um por avaliacao."""
    casos = conv.converter_k2(amostra, conv.carregar_camadas(_CAMADAS))
    assert len(casos) == len(amostra.avaliacoes) > 0
    gerados = {f"EVL-HELENA-COB-{c.id}.json": conv.como_texto(c).encode("utf-8") for c in casos}
    versionados = {p.name: p.read_bytes() for p in sorted(_SAIDA.glob("*.json"))}
    assert gerados == versionados


def test_o_cli_k2_gera_os_mesmos_arquivos(tmp_path: Path) -> None:
    argv = [
        "--k2",
        str(_AMOSTRA_K2),
        "--pin-versao",
        _PIN.versao,
        "--pin-sha256",
        _PIN.sha256_manifesto,
        "--aceitar-nao-ratificado",
        "--camadas",
        str(_CAMADAS),
        "--saida",
        str(tmp_path),
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAEZO_ENV", "dev")
        assert conv.main(argv) == 0
    gerados = {p.name: p.read_bytes() for p in sorted(tmp_path.glob("*.json"))}
    assert gerados == {p.name: p.read_bytes() for p in sorted(_SAIDA.glob("*.json"))}


def test_o_cli_nao_define_o_ambiente_e_recusa_nao_ratificado_fora_de_dev(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--k2", str(_AMOSTRA_K2), "--pin-versao", _PIN.versao, "--pin-sha256", _PIN.sha256_manifesto]
    argv += ["--aceitar-nao-ratificado", "--camadas", str(_CAMADAS), "--saida", str(tmp_path / "s")]
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("MAEZO_ENV", raising=False)
        assert conv.main(argv) == 2
    assert not (tmp_path / "s").exists()
    assert "pacote_nao_ratificado" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--k2", "p", "--perguntas", "q"],
        ["--k2", "p", "--pin-versao", "v"],
        ["--k2", "p", "--pin-versao", "v", "--pin-sha256", "h", "--gabarito", "g"],
        ["--perguntas", "q"],
        ["--perguntas", "q", "--gabarito", "g", "--pin-versao", "v"],
        ["--perguntas", "q", "--gabarito", "g", "--pin-sha256", ""],
        ["--perguntas", "q", "--gabarito", "g", "--pin-commit", "c"],
        ["--perguntas", "q", "--gabarito", "g", "--aceitar-nao-ratificado"],
    ],
)
def test_o_cli_separa_as_duas_fontes(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as saida:
        conv.main([*argv, "--camadas", str(_CAMADAS), "--saida", "nao-usado"])
    assert saida.value.code == 2


def _argv_k2(saida: Path, *extra: str, camadas: Path = _CAMADAS) -> list[str]:
    return [
        *("--k2", str(_AMOSTRA_K2), "--pin-versao", _PIN.versao, "--pin-sha256", _PIN.sha256_manifesto),
        *("--aceitar-nao-ratificado", "--camadas", str(camadas), "--saida", str(saida), *extra),
    ]


def _main_em_dev(argv: list[str]) -> int:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MAEZO_ENV", "dev")
        return conv.main(argv)


def test_o_cli_limpa_golden_antigo_da_saida(tmp_path: Path) -> None:
    (tmp_path / "EVL-HELENA-COB-REMOVIDO-001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "outro.txt").write_text("fica", encoding="utf-8")
    assert _main_em_dev(_argv_k2(tmp_path)) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [*(p.name for p in _SAIDA.glob("*.json")), "outro.txt"]
    )


def test_o_cli_fonte_invalida_sai_com_2_e_motivo_no_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    camadas = tmp_path / "camadas.yaml"
    camadas.write_text("camadas: {autorizacao: 1, prazos: 1}\n", encoding="utf-8")
    assert _main_em_dev(_argv_k2(tmp_path / "s", camadas=camadas)) == 2
    capturado = capsys.readouterr()
    assert "quantidades" in capturado.err and not capturado.out
    assert not (tmp_path / "s").exists()


def test_o_cli_passa_o_commit_ao_pin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main_em_dev(_argv_k2(tmp_path / "ok", "--pin-commit", "0" * 40)) == 0
    assert _main_em_dev(_argv_k2(tmp_path / "x", "--pin-commit", "f" * 40)) == 2
    assert "commit_rco_diferente_do_pin" in capsys.readouterr().err


def test_carregar_camadas_recusa_a_chave_reservada(tmp_path: Path) -> None:
    camadas = tmp_path / "camadas.yaml"
    camadas.write_text("camadas: {autorizacao: 1, a_classificar: 1}\n", encoding="utf-8")
    with pytest.raises(conv.FonteInvalidaError, match="a_classificar"):
        conv.carregar_camadas(camadas)


# --- Avaliacao de desfechos que nao sao `resposta` ---
def _caso_da_amostra(amostra: ManualK2, id_: str) -> conv.CasoDeCobertura:
    [caso] = [c for c in conv.converter_k2(amostra, conv.carregar_camadas(_CAMADAS)) if c.id == id_]
    return caso


def test_transferir_com_trecho_de_invencao_e_invencao(amostra: ManualK2) -> None:
    caso = _caso_da_amostra(amostra, "AMOSTRA-AV-003")
    r = conv.avaliar(caso, desfecho="transferir", texto="Sim, ha cobertura no exterior. Vou te transferir.")
    assert r.invencao and not r.correta


def test_transferir_com_passo_a_passo_do_prestador_e_errado(amostra: ManualK2) -> None:
    caso = _caso_da_amostra(amostra, "AMOSTRA-AV-004")
    r = conv.avaliar(caso, desfecho="transferir", texto="Segue o passo a passo do prestador: ...")
    assert not r.correta and r.invencao


def test_transferir_sem_elemento_obrigatorio_e_errado(amostra: ManualK2) -> None:
    caso = _caso_da_amostra(amostra, "AMOSTRA-AV-003")
    r = conv.avaliar(caso, desfecho="transferir", texto="ok")
    assert not r.correta and "elemento_ausente" in r.motivos and not r.invencao
    certo = conv.avaliar(caso, desfecho="transferir", texto="Vou te passar aos canais oficiais.")
    assert certo.correta


def test_transferir_com_fatos_que_nao_conferem_e_invencao(amostra: ManualK2) -> None:
    caso = _caso_da_amostra(amostra, "AMOSTRA-AV-003")
    r = conv.avaliar(caso, desfecho="transferir", texto="Pelos canais oficiais.", fatos_conferem=False)
    assert r.invencao and not r.correta


def test_corte_exige_minimo_de_casos_de_camada_1() -> None:
    um = [conv.Resultado("A", "1", correta=True, invencao=False, motivos=())]
    assert conv.corte(um).aprovado
    assert not conv.corte(um, minimo_camada_1=2).aprovado


# --- Mapeamento (manual construido direto, sem disco) ---
def _regra(id_: str, dominio: str, titulo: str) -> RegraDoManual:
    return RegraDoManual(
        id=id_,
        url="u",
        titulo=titulo,
        resumo="r",
        texto="t",
        sinonimos=(),
        versao="1",
        dominio=dominio,
        dominio_nome=dominio,
    )


def _av(id_: str, regra_origem: tuple[str, ...], comportamento: str | None = None) -> AvaliacaoDoManual:
    return AvaliacaoDoManual(
        id=id_,
        publico="beneficiario",
        pergunta=f"pergunta {id_}",
        resposta_esperada="resposta",
        elementos_obrigatorios=("elemento",),
        o_que_seria_invencao=("invencao",),
        regra_origem=regra_origem,
        comportamento=comportamento,
    )


def _manual(*avaliacoes: AvaliacaoDoManual) -> ManualK2:
    regras = {
        "R-AUT": _regra("R-AUT", "autorizacao", "Titulo autorizacao"),
        "R-AUT2": _regra("R-AUT2", "autorizacao", "Outra autorizacao"),
        "R-CAR": _regra("R-CAR", "carencia", "Titulo carencia"),
        "R-PRZ": _regra("R-PRZ", "prazos", "Titulo prazos"),
    }
    return ManualK2(
        versao="9.9.9",
        commit_rco=_COMMIT,
        ratificado=True,
        regras=regras,
        capitulo_geral=(),
        termos={},
        termos_tecnicos={},
        avaliacoes=tuple(avaliacoes),
    )


_CAMADAS_TESTE: dict[str, conv.Camada] = {"autorizacao": "1", "carencia": "2", "prazos": "1"}


@pytest.mark.parametrize(
    ("comportamento", "armadilha", "tipo", "desfechos"),
    [
        (None, False, None, ("resposta",)),
        ("corrige_premissa", True, "premissa_falsa", ("resposta",)),
        ("declara_ausencia", True, "nao_informa", ("transferir",)),
        ("identifica_publico", True, "publico_errado", ("transferir",)),
    ],
)
def test_comportamento_vira_armadilha_e_desfecho(
    comportamento: str | None, armadilha: bool, tipo: str | None, desfechos: tuple[str, ...]
) -> None:
    [caso] = conv.converter_k2(_manual(_av("A1", ("R-AUT",), comportamento)), _CAMADAS_TESTE)
    assert (caso.armadilha, caso.tipo_resposta, caso.desfechos_aceitos) == (armadilha, tipo, desfechos)
    assert (caso.id, caso.pergunta, caso.dominio, caso.camada, caso.tema) == (
        "A1",
        "pergunta A1",
        "autorizacao",
        "1",
        "Titulo autorizacao",
    )
    assert caso.regras_esperadas == ("R-AUT",)
    assert (caso.elementos_obrigatorios, caso.invencoes, caso.nota_de_invencao) == (
        ("elemento",),
        ("invencao",),
        None,
    )


def test_regra_origem_vazia_fica_a_classificar() -> None:
    [caso] = conv.converter_k2(_manual(_av("A2", (), "declara_ausencia")), _CAMADAS_TESTE)
    assert (caso.dominio, caso.camada, caso.tema, caso.regras_esperadas) == (
        "a_classificar",
        "a_classificar",
        "",
        (),
    )


def test_regras_de_dominios_diferentes_ficam_a_classificar() -> None:
    misto, mesmo = conv.converter_k2(
        _manual(_av("A3", ("R-CAR", "R-AUT")), _av("A4", ("R-AUT", "R-AUT2"))), _CAMADAS_TESTE
    )
    assert (misto.dominio, misto.camada, misto.tema) == ("a_classificar", "a_classificar", "Titulo carencia")
    assert misto.regras_esperadas == ("R-CAR", "R-AUT")
    assert (mesmo.dominio, mesmo.camada) == ("autorizacao", "1")


def test_dominios_diferentes_na_mesma_camada_ficam_nessa_camada() -> None:
    [caso] = conv.converter_k2(_manual(_av("A7", ("R-AUT", "R-PRZ"))), _CAMADAS_TESTE)
    assert (caso.dominio, caso.camada) == ("a_classificar", "1")


def test_dominio_fora_das_camadas_falha_fechado() -> None:
    with pytest.raises(conv.FonteInvalidaError, match="carencia"):
        conv.converter_k2(_manual(_av("A5", ("R-CAR",))), {"autorizacao": "1"})


@pytest.mark.parametrize("id_", ["../fora", "a/b", "", "com espaco"])
def test_id_fora_do_padrao_falha_fechado(id_: str) -> None:
    with pytest.raises(conv.FonteInvalidaError, match="id"):
        conv.converter_k2(_manual(_av(id_, ("R-AUT",))), _CAMADAS_TESTE)


def test_origem_tem_arquivo_commit_e_versao_e_serializa() -> None:
    [caso] = conv.converter_k2(_manual(_av("A6", ("R-AUT",))), _CAMADAS_TESTE)
    esperado = {"arquivo": "k2/avaliacoes.yaml", "commit_referencia": _COMMIT, "versao": "9.9.9"}
    assert dict(caso.origem) == esperado
    dados = caso.como_json()
    assert dados["origem"] == esperado
    assert dados["eval_id"] == "EVL-HELENA-COB-A6" and dados["formato"] == conv.FORMATO
    assert conv.como_texto(caso).endswith("}\n")

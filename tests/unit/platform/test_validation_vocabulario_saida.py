"""Testes do lint de vocabulario de saida DMN (MZO-1b) — `vocabulario_saida.py`.

(a) Tabela do Maezo (primeiro nivel de `spec/processes/dmn/`) nao emite valor adverso
    (ADR-0018 parte 2, emendado por ADR-0040 D4), salvo permissao declarada no arquivo de dados.
(b) Tabela do K1 (`spec/processes/dmn/rco/`) segue o vocabulario fechado (ADR-0075 (d)).

Fixtures DMN em `tmp_path`; o arquivo de dados real e usado, salvo nos testes de permissao e de
schema, que escrevem uma copia alterada. O ultimo bloco roda o gate contra a arvore real.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from maezo.platform.validation import dmn, vocabulario_saida
from maezo.platform.validation.cli import validate_artifacts
from maezo.platform.validation.result import Report
from maezo.platform.validation.vocabulario_saida import Tipo, carregar, ler_celula, normalizar

REPO_ROOT = Path(__file__).resolve().parents[3]
VOCAB = REPO_ROOT / vocabulario_saida.VOCABULARIO_RELPATH
DMN_DIR = REPO_ROOT / "spec" / "processes" / "dmn"

_NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"


def _dmn(decision_id: str, outputs: list[str], rules: list[list[str]], extra_output_xml: str = "") -> str:
    outs = "\n".join(
        f'<output id="o{i}" name="{name}" typeRef="string">{extra_output_xml if i == 0 else ""}</output>'
        for i, name in enumerate(outputs)
    )
    body = "\n".join(
        f'<rule id="r{r}"><inputEntry id="r{r}i"><text>-</text></inputEntry>'
        + "".join(
            f'<outputEntry id="r{r}o{c}"><text>{cell}</text></outputEntry>' for c, cell in enumerate(row)
        )
        + "</rule>"
        for r, row in enumerate(rules)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="{_NS}" id="defs_{decision_id}" name="t" namespace="http://maezo.health/dmn">
  <decision id="{decision_id}" name="t">
    <decisionTable id="dt_{decision_id}" hitPolicy="FIRST">
      <input id="in1"><inputExpression id="ie1" typeRef="string"><text>x</text></inputExpression></input>
      {outs}
      {body}
    </decisionTable>
  </decision>
</definitions>"""


def _maezo(tmp_path: Path, name: str, outputs: list[str], rules: list[list[str]], extra: str = "") -> Path:
    path = tmp_path / "dmn" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dmn(path.stem, outputs, rules, extra), encoding="utf-8")
    return path


def _rco(tmp_path: Path, rows: list[tuple[str, str, str]], extra: str = "", name: str = "rco_t.dmn") -> Path:
    path = tmp_path / "dmn" / "rco" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _dmn(path.stem, ["decisao", "classeAdverso", "fila"], [list(r) for r in rows], extra),
        encoding="utf-8",
    )
    return path


def _run(files: list[Path], vocab: Path = VOCAB) -> Report:
    report = Report()
    vocabulario_saida.check(files, vocab, report)
    return report


def _messages(report: Report) -> str:
    return "\n".join(f.message for f in report.findings)


def _vocab_copy(tmp_path: Path, **maezo_overrides: object) -> Path:
    data = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    data["maezo"].update(maezo_overrides)
    out = tmp_path / "vocab.yaml"
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------------------------
# Leitura das celulas
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "tipo", "valor"),
    [
        ('"negar"', Tipo.STRING, "negar"),
        ('  "NEGAR"  ', Tipo.STRING, "NEGAR"),
        ('"a \\"b\\""', Tipo.STRING, 'a "b"'),
        ("null", Tipo.AUSENTE, ""),
        ("", Tipo.AUSENTE, ""),
        ("-", Tipo.EXPRESSAO, ""),
        ("true", Tipo.EXPRESSAO, ""),
        ("10", Tipo.EXPRESSAO, ""),
        ('"a" + "b"', Tipo.EXPRESSAO, ""),
        ('string(duration("P5D"))', Tipo.EXPRESSAO, ""),
    ],
)
def test_ler_celula_classifica_a_representacao(texto: str, tipo: Tipo, valor: str) -> None:
    from xml.etree import ElementTree as ET

    entry = ET.fromstring(f'<outputEntry xmlns="{_NS}"><text>{texto}</text></outputEntry>')
    celula = ler_celula(entry)
    assert celula.tipo is tipo
    if tipo is Tipo.STRING:
        assert celula.valor == valor


def test_normalizar_trata_hifen_espaco_e_caixa() -> None:
    assert normalizar("aceitar-glosa") == "ACEITAR_GLOSA"
    assert normalizar(" Pagar parcial ") == "PAGAR_PARCIAL"


# ---------------------------------------------------------------------------------------------
# (a) Tabelas do Maezo
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor", ["NEGAR", "negar", "Glosar", "INDEFERIR", "aceitar-glosa", "MANTER_NEGATIVA"]
)
def test_saida_adversa_em_tabela_do_maezo_reprova(tmp_path: Path, valor: str) -> None:
    path = _maezo(tmp_path, "x_routing.dmn", ["roteamento"], [['"ANALISE_HUMANA"'], [f'"{valor}"']])
    report = _run([path])
    assert not report.ok
    assert "valor adverso" in _messages(report) and "ADR-0018" in _messages(report)


def test_adverso_em_qualquer_coluna_reprova(tmp_path: Path) -> None:
    path = _maezo(tmp_path, "x.dmn", ["roteamento", "grupo"], [['"ANALISE_HUMANA"', '"NEGAR"']])
    assert "saida 'grupo'" in _messages(_run([path]))


def test_adverso_em_default_output_entry_e_output_values_reprova(tmp_path: Path) -> None:
    extra = (
        '<outputValues id="ov"><text>"ANALISE_HUMANA","GLOSAR"</text></outputValues>'
        '<defaultOutputEntry id="d"><text>"NEGAR"</text></defaultOutputEntry>'
    )
    path = _maezo(tmp_path, "x.dmn", ["roteamento"], [['"ANALISE_HUMANA"']], extra)
    msgs = _messages(_run([path]))
    assert "outputValues" in msgs and "GLOSAR" in msgs
    assert "defaultOutputEntry" in msgs and "NEGAR" in msgs


@pytest.mark.parametrize(
    "cell",
    [
        '"ASSISTENCIAL_CONTESTA_NEGATIVA"',  # rotulo de classificacao, nao merito (cerca da NIP)
        '"Nunca negativa automatica (L0 hard)"',  # texto livre
        '"NAO_ELEGIVEL"',
        '"RECUSADA"',  # pagto_comite_coordenacao: recusa da auto-assuncao, volta a humano
        "null",
        "false",
    ],
)
def test_valor_inteiro_e_nao_substring(tmp_path: Path, cell: str) -> None:
    path = _maezo(tmp_path, "x.dmn", ["roteamento"], [[cell]])
    assert _run([path]).ok


def test_permissao_declarada_libera_so_o_trio_declarado(tmp_path: Path) -> None:
    permitido = _maezo(tmp_path, "permitida.dmn", ["recomendacao"], [['"NEGAR"']])
    outra_coluna = _maezo(
        tmp_path, "permitida2.dmn", ["recomendacao", "outra"], [['"ANALISE_HUMANA"', '"NEGAR"']]
    )
    vocab = _vocab_copy(
        tmp_path,
        permissoes=[
            {
                "arquivo": "permitida.dmn",
                "saida": "recomendacao",
                "valor": "NEGAR",
                "citacao": "ADR-XXXX (teste)",
            },
            {
                "arquivo": "permitida2.dmn",
                "saida": "recomendacao",
                "valor": "NEGAR",
                "citacao": "ADR-XXXX (teste)",
            },
        ],
    )
    so_permitido = _run([permitido], vocab)
    assert not any("valor adverso" in f.message for f in so_permitido.findings)
    assert any("permitida.dmn/recomendacao=NEGAR usada" in n for n in so_permitido.notices)

    ambos = _run([permitido, outra_coluna], vocab)
    # A permissao de permitida2.dmn cobre `recomendacao`, nao `outra`: a coluna nao declarada reprova,
    # e a permissao que nunca casou reprova como orfa.
    msgs = _messages(ambos)
    assert not any(f.path == permitido for f in ambos.findings)
    assert any(f.path == outra_coluna and "saida 'outra'" in f.message for f in ambos.findings)
    assert "permissao orfa permitida2.dmn/recomendacao=NEGAR" in msgs


def test_permissao_em_outro_valor_nao_libera(tmp_path: Path) -> None:
    path = _maezo(tmp_path, "p.dmn", ["recomendacao"], [['"GLOSAR"']])
    vocab = _vocab_copy(
        tmp_path, permissoes=[{"arquivo": "p.dmn", "saida": "recomendacao", "valor": "NEGAR", "citacao": "x"}]
    )
    msgs = _messages(_run([path], vocab))
    assert "GLOSAR" in msgs and "permissao orfa" in msgs


def test_permissao_nao_alcanca_tabela_do_k1(tmp_path: Path) -> None:
    """Permissao e da regra (a); tabela sob rco/ com o mesmo nome nao a consome."""
    path = _rco(tmp_path, [('"liberar"', "null", "null")], name="p.dmn")
    vocab = _vocab_copy(
        tmp_path, permissoes=[{"arquivo": "p.dmn", "saida": "decisao", "valor": "NEGAR", "citacao": "x"}]
    )
    assert "permissao orfa" in _messages(_run([path], vocab))


@pytest.mark.parametrize(
    "permissao",
    [
        {"arquivo": "p.dmn", "saida": "r", "valor": "NEGAR"},  # sem citacao
        {"arquivo": "p.dmn", "saida": "r", "valor": "NEGAR", "citacao": "  "},
        {"arquivo": "p.dmn", "saida": "r", "valor": "ANALISE_HUMANA", "citacao": "x"},  # nao e adverso
    ],
)
def test_permissao_malformada_reprova_o_arquivo_de_dados(tmp_path: Path, permissao: dict[str, str]) -> None:
    path = _maezo(tmp_path, "p.dmn", ["r"], [['"ANALISE_HUMANA"']])
    report = _run([path], _vocab_copy(tmp_path, permissoes=[permissao]))
    assert [f.path.name for f in report.findings] == ["vocab.yaml"]
    assert "vocabulario de saida invalido" in report.findings[0].message


def test_outputentry_a_mais_ou_a_menos_reprova(tmp_path: Path) -> None:
    path = _maezo(tmp_path, "x.dmn", ["a", "b"], [['"ANALISE_HUMANA"']])
    assert "nao da para atribuir valor a coluna" in _messages(_run([path]))


# ---------------------------------------------------------------------------------------------
# (b) Tabelas do K1
# ---------------------------------------------------------------------------------------------

_VALIDAS: list[tuple[str, str, str]] = [
    ('"liberar"', "null", "null"),
    ('"informar"', "", '"cadastro"'),
    ('"negar"', '"administrativo"', '"auditoria_administrativa"'),
    ('"glosar"', '"contas"', '"auditoria_contas"'),
    ('"recomendar_negativa"', '"clinico"', '"auditoria_medica"'),
    ('"prazo"', "null", "null"),
]


def test_tabela_k1_valida_passa(tmp_path: Path) -> None:
    report = _run([_rco(tmp_path, _VALIDAS)])
    assert report.ok, _messages(report)


def test_k1_nao_passa_pela_regra_do_maezo(tmp_path: Path) -> None:
    """`negar`/`glosar` sao vocabulario do K1 (ADR-0075 (d)), nao saida adversa proibida la."""
    assert _run([_rco(tmp_path, [('"negar"', '"contas"', '"f"'), ('"glosar"', '"contas"', '"f"')])]).ok


@pytest.mark.parametrize(
    ("row", "trecho"),
    [
        (('"recusar"', "null", "null"), "fora do vocabulario fechado"),
        (('"NEGAR"', '"contas"', '"f"'), "fora do vocabulario fechado"),  # sensivel a caixa
        (("null", "null", "null"), "decisao vazia"),
        (("decisao_dinamica", "null", "null"), "fora do vocabulario fechado"),
        (('"liberar"', '"financeiro"', "null"), "classeAdverso"),
        (('"liberar"', '"vazio"', "null"), "classeAdverso"),  # vazio e ausencia, nao literal
        (('"liberar"', '""', "null"), "classeAdverso"),
        (('"liberar"', "null", "fila_dinamica"), "nao e literal string"),
        (('"negar"', "null", '"f"'), "exige classeAdverso"),
        (('"glosar"', '"contas"', "null"), "exige fila"),
        (('"glosar"', '"contas"', '""'), "exige fila"),
        (('"recomendar_negativa"', '"clinico"', "null"), "exige fila"),
        (('"recomendar_negativa"', "", '"f"'), "exige classeAdverso"),
        (('"negar"', '"clinico"', '"auditoria_medica"'), "'clinico' com decisao 'negar' e proibido"),
    ],
)
def test_cada_regra_k1_reprova(tmp_path: Path, row: tuple[str, str, str], trecho: str) -> None:
    report = _run([_rco(tmp_path, [*_VALIDAS, row])])
    assert not report.ok
    assert trecho in _messages(report)
    assert len(report.findings) == 1, _messages(report)


def test_k1_sem_saida_obrigatoria_reprova(tmp_path: Path) -> None:
    path = tmp_path / "dmn" / "rco" / "rco_x.dmn"
    path.parent.mkdir(parents=True)
    path.write_text(_dmn("rco_x", ["decisao", "fila"], [['"liberar"', "null"]]), encoding="utf-8")
    assert "faltam as saidas obrigatorias ['classeAdverso']" in _messages(_run([path]))


def test_k1_default_output_entry_reprova(tmp_path: Path) -> None:
    extra = '<defaultOutputEntry id="d"><text>"liberar"</text></defaultOutputEntry>'
    assert "defaultOutputEntry" in _messages(_run([_rco(tmp_path, _VALIDAS, extra)]))


def test_k1_output_values_fora_do_vocabulario_reprova(tmp_path: Path) -> None:
    extra = '<outputValues id="ov"><text>"liberar","aprovar"</text></outputValues>'
    assert "'aprovar'" in _messages(_run([_rco(tmp_path, _VALIDAS, extra)]))


def test_exemplo_sintetico_real_passa() -> None:
    path = DMN_DIR / "rco" / "rco_exemplo_sintetico.dmn"
    assert dmn.is_rco_dmn(path)
    report = _run([path])
    assert report.ok, _messages(report)


# ---------------------------------------------------------------------------------------------
# O arquivo de dados
# ---------------------------------------------------------------------------------------------


def test_arquivo_de_dados_real_carrega_e_declara_o_k1() -> None:
    vocab = carregar(VOCAB)
    assert vocab.versao == 1 and vocab.estado in {"provisorio", "congelado"}
    assert vocab.k1.decisoes == {
        "liberar",
        "exigir_autorizacao",
        "exigir_documento",
        "revisao_humana",
        "recomendar_negativa",
        "negar",
        "glosar",
        "pagar_pacote",
        "pagar_referencia",
        "limitar",
        "informar",
        "prazo",
    }
    assert vocab.k1.classes == {"clinico", "administrativo", "contas", "vazio"}
    assert {
        "NEGAR",
        "GLOSAR",
        "INDEFERIR",
        "ACEITAR_GLOSA",
        "PAGAR_PARCIAL",
        "MANTER_NEGATIVA",
    } <= vocab.adversos
    # Nada da lista de adversos e desfecho favoravel legitimo (ADR-0040 D4: PAGAR; auth: AUTO_APROVAR).
    assert not {"PAGAR", "AUTO_APROVAR", "ANALISE_HUMANA"} & vocab.adversos


def test_arquivo_de_dados_fica_fora_da_pasta_do_pin() -> None:
    assert not VOCAB.resolve().is_relative_to((DMN_DIR / "rco").resolve())
    assert not VOCAB.resolve().is_relative_to(DMN_DIR.resolve())


@pytest.mark.parametrize(
    "mutacao",
    [
        lambda d: d.update(schema="outro"),
        lambda d: d.update(versao=99),
        lambda d: d.update(versao=True),
        lambda d: d.update(estado="qualquer"),
        lambda d: d["maezo"].update(adversos=[]),
        lambda d: d["maezo"]["adversos"].append({"valor": "negar", "fontes": ["x"]}),  # nao normalizado
        lambda d: d["maezo"]["adversos"].append({"valor": "NOVO"}),  # sem fontes
        lambda d: d["maezo"]["adversos"].append({"valor": "NEGAR", "fontes": ["x"]}),  # repetido
        lambda d: d["k1"]["classeAdverso"].remove("vazio"),
        lambda d: d["k1"]["vazio"].update(textos_de_saida=['"vazio"']),
        lambda d: d["k1"]["regras"].update(exigem_classe_e_fila=["desconhecida"]),
        lambda d: d["k1"]["saidas_obrigatorias"].remove("fila"),
        lambda d: d.pop("k1"),
    ],
)
def test_arquivo_de_dados_fora_do_schema_reprova_fail_closed(tmp_path: Path, mutacao: object) -> None:
    data = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    mutacao(data)  # type: ignore[operator]
    out = tmp_path / "vocab.yaml"
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    path = _maezo(tmp_path, "x.dmn", ["r"], [['"ANALISE_HUMANA"']])
    report = _run([path], out)
    assert [f.path for f in report.findings] == [out]


def test_arquivo_de_dados_ausente_reprova(tmp_path: Path) -> None:
    report = _run([], tmp_path / "nao-existe.yaml")
    assert not report.ok and "ausente" in _messages(report)


# ---------------------------------------------------------------------------------------------
# A arvore real
# ---------------------------------------------------------------------------------------------


def test_arvore_real_passa_sem_permissoes() -> None:
    files = dmn.list_dmn_files(DMN_DIR)
    assert sum(1 for p in files if not dmn.is_rco_dmn(p)) == 70
    report = _run(files)
    assert report.ok, _messages(report)
    assert carregar(VOCAB).permissoes == ()


def test_gate_esta_ligado_no_validate_artifacts(tmp_path: Path) -> None:
    """Prova de ligacao: uma saida adversa numa copia da arvore real reprova `validate_artifacts`."""
    copy = tmp_path / "processes"
    shutil.copytree(REPO_ROOT / "spec" / "processes", copy)
    target = copy / "dmn" / "glosa_triage.dmn"
    text = target.read_text(encoding="utf-8")
    assert '"PAGAR"' in text
    target.write_text(text.replace('"PAGAR"', '"GLOSAR"', 1), encoding="utf-8")
    assert validate_artifacts([str(copy)]) == 1


def test_gate_k1_esta_ligado_no_validate_artifacts(tmp_path: Path) -> None:
    copy = tmp_path / "processes"
    shutil.copytree(REPO_ROOT / "spec" / "processes", copy)
    target = copy / "dmn" / "rco" / "rco_exemplo_sintetico.dmn"
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace('"liberar"', '"negar"', 1), encoding="utf-8")
    assert validate_artifacts([str(copy)]) == 1


# ---------------------------------------------------------------------------------------------
# Revisao independente (M1-M4)
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    [
        'if x &gt; 1 then "NEGAR" else "ANALISE_HUMANA"',
        '["ANALISE_HUMANA", "glosar"]',
        'string("indeferir")',
    ],
)
def test_m1_literal_adverso_dentro_de_expressao_reprova(tmp_path: Path, cell: str) -> None:
    path = _maezo(tmp_path, "x.dmn", ["roteamento"], [[cell]])
    msgs = _messages(_run([path]))
    assert "dentro de expressao" in msgs and "valor adverso" in msgs


def test_m1_expressao_sem_literal_adverso_passa(tmp_path: Path) -> None:
    cell = 'string(date and time(d + "T00:00:00") + duration("P18D"))'
    assert _run([_maezo(tmp_path, "x.dmn", ["sla"], [[cell]])]).ok


def test_m1_default_output_entry_em_expressao_reprova(tmp_path: Path) -> None:
    extra = '<defaultOutputEntry id="d"><text>if a then "NEGAR" else null</text></defaultOutputEntry>'
    path = _maezo(tmp_path, "x.dmn", ["roteamento"], [['"ANALISE_HUMANA"']], extra)
    assert "defaultOutputEntry" in _messages(_run([path]))


_LITERAL_DECISION = f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="{_NS}" id="defs_l" name="t" namespace="http://maezo.health/dmn">
  <decision id="{{did}}" name="t">
    <variable name="roteamento" typeRef="string"/>
    <literalExpression id="le"><text>"ANALISE_HUMANA"</text></literalExpression>
  </decision>
</definitions>"""


@pytest.mark.parametrize("sub", ["", "rco"])
def test_m2_decisao_que_nao_e_tabela_reprova(tmp_path: Path, sub: str) -> None:
    path = tmp_path / "dmn" / sub / ("rco_l.dmn" if sub else "l.dmn")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LITERAL_DECISION.format(did=path.stem), encoding="utf-8")
    assert dmn.is_rco_dmn(path) is bool(sub)
    msgs = _messages(_run([path]))
    assert "nao e exatamente uma decisionTable" in msgs and "literalExpression" in msgs


def test_m2_toda_decisao_real_e_tabela() -> None:
    report = _run(dmn.list_dmn_files(DMN_DIR))
    assert not any("decisionTable" in f.message for f in report.findings)


def _sem(lista: list[object], *remover: str) -> list[object]:
    return [item for item in lista if not (isinstance(item, dict) and item.get("valor") in remover)]


@pytest.mark.parametrize(
    ("mutacao", "trecho"),
    [
        (lambda d: d["maezo"].update(adversos=_sem(d["maezo"]["adversos"], "NEGAR")), "NEGAR"),
        (lambda d: d["maezo"].update(adversos=_sem(d["maezo"]["adversos"], "DENY", "GLOSAR")), "DENY"),
        (lambda d: d["k1"]["regras"]["exigem_classe_e_fila"].remove("negar"), "negar"),
        (lambda d: d["k1"]["regras"].update(classe_proibida=[]), "clinico"),
        (
            lambda d: d["k1"]["regras"].update(
                classe_proibida=[{"classe": "clinico", "decisoes": ["glosar"], "citacao": "x"}]
            ),
            "clinico",
        ),
    ],
)
def test_m3_dado_nao_encolhe_o_piso_do_codigo(tmp_path: Path, mutacao: object, trecho: str) -> None:
    data = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    mutacao(data)  # type: ignore[operator]
    out = tmp_path / "vocab.yaml"
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    report = _run([], out)
    assert [f.path for f in report.findings] == [out]
    assert "piso" in report.findings[0].message and trecho in report.findings[0].message


def test_m3_dado_pode_estender_o_piso(tmp_path: Path) -> None:
    data = yaml.safe_load(VOCAB.read_text(encoding="utf-8"))
    data["maezo"]["adversos"].append({"valor": "NOVO_ADVERSO", "fontes": ["teste"]})
    out = tmp_path / "vocab.yaml"
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert "NOVO_ADVERSO" in carregar(out).adversos


@pytest.mark.parametrize(
    "valor",
    [
        "INDEFERIDO",
        "INDEFERIDA",
        "NEGADA",
        "NEGADO",
        "GLOSADO",
        "GLOSADA",
        "INADMITIDO",
        "INADMITIDA",
        "DEFERIDO_PARCIAL",
        "RESCISAO",
        "SUSPENSAO",
    ],
)
def test_m4_formas_derivadas_sao_adversas(tmp_path: Path, valor: str) -> None:
    assert valor in carregar(VOCAB).adversos
    path = _maezo(tmp_path, "x.dmn", ["status"], [[f'"{valor.lower()}"']])
    assert "valor adverso" in _messages(_run([path]))

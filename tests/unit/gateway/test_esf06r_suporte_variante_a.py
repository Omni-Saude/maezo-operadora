"""E-SF-06r — suporte A5 do prospect, variante (a): estado fechado afirmado, nada ligado.

O que esta linha NÃO faz: não cria parâmetro de prazo do lead não convertido em `src/` (nem vazio,
nem com valor), não liga nada do caminho de suporte, não muda vocabulário de recusa e não toca
`src/`, `spec/` nem contrato. Enquanto o registro de admissão A5 não existir e a matriz de retenção
não for ratificada, o estado abaixo é o que vale, e qualquer desvio quebra um teste daqui.

Cada detector estático tem autoteste: a mesma função que varre o estado real é exercitada contra uma
entrada sintética violadora e tem de acusá-la. Guardas que já pinam partes deste estado noutros
arquivos são referenciadas, não duplicadas (`test_e_sf_06r_reused_guards_are_still_present`).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
import yaml

from maezo.gateway.commercial_support_admission import A5_ADMISSION_ID, ADMISSION_RECORD_SCHEMA
from maezo.platform.lifecycle.legal_bases_matrix import LEAD_CATEGORIES

_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE_RETENCAO = _REPO / "spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml"

#: Módulos do caminho do prospect (contato, suporte, custódia e retomada do lead). Padrões `glob`
#: relativos à raiz; a lista é declarada aqui para que um módulo novo do caminho entre na varredura.
_CAMINHO_DO_PROSPECT: tuple[str, ...] = (
    "src/maezo/gateway/commercial_*.py",
    "src/maezo/adapters/amh/commercial*.py",
    "src/maezo/gateway/communications/lead_custody.py",
    "src/maezo/gateway/communications/recovery_dispatch.py",
)

#: Referência à norma de atendimento que não alcança o prospect (RN 623/2024), em qualquer grafia.
_RN_623 = re.compile(r"(?i)\bRN[\s._-]*n?[ºo°.]*\s*623\b|\b623[/_-]2024\b")

#: Nome de constante de módulo que carregaria um prazo do lead não convertido.
_NOME_DE_PRAZO_DO_LEAD = re.compile(
    r"(?i)(lead|prospect|nao_convertid|not_converted|unconverted)"
    r".*(prazo|reten|ttl|dias|days|expir)"
    r"|(prazo|reten|ttl|dias|days|expir).*(lead|prospect|nao_convertid|not_converted|unconverted)"
)


# --------------------------------------------------------------------------- detectores


def _modulos_do_prospect(raiz: Path) -> list[Path]:
    achados: set[Path] = set()
    for padrao in _CAMINHO_DO_PROSPECT:
        achados.update(raiz.glob(padrao))
    return sorted(achados)


def _citacoes_rn_623(arquivos: Iterable[Path]) -> list[str]:
    problemas: list[str] = []
    for arquivo in arquivos:
        for numero, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), start=1):
            if _RN_623.search(linha):
                problemas.append(f"{arquivo.name}:{numero}")
    return problemas


def _constantes_de_prazo_do_lead(fonte: str) -> list[str]:
    """Nomes de atribuição no corpo do módulo que casam com um prazo do lead não convertido."""
    nomes: list[str] = []
    for no in ast.parse(fonte).body:
        alvos: list[ast.expr] = []
        if isinstance(no, ast.Assign):
            alvos = list(no.targets)
        elif isinstance(no, ast.AnnAssign):
            alvos = [no.target]
        for alvo in alvos:
            if isinstance(alvo, ast.Name) and _NOME_DE_PRAZO_DO_LEAD.search(alvo.id):
                nomes.append(alvo.id)
    return nomes


def _python_versionado_em_src() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files", "-z", "src/*.py"],
        cwd=_REPO,
        check=True,
        capture_output=True,
    ).stdout
    return [_REPO / p for p in saida.decode("utf-8").split("\0") if p]


def _retencoes_de_lead_com_valor(linhas: Iterable[Mapping[str, object]]) -> list[str]:
    """Categorias de lead cuja retenção não é marcador vazio (traria dígito ou não seria PLACEHOLDER)."""
    problemas: list[str] = []
    for linha in linhas:
        categoria = str(linha.get("categoria", ""))
        if not any(categoria.endswith(c) for c in LEAD_CATEGORIES):
            continue
        retencao = str(linha.get("retencao", ""))
        if not retencao.startswith("PLACEHOLDER-") or re.search(r"\d", retencao):
            problemas.append(categoria)
    return problemas


def _linhas_do_template(texto: str) -> list[Mapping[str, object]]:
    dados = yaml.safe_load(texto)
    encontradas: list[Mapping[str, object]] = []

    def visitar(no: object) -> None:
        if isinstance(no, dict):
            if "categoria" in no and "retencao" in no:
                encontradas.append(no)
            for valor in no.values():
                visitar(valor)
        elif isinstance(no, list):
            for item in no:
                visitar(item)

    visitar(dados)
    return encontradas


def _e_registro_a5(objeto: object) -> bool:
    return isinstance(objeto, dict) and objeto.get("admission_id") == A5_ADMISSION_ID


def _contem_registro_a5(objeto: object) -> bool:
    if _e_registro_a5(objeto):
        return True
    if isinstance(objeto, dict):
        return any(_contem_registro_a5(v) for v in objeto.values())
    if isinstance(objeto, list):
        return any(_contem_registro_a5(v) for v in objeto)
    return False


def _registros_a5_em(caminhos: Iterable[Path]) -> list[Path]:
    achados: list[Path] = []
    for caminho in caminhos:
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if _contem_registro_a5(dados):
            achados.append(caminho)
    return achados


def _json_versionados() -> list[Path]:
    saida = subprocess.run(
        ["git", "ls-files", "-z", "*.json"],
        cwd=_REPO,
        check=True,
        capture_output=True,
    ).stdout
    return [_REPO / p for p in saida.decode("utf-8").split("\0") if p]


# --------------------------------------------------------------------------- autotestes


def test_detector_de_rn_623_acusa_citacao(tmp_path: Path) -> None:
    violador = tmp_path / "v.py"
    violador.write_text(
        'X = "prazo da RN 623"\nY = "RN nº 623/2024"\nZ = "rn-623-2024.md"\n', encoding="utf-8"
    )
    limpo = tmp_path / "l.py"
    limpo.write_text('A = "RN 413"\nB = 16230\n', encoding="utf-8")
    assert _citacoes_rn_623([violador]) == ["v.py:1", "v.py:2", "v.py:3"]
    assert _citacoes_rn_623([limpo]) == []


def test_detector_de_constante_de_prazo_acusa_fonte_violadora() -> None:
    fonte = (
        "PRAZO_LEAD_NAO_CONVERTIDO = None\n"
        "LEAD_RETENTION_DAYS: int = 90\n"
        "DIAS_DE_GUARDA_DO_PROSPECT = 1\n"
        "LEAD_CATEGORIES = ()\n"
        "GUARDA_DE_PROSPECT_NO_ACESSO = False\n"
        "def f():\n    LEAD_TTL = 1\n"
    )
    assert _constantes_de_prazo_do_lead(fonte) == [
        "PRAZO_LEAD_NAO_CONVERTIDO",
        "LEAD_RETENTION_DAYS",
        "DIAS_DE_GUARDA_DO_PROSPECT",
    ]


def test_detector_de_retencao_acusa_valor_no_template() -> None:
    linhas = [
        {"categoria": "PLACEHOLDER-CATEGORIA-lead_intake", "retencao": "PLACEHOLDER-RETENCAO-x"},
        {"categoria": "PLACEHOLDER-CATEGORIA-lead_cotacao_jornada", "retencao": "90 dias"},
        {"categoria": "PLACEHOLDER-CATEGORIA-supressao_lead", "retencao": "PLACEHOLDER-90-dias"},
        {"categoria": "PLACEHOLDER-CATEGORIA-evidencia_fraude", "retencao": "5 anos"},
    ]
    assert _retencoes_de_lead_com_valor(linhas) == [
        "PLACEHOLDER-CATEGORIA-lead_cotacao_jornada",
        "PLACEHOLDER-CATEGORIA-supressao_lead",
    ]


def test_detector_de_registro_a5_acusa_arquivo_violador(tmp_path: Path) -> None:
    aninhado = tmp_path / "a.json"
    aninhado.write_text(
        json.dumps({"x": [{"schema": ADMISSION_RECORD_SCHEMA, "admission_id": A5_ADMISSION_ID}]}),
        encoding="utf-8",
    )
    limpo = tmp_path / "b.json"
    limpo.write_text(json.dumps({"a_id": A5_ADMISSION_ID, "admission_id": "A6"}), encoding="utf-8")
    ilegivel = tmp_path / "c.json"
    ilegivel.write_text("{", encoding="utf-8")
    assert _registros_a5_em([aninhado, limpo, ilegivel]) == [aninhado]


# --------------------------------------------------------------------------- estado fechado


def test_caminho_do_prospect_existe_na_varredura() -> None:
    nomes = {p.name for p in _modulos_do_prospect(_REPO)}
    assert {"commercial_admission.py", "commercial_support_admission.py", "lead_custody.py"} <= nomes


def test_caminho_do_prospect_nao_cita_rn_623() -> None:
    assert _citacoes_rn_623(_modulos_do_prospect(_REPO)) == []


def test_nenhum_prazo_do_lead_nao_convertido_em_src() -> None:
    arquivos = _python_versionado_em_src()
    assert arquivos, "git ls-files não listou src/"
    achados = {
        p.relative_to(_REPO).as_posix(): nomes
        for p in arquivos
        if (nomes := _constantes_de_prazo_do_lead(p.read_text(encoding="utf-8")))
    }
    assert achados == {}


def test_template_de_retencao_sem_prazo_para_lead() -> None:
    linhas = _linhas_do_template(_TEMPLATE_RETENCAO.read_text(encoding="utf-8"))
    categorias = {str(linha["categoria"]) for linha in linhas}
    for categoria in LEAD_CATEGORIES:
        assert f"PLACEHOLDER-CATEGORIA-{categoria}" in categorias
    assert _retencoes_de_lead_com_valor(linhas) == []


def test_nenhum_registro_de_admissao_a5_versionado() -> None:
    arquivos = _json_versionados()
    assert arquivos, "git ls-files não listou nenhum JSON"
    assert _registros_a5_em(arquivos) == []


# --------------------------------------------------------------------------- reuso

#: Guardas que já pinam partes deste estado (arquivo -> nomes). Referenciadas, não duplicadas.
_REUSED: Mapping[str, frozenset[str]] = {
    "tests/unit/gateway/test_esf06_commercial_support_protocol.py": frozenset(
        {
            "test_protocol_ref_exists_only_with_op07_receipt",
            "test_prospect_contact_protocol_first_is_gap_at_c64_pin",
            "test_prospect_no_sla_claim_before_q1",
        }
    ),
    "tests/unit/gateway/test_esf06_refusal_vocabulary.py": frozenset(
        {
            "test_refusal_vocabulary_closed_and_non_clinical",
            "test_refusal_enums_have_no_health_age_disability_tokens",
            "test_admission_refusal_literals_have_no_health_age_disability_tokens",
        }
    ),
    "tests/unit/gateway/test_esf06_refusal_autonomy.py": frozenset(
        {"test_merit_refusal_requires_human_review"}
    ),
    "tests/unit/test_commercial_admission_a5.py": frozenset(
        {
            "test_default_and_support_only_compositions_never_bind",
            "test_without_a5_no_support_or_work_command_reaches_the_source",
        }
    ),
    "tests/unit/platform/test_legal_bases_matrix.py": frozenset(
        {
            "test_intake_refused_without_ratified_retention_matrix",
            "test_template_lists_the_six_lead_categories_as_placeholders",
        }
    ),
}

_MARCAS_QUE_DESLIGAM = ("mark.skip", "mark.skipif", "mark.xfail")


def _desligada(funcao: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorador in funcao.decorator_list:
        alvo = decorador.func if isinstance(decorador, ast.Call) else decorador
        if ast.unparse(alvo).endswith(_MARCAS_QUE_DESLIGAM):
            return True
    return False


def _testes_ativos(caminho: Path) -> frozenset[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return frozenset(
        n.name
        for n in arvore.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.name.startswith("test_")
        and not _desligada(n)
    )


def test_detector_de_reuso_acusa_guarda_ausente_ou_desligada(tmp_path: Path) -> None:
    arquivo = tmp_path / "t.py"
    arquivo.write_text(
        "import pytest\n"
        "def test_a() -> None:\n    def test_aninhado() -> None: ...\n"
        "@pytest.mark.skip(reason='x')\ndef test_b() -> None: ...\n"
        "@pytest.mark.xfail\nasync def test_c() -> None: ...\n"
        "@pytest.mark.parametrize('v', [1])\ndef test_d(v: int) -> None: ...\n",
        encoding="utf-8",
    )
    assert _testes_ativos(arquivo) == {"test_a", "test_d"}


@pytest.mark.parametrize("relativo", sorted(_REUSED))
def test_e_sf_06r_reused_guards_are_still_present(relativo: str) -> None:
    faltando = _REUSED[relativo] - _testes_ativos(_REPO / relativo)
    assert not faltando, f"{relativo}: guardas reusadas sumiram ou foram desligadas: {sorted(faltando)}"

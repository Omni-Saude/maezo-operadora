"""Guarda R-115 (F-146 / F-DL-09): toda linha de DL marcada `parcial` aponta um teste localizavel.

A auditoria forense de 2026-10 (RTM `raw/rtm-dl.csv`, tag f57f0821) deixou 60 linhas de regra de DL
como `parcial`: o codigo existe, mas a DL nao cita teste e o grep do simbolo nao achou um. O registro
`docs/dl-runtime-evidence.yaml` fecha esse rastro SEM tocar o texto do `docs/decisions-log.md` (o
formato do log depende de decisoes do dono ainda abertas, R-048/R-094). Esta guarda exige que:

- o registro cubra EXATAMENTE o universo das 60 linhas da auditoria (nem some, nem invente linha);
- cada DL citada exista no `docs/decisions-log.md`;
- cada linha seja `teste-localizado` (com pelo menos um teste que EXISTE, nao esta marcado skip/xfail e
  contem a ancora declarada no proprio corpo — a ancora e' o que liga o teste a regra) ou
  `sem-teste-pendente` com o motivo explicito. Nenhuma terceira forma e' aceita.

A guarda e' estatica (AST/texto): prova que o teste e' LOCALIZAVEL e amarrado a regra; quem prova o
comportamento e' o proprio teste apontado, que roda na suite normal.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).parents[3]
_REGISTRO = _REPO_ROOT / "docs" / "dl-runtime-evidence.yaml"
_DECISIONS_LOG = _REPO_ROOT / "docs" / "decisions-log.md"

_ESTADOS = frozenset({"teste-localizado", "sem-teste-pendente"})
_MOTIVO_MINIMO = 40  # um motivo de pendencia tem de dizer POR QUE, nao so' "pendente"

# O universo vem da auditoria (RTM rtm-dl.csv, status `parcial`, 60 linhas). Fica congelado AQUI, e nao
# so' no registro, para que apagar uma linha do registro reprove em vez de encolher o universo.
_UNIVERSO_F146: frozenset[str] = frozenset(
    {
        "DL-0047/regra/1",
        "DL-0048/regra/1",
        "DL-0055/regra/1",
        "DL-0057/regra/1",
        "DL-0072/regra/1",
        "DL-0085/regra/1",
        "DL-0088/regra/2",
        "DL-0089/regra/1",
        "DL-0090/regra/1",
        "DL-0091/regra/1",
        "DL-0092/regra/1",
        "DL-0093/regra/1",
        "DL-0094/regra/1",
        "DL-0095/regra/1",
        "DL-0096/regra/1",
        "DL-0097/regra/1",
        "DL-0099/regra/1",
        "DL-0100/regra/1",
        "DL-0105/regra/1",
        "DL-0106/regra/1",
        "DL-0108/regra/1",
        "DL-0113/regra/1",
        "DL-0114/regra/1",
        "DL-0115/regra/1",
        "DL-0115/regra/2",
        "DL-0117/regra/1",
        "DL-0117/regra/4",
        "DL-0119/regra/1",
        "DL-0121/regra/1",
        "DL-0121/regra/2",
        "DL-0123/regra/2",
        "DL-0124/regra/1",
        "DL-0124/regra/2",
        "DL-0125/regra/1",
        "DL-0131/regra/1",
        "DL-0132/regra/1",
        "DL-0135/regra/1",
        "DL-0136/regra/1",
        "DL-0136/regra/3",
        "DL-0136/regra/4",
        "DL-0137/regra/1",
        "DL-0138/regra/1",
        "DL-0139/regra/1",
        "DL-0140/regra/1",
        "DL-0141/regra/1",
        "DL-0148/regra/1",
        "DL-0154/regra/3",
        "DL-0154/regra/5",
        "DL-0154/regra/6",
        "DL-0154/regra/7",
        "DL-0154/regra/10",
        "DL-0154/regra/11",
        "DL-0154/regra/12",
        "DL-0154/regra/13",
        "DL-0154/regra/14",
        "DL-0154/regra/16",
        "DL-0154/regra/17",
        "DL-0154/regra/18",
        "DL-0154/regra/19",
        "DL-0154/regra/20",
    }
)

_ID_RE = re.compile(r"(DL-\d{4})/regra/\d+")
_HCL_RUN_RE = r'^run\s+"{nome}"\s*\{{'


class _RecusaDuplicada(yaml.SafeLoader):
    """SafeLoader que recusa chave repetida (a segunda ocorrencia apagaria a primeira em silencio)."""


def _mapeamento_sem_duplicata(loader: _RecusaDuplicada, node: yaml.MappingNode) -> dict[Any, Any]:
    vistos: set[Any] = set()
    for chave_node, _ in node.value:
        chave = loader.construct_object(chave_node)
        if chave in vistos:
            raise yaml.constructor.ConstructorError(
                None, None, f"chave repetida: {chave!r}", chave_node.start_mark
            )
        vistos.add(chave)
    return loader.construct_mapping(node)


_RecusaDuplicada.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapeamento_sem_duplicata)


def _carregar_registro() -> dict[str, Any]:
    dados = yaml.load(_REGISTRO.read_text(encoding="utf-8"), Loader=_RecusaDuplicada)  # SafeLoader endurecido
    assert isinstance(dados, dict), "o registro e' um mapeamento"
    return dados


def _fonte_da_funcao_python(caminho: Path, nome: str) -> tuple[str, list[str]] | None:
    """Fonte da funcao de teste (com os decoradores, onde vivem os parametros) e as marcas que a alcancam
    (decoradores da funcao e o `pytestmark` do modulo)."""
    fonte = caminho.read_text(encoding="utf-8")
    linhas = fonte.splitlines()
    arvore = ast.parse(fonte)
    marcas_do_modulo = [
        ast.get_source_segment(fonte, no) or ""
        for no in arvore.body
        if isinstance(no, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in no.targets)
    ]
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == nome:
            inicio = min([no.lineno, *(d.lineno for d in no.decorator_list)])
            fim = no.end_lineno or no.lineno
            decoradores = [ast.get_source_segment(fonte, d) or "" for d in no.decorator_list]
            return "\n".join(linhas[inicio - 1 : fim]), [*decoradores, *marcas_do_modulo]
    return None


def _fonte_do_run_hcl(caminho: Path, nome: str) -> str | None:
    texto = caminho.read_text(encoding="utf-8")
    achado = re.search(_HCL_RUN_RE.format(nome=re.escape(nome)), texto, re.MULTILINE)
    if achado is None:
        return None
    proximo = re.search(r"^run\s+\"", texto[achado.end() :], re.MULTILINE)
    return texto[achado.start() : achado.end() + proximo.start()] if proximo else texto[achado.start() :]


def _erros_da_evidencia(evidencia: Any, raiz: Path) -> list[str]:
    if not isinstance(evidencia, dict) or set(evidencia) != {"teste", "ancora"}:
        return [f"evidencia tem de ser {{teste, ancora}}: {evidencia!r}"]
    teste, ancora = evidencia["teste"], evidencia["ancora"]
    if not isinstance(teste, str) or teste.count("::") != 1:
        return [f"teste tem de ser `caminho::nome`: {teste!r}"]
    if not isinstance(ancora, str) or len(ancora.strip()) < 3:
        return [f"{teste}: ancora vazia ou curta demais: {ancora!r}"]
    relativo, nome = teste.split("::")
    if not (relativo.startswith("tests/") or (relativo.startswith("deploy/") and "/tests/" in relativo)):
        return [f"{teste}: o teste tem de viver em tests/ (ou nos testes do terraform em deploy/**/tests/)"]
    caminho = raiz / relativo
    if not caminho.is_file():
        return [f"{teste}: arquivo inexistente"]
    if caminho.suffix == ".py":
        if not Path(relativo).name.startswith("test_") or not nome.startswith("test"):
            return [f"{teste}: nao e' um teste que o pytest coleta"]
        achado = _fonte_da_funcao_python(caminho, nome)
        if achado is None:
            return [f"{teste}: funcao de teste inexistente"]
        corpo, decoradores = achado
        if any(re.search(r"\b(skip|skipif|xfail)\b", d) for d in decoradores):
            return [f"{teste}: teste marcado skip/xfail nao e' evidencia"]
    elif caminho.name.endswith(".tftest.hcl"):
        corpo_hcl = _fonte_do_run_hcl(caminho, nome)
        if corpo_hcl is None:
            return [f"{teste}: bloco run inexistente"]
        corpo = corpo_hcl
    else:
        return [f"{teste}: tipo de teste nao reconhecido"]
    if ancora not in corpo:
        return [f"{teste}: a ancora {ancora!r} nao aparece no corpo do teste"]
    return []


def _erros_da_linha(linha: Any, raiz: Path) -> list[str]:
    if not isinstance(linha, dict):
        return [f"linha nao e' mapeamento: {linha!r}"]
    ident = linha.get("id", "?")
    estado = linha.get("estado")
    if estado not in _ESTADOS:
        return [f"{ident}: estado {estado!r} fora de {sorted(_ESTADOS)}"]
    if not isinstance(linha.get("regra"), str) or not linha["regra"].strip():
        return [f"{ident}: falta o texto da regra"]
    if estado == "sem-teste-pendente":
        erros = []
        if linha.get("runtime_evidence"):
            erros.append(f"{ident}: pendente nao carrega runtime_evidence")
        motivo = linha.get("motivo")
        if not isinstance(motivo, str) or len(motivo.strip()) < _MOTIVO_MINIMO:
            erros.append(f"{ident}: pendente exige motivo explicito (>= {_MOTIVO_MINIMO} caracteres)")
        return erros
    evidencias = linha.get("runtime_evidence")
    if not isinstance(evidencias, list) or not evidencias:
        return [f"{ident}: teste-localizado exige ao menos um runtime_evidence"]
    erros = [f"{ident}: {e}" for ev in evidencias for e in _erros_da_evidencia(ev, raiz)]
    if "lacuna" in linha and (not isinstance(linha["lacuna"], str) or not linha["lacuna"].strip()):
        erros.append(f"{ident}: lacuna declarada vazia")
    return erros


def _dls_do_log() -> set[str]:
    texto = _DECISIONS_LOG.read_text(encoding="utf-8")
    return set(re.findall(r"^\|[^|]*\|\s*(DL-\d{4})\b", texto, re.MULTILINE))


def test_dl_runtime_evidence_registro_existe_e_cobre_exatamente_o_universo_da_auditoria() -> None:
    assert _REGISTRO.is_file(), f"{_REGISTRO.relative_to(_REPO_ROOT)} nao existe (R-115 / F-146)"
    regras = _carregar_registro().get("regras")
    assert isinstance(regras, list)
    ids = [linha.get("id") for linha in regras if isinstance(linha, dict)]
    assert len(ids) == len(set(ids)), "id repetido no registro"
    assert set(ids) == _UNIVERSO_F146, {
        "faltam": sorted(_UNIVERSO_F146 - set(ids)),
        "sobram": sorted(set(ids) - _UNIVERSO_F146),
    }


def test_dl_runtime_evidence_toda_dl_citada_existe_no_decisions_log() -> None:
    no_log = _dls_do_log()
    ausentes = sorted({m.group(1) for i in _UNIVERSO_F146 if (m := _ID_RE.fullmatch(i))} - no_log)
    assert not ausentes, ausentes


def test_dl_runtime_evidence_toda_linha_aponta_teste_localizavel_ou_pendencia_explicita() -> None:
    erros = [e for linha in _carregar_registro()["regras"] for e in _erros_da_linha(linha, _REPO_ROOT)]
    assert not erros, "\n".join(erros)


# --- a guarda nao e' vacua: cada forma de mapeamento falso reprova ---------------------------------

_TESTE_REAL = (
    "tests/unit/docs/test_dl_runtime_evidence.py::"
    "test_dl_runtime_evidence_registro_existe_e_cobre_exatamente_o_universo_da_auditoria"
)


@pytest.mark.parametrize(
    ("linha", "trecho"),
    [
        ({"id": "X", "regra": "r", "estado": "ok"}, "estado"),
        ({"id": "X", "regra": "r", "estado": "teste-localizado"}, "ao menos um"),
        ({"id": "X", "regra": "r", "estado": "teste-localizado", "runtime_evidence": []}, "ao menos um"),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "teste-localizado",
                "runtime_evidence": [{"teste": "tests/unit/nao_existe/test_x.py::test_y", "ancora": "abc"}],
            },
            "arquivo inexistente",
        ),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "teste-localizado",
                "runtime_evidence": [
                    {"teste": "tests/unit/docs/test_dl_runtime_evidence.py::test_nao_existe", "ancora": "abc"}
                ],
            },
            "funcao de teste inexistente",
        ),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "teste-localizado",
                "runtime_evidence": [
                    {"teste": _TESTE_REAL, "ancora": "texto que nao esta no corpo do teste"}
                ],
            },
            "nao aparece no corpo",
        ),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "teste-localizado",
                "runtime_evidence": [{"teste": "src/maezo/__init__.py::test_x", "ancora": "abc"}],
            },
            "tem de viver em tests/",
        ),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "teste-localizado",
                "runtime_evidence": [{"teste": _TESTE_REAL}],
            },
            "{teste, ancora}",
        ),
        ({"id": "X", "regra": "r", "estado": "sem-teste-pendente"}, "motivo explicito"),
        ({"id": "X", "regra": "r", "estado": "sem-teste-pendente", "motivo": "pendente"}, "motivo explicito"),
        (
            {
                "id": "X",
                "regra": "r",
                "estado": "sem-teste-pendente",
                "motivo": "m" * _MOTIVO_MINIMO,
                "runtime_evidence": [{"teste": _TESTE_REAL, "ancora": "_REGISTRO"}],
            },
            "nao carrega runtime_evidence",
        ),
        ({"id": "X", "estado": "sem-teste-pendente", "motivo": "m" * _MOTIVO_MINIMO}, "texto da regra"),
    ],
)
def test_dl_runtime_evidence_guarda_reprova_mapeamento_falso(linha: dict[str, Any], trecho: str) -> None:
    erros = _erros_da_linha(linha, _REPO_ROOT)
    assert erros and any(trecho in e for e in erros), erros


def test_dl_runtime_evidence_guarda_reprova_teste_marcado_skip(tmp_path: Path) -> None:
    alvo = tmp_path / "tests" / "unit" / "test_falso.py"
    alvo.parent.mkdir(parents=True)
    alvo.write_text(
        'import pytest\n\n\n@pytest.mark.skip(reason="x")\ndef test_falso() -> None:\n    assert "ancora"\n'
    )
    evidencia = {"teste": "tests/unit/test_falso.py::test_falso", "ancora": "ancora"}
    assert any("skip/xfail" in e for e in _erros_da_evidencia(evidencia, tmp_path))
    alvo.write_text(
        "import pytest\n\npytestmark = pytest.mark.xfail\n\n\n"
        'def test_falso() -> None:\n    assert "ancora"\n'
    )
    assert any("skip/xfail" in e for e in _erros_da_evidencia(evidencia, tmp_path))


def test_dl_runtime_evidence_guarda_aceita_teste_real_e_pendencia_explicita() -> None:
    real = {"teste": _TESTE_REAL, "ancora": "_UNIVERSO_F146"}
    assert _erros_da_evidencia(real, _REPO_ROOT) == []
    pendente = {"id": "X", "regra": "r", "estado": "sem-teste-pendente", "motivo": "m" * _MOTIVO_MINIMO}
    assert _erros_da_linha(pendente, _REPO_ROOT) == []


def test_dl_runtime_evidence_guarda_le_run_do_terraform(tmp_path: Path) -> None:
    alvo = tmp_path / "deploy" / "env" / "tests" / "x.tftest.hcl"
    alvo.parent.mkdir(parents=True)
    alvo.write_text(
        'run "primeiro" {\n  assert { condition = true\n error_message = "DL-0001" }\n}\n\n'
        'run "segundo" {\n}\n'
    )
    ok = {"teste": "deploy/env/tests/x.tftest.hcl::primeiro", "ancora": "DL-0001"}
    assert _erros_da_evidencia(ok, tmp_path) == []
    fora_do_bloco = {"teste": "deploy/env/tests/x.tftest.hcl::segundo", "ancora": "DL-0001"}
    assert _erros_da_evidencia(fora_do_bloco, tmp_path)
    ausente = {"teste": "deploy/env/tests/x.tftest.hcl::terceiro", "ancora": "DL-0001"}
    assert any("bloco run inexistente" in e for e in _erros_da_evidencia(ausente, tmp_path))

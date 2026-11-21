"""Tabelas do K1 pinado fora do corpus da cerca de perspectiva (adendo ao ADR-0040, Proposed; PR-A).

`tabelas_k1_pinadas` devolve os `artifacts` de um pin `fixado` que confere com o manifesto, e so eles;
qualquer divergencia devolve o conjunto vazio, e a cerca le `rco/` inteira (o comportamento de antes). Os
testes usam o pacote K1 SINTETICO (`rco_k1_sintetico`, nenhum byte do rco) e o pin comitado (`sem_pacote`).

Negativos obrigatorios: arquivo fora de `rco/` nunca sai do corpus; divergencia entre pin e manifesto (sha256,
`.dmn` nao pinado em `rco/`, `artifacts` diferente de `decisoes[]`) zera a saida; o exemplo sintetico do Maezo
nunca sai. E a prova de que a saida e a do GATE do pin: em cada mutacao, conjunto vazio <=> gate reprova.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.verify_rco_rules_pin import load_lock, verify_lock

from maezo.platform.validation import cli
from maezo.platform.validation.result import Report
from maezo.platform.validation.tabelas_k1_pinadas import LOCK_RELPATH, tabelas_k1_pinadas
from tests.unit.platform import rco_k1_sintetico as sint

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HIT = "provider-perspective vocabulary"
#: Um hit de Tier A (R1, verbo `RECORRER`) num comentario XML: a cerca le comentarios de DMN.
_PLANTADO = b"<!-- RECORRER -->\n"


def _dmn_dir(raiz: Path) -> Path:
    return raiz / "spec" / "processes" / "dmn"


def _pinadas(raiz: Path) -> frozenset[Path]:
    return tabelas_k1_pinadas(_dmn_dir(raiz), raiz / LOCK_RELPATH).caminhos


def _plantar(raiz: Path, manifesto: dict[str, Any], original: str = "sint_a") -> Path:
    """Planta o hit numa tabela do pacote e repina manifesto e lock (o pacote continua coerente)."""
    caminho = raiz / sint.RULES_DIR / f"{original}.dmn"
    raw = caminho.read_bytes()
    corpo = raw.index(b"<definitions")
    novo = raw[:corpo] + _PLANTADO + raw[corpo:]
    caminho.write_bytes(novo)
    for decisao in manifesto["decisoes"]:
        if decisao["id_original"] == original:
            decisao["sha256"] = sint.sha(novo)
    sint.regravar(raiz, manifesto)
    lock = sint.lock(raiz)
    lock["artifacts"] = [
        {"path": d["caminho_consumidor"], "sha256": d["sha256"]} for d in manifesto["decisoes"]
    ]
    sint.gravar_lock(raiz, lock)
    return caminho


def _exemplo_sintetico(raiz: Path) -> Path:
    """Um exemplo sintetico do Maezo, declarado no lock, com o mesmo hit plantado."""
    caminho = raiz / sint.RULES_DIR / "rco_exemplo_x.dmn"
    raw = sint.dmn_bytes("rco_exemplo_x", ["decisao"], [['"liberar"'], ['"liberar"']])
    corpo = raw.index(b"<definitions")
    caminho.write_bytes(raw[:corpo] + _PLANTADO + raw[corpo:])
    lock = sint.lock(raiz)
    lock["exemplos_sinteticos"] = [
        {"path": f"{sint.RULES_DIR}/rco_exemplo_x.dmn", "sha256": sint.sha(caminho.read_bytes())}
    ]
    sint.gravar_lock(raiz, lock)
    return caminho


@pytest.fixture
def pacote(tmp_path: Path) -> tuple[Path, dict[str, Any], Path]:
    manifesto = sint.montar(tmp_path)
    plantada = _plantar(tmp_path, manifesto)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")  # raiz para `cli._find_repo_root`
    return tmp_path, manifesto, plantada


def _gate_ok(raiz: Path) -> bool:
    """O gate do pin (`scripts/ci/verify_rco_rules_pin.py`), lido como o CLI o le."""
    lock, violacoes = load_lock(raiz / LOCK_RELPATH)
    return lock is not None and not violacoes and verify_lock(lock, repo_root=raiz) == []


def _hits_da_cerca(raiz: Path) -> set[Path]:
    """Arquivos com hit da cerca quando `make validate-artifacts` valida `spec/processes` da raiz."""
    report = Report()
    cli._validate_processes_root(raiz / "spec" / "processes", report)
    return {f.path for f in report.findings if _HIT in f.message}


# --- caminho feliz e estado comitado ------------------------------------------------------------------


def test_pin_fixado_coerente_tira_so_os_artifacts_do_corpus(
    pacote: tuple[Path, dict[str, Any], Path],
) -> None:
    raiz, manifesto, plantada = pacote
    assert _gate_ok(raiz)
    esperado = {_dmn_dir(raiz) / "rco" / Path(d["caminho_consumidor"]).name for d in manifesto["decisoes"]}
    assert _pinadas(raiz) == esperado
    assert plantada in esperado
    assert _hits_da_cerca(raiz) == set()


def test_a_cerca_continua_lendo_o_mesmo_arquivo_quando_ele_nao_vem_do_cli(
    pacote: tuple[Path, dict[str, Any], Path],
) -> None:
    """A cerca nao tem excecao: `check_processes_root` recebe a tabela pinada e reprova o hit."""
    from maezo.platform.validation import perspective

    raiz, _, plantada = pacote
    report = Report()
    perspective.check_processes_root(raiz / "spec" / "processes", report)
    assert plantada in {f.path for f in report.findings}


def test_o_pin_comitado_sem_pacote_nao_tira_nada_do_corpus() -> None:
    lock = json.loads((_REPO_ROOT / LOCK_RELPATH).read_text(encoding="utf-8"))
    resultado = tabelas_k1_pinadas(_dmn_dir(_REPO_ROOT), _REPO_ROOT / LOCK_RELPATH)
    if lock["estado"] == "sem_pacote":
        assert resultado.caminhos == frozenset()
        assert "sem_pacote" in resultado.motivo
    else:  # PR do pin: sai exatamente o que o pin lista
        assert {p.name for p in resultado.caminhos} == {Path(a["path"]).name for a in lock["artifacts"]}


def test_sem_pacote_o_hit_em_rco_continua_reprovando(tmp_path: Path) -> None:
    """Estado comitado: so o exemplo sintetico em `rco/`; o hit plantado nele e erro, como antes."""
    (tmp_path / sint.RULES_DIR).mkdir(parents=True)
    (tmp_path / sint.LOCK_REL).parent.mkdir(parents=True)
    sint.gravar_lock(
        tmp_path,
        {
            "lock_format": {"name": "rco-rules-lock", "rules_dir": sint.RULES_DIR, "version": "2.0.0"},
            "estado": "sem_pacote",
            "provenance": {"rco_commit_sha": None, "rco_repo": None, "status": "NAO_PUBLICADO"},
            "manifest_pin": None,
            "artifacts": [],
            "exemplos_sinteticos": [],
        },
    )
    caminho = _exemplo_sintetico(tmp_path)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert _gate_ok(tmp_path)
    assert _pinadas(tmp_path) == frozenset()
    assert caminho in _hits_da_cerca(tmp_path)


# --- negativos ----------------------------------------------------------------------------------------


def test_exemplo_sintetico_nunca_sai_do_corpus(pacote: tuple[Path, dict[str, Any], Path]) -> None:
    raiz, _, plantada = pacote
    exemplo = _exemplo_sintetico(raiz)
    assert _gate_ok(raiz)
    assert plantada in _pinadas(raiz) and exemplo not in _pinadas(raiz)
    assert _hits_da_cerca(raiz) == {exemplo}


def test_arquivo_fora_de_rco_nunca_sai_mesmo_com_os_bytes_de_um_artefato(
    pacote: tuple[Path, dict[str, Any], Path],
) -> None:
    """A copia de uma tabela pinada no primeiro nivel de `dmn/` (mesmo nome, mesmos bytes) segue na cerca."""
    raiz, _, plantada = pacote
    copia = _dmn_dir(raiz) / plantada.name
    copia.write_bytes(plantada.read_bytes())
    assert copia not in _pinadas(raiz)
    assert all(p.parent == _dmn_dir(raiz) / "rco" for p in _pinadas(raiz))
    assert copia in _hits_da_cerca(raiz)


def test_artifact_que_aponta_para_fora_de_rco_zera_a_saida(pacote: tuple[Path, dict[str, Any], Path]) -> None:
    raiz, _, plantada = pacote
    (_dmn_dir(raiz) / plantada.name).write_bytes(plantada.read_bytes())
    lock = sint.lock(raiz)
    lock["artifacts"][0]["path"] = f"spec/processes/dmn/{Path(lock['artifacts'][0]['path']).name}"
    sint.gravar_lock(raiz, lock)
    assert not _gate_ok(raiz)
    assert _pinadas(raiz) == frozenset()


def test_outra_pasta_dmn_nao_herda_o_pin(
    pacote: tuple[Path, dict[str, Any], Path], tmp_path_factory: Any
) -> None:
    """Uma copia de `spec/processes/dmn` em outro lugar, lida com o pin original, nao sai do corpus."""
    import shutil

    raiz, _, _ = pacote
    outra = tmp_path_factory.mktemp("outra") / "dmn"
    shutil.copytree(_dmn_dir(raiz), outra)
    resultado = tabelas_k1_pinadas(outra, raiz / LOCK_RELPATH)
    assert resultado.caminhos == frozenset()
    assert "repositorio do pin" in resultado.motivo


def _mudar_lock(**campos: Any) -> Callable[[Path, dict[str, Any]], None]:
    def aplicar(raiz: Path, _manifesto: dict[str, Any]) -> None:
        lock = sint.lock(raiz)
        lock.update(campos)
        sint.gravar_lock(raiz, lock)

    return aplicar


def _sha_do_manifesto_divergente(raiz: Path, _manifesto: dict[str, Any]) -> None:
    lock = sint.lock(raiz)
    lock["manifest_pin"]["sha256"] = sint.sha(b"outro manifesto")
    sint.gravar_lock(raiz, lock)


def _manifesto_trocado_sem_repinar(raiz: Path, _manifesto: dict[str, Any]) -> None:
    caminho = raiz / sint.K1_DIR / "manifesto.json"
    caminho.write_bytes(caminho.read_bytes().replace(b'"versao_pacote":"1.0.0"', b'"versao_pacote":"1.0.1"'))


def _artefato_adulterado(raiz: Path, _manifesto: dict[str, Any]) -> None:
    caminho = raiz / sint.RULES_DIR / "sint_b.dmn"
    caminho.write_bytes(caminho.read_bytes() + b"\n")


def _artefato_repinado_so_no_lock(raiz: Path, _manifesto: dict[str, Any]) -> None:
    """O lock acompanha o arquivo adulterado, o manifesto nao: `artifacts` != `decisoes[]`."""
    caminho = raiz / sint.RULES_DIR / "sint_b.dmn"
    caminho.write_bytes(caminho.read_bytes() + b"\n")
    lock = sint.lock(raiz)
    for item in lock["artifacts"]:
        if item["path"].endswith("/sint_b.dmn"):
            item["sha256"] = sint.sha(caminho.read_bytes())
    sint.gravar_lock(raiz, lock)


def _artefato_a_menos(raiz: Path, _manifesto: dict[str, Any]) -> None:
    lock = sint.lock(raiz)
    lock["artifacts"] = lock["artifacts"][:1]
    sint.gravar_lock(raiz, lock)


def _dmn_nao_pinado_em_rco(raiz: Path, _manifesto: dict[str, Any]) -> None:
    (raiz / sint.RULES_DIR / "rco_solto.dmn").write_bytes(
        sint.dmn_bytes("rco_solto", ["decisao"], [['"liberar"'], ['"liberar"']])
    )


def _arquivo_solto_em_k1(raiz: Path, _manifesto: dict[str, Any]) -> None:
    (raiz / sint.K1_DIR / "solto.json").write_text("{}", encoding="utf-8")


def _exemplo_com_sha_divergente(raiz: Path, _manifesto: dict[str, Any]) -> None:
    caminho = _exemplo_sintetico(raiz)
    caminho.write_bytes(caminho.read_bytes() + b"\n")


def _pin_ilegivel(raiz: Path, _manifesto: dict[str, Any]) -> None:
    (raiz / LOCK_RELPATH).write_text("{", encoding="utf-8")


def _pin_com_chave_repetida(raiz: Path, _manifesto: dict[str, Any]) -> None:
    texto = (raiz / LOCK_RELPATH).read_text(encoding="utf-8")
    (raiz / LOCK_RELPATH).write_text(
        texto.replace('"estado": "fixado"', '"estado": "fixado", "estado": "fixado"'), encoding="utf-8"
    )


#: Cada divergencia entre pin, manifesto e arvore. Todas zeram a saida E reprovam o gate.
DIVERGENCIAS: dict[str, Callable[[Path, dict[str, Any]], None]] = {
    "estado sem_pacote": _mudar_lock(estado="sem_pacote"),
    "estado desconhecido": _mudar_lock(estado="publicado"),
    "manifest_pin com outro sha256": _sha_do_manifesto_divergente,
    "manifesto trocado sem repinar": _manifesto_trocado_sem_repinar,
    "manifest_pin nulo": _mudar_lock(manifest_pin=None),
    "provenance divergente": _mudar_lock(
        provenance={"rco_commit_sha": "0" * 40, "rco_repo": sint.REPO, "status": "PUBLISHED"}
    ),
    "artefato adulterado": _artefato_adulterado,
    "artifacts diferente de decisoes[]": _artefato_repinado_so_no_lock,
    "artefato a menos no lock": _artefato_a_menos,
    "dmn nao pinado em rco/": _dmn_nao_pinado_em_rco,
    "arquivo solto em k1/": _arquivo_solto_em_k1,
    "exemplo sintetico com sha256 divergente": _exemplo_com_sha_divergente,
    "pin ilegivel": _pin_ilegivel,
    "pin com chave repetida": _pin_com_chave_repetida,
}


@pytest.mark.parametrize("nome", sorted(DIVERGENCIAS))
def test_divergencia_do_pin_zera_a_saida_e_o_gate_reprova(
    pacote: tuple[Path, dict[str, Any], Path], nome: str
) -> None:
    raiz, manifesto, plantada = pacote
    assert _gate_ok(raiz) and _pinadas(raiz)
    DIVERGENCIAS[nome](raiz, manifesto)
    assert _pinadas(raiz) == frozenset(), nome
    assert not _gate_ok(raiz), nome
    assert plantada in _hits_da_cerca(raiz), nome  # a cerca volta a ler a tabela pinada


def test_pin_sem_provas_zera_a_saida(tmp_path: Path) -> None:
    """Formato 2.0.0 (sem `provas[]`): o gate recusa o pin, e a saida fica vazia."""
    sint.montar(tmp_path, provas=False)
    assert not _gate_ok(tmp_path)
    assert _pinadas(tmp_path) == frozenset()


def test_o_modulo_nao_le_arquivo_proprio_de_configuracao() -> None:
    """Sem lista editavel: o unico arquivo que o modulo nomeia e o pin (o manifesto vem do leitor K1)."""
    import ast

    fonte = (_REPO_ROOT / "src" / "maezo" / "platform" / "validation" / "tabelas_k1_pinadas.py").read_text(
        encoding="utf-8"
    )
    arvore = ast.parse(fonte)
    docstrings = {
        ast.get_docstring(n, clean=False)
        for n in ast.walk(arvore)
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef)
    }
    literais = {
        n.value for n in ast.walk(arvore) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    } - docstrings
    nomeados = {lit for lit in literais if lit.endswith((".yaml", ".yml", ".json", ".toml", ".txt", ".csv"))}
    assert nomeados == {"rules.lock.json"}

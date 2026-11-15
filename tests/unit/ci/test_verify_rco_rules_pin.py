"""Testes do gate do pin do pacote K1 do rco (ADR-0075 secao b, MZO-1d).

Duas camadas:

1. **Nucleo puro + CLI** contra arvores sinteticas em `tmp_path`: um manifesto de EXEMPLO e DMNs
   de EXEMPLO (nenhum byte real do rco). Verde no caminho feliz; vermelho em DMN adulterado, DMN a
   mais nao listado, arquivo listado ausente, pin malformado e estado `sem_pacote` com DMN solto.
2. **Arvore real**: o pin comitado passa contra a arvore deste checkout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.verify_rco_rules_pin import RULES_DIR, main, verify_lock

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCK_REL = "config/integrations/rco/rules.lock.json"

_EXAMPLE_DMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="{id}" name="{id}"
  namespace="http://example.invalid/rco-teste">
  <decision id="{id}" name="{id}">
    <decisionTable id="{id}_tabela" hitPolicy="UNIQUE">
      <input id="{id}_in"><inputExpression typeRef="string"><text>codigo</text></inputExpression></input>
      <output id="{id}_out" name="decisao" typeRef="string" />
      <rule id="{id}_r1"><inputEntry><text>-</text></inputEntry>
        <outputEntry><text>"EXEMPLO"</text></outputEntry></rule>
    </decisionTable>
  </decision>
</definitions>
"""

_COMMIT = "0" * 39 + "1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_dmn(root: Path, name: str) -> Path:
    path = root / RULES_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXAMPLE_DMN.format(id=path.stem), encoding="utf-8")
    return path


def _entry(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}


def _base_lock(estado: str) -> dict[str, Any]:
    return {
        "lock_format": {"name": "rco-rules-lock", "rules_dir": RULES_DIR, "version": "1.0.0"},
        "estado": estado,
        "provenance": {"rco_commit_sha": None, "rco_repo": None, "status": "NAO_PUBLICADO"},
        "manifest_pin": None,
        "artifacts": [],
        "exemplos_sinteticos": [],
    }


@pytest.fixture
def pinned_tree(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Arvore com manifesto de exemplo + 2 DMNs de exemplo, pin em estado `fixado`."""
    manifest = tmp_path / RULES_DIR / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"pacote": "K1-exemplo", "versaoRegra": "0.0.0-teste"}), encoding="utf-8")
    (tmp_path / RULES_DIR / "README.md").write_text("# rco (teste)\n", encoding="utf-8")
    a = _write_dmn(tmp_path, "rco_exemplo_a.dmn")
    b = _write_dmn(tmp_path, "rco_exemplo_b.dmn")
    lock = _base_lock("fixado")
    lock["provenance"] = {"rco_commit_sha": _COMMIT, "rco_repo": "exemplo/rco-teste", "status": "PUBLISHED"}
    lock["manifest_pin"] = _entry(tmp_path, manifest)
    lock["artifacts"] = [_entry(tmp_path, a), _entry(tmp_path, b)]
    return tmp_path, lock


def _codes(lock: dict[str, Any], root: Path) -> set[str]:
    return {v.code for v in verify_lock(lock, repo_root=root)}


def _run_cli(root: Path, lock: dict[str, Any] | str) -> int:
    lock_path = root / _LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(lock if isinstance(lock, str) else json.dumps(lock), encoding="utf-8")
    return main(["--repo-root", str(root)])


def test_caminho_feliz_fixado(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    assert verify_lock(lock, repo_root=root) == []
    assert _run_cli(root, lock) == 0


def test_dmn_adulterado_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    target = root / RULES_DIR / "rco_exemplo_a.dmn"
    target.write_text(target.read_text(encoding="utf-8").replace("EXEMPLO", "ADULTERADO"), encoding="utf-8")
    assert "sha256-divergente" in _codes(lock, root)
    assert _run_cli(root, lock) == 1


def test_manifesto_adulterado_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    (root / RULES_DIR / "manifest.json").write_text("{}", encoding="utf-8")
    assert "sha256-divergente" in _codes(lock, root)


def test_dmn_extra_nao_listado_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    _write_dmn(root, "rco_intruso.dmn")
    assert _codes(lock, root) == {"dmn-nao-listado"}
    assert _run_cli(root, lock) == 1


def test_subpasta_em_rco_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    _write_dmn(root, "sub/rco_escondido.dmn")
    assert {"subpasta-nao-permitida", "dmn-nao-listado"} <= _codes(lock, root)


def test_arquivo_listado_ausente_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    (root / RULES_DIR / "rco_exemplo_b.dmn").unlink()
    assert _codes(lock, root) == {"arquivo-listado-ausente"}
    assert _run_cli(root, lock) == 1


@pytest.mark.parametrize("raw", ["", "{nao e json", "[]", '"texto"'])
def test_pin_malformado_reprova(tmp_path: Path, raw: str) -> None:
    assert _run_cli(tmp_path, raw) == 1


def test_pin_ausente_reprova(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda lk: lk.pop("artifacts"), "secao-ausente"),
        (lambda lk: lk.update(estado="talvez"), "estado-invalido"),
        (lambda lk: lk["lock_format"].update(name="outro"), "lock-nome"),
        (lambda lk: lk["provenance"].update(rco_commit_sha="abc"), "proveniencia"),
        (lambda lk: lk.update(manifest_pin=None), "manifesto"),
        (lambda lk: lk["artifacts"][0].update(sha256="XYZ"), "sha256-invalido"),
        (lambda lk: lk["artifacts"][0].update(path="../fora.dmn"), "caminho-invalido"),
        (lambda lk: lk["artifacts"][0].update(path="spec/processes/dmn/fora.dmn"), "caminho-fora-da-pasta"),
        (lambda lk: lk["artifacts"].append(dict(lk["artifacts"][0])), "entrada-duplicada"),
        (lambda lk: lk.update(artifacts=[]), "pacote-vazio"),
    ],
)
def test_campos_malformados_reprovam(
    pinned_tree: tuple[Path, dict[str, Any]], mutate: Any, code: str
) -> None:
    root, lock = pinned_tree
    mutate(lock)
    assert code in _codes(lock, root)
    assert _run_cli(root, lock) == 1


def test_sem_pacote_pasta_ausente_passa(tmp_path: Path) -> None:
    lock = _base_lock("sem_pacote")
    assert verify_lock(lock, repo_root=tmp_path) == []
    assert _run_cli(tmp_path, lock) == 0


def test_sem_pacote_so_readme_e_exemplos_declarados_passa(tmp_path: Path) -> None:
    (tmp_path / RULES_DIR).mkdir(parents=True)
    (tmp_path / RULES_DIR / "README.md").write_text("# rco\n", encoding="utf-8")
    exemplo = _write_dmn(tmp_path, "rco_exemplo_sintetico.dmn")
    lock = _base_lock("sem_pacote")
    lock["exemplos_sinteticos"] = [_entry(tmp_path, exemplo)]
    assert verify_lock(lock, repo_root=tmp_path) == []
    assert _run_cli(tmp_path, lock) == 0


def test_sem_pacote_exemplo_nao_declarado_reprova(tmp_path: Path) -> None:
    _write_dmn(tmp_path, "rco_exemplo_sintetico.dmn")
    assert _codes(_base_lock("sem_pacote"), tmp_path) == {"dmn-nao-listado"}


def test_sem_pacote_dmn_real_solto_reprova(tmp_path: Path) -> None:
    (tmp_path / RULES_DIR).mkdir(parents=True)
    (tmp_path / RULES_DIR / "README.md").write_text("# rco\n", encoding="utf-8")
    exemplo = _write_dmn(tmp_path, "rco_exemplo_sintetico.dmn")
    _write_dmn(tmp_path, "rco_carencia_check.dmn")
    lock = _base_lock("sem_pacote")
    lock["exemplos_sinteticos"] = [_entry(tmp_path, exemplo)]
    assert _codes(lock, tmp_path) == {"dmn-nao-listado"}
    assert _run_cli(tmp_path, lock) == 1


def test_sem_pacote_outro_arquivo_solto_reprova(tmp_path: Path) -> None:
    (tmp_path / RULES_DIR).mkdir(parents=True)
    (tmp_path / RULES_DIR / "notas.txt").write_text("x\n", encoding="utf-8")
    assert _codes(_base_lock("sem_pacote"), tmp_path) == {"arquivo-nao-listado"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda lk: lk["provenance"].update(rco_commit_sha=_COMMIT),
        lambda lk: lk.update(manifest_pin={"path": f"{RULES_DIR}/manifest.json", "sha256": "0" * 64}),
        lambda lk: lk.update(artifacts=[{"path": f"{RULES_DIR}/rco_x.dmn", "sha256": "0" * 64}]),
    ],
)
def test_sem_pacote_incoerente_reprova(tmp_path: Path, mutate: Any) -> None:
    lock = _base_lock("sem_pacote")
    mutate(lock)
    assert "estado-incoerente" in _codes(lock, tmp_path)


def test_pin_comitado_passa_na_arvore_real() -> None:
    lock = json.loads((_REPO_ROOT / _LOCK_REL).read_text(encoding="utf-8"))
    assert lock["lock_format"]["rules_dir"] == RULES_DIR
    assert verify_lock(lock, repo_root=_REPO_ROOT) == []
    assert main([]) == 0

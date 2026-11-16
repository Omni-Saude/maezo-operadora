"""Testes do gate do pin do pacote K1 do rco (ADR-0075 secao b, MZO-1d).

Duas camadas:

1. **Nucleo puro + CLI** contra arvores sinteticas em `tmp_path`: o pacote K1 SINTETICO de
   `tests/unit/platform/rco_k1_sintetico.py` (nenhum byte real do rco). Verde no caminho feliz;
   vermelho em DMN adulterado, DMN a mais nao listado, arquivo listado ausente, pin malformado,
   digest cru, manifesto recusado pelo leitor (MZO-2a, contrato-k1.md §11 itens 1-6), artifacts
   diferentes de `decisoes[]`, arquivo solto em `k1/` e estado `sem_pacote` com DMN solto.
2. **Arvore real**: o pin comitado passa contra a arvore deste checkout.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.verify_rco_rules_pin import K1_DIR, K1_MANIFEST_PATH, RULES_DIR, main, verify_lock

from tests.unit.platform import rco_k1_sintetico as sint

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
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_dmn(root: Path, name: str) -> Path:
    path = root / RULES_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXAMPLE_DMN.format(id=path.stem), encoding="utf-8")
    return path


def _entry(root: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}


def _base_lock(estado: str) -> dict[str, Any]:
    return {
        "lock_format": {"name": "rco-rules-lock", "rules_dir": RULES_DIR, "version": "2.0.0"},
        "estado": estado,
        "provenance": {"rco_commit_sha": None, "rco_repo": None, "status": "NAO_PUBLICADO"},
        "manifest_pin": None,
        "artifacts": [],
        "exemplos_sinteticos": [],
    }


@pytest.fixture
def pinned_tree(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """Arvore com o pacote K1 SINTETICO (manifesto + anexos + 2 DMNs), pin em estado `fixado`."""
    sint.montar(tmp_path)
    return tmp_path, sint.lock(tmp_path)


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
    target = root / RULES_DIR / "sint_a.dmn"
    target.write_text(target.read_text(encoding="utf-8").replace("liberar", "informar"), encoding="utf-8")
    assert "sha256-divergente" in _codes(lock, root)
    assert _run_cli(root, lock) == 1


def test_manifesto_adulterado_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    (root / K1_MANIFEST_PATH).write_text("{}", encoding="utf-8")
    assert "sha256-divergente" in _codes(lock, root)
    assert _run_cli(root, lock) == 1


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
    (root / RULES_DIR / "sint_b.dmn").unlink()
    assert _codes(lock, root) == {"arquivo-listado-ausente", "pacote-k1"}
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
        (lambda lk: lk["artifacts"][0].update(sha256=lk["artifacts"][0]["sha256"][7:]), "sha256-invalido"),
        (lambda lk: lk["manifest_pin"].update(sha256=lk["manifest_pin"]["sha256"][7:]), "sha256-invalido"),
        (lambda lk: lk["lock_format"].update(version="1.0.0"), "lock-versao"),
        (lambda lk: lk["manifest_pin"].update(path=f"{RULES_DIR}/manifesto.json"), "manifesto-caminho"),
        (lambda lk: lk["manifest_pin"].update(extra=1), "manifesto"),
        (lambda lk: lk["provenance"].update(rco_commit_sha="f" * 40), "proveniencia"),
        (lambda lk: lk["provenance"].update(rco_repo="outro/repo"), "proveniencia"),
        (lambda lk: lk["artifacts"].pop(), "artefato-fora-do-pin"),
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
        lambda lk: lk.update(manifest_pin={"path": K1_MANIFEST_PATH, "sha256": "sha256:" + "0" * 64}),
        lambda lk: lk.update(artifacts=[{"path": f"{RULES_DIR}/rco_x.dmn", "sha256": "sha256:" + "0" * 64}]),
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


def test_manifesto_dmn_nao_libera_dmn_nao_listado(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    real = _write_dmn(root, "carencia_real.dmn")
    lock["manifest_pin"] = _entry(root, real)
    assert {"manifesto-caminho", "dmn-nao-listado"} <= _codes(lock, root)
    assert _run_cli(root, lock) == 1


def test_manifesto_via_pasta_link_simbolico_reprova(
    pinned_tree: tuple[Path, dict[str, Any]], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, lock = pinned_tree
    outside = tmp_path_factory.mktemp("fora")
    (outside / "x.json").write_text("{}", encoding="utf-8")
    (root / "lnk").symlink_to(outside, target_is_directory=True)
    (root / K1_MANIFEST_PATH).unlink()
    (root / K1_MANIFEST_PATH).symlink_to(outside / "x.json")
    lock["manifest_pin"] = {"path": K1_MANIFEST_PATH, "sha256": _sha(outside / "x.json")}
    assert "link-simbolico" in _codes(lock, root)
    assert _run_cli(root, lock) == 1


def test_ancestral_da_pasta_rco_link_simbolico_reprova(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("dmn_fora")
    (outside / "rco").mkdir()
    (tmp_path / "spec" / "processes").mkdir(parents=True)
    (tmp_path / "spec" / "processes" / "dmn").symlink_to(outside, target_is_directory=True)
    assert "link-simbolico" in _codes(_base_lock("sem_pacote"), tmp_path)


def test_chave_json_duplicada_reprova(tmp_path: Path) -> None:
    raw = json.dumps(_base_lock("sem_pacote"))
    raw = raw.replace('"estado": "sem_pacote"', '"estado": "fixado", "estado": "sem_pacote"')
    assert '"estado": "fixado", "estado"' in raw
    assert _run_cli(tmp_path, raw) == 1


# ---------------------------------------------------------------------------------------------
# MZO-2a: leitor do manifesto K1 no gate (contrato-k1.md §11 itens 1-6)
# ---------------------------------------------------------------------------------------------


def test_manifesto_sujo_reprova_mesmo_com_pin_coerente(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, _ = pinned_tree
    manifesto = json.loads((root / K1_MANIFEST_PATH).read_bytes())
    manifesto["origem"]["sujo"] = True
    sint.regravar(root, manifesto)  # pin repinado: so o leitor pode recusar
    assert _codes(sint.lock(root), root) == {"manifesto-invalido"}
    assert main(["--repo-root", str(root)]) == 1


@pytest.mark.parametrize("formato", ["3.0.0", "2.1.0", "1.1.0", "0.9.0"])
def test_formato_de_manifesto_nao_suportado_reprova(
    pinned_tree: tuple[Path, dict[str, Any]], formato: str
) -> None:
    root, _ = pinned_tree
    manifesto = json.loads((root / K1_MANIFEST_PATH).read_bytes())
    manifesto["formato_manifesto"] = formato
    sint.regravar(root, manifesto)
    assert _codes(sint.lock(root), root) == {"manifesto-invalido"}


def test_campo_desconhecido_no_manifesto_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, _ = pinned_tree
    manifesto = json.loads((root / K1_MANIFEST_PATH).read_bytes())
    manifesto["decisoes"][0]["regras"][0]["campo_novo"] = "x"
    sint.regravar(root, manifesto)
    assert _codes(sint.lock(root), root) == {"manifesto-invalido"}


def test_anexo_adulterado_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    vocab = root / K1_DIR / "vocabulario.json"
    vocab.write_bytes(vocab.read_bytes().replace(b"motivoTab38", b"motivoTab39"))
    assert _codes(lock, root) == {"pacote-k1"}
    assert _run_cli(root, lock) == 1


def test_artefato_com_sha_diferente_do_manifesto_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    """O pin e o manifesto tem de concordar: um DMN repinado so no lock nao passa."""
    root, lock = pinned_tree
    target = root / RULES_DIR / "sint_a.dmn"
    target.write_bytes(target.read_bytes().replace(b"liberar", b"informar"))
    lock["artifacts"][0]["sha256"] = _sha(target)
    assert _codes(lock, root) == {"sha256-divergente", "pacote-k1"}


def test_coluna_de_saida_fora_do_saidas_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, _ = pinned_tree
    manifesto = json.loads((root / K1_MANIFEST_PATH).read_bytes())
    manifesto["decisoes"][1]["saidas"] = manifesto["decisoes"][1]["saidas"][:5]  # tira acaoSintetica
    sint.regravar(root, manifesto)
    violations = verify_lock(sint.lock(root), repo_root=root)
    assert {v.code for v in violations} == {"pacote-k1"}
    assert "acaoSintetica" in violations[0].detail


@pytest.mark.parametrize("nome", ["exemplos", "rco_sint_a.json", "manifesto.sha256"])
def test_arquivo_solto_em_k1_reprova(pinned_tree: tuple[Path, dict[str, Any]], nome: str) -> None:
    """Exemplos do pacote ficam fora do lock e fora da arvore (contrato-k1.md §3, §11 item 5)."""
    root, lock = pinned_tree
    alvo = root / K1_DIR / nome
    if nome == "exemplos":
        alvo.mkdir()
        (alvo / "rco_sint_a.json").write_text("{}", encoding="utf-8")
    else:
        alvo.write_text("{}", encoding="utf-8")
    assert _codes(lock, root) & {"arquivo-nao-listado", "subpasta-nao-permitida"}
    assert _run_cli(root, lock) == 1


def test_sem_pacote_com_k1_preenchida_reprova(tmp_path: Path) -> None:
    (tmp_path / K1_DIR).mkdir(parents=True)
    (tmp_path / K1_DIR / "manifesto.json").write_text("{}", encoding="utf-8")
    assert _codes(_base_lock("sem_pacote"), tmp_path) == {"arquivo-nao-listado"}


# ---------------------------------------------------------------------------------------------
# Revisao de 2403c8ed (itens 2, 3, 6, 7 e 8)
# ---------------------------------------------------------------------------------------------


def test_major2_so_plena_sem_aditivo_passa(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree  # pacote sintetico 2.0.0, todas as regras `plena` sem aditivo
    assert verify_lock(lock, repo_root=root) == []


@pytest.mark.parametrize(
    "ativacao", [("em_correcao", False), ("conferida", False), ("plena", True), ("em_correcao", True)]
)
def test_major2_ativacao_nao_ligada_reprova(tmp_path: Path, ativacao: tuple[str, bool]) -> None:
    sint.montar(tmp_path, ativacoes={"SINT-B-0001": ativacao})
    violations = verify_lock(sint.lock(tmp_path), repo_root=tmp_path)
    assert {v.code for v in violations} == {"ativacao-nao-suportada"}
    assert "Onda 2 item 5" in violations[0].detail
    assert main(["--repo-root", str(tmp_path)]) == 1


def test_regra_sem_entrada_em_regras_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, _ = pinned_tree
    manifesto = json.loads((root / K1_MANIFEST_PATH).read_bytes())
    manifesto["decisoes"][0]["regras"].pop()
    sint.regravar(root, manifesto)
    violations = verify_lock(sint.lock(root), repo_root=root)
    assert {v.code for v in violations} == {"pacote-k1"}
    assert "SINT-A-0002" in violations[0].detail


@pytest.mark.parametrize("const", ["NaN", "Infinity", "-Infinity"])
def test_lock_com_constante_nao_padrao_reprova(tmp_path: Path, const: str) -> None:
    raw = json.dumps(_base_lock("sem_pacote")).replace('"status": "NAO_PUBLICADO"', f'"status": {const}')
    assert const in raw
    assert _run_cli(tmp_path, raw) == 1


def test_exemplo_sintetico_sem_prefixo_rco_reprova(tmp_path: Path) -> None:
    (tmp_path / RULES_DIR).mkdir(parents=True)
    path = tmp_path / RULES_DIR / "rco_exemplo.dmn"
    path.write_text(_EXAMPLE_DMN.format(id="exemplo_sem_prefixo"), encoding="utf-8")
    lock = _base_lock("sem_pacote")
    lock["exemplos_sinteticos"] = [_entry(tmp_path, path)]
    assert _codes(lock, tmp_path) == {"decisao-invalida"}


def test_exemplo_com_id_de_decisao_do_pacote_reprova(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    root, lock = pinned_tree
    path = root / RULES_DIR / "rco_exemplo.dmn"
    path.write_text(_EXAMPLE_DMN.format(id="rco_sint_a"), encoding="utf-8")
    lock["exemplos_sinteticos"] = [_entry(root, path)]
    assert _codes(lock, root) == {"decisao-duplicada"}


def test_gate_continua_so_biblioteca_padrao(pinned_tree: tuple[Path, dict[str, Any]]) -> None:
    """Item 7: o gate le o manifesto sem importar o pacote `maezo` (nem structlog)."""
    root, _ = pinned_tree
    script = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "verify_rco_rules_pin.py"
    codigo = (
        "import runpy, sys\n"
        f"sys.argv = ['verify', '--repo-root', {str(root)!r}]\n"
        "try:\n"
        f"    runpy.run_path({str(script)!r}, run_name='__main__')\n"
        "except SystemExit as exc:\n"
        "    rc = exc.code\n"
        "carregados = sorted("
        "    m for m in sys.modules if m == 'maezo' or m.startswith(('maezo.', 'structlog'))"
        ")\n"
        "print(rc, carregados)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", codigo], capture_output=True, text=True, check=True, timeout=120
    )
    assert out.stdout.strip().splitlines()[-1] == "0 []", out.stdout + out.stderr


@pytest.mark.parametrize("ativacao", [[], {}])
def test_ativacao_nao_texto_e_violacao_codificada(tmp_path: Path, ativacao: object) -> None:
    """Re-revisao de e70d9b79: lista/objeto em `ativacao` vira violacao, nao traceback."""
    sint.montar(tmp_path)
    manifesto = json.loads((tmp_path / K1_MANIFEST_PATH).read_bytes())
    manifesto["decisoes"][0]["regras"][0]["ativacao"] = ativacao
    sint.regravar(tmp_path, manifesto)
    assert _codes(sint.lock(tmp_path), tmp_path) == {"manifesto-invalido"}
    assert main(["--repo-root", str(tmp_path)]) == 1

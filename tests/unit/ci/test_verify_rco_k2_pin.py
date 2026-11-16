"""Testes do gate do pin do pacote K2 do rco (Manual do Beneficiario, contrato-k2.md §2).

Pacote K2 SINTETICO montado em `tmp_path` (nenhum conteudo real do rco): cinco arquivos de dados com
bytes inventados, manifesto canonico, `manifesto.sha256` e o lock. Cada recusa do gate tem um teste;
o cruzamento com o K1 usa o pacote K1 sintetico de `tests/unit/platform/rco_k1_sintetico.py`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.ci.verify_rco_k2_pin import (
    DATA_FILES,
    K2_DIR,
    K2_MANIFEST_PATH,
    main,
    verify_lock,
)

from tests.unit.platform import rco_k1_sintetico as sint

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCK_REL = "config/integrations/rco/k2.lock.json"
_COMMIT = sint.COMMIT  # o do K1 sintetico: o K2 fixado exige K1 fixado coerente
_VERSAO = "1.0.0"
_RCO_REPO = "Omni-Saude/regras-contratos-operadora"


def _manifesto_base(root: Path) -> dict[str, Any]:
    return {
        "formato": "rco-k2-v1",
        "versao": _VERSAO,
        "commit_rco": _COMMIT,
        "gerado_em": "2026-01-02T03:04:05Z",
        "ratificado": False,
        "arquivos": {name: sint.sha((root / K2_DIR / name).read_bytes()) for name in DATA_FILES},
    }


def _regravar(root: Path, manifesto: dict[str, Any], *, raw: bytes | None = None) -> None:
    """Grava manifesto + manifesto.sha256 + lock coerentes (uma mutacao por vez nos testes)."""
    raw = sint.canonico(manifesto) if raw is None else raw
    pasta = root / K2_DIR
    (pasta / "manifesto.json").write_bytes(raw)
    digest = sint.sha(raw)
    (pasta / "manifesto.sha256").write_bytes(f"{digest}\n".encode())
    lock = {
        "lock_format": {"name": "rco-k2-lock", "version": "1.0.0"},
        "estado": "fixado",
        "provenance": {"rco_repo": _RCO_REPO, "rco_commit_sha": _COMMIT},
        "manifest_pin": {"path": K2_MANIFEST_PATH, "sha256": digest},
        "versao": _VERSAO,
    }
    _gravar_lock(root, lock)


def _gravar_lock(root: Path, lock: dict[str, Any]) -> None:
    path = root / _LOCK_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock), encoding="utf-8")


def _ler_lock(root: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((root / _LOCK_REL).read_text(encoding="utf-8"))
    return loaded


def _montar_k2(root: Path) -> None:
    pasta = root / K2_DIR
    pasta.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        (pasta / name).write_text(f"conteudo sintetico de {name}\n", encoding="utf-8")
    _regravar(root, _manifesto_base(root))


def _montar_sem_pacote(root: Path) -> dict[str, Any]:
    lock: dict[str, Any] = {
        "lock_format": {"name": "rco-k2-lock", "version": "1.0.0"},
        "estado": "sem_pacote",
        "provenance": {"rco_repo": _RCO_REPO, "rco_commit_sha": None},
        "manifest_pin": None,
        "versao": None,
    }
    _gravar_lock(root, lock)
    return lock


def _codes(root: Path) -> set[str]:
    return {v.code for v in verify_lock(_ler_lock(root), repo_root=root)}


def _mutar_manifesto(root: Path, mutacao: Any) -> None:
    manifesto = _manifesto_base(root)
    mutacao(manifesto)
    _regravar(root, manifesto)


@pytest.fixture
def fixado(tmp_path: Path) -> Path:
    sint.montar(tmp_path)  # K1 sintetico fixado (versao_pacote 1.0.0, mesmo commit)
    _montar_k2(tmp_path)
    return tmp_path


def test_sem_pacote_pasta_ausente_passa(tmp_path: Path) -> None:
    _montar_sem_pacote(tmp_path)
    assert _codes(tmp_path) == set()
    assert main(["--repo-root", str(tmp_path)]) == 0


def test_sem_pacote_so_readme_passa(tmp_path: Path) -> None:
    _montar_sem_pacote(tmp_path)
    (tmp_path / K2_DIR).mkdir(parents=True)
    (tmp_path / K2_DIR / "README.md").write_text("nada\n", encoding="utf-8")
    assert _codes(tmp_path) == set()


def test_sem_pacote_arquivo_solto_reprova(tmp_path: Path) -> None:
    _montar_sem_pacote(tmp_path)
    (tmp_path / K2_DIR).mkdir(parents=True)
    (tmp_path / K2_DIR / "search-index.json").write_text("{}", encoding="utf-8")
    assert _codes(tmp_path) == {"arquivo-nao-listado"}
    assert main(["--repo-root", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    "campo",
    [
        ("rco_commit_sha", _COMMIT),
        ("manifest_pin", {"path": K2_MANIFEST_PATH, "sha256": "x"}),
        ("versao", "1"),
    ],
)
def test_sem_pacote_com_campo_preenchido_reprova(tmp_path: Path, campo: tuple[str, Any]) -> None:
    lock = _montar_sem_pacote(tmp_path)
    chave, valor = campo
    if chave == "rco_commit_sha":
        lock["provenance"]["rco_commit_sha"] = valor
    else:
        lock[chave] = valor
    _gravar_lock(tmp_path, lock)
    assert "estado-incoerente" in _codes(tmp_path)


def test_fixado_passa(fixado: Path) -> None:
    assert _codes(fixado) == set()
    assert main(["--repo-root", str(fixado)]) == 0


def test_fixado_ratificado_false_e_true_passam(fixado: Path) -> None:
    _mutar_manifesto(fixado, lambda m: m.update(ratificado=True))
    assert _codes(fixado) == set()


def test_pin_ausente_e_malformado_reprovam(tmp_path: Path) -> None:
    assert main(["--repo-root", str(tmp_path)]) == 1
    for raw in ["", "{nao e json", "[]", '{"a": 1, "a": 2}', '{"a": NaN}']:
        path = tmp_path / _LOCK_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        assert main(["--repo-root", str(tmp_path)]) == 1


def test_manifesto_nao_canonico_reprova(fixado: Path) -> None:
    raw = json.dumps(_manifesto_base(fixado), indent=2).encode()
    _regravar(fixado, _manifesto_base(fixado), raw=raw)
    assert _codes(fixado) == {"manifesto-nao-canonico"}


def test_manifesto_com_quebra_final_reprova(fixado: Path) -> None:
    _regravar(fixado, _manifesto_base(fixado), raw=sint.canonico(_manifesto_base(fixado)) + b"\n")
    assert _codes(fixado) == {"manifesto-nao-canonico"}


def test_manifesto_chave_duplicada_reprova(fixado: Path) -> None:
    raw = b'{"formato":"rco-k2-v1","formato":"rco-k2-v1"}'
    _regravar(fixado, _manifesto_base(fixado), raw=raw)
    assert "manifesto-chave-duplicada" in _codes(fixado)


def test_manifesto_nan_reprova(fixado: Path) -> None:
    _regravar(fixado, _manifesto_base(fixado), raw=b'{"ratificado":NaN}')
    assert "manifesto-json-invalido" in _codes(fixado)


def test_manifesto_campo_extra_ou_faltando_reprova(fixado: Path) -> None:
    _mutar_manifesto(fixado, lambda m: m.update(extra="x"))
    assert "manifesto-campos" in _codes(fixado)
    _mutar_manifesto(fixado, lambda m: m.pop("ratificado"))
    assert "manifesto-campos" in _codes(fixado)


@pytest.mark.parametrize(
    ("campo", "valor", "codigo"),
    [
        ("formato", "rco-k2-v2", "manifesto-formato"),
        ("versao", "", "manifesto-versao"),
        ("versao", 1, "manifesto-versao"),
        ("commit_rco", "abc", "manifesto-commit"),
        ("commit_rco", "A" * 40, "manifesto-commit"),
        ("gerado_em", "2026-01-02T03:04:05+00:00", "manifesto-gerado-em"),
        ("gerado_em", "2026-13-02T03:04:05Z", "manifesto-gerado-em"),
        ("gerado_em", "2026-01-02", "manifesto-gerado-em"),
        ("gerado_em", "2026-1-2T3:4:5Z", "manifesto-gerado-em"),
        ("gerado_em", "٢٠٢٦-٠١-٠٢T٠٣:٠٤:٠٥Z", "manifesto-gerado-em"),
        ("ratificado", "false", "manifesto-ratificado"),
        ("ratificado", 0, "manifesto-ratificado"),
    ],
)
def test_manifesto_campo_invalido_reprova(fixado: Path, campo: str, valor: Any, codigo: str) -> None:
    _mutar_manifesto(fixado, lambda m: m.update({campo: valor}))
    assert codigo in _codes(fixado)
    assert main(["--repo-root", str(fixado)]) == 1


def test_manifesto_arquivos_nao_sao_os_cinco_reprova(fixado: Path) -> None:
    _mutar_manifesto(fixado, lambda m: m["arquivos"].pop("avaliacoes.yaml"))
    assert "manifesto-arquivos" in _codes(fixado)
    _mutar_manifesto(fixado, lambda m: m["arquivos"].update({"extra.json": "sha256:" + "0" * 64}))
    assert "manifesto-arquivos" in _codes(fixado)


def test_digest_cru_ou_maiusculo_no_manifesto_reprova(fixado: Path) -> None:
    cru = "0" * 64
    _mutar_manifesto(fixado, lambda m: m["arquivos"].update({"sinonimos.json": cru}))
    assert "sha256-invalido" in _codes(fixado)
    _mutar_manifesto(fixado, lambda m: m["arquivos"].update({"sinonimos.json": "sha256:" + "A" * 64}))
    assert "sha256-invalido" in _codes(fixado)


def test_arquivo_de_dados_adulterado_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "search-index.json").write_text("adulterado", encoding="utf-8")
    assert _codes(fixado) == {"sha256-divergente"}


def test_arquivo_de_dados_ausente_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "capitulo-geral.yaml").unlink()
    assert _codes(fixado) == {"arquivo-listado-ausente"}


def test_arquivo_fora_do_manifesto_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "intruso.json").write_text("{}", encoding="utf-8")
    assert _codes(fixado) == {"arquivo-nao-listado"}


def test_readme_dentro_do_pacote_fixado_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "README.md").write_text("x", encoding="utf-8")
    assert _codes(fixado) == {"arquivo-nao-listado"}


def test_subpasta_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "sub").mkdir()
    (fixado / K2_DIR / "sub" / "x.json").write_text("{}", encoding="utf-8")
    assert {"subpasta-nao-permitida", "arquivo-nao-listado"} <= _codes(fixado)


def test_manifesto_sha256_formato_reprova(fixado: Path) -> None:
    pasta = fixado / K2_DIR
    digest = (pasta / "manifesto.sha256").read_text(encoding="utf-8").strip()
    for raw, codigo in [
        (digest.encode(), "sha256-divergente"),  # sem LF final
        (f"{digest}\n\n".encode(), "sha256-divergente"),
        (digest.removeprefix("sha256:").encode() + b"\n", "sha256-invalido"),  # hex cru
        (f"sha256:{'0' * 64}\n".encode(), "sha256-divergente"),
    ]:
        (pasta / "manifesto.sha256").write_bytes(raw)
        assert codigo in _codes(fixado), raw


def test_manifesto_sha256_ausente_reprova(fixado: Path) -> None:
    (fixado / K2_DIR / "manifesto.sha256").unlink()
    assert _codes(fixado) == {"arquivo-listado-ausente"}


def test_lock_sha256_divergente_reprova(fixado: Path) -> None:
    lock = _ler_lock(fixado)
    lock["manifest_pin"]["sha256"] = "sha256:" + "1" * 64
    _gravar_lock(fixado, lock)
    assert _codes(fixado) == {"sha256-divergente"}


def test_lock_versao_diferente_do_manifesto_reprova(fixado: Path) -> None:
    lock = _ler_lock(fixado)
    lock["versao"] = "9.9.9"
    _gravar_lock(fixado, lock)
    assert _codes(fixado) == {"lock-versao"}


def test_lock_commit_diferente_do_manifesto_reprova(fixado: Path) -> None:
    lock = _ler_lock(fixado)
    lock["provenance"]["rco_commit_sha"] = "c" * 40
    _gravar_lock(fixado, lock)
    assert _codes(fixado) == {"proveniencia"}


@pytest.mark.parametrize(
    ("mutacao", "codigo"),
    [
        (
            lambda lock: lock["manifest_pin"].update(path="config/integrations/rco/k2/../k2/m.json"),
            "manifesto-caminho",
        ),
        (lambda lock: lock["manifest_pin"].update(path="/abs/manifesto.json"), "manifesto-caminho"),
        (lambda lock: lock["manifest_pin"].update(extra=1), "manifesto"),
        (lambda lock: lock["provenance"].update(rco_repo="outro/repo"), "proveniencia"),
        (lambda lock: lock["provenance"].update(rco_commit_sha="abc"), "proveniencia"),
        (lambda lock: lock["provenance"].update(extra=1), "proveniencia"),
        (lambda lock: lock["lock_format"].update(version="2.0.0"), "lock-formato"),
        (lambda lock: lock.update(extra=1), "lock-campos"),
        (lambda lock: lock.update(estado="outro"), "estado-invalido"),
        (lambda lock: lock.update(manifest_pin=None), "manifesto"),
        (lambda lock: lock.update(versao=""), "versao"),
    ],
)
def test_lock_malformado_reprova(fixado: Path, mutacao: Any, codigo: str) -> None:
    lock = _ler_lock(fixado)
    mutacao(lock)
    _gravar_lock(fixado, lock)
    assert codigo in _codes(fixado)
    assert main(["--repo-root", str(fixado)]) == 1


def test_link_simbolico_de_arquivo_reprova(fixado: Path) -> None:
    alvo = fixado / K2_DIR / "sinonimos.json"
    conteudo = alvo.read_bytes()
    alvo.unlink()
    real = fixado / "fora.json"
    real.write_bytes(conteudo)
    alvo.symlink_to(real)
    codes = _codes(fixado)
    assert "link-simbolico" in codes
    assert "arquivo-listado-ausente" in codes


def test_link_simbolico_da_pasta_reprova(fixado: Path) -> None:
    pasta = fixado / K2_DIR
    real = fixado / "k2-real"
    pasta.rename(real)
    pasta.symlink_to(real)
    assert "link-simbolico" in _codes(fixado)


def _mutar_k2(root: Path, *, versao: str, commit: str) -> None:
    manifesto = _manifesto_base(root)
    manifesto["versao"], manifesto["commit_rco"] = versao, commit
    _regravar(root, manifesto)
    lock = _ler_lock(root)
    lock["versao"], lock["provenance"]["rco_commit_sha"] = versao, commit
    _gravar_lock(root, lock)


def test_cruzamento_k1_k2_coerente_passa(fixado: Path) -> None:
    assert _codes(fixado) == set()


def test_cruzamento_versao_diferente_reprova(fixado: Path) -> None:
    _mutar_k2(fixado, versao="2.0.0", commit=sint.COMMIT)
    assert _codes(fixado) == {"cruzamento-versao"}


def test_cruzamento_commit_diferente_reprova(fixado: Path) -> None:
    _mutar_k2(fixado, versao="1.0.0", commit="d" * 40)
    assert _codes(fixado) == {"cruzamento-commit"}


def test_k2_fixado_com_k1_sem_pacote_reprova(fixado: Path) -> None:
    k1 = copy.deepcopy(sint.lock(fixado))
    k1["estado"] = "sem_pacote"
    sint.gravar_lock(fixado, k1)
    assert _codes(fixado) == {"cruzamento-k1"}


def test_k1_fixado_com_k2_sem_pacote_passa(tmp_path: Path) -> None:
    sint.montar(tmp_path)
    _montar_sem_pacote(tmp_path)
    assert _codes(tmp_path) == set()


def test_k2_fixado_sem_lock_k1_reprova(fixado: Path) -> None:
    (fixado / sint.LOCK_REL).unlink()
    assert _codes(fixado) == {"cruzamento-k1"}


@pytest.mark.parametrize("raw", [b"\xff\xfe", b'{"estado":"fixado","estado":"fixado"}', b"[]", b"{"])
def test_k2_fixado_com_lock_k1_ilegivel_reprova(fixado: Path, raw: bytes) -> None:
    (fixado / sint.LOCK_REL).write_bytes(raw)
    assert _codes(fixado) == {"cruzamento-k1"}


def test_k2_fixado_com_lock_k1_link_simbolico_reprova(fixado: Path) -> None:
    lock_k1 = fixado / sint.LOCK_REL
    real = fixado / "k1-real.json"
    real.write_bytes(lock_k1.read_bytes())
    lock_k1.unlink()
    lock_k1.symlink_to(real)
    assert _codes(fixado) == {"cruzamento-k1"}


def test_cruzamento_k1_fixado_sem_manifesto_reprova(fixado: Path) -> None:
    (fixado / sint.K1_DIR / "manifesto.json").unlink()
    assert "cruzamento-k1" in _codes(fixado)


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"a":1,"a":2}', b"[" * 100_000])
def test_cruzamento_manifesto_k1_invalido_reprova(fixado: Path, raw: bytes) -> None:
    (fixado / sint.K1_DIR / "manifesto.json").write_bytes(raw)
    assert "cruzamento-k1" in _codes(fixado)


def test_json_aninhado_demais_reprova(fixado: Path) -> None:
    _regravar(fixado, _manifesto_base(fixado), raw=b"[" * 100_000)
    assert "manifesto-json-invalido" in _codes(fixado)


def test_arvore_real_passa() -> None:
    assert main(["--repo-root", str(_REPO_ROOT)]) == 0

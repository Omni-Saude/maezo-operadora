"""A composicao da cobertura com o K2 pinado (`cobertura.composicao.compor_cobertura`): so' com pin E pacote,
o mesmo hash do lock vai a isencao de prazo, nunca liga a habilidade, producao nunca aceita nao ratificado.

Pacote SINTETICO (`helena_k2_amostra/pacote`), copiado para uma raiz temporaria; nenhum dado pessoal."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from maezo.agents.helena.habilidades import cobertura as cob
from maezo.agents.helena.habilidades.cobertura import manual as k2
from maezo.agents.helena.habilidades.cobertura.composicao import (
    DIRETORIO_DO_K2,
    LOCK_DO_K2,
    compor_cobertura,
)
from maezo.agents.helena.habilidades.cobertura.regra_geral import CamadaRegraGeral

_AMOSTRA = Path(__file__).parent / "helena_k2_amostra" / "pacote"


def _raiz(tmp_path: Path, *, estado: str = "fixado") -> tuple[Path, str]:
    destino = tmp_path / DIRETORIO_DO_K2
    shutil.copytree(_AMOSTRA, destino)
    manifesto = json.loads((destino / "manifesto.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256((destino / "manifesto.json").read_bytes()).hexdigest()
    lock: dict[str, Any] = {
        "lock_format": {"name": "rco-k2-lock", "version": "1.0.0"},
        "estado": estado,
        "provenance": {"rco_repo": "exemplo/rco", "rco_commit_sha": None},
        "manifest_pin": None,
        "versao": None,
    }
    if estado == "fixado":
        lock["provenance"]["rco_commit_sha"] = manifesto["commit_rco"]
        lock["manifest_pin"] = {"path": f"{DIRETORIO_DO_K2}/manifesto.json", "sha256": f"sha256:{sha}"}
        lock["versao"] = manifesto["versao"]
    (tmp_path / LOCK_DO_K2).write_text(json.dumps(lock), encoding="utf-8")
    return tmp_path, sha


def test_em_dev_com_a_chave_registra_o_hash_do_lock_e_a_camada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, "dev")
    raiz, sha = _raiz(tmp_path)
    cfg: dict[str, Any] = {k2.CHAVE_ACEITAR_NAO_RATIFICADO: True}
    compor_cobertura(raiz, cfg)
    assert cfg[cob.CHAVE_DO_HASH_K2] == sha  # o MESMO hash do lock, sem prefixo
    [camada] = cfg[cob.CHAVE_DAS_CAMADAS]
    assert isinstance(camada, CamadaRegraGeral)
    assert cob.CHAVE_DO_GRAFO not in cfg  # nunca liga a habilidade
    configuracao = cob.da_configuracao({**cfg, cob.CHAVE_DO_GRAFO: True})
    assert configuracao is not None and configuracao.hash_do_k2 == sha


@pytest.mark.parametrize(("ambiente", "chave"), [("prod", True), ("dev", False), ("", True)])
def test_pacote_nao_ratificado_recusado_nao_registra_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ambiente: str, chave: bool
) -> None:
    """A amostra tem `ratificado: false`: producao (mesmo com a chave), dev sem a chave e ambiente vazio."""
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, ambiente)
    raiz, _sha = _raiz(tmp_path)
    cfg: dict[str, Any] = {k2.CHAVE_ACEITAR_NAO_RATIFICADO: chave}
    compor_cobertura(raiz, cfg)
    assert cob.CHAVE_DO_HASH_K2 not in cfg and cob.CHAVE_DAS_CAMADAS not in cfg


def test_sem_pacote_ou_sem_lock_e_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, "dev")
    cfg: dict[str, Any] = {k2.CHAVE_ACEITAR_NAO_RATIFICADO: True}
    compor_cobertura(tmp_path, cfg)  # sem lock
    raiz, _sha = _raiz(tmp_path / "outra", estado="sem_pacote")
    compor_cobertura(raiz, cfg)
    assert cfg == {k2.CHAVE_ACEITAR_NAO_RATIFICADO: True}


def test_lock_do_repositorio_em_sem_pacote_e_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contra a raiz REAL: hoje o lock (se existir) esta' em `sem_pacote`, entao a chamada nao muda nada."""
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, "dev")
    raiz = Path(__file__).resolve().parents[3]
    cfg: dict[str, Any] = {k2.CHAVE_ACEITAR_NAO_RATIFICADO: True}
    compor_cobertura(raiz, cfg)
    assert cob.CHAVE_DAS_CAMADAS not in cfg


def test_pacote_corrompido_nao_registra_nada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, "dev")
    raiz, _sha = _raiz(tmp_path)
    alvo = raiz / DIRETORIO_DO_K2 / "search-index.json"
    alvo.write_bytes(alvo.read_bytes() + b" ")
    cfg: dict[str, Any] = {k2.CHAVE_ACEITAR_NAO_RATIFICADO: True}
    compor_cobertura(raiz, cfg)
    assert cob.CHAVE_DAS_CAMADAS not in cfg


def test_camada_regra_geral_anterior_e_substituida(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(k2.VARIAVEL_DO_AMBIENTE, "dev")
    raiz, _sha = _raiz(tmp_path)

    class _Velha:
        camada = "regra_geral"

    class _SeuPlano:
        camada = "seu_plano"

    seu_plano = _SeuPlano()
    cfg: dict[str, Any] = {
        k2.CHAVE_ACEITAR_NAO_RATIFICADO: True,
        cob.CHAVE_DAS_CAMADAS: [_Velha(), seu_plano],
    }
    compor_cobertura(raiz, cfg)
    primeira, segunda = cfg[cob.CHAVE_DAS_CAMADAS]
    assert isinstance(primeira, CamadaRegraGeral) and segunda is seu_plano

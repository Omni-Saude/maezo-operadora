"""MPTT P3 — fiacao da bandeira DARK `MAEZO_PRESTADOR_STATUS_LEDGER` e da raiz da CLI.

Provas que morrem sob mutacao:
* o padrao e' DESLIGADO e o alias de ambiente liga a bandeira (mutante: default True / alias
  trocado -> vermelho);
* desligada, `importar-baseline` e `reconciliar` recusam ANTES de qualquer fabrica de banco/motor
  (mutante: remover `exige_bandeira` -> as fabricas sao chamadas -> vermelho);
* ligada, as fabricas SAO chamadas e o resultado chega ao ledger (nao-vacuidade);
* a raiz de producao monta o seam GATED do registry com a leitura do #1167.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.prestador_status import cli
from maezo.platform.prestador_status.reconciliador import ReconciliadorStatusPrestador
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, HistoricInstanceRecord
from tests.unit.platform.test_prestador_status_baseline import baseline_atestada
from tests.unit.platform.test_prestador_status_reconciliador import LedgerMemoria

T = "amh"


def _settings(**valores: Any) -> WorkerRuntimeSettings:
    return WorkerRuntimeSettings(tenant_id=T, database_url="postgresql://x@db.invalid/x", **valores)


class Fabricas:
    def __init__(self) -> None:
        self.ledger = LedgerMemoria()
        self.transport = FakeCibSevenTransport()
        self.chamadas: list[str] = []

    def fabrica_ledger(self, _settings: Any) -> LedgerMemoria:
        self.chamadas.append("ledger")
        return self.ledger

    def fabrica_transporte(self, _settings: Any) -> FakeCibSevenTransport:
        self.chamadas.append("transporte")
        return self.transport


def _arquivo(tmp_path: Path, dados: dict[str, Any]) -> str:
    caminho = tmp_path / "baseline.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return str(caminho)


def test_bandeira_nasce_desligada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_PRESTADOR_STATUS_LEDGER", raising=False)
    assert WorkerRuntimeSettings().prestador_status_ledger is False


def test_alias_de_ambiente_liga_a_bandeira(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_PRESTADOR_STATUS_LEDGER", "true")
    assert WorkerRuntimeSettings().prestador_status_ledger is True


@pytest.mark.parametrize("comando", ["importar-baseline", "reconciliar"])
def test_desligada_recusa_antes_de_qualquer_fabrica(tmp_path: Path, comando: str) -> None:
    fab = Fabricas()
    argv = (
        [comando, "--arquivo", _arquivo(tmp_path, baseline_atestada())]
        if comando == "importar-baseline"
        else [
            comando,
            "--varrer",
        ]
    )
    codigo = cli.main(
        argv,
        settings=_settings(),
        fabrica_ledger=fab.fabrica_ledger,
        fabrica_transporte=fab.fabrica_transporte,
    )
    assert codigo == cli.SAIDA_RECUSA
    assert fab.chamadas == []
    assert fab.ledger.linhas == []


def test_ligada_importa_a_baseline_atestada(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fab = Fabricas()
    codigo = cli.main(
        ["importar-baseline", "--arquivo", _arquivo(tmp_path, baseline_atestada())],
        settings=_settings(prestador_status_ledger=True),
        fabrica_ledger=fab.fabrica_ledger,
    )
    assert codigo == cli.SAIDA_OK
    assert fab.chamadas == ["ledger"]
    assert {e.prestador_id for e in fab.ledger.linhas} == {"SINT-0001", "SINT-0002"}
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["contagem"]["GRAVADO"] == 2


def test_ligada_ainda_recusa_baseline_nao_atestada(tmp_path: Path) -> None:
    fab = Fabricas()
    bruta = baseline_atestada()
    bruta["atestacao"]["atestado"] = False
    codigo = cli.main(
        ["importar-baseline", "--arquivo", _arquivo(tmp_path, bruta)],
        settings=_settings(prestador_status_ledger=True),
        fabrica_ledger=fab.fabrica_ledger,
    )
    assert codigo == cli.SAIDA_RECUSA
    assert fab.ledger.linhas == []


def test_ligada_reconcilia_e_sinaliza_indisponivel_com_codigo_3(capsys: pytest.CaptureFixture[str]) -> None:
    fab = Fabricas()
    fab.transport.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id="i-1",
            business_key=f"CRED-{T}-SINT-0009",
            process_key="SP-OP-CRED-001",
            state="COMPLETED",
            end_activity_id="End_PrestadorCredenciado",
            variables={"prestador_id": "SINT-0009"},
        )
    )
    fab.transport.seed_historic_instance_record(
        HistoricInstanceRecord(
            instance_id="i-2",
            business_key=f"CRED-{T}-SINT-0010",
            process_key="SP-OP-CRED-001",
            state="COMPLETED",
            end_activity_id="End_DesconhecidoV2",
            variables={"prestador_id": "SINT-0010"},
        )
    )
    codigo = cli.main(
        ["reconciliar", "--cred-chave", f"CRED-{T}-SINT-0009", "--cred-chave", f"CRED-{T}-SINT-0010"],
        settings=_settings(prestador_status_ledger=True),
        fabrica_ledger=fab.fabrica_ledger,
        fabrica_transporte=fab.fabrica_transporte,
    )
    assert codigo == cli.SAIDA_INDISPONIVEL
    assert fab.chamadas == ["ledger", "transporte"]
    # A ultima linha e' o JSON da CLI (structlog pode escrever linhas antes dela).
    saida = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert [s["resultado"] for s in saida] == ["GRAVADO", "INDISPONIVEL"]
    assert [e.prestador_id for e in fab.ledger.linhas] == ["SINT-0009"]


def test_validar_e_digest_nao_precisam_da_bandeira_e_nao_tocam_banco(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fab = Fabricas()
    arquivo = _arquivo(tmp_path, baseline_atestada())
    assert (
        cli.main(
            ["validar-baseline", "--arquivo", arquivo],
            settings=_settings(),
            fabrica_ledger=fab.fabrica_ledger,
        )
        == 0
    )
    assert (
        cli.main(
            ["digest-baseline", "--arquivo", arquivo], settings=_settings(), fabrica_ledger=fab.fabrica_ledger
        )
        == 0
    )
    assert fab.chamadas == []
    linhas = capsys.readouterr().out.strip().splitlines()
    assert linhas[-1] == baseline_atestada()["atestacao"]["conteudo_sha256"]


def test_sem_database_url_recusa(tmp_path: Path) -> None:
    settings = WorkerRuntimeSettings(tenant_id=T, prestador_status_ledger=True, database_url=None)
    codigo = cli.main(
        ["importar-baseline", "--arquivo", _arquivo(tmp_path, baseline_atestada())], settings=settings
    )
    assert codigo == cli.SAIDA_RECUSA


def test_raiz_de_producao_monta_o_seam_gated_com_a_leitura_do_1167() -> None:
    from maezo.gateway.seams._base import is_gated_seam
    from maezo.gateway.seams.cibseven import GatedHistoryResolvingCibSevenTransport

    transport = cli.transporte_padrao(_settings(cibseven_base_url="http://engine.invalid/engine-rest"))
    assert is_gated_seam(transport)
    assert isinstance(transport, GatedHistoryResolvingCibSevenTransport)
    # E o reconciliador aceita exatamente esse objeto.
    ReconciliadorStatusPrestador(transport=transport, ledger=LedgerMemoria(), tenant=T)

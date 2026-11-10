"""Limpeza agendada da custodia do telefone (ADR-0061): desligada por padrao, sem chave, e o
`purge_expired` que ela chama apaga SO' o que venceu.

Dentes: desligado nunca monta o cofre; ligado chama `purge_expired` exatamente uma vez e fecha o
cofre (tambem na falha); o wrapper da limpeza recusa cifrar e decifrar; o SQL da purga e' o predicado
de vencimento; o `.tf` nasce sem recurso e com o agendamento desligado.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import structlog

from maezo.gateway.recipient_custody import (
    PostgresRecipientVault,
    RecipientCustodyError,
    open_phone,
    seal_phone,
)
from maezo.platform.custodia_limpeza import (
    ComposicaoDaLimpezaError,
    LimpezaCustodiaSettings,
    SemChaveKeyWrapper,
    montar_cofre,
    rodar,
)
from maezo.platform.custodia_limpeza import __main__ as entrada
from tests.support.recipient_custody_fakes import FakeKms

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TF = _REPO_ROOT / "deploy" / "aws-ecs" / "envs" / "dev-sa-east-1" / "task-custodia-limpeza.tf"
_CONV = "wa:amh:hk1_" + "c" * 64


class _Cofre:
    def __init__(self, apagadas: int = 0, falha: Exception | None = None) -> None:
        self.apagadas = apagadas
        self.falha = falha
        self.purgas = 0
        self.fechado = 0

    async def purge_expired(self) -> int:
        self.purgas += 1
        if self.falha is not None:
            raise self.falha
        return self.apagadas

    async def aclose(self) -> None:
        self.fechado += 1


def _cfg(**env: Any) -> LimpezaCustodiaSettings:
    """Pelos nomes de ambiente (os aliases), sem ler o ambiente do processo."""
    return LimpezaCustodiaSettings.model_validate(env)


def _ligado(**env: Any) -> LimpezaCustodiaSettings:
    return _cfg(MAEZO_CUSTODIA_LIMPEZA=True, DATABASE_URL="postgresql://x/y", **env)


# ------------------------------------------------------------------------------------- interruptor
def test_desligado_por_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_CUSTODIA_LIMPEZA", raising=False)
    assert LimpezaCustodiaSettings().habilitado is False


async def test_desligado_nao_monta_nada() -> None:
    def montar(_: LimpezaCustodiaSettings) -> _Cofre:
        raise AssertionError("desligado nao pode montar o cofre")

    with structlog.testing.capture_logs() as logs:
        assert await rodar(_cfg(MAEZO_CUSTODIA_LIMPEZA=False), montar=montar) == 0
    assert [e["modo"] for e in logs] == ["desligado"]


def test_main_desligado_sai_zero_sem_banco(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAEZO_CUSTODIA_LIMPEZA", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert entrada.main() == 0


# ---------------------------------------------------------------------------------------- rodada
async def test_ligado_purga_uma_vez_loga_a_contagem_e_fecha() -> None:
    cofre = _Cofre(apagadas=3)
    with structlog.testing.capture_logs() as logs:
        assert await rodar(_ligado(), montar=lambda _: cofre) == 0
    assert (cofre.purgas, cofre.fechado) == (1, 1)
    assert logs[-1]["modo"] == "executado" and logs[-1]["apagadas"] == 3


async def test_falha_na_purga_sai_um_fecha_e_nao_vaza_a_mensagem() -> None:
    cofre = _Cofre(falha=RuntimeError("wa:amh:hk1_segredo 5511987654321"))
    with structlog.testing.capture_logs() as logs:
        assert await rodar(_ligado(), montar=lambda _: cofre) == 1
    assert cofre.fechado == 1
    assert logs[-1] == {
        "event": "custodia_limpeza_resumo",
        "log_level": "error",
        "modo": "falhou",
        "erro": "RuntimeError",
    }
    assert "5511987654321" not in repr(logs) and "segredo" not in repr(logs)


async def test_ligado_sem_database_url_recusa_sem_purgar() -> None:
    with structlog.testing.capture_logs() as logs:
        code = await rodar(_cfg(MAEZO_CUSTODIA_LIMPEZA=True, DATABASE_URL=None))
    assert code == 1 and logs[-1]["modo"] == "nao_montou"
    with pytest.raises(ComposicaoDaLimpezaError):
        montar_cofre(_cfg(MAEZO_CUSTODIA_LIMPEZA=True, DATABASE_URL=""))


def test_montar_usa_o_tenant_e_o_wrapper_sem_chave() -> None:
    cofre = montar_cofre(_ligado(TENANT_ID="amh"))
    assert isinstance(cofre, PostgresRecipientVault)
    assert isinstance(cofre._wrapper, SemChaveKeyWrapper)
    assert cofre._tenant == "amh"


# ------------------------------------------------------------------------------------- sem chave
async def test_wrapper_da_limpeza_recusa_cifrar_e_decifrar() -> None:
    sem_chave = SemChaveKeyWrapper()
    with pytest.raises(RecipientCustodyError):
        await seal_phone("5511987654321", sem_chave, tenant="amh", conversation_id=_CONV)
    with pytest.raises(RecipientCustodyError, match="a limpeza nao decifra"):
        await sem_chave.unwrap(b"embrulhada", {"tenant": "amh"})
    selado = await seal_phone("5511987654321", FakeKms(), tenant="amh", conversation_id=_CONV)
    with pytest.raises(RecipientCustodyError):
        await open_phone(selado, sem_chave, tenant="amh", conversation_id=_CONV)


# ------------------------------------------------------------------- o purge_expired que e' chamado
class _Conn:
    def __init__(self, status: str) -> None:
        self.status = status
        self.sql: list[str] = []

    async def execute(self, sql: str, *args: object) -> str:
        self.sql.append(sql)
        return self.status


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.closed = 0

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)

    async def close(self) -> None:
        self.closed += 1


@pytest.mark.parametrize(("status", "esperado"), [("DELETE 7", 7), ("DELETE 0", 0)])
async def test_purge_expired_apaga_so_o_vencido_e_conta(status: str, esperado: int) -> None:
    conn = _Conn(status)
    pool = _Pool(conn)
    cofre = PostgresRecipientVault(
        dsn="postgresql://x/y", tenant="amh", wrapper=SemChaveKeyWrapper(), pool=pool
    )
    assert await cofre.purge_expired() == esperado
    (sql,) = conn.sql
    assert re.fullmatch(
        r"DELETE FROM beneficiario_contato_retomada WHERE expires_at <= clock_timestamp\(\)", sql
    ), sql
    await cofre.aclose()
    await cofre.aclose()
    assert pool.closed == 1


# ----------------------------------------------------------------------------------- terraform
def _codigo_tf() -> str:
    return "\n".join(
        line for line in _TF.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")
    )


def test_tf_nasce_sem_recurso_e_com_agendamento_desligado() -> None:
    codigo = _codigo_tf()
    bloco = re.search(r'variable "custodia_limpeza" \{(.*?)\n\}', codigo, re.DOTALL)
    assert bloco is not None
    assert re.search(r"^\s*default\s*=\s*null\s*$", bloco.group(1), re.MULTILINE)
    assert re.search(r"habilitado\s*=\s*optional\(bool,\s*false\)", bloco.group(1))
    assert re.search(r"schedule_enabled\s*=\s*optional\(bool,\s*false\)", bloco.group(1))
    assert 'state      = each.value.schedule_enabled ? "ENABLED" : "DISABLED"' in codigo
    assert "exec python -m maezo.platform.custodia_limpeza" in codigo
    assert '{ name = "MAEZO_CUSTODIA_LIMPEZA", value = tostring(each.value.habilitado) }' in codigo
    # sem KMS: a limpeza nao recebe a chave nem a role de retomada
    assert "RECIPIENT_VAULT" not in codigo and "recipient_vault" not in codigo
    assert "agent_resume_task" not in codigo


def test_nenhum_tfvars_liga_a_limpeza() -> None:
    envs = _TF.parent
    ligam = [p.name for p in envs.glob("*.tfvars") if "custodia_limpeza" in p.read_text(encoding="utf-8")]
    assert ligam == [], f"a limpeza da custodia nasce desligada; tfvars que a declaram: {ligam}"

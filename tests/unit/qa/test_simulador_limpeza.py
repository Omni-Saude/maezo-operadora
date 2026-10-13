"""Limpeza das conversas simuladas e a conferencia de que o motor nao tem caso ESC delas."""

from __future__ import annotations

from typing import Any

import pytest

from maezo.qa.simulador.composicao import RegistroDeEscalonamentos
from maezo.qa.simulador.limpeza import TABELA_ACESSO, TABELA_ROTEADOR, Limpeza

_CID = "wa:amh:hk1_abc"
_CID2 = "wa:amh:hk1_def"


class _Conexao:
    def __init__(self, registro: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._registro = registro

    async def execute(self, sql: str, *args: Any) -> str:
        self._registro.append((sql, args))
        return f"DELETE {len(args[1]) if len(args) > 1 else 0}"


class _Pool:
    def __init__(self) -> None:
        self.comandos: list[tuple[str, tuple[Any, ...]]] = []
        self.fechado = False

    def acquire(self) -> Any:
        pool = self

        class _Ctx:
            async def __aenter__(self) -> _Conexao:
                return _Conexao(pool.comandos)

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()

    async def close(self) -> None:
        self.fechado = True


class _Saver:
    def __init__(self, falhar: bool = False) -> None:
        self.apagados: list[str] = []
        self._falhar = falhar

    async def adelete_thread(self, thread_id: str) -> None:
        if self._falhar:
            raise OSError("db fora")
        self.apagados.append(thread_id)


class _Identidade:
    def __init__(self) -> None:
        self.invalidadas: list[str] = []

    def invalidar(self, conversation_id: str) -> None:
        self.invalidadas.append(conversation_id)


class _Motor:
    def __init__(self, existentes: set[str] | None = None, falhar: bool = False) -> None:
        self.existentes = existentes or set()
        self.perguntas: list[str] = []
        self._falhar = falhar

    async def find_active_instance(self, business_key: str, *, process_key: str = "") -> object | None:
        if self._falhar:
            raise RuntimeError("motor fora")
        self.perguntas.append(business_key)
        return object() if business_key in self.existentes else None


def _limpeza(**kwargs: Any) -> tuple[Limpeza, _Pool]:
    pool = _Pool()
    padrao: dict[str, Any] = {
        "dsn": "postgresql+asyncpg://u:p@h/db",
        "tenant_id": "amh",
        "saver": _Saver(),
        "identidade": _Identidade(),
        "escalonamentos": RegistroDeEscalonamentos(),
        "motor_real": _Motor(),
        "pool": pool,
    }
    padrao.update(kwargs)
    return Limpeza(**padrao), pool


async def test_reiniciar_conversa_apaga_checkpoint_roteador_cache_e_casos() -> None:
    saver, identidade, registro = _Saver(), _Identidade(), RegistroDeEscalonamentos()
    registro.registrar(f"ESC-amh-{_CID}", {})
    limpeza, pool = _limpeza(saver=saver, identidade=identidade, escalonamentos=registro)
    await limpeza.reiniciar_conversa(_CID)
    assert saver.apagados == [_CID] and identidade.invalidadas == [_CID]
    assert registro.ativa(f"ESC-amh-{_CID}") is None
    ((sql, args),) = pool.comandos
    assert TABELA_ROTEADOR in sql and args == ("amh", [_CID])


async def test_conversation_id_fora_do_formato_recusa_antes_de_qualquer_sql() -> None:
    limpeza, pool = _limpeza()
    for ruim in ("wa:outro:hk1_x", "wa:amh:5511900000500"):
        with pytest.raises(ValueError):
            await limpeza.reiniciar_conversa(ruim)
    assert pool.comandos == []


async def test_apagar_acesso() -> None:
    limpeza, pool = _limpeza()
    assert await limpeza.apagar_acesso(_CID) == 1
    assert TABELA_ACESSO in pool.comandos[0][0]


async def test_limpar_execucao_e_motor_limpo() -> None:
    motor = _Motor()
    limpeza, pool = _limpeza(motor_real=motor)
    resultado = await limpeza.limpar_execucao([_CID2, _CID, _CID])
    assert resultado.checkpoints_apagados == 2
    assert (resultado.linhas_roteador, resultado.linhas_acesso) == (2, 2)
    assert resultado.motor_limpo and resultado.como_dict()["motor_limpo"] is True
    assert motor.perguntas == [
        f"ESC-amh-{_CID}",
        f"ESC-amh-{_CID}-clin",
        f"ESC-amh-{_CID2}",
        f"ESC-amh-{_CID2}-clin",
    ]
    await limpeza.aclose()
    assert pool.fechado


async def test_motor_com_caso_ou_fora_nao_e_limpo() -> None:
    limpeza, _ = _limpeza(motor_real=_Motor({f"ESC-amh-{_CID}"}))
    assert (await limpeza.limpar_execucao([_CID])).casos_no_motor == 1
    limpeza, _ = _limpeza(motor_real=_Motor(falhar=True))
    resultado = await limpeza.limpar_execucao([_CID])
    assert not resultado.motor_limpo and resultado.erros[0].startswith("motor:")
    limpeza, _ = _limpeza(motor_real=None)
    assert not (await limpeza.limpar_execucao([_CID])).motor_limpo


async def test_falha_de_checkpoint_e_registrada_e_a_limpeza_segue() -> None:
    limpeza, _ = _limpeza(saver=_Saver(falhar=True))
    resultado = await limpeza.limpar_execucao([_CID])
    assert resultado.erros == ["checkpoint:OSError"] and resultado.linhas_acesso == 1

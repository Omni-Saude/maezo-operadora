"""REG-03: `emit_turn_desfecho` absorve a falha de METRICA e propaga o BUG (§Delta-F2).

O QUE ESTE ARQUIVO PINA, E POR QUE ELE PRECISOU EXISTIR
---------------------------------------------------------
O conserto de REG-03 tem DUAS metades, e nenhuma das duas tinha teste:

(a) LARGURA — `except Exception:` virou `except ValueError:`. O corpo do `try` le `state` por
    `.get`, normaliza e chama `record_agent_desfecho`; a UNICA falha externa que pode nascer ali
    e' o `ValueError` que o `prometheus_client` levanta para uso indevido do registro
    (`.labels()` com nomes/quantidade errados, serie duplicada). Um `AttributeError` de um
    chamador que passou algo que nao e' `Mapping`, um `KeyError` de contrato de estado violado
    ou um `TypeError` de assinatura derivada sao BUGS deste modulo, e eram absorvidos
    exatamente como uma falha de metrica.
(b) NIVEL — `logger.debug` virou `logger.warning`. Em DEBUG (que nenhum ambiente liga por
    padrao) o contador `maezo_agent_desfecho_total` podia parar de emitir NA FROTA INTEIRA sem
    sinal nenhum — o KPI inaferivel que CC-09 foi aberto para fechar.

A cerca AST irma (`test_broad_except_allowlist.py`) NAO cobre nenhuma das duas: ela dispara so'
para `Exception`/`BaseException`/`except:` nu, entao o inventario ficava verde tanto com
`except ValueError` quanto com `except (ValueError, AttributeError)`, e o nivel do log nao e'
uma dimensao que ela enxergue. Medido: as duas mutacoes obvias sobreviviam com 3472 testes
verdes. Este arquivo e' o pino que faltava.

A FALHA DE METRICA E' A DE VERDADE, nao um duplo: o contador do coletor e' trocado por um
`Counter` REAL do `prometheus_client` com `labelnames` diferentes, e quem levanta o `ValueError`
("Incorrect label names") e' a propria biblioteca.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import pytest
import structlog
from prometheus_client import CollectorRegistry, Counter

from maezo.platform import observability
from maezo.runtime.turn_telemetry import emit_turn_desfecho

#: Estado terminal valido de Carolina — vocabulario fechado, nada aqui e' o que quebra.
_ESTADO_VALIDO: Final[dict[str, Any]] = {
    "desfecho": "analise_humana",
    "route": "human_review",
    "mensagem_enviada": False,
}


def _quebra_o_contador(monkeypatch: pytest.MonkeyPatch) -> None:
    """Troca `maezo_agent_desfecho_total` por um contador REAL de labels incompativeis."""
    coletor = observability.get_metrics_collector()
    contador_torto = Counter(
        "maezo_agent_desfecho_total_labels_incompativeis",
        "Contador de teste com labelnames que NAO sao os quatro do contador real",
        labelnames=["dimensao_inexistente"],
        registry=CollectorRegistry(),
    )
    monkeypatch.setattr(coletor, "_agent_desfecho_total", contador_torto)


def test_falha_de_cardinalidade_e_absorvida_e_registrada_em_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) NIVEL + (a) metade externa: a metrica falha, o turno NAO cai, e o log e' WARNING.

    `warning` e' o ponto: em `debug` o contador da frota pode morrer em silencio, que e'
    literalmente o modo de falha que CC-09 existe para tornar visivel.
    """
    _quebra_o_contador(monkeypatch)

    with structlog.testing.capture_logs() as registros:
        emit_turn_desfecho(_ESTADO_VALIDO, agent_id="carolina")  # nao levanta

    falhas = [r for r in registros if r.get("event") == "agent_desfecho_telemetry_emit_failed"]
    assert falhas, f"nenhum registro da falha de metrica: {registros}"
    assert falhas[0]["log_level"] == "warning", falhas[0]
    assert falhas[0]["erro"] == "ValueError", falhas[0]
    assert falhas[0]["agent_id"] == "carolina", falhas[0]


def test_a_falha_de_metrica_e_a_da_biblioteca_de_verdade(monkeypatch: pytest.MonkeyPatch) -> None:
    """NAO-VACUIDADE: o `ValueError` absorvido acima e' mesmo o do `prometheus_client`."""
    contador_torto = Counter(
        "maezo_agent_desfecho_total_prova_de_cardinalidade",
        "Contador de teste com labelnames incompativeis",
        labelnames=["dimensao_inexistente"],
        registry=CollectorRegistry(),
    )

    with pytest.raises(ValueError, match="label"):
        contador_torto.labels(
            agent_id="carolina", desfecho="analise_humana", route="human_review", motivo_categoria=""
        )


class _NaoEhMapping:
    """Um `state` que nao satisfaz `Mapping` — `.get` nem existe."""


class _MappingQueLevantaKeyError:
    """Contrato de estado violado por dentro: `.get` levanta `KeyError`."""

    def get(self, chave: str, padrao: Any = None) -> Any:
        raise KeyError("contrato de estado violado")


class _MappingComAssinaturaDerivada:
    """Deriva de assinatura: `.get` nao aceita o segundo argumento que o modulo passa."""

    def get(self) -> Any:
        raise AssertionError("inalcancavel: a ligacao de argumentos levanta TypeError antes")


_ESTADOS_COM_BUG: Final[dict[str, tuple[Callable[[], Any], type[Exception]]]] = {
    "attribute_error": (_NaoEhMapping, AttributeError),
    "key_error": (_MappingQueLevantaKeyError, KeyError),
    "type_error": (_MappingComAssinaturaDerivada, TypeError),
}


@pytest.mark.parametrize("caso", sorted(_ESTADOS_COM_BUG), ids=sorted(_ESTADOS_COM_BUG))
def test_bug_de_programacao_propaga_em_vez_de_virar_falha_de_metrica(caso: str) -> None:
    """(a) LARGURA: as tres classes de bug que o `except Exception:` anterior engolia."""
    fabrica, esperado = _ESTADOS_COM_BUG[caso]

    with pytest.raises(esperado):
        emit_turn_desfecho(fabrica(), agent_id="carolina")


def test_o_caminho_feliz_continua_emitindo() -> None:
    """NAO-VACUIDADE: o estreitamento nao pode ter quebrado a emissao normal."""
    coletor = observability.get_metrics_collector()
    antes = coletor.agent_desfecho_total.labels(
        agent_id="carolina", desfecho="analise_humana", route="human_review", motivo_categoria=""
    )._value.get()

    emit_turn_desfecho(_ESTADO_VALIDO, agent_id="carolina")

    depois = coletor.agent_desfecho_total.labels(
        agent_id="carolina", desfecho="analise_humana", route="human_review", motivo_categoria=""
    )._value.get()
    assert depois == antes + 1

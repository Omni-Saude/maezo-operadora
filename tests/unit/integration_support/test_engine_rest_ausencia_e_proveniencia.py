"""Unit tests for the two ENGINE-FREE helpers of `tests/integration/processes/engine_rest.py`.

`engine_rest.py` is a self-contained httpx wrapper (no maezo import, no live engine required to
import it), so its PURE logic is exercised here without docker-compose — exactly as
`test_engine_rest_vars.py` does for `_to_camunda_vars`. Nothing here mocks the engine (AGENTS.md
rule 3 / CONTRIBUTING §3): both helpers are pure functions over values the engine already
returned, and the async methods that call them stay in the integration lane.

What is pinned, and why each exists:

1. `is_variable_absent_response` — `get_variable` raises on ANY status != 200, and the engine
   answers **404 `InvalidRequestException: process instance variable with name <n> does not
   exist`** for a variable that was never set. So `assert await engine.get_variable(iid,
   forbidden) is None` — the shape I-PAGTO-1's engine-side proof used — could NEVER pass: the
   404 that IS the proof of absence became an `EngineRestError`. Only that one EXACT pair means
   absence (status **and** the body's `type` + `message` shape), so an engine that is down can
   never be read as "the variable is absent". The predicate used to match `404` plus the bare
   substring `"does not exist"`, which ALSO swallowed the engine's 404s about the *instance* and
   about a *deployment* — a dead or nonexistent instance would then have satisfied the
   leak sweep by vacuity. Those two adversarial bodies are pinned below as FAILURES.

2. `assert_definition_provenance` — the dev-stack engine is SHARED and
   `POST /deployment/create` creates a NEW VERSION of the same process-definition-key, which
   `start_by_key` then instantiates. A `make deploy-artifacts` from ANOTHER checkout, mid
   pytest session, silently takes over the suite. This guard makes that contamination fail
   loud, naming the markers that diverged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.processes.engine_rest import (
    EngineDefinitionProvenanceError,
    EngineRestError,
    assert_definition_provenance,
    is_variable_absent_response,
)
from tests.integration.processes.test_sp_op_recurso_001 import (
    _MARCADORES_DE_OUTRA_DEFINICAO,
    _MARCADORES_DO_CHECKOUT,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BPMN_RECURSO = _REPO_ROOT / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn"

#: Corpo verbatim do 404 do CIB Seven 2.1.0 para uma variavel nunca setada (colhido do log da
#: janela 6: `integ-br-recurso-v2-test_sp_op_recurso_001.log`).
_CORPO_404_AUSENTE = (
    '{"type":"InvalidRequestException","message":"process instance variable with name '
    'dados_pagamento_validos does not exist","code":null}'
)


# ---------------------------------------------------------------------------
# is_variable_absent_response
# ---------------------------------------------------------------------------


def test_404_does_not_exist_e_ausencia() -> None:
    """O 404 canonico do engine E a prova de ausencia — nunca uma falha de comunicacao."""
    assert is_variable_absent_response(404, _CORPO_404_AUSENTE) is True


def test_200_nunca_e_ausencia() -> None:
    """Um 200 devolve valor; a ausencia nao pode ser inferida de um sucesso."""
    assert is_variable_absent_response(200, '{"value":true,"type":"Boolean"}') is False


@pytest.mark.parametrize(
    ("status", "corpo"),
    [
        (500, '{"type":"RestException","message":"execution is null"}'),
        (503, "engine indisponivel"),
        (404, '{"type":"RestException","message":"Process instance not found"}'),
        (401, "unauthorized"),
        # --- os dois corpos ADVERSARIAIS: 404 do proprio engine, mensagem terminando em
        # "does not exist", mas falando de OUTRO recurso. O casamento por substring anterior
        # mapeava ambos para ausencia; sao os que a checagem de `type` + forma da `message`
        # passa a recusar.
        (
            404,
            '{"type":"RestException","message":"Process instance with id foo does not exist"}',
        ),
        (
            404,
            '{"type":"RestException","message":"Deployment with id X does not exist"}',
        ),
        # corpo nao-JSON e corpo JSON sem as chaves: fail-closed
        (404, "does not exist"),
        (404, '{"message":"process instance variable with name x does not exist"}'),
        (404, '{"type":"InvalidRequestException","message":null}'),
    ],
)
def test_falhas_nunca_viram_ausencia(status: int, corpo: str) -> None:
    """FAIL-CLOSED: engine fora do ar, instancia inexistente ou ja concluida (500 "execution is
    null") NAO podem se disfarcar de "variavel ausente" — senao uma assercao de vazamento
    passaria por vacuidade. So o par EXATO (404 + `type` InvalidRequestException + `message` da
    forma `process instance variable with name <n> does not exist`) e ausencia."""
    assert is_variable_absent_response(status, corpo) is False


def test_404_de_outra_variavel_continua_sendo_ausencia() -> None:
    """A checagem e da FORMA da mensagem, nao do nome da variavel: qualquer variavel ausente de
    uma instancia viva continua sendo lida como ausencia (o aperto nao quebrou o caso real)."""
    corpo = (
        '{"type":"InvalidRequestException","message":"process instance variable with name '
        'lastro_confirmado does not exist","code":null}'
    )
    assert is_variable_absent_response(404, corpo) is True


# ---------------------------------------------------------------------------
# assert_definition_provenance
# ---------------------------------------------------------------------------


def test_bpmn_do_checkout_satisfaz_os_marcadores() -> None:
    """Os marcadores usados pelo guard descrevem ESTA arvore — pin contra drift.

    Se um refactor renomear `Start_RecursoRecebido`/`ST_ValidarRecurso`, ou reintroduzir uma das
    palavras da definicao antiga, este teste falha ANTES de o guard de integracao virar um
    falso positivo (ou, pior, um falso negativo silencioso).
    """
    assert_definition_provenance(
        _BPMN_RECURSO.read_text(encoding="utf-8"),
        must_contain=_MARCADORES_DO_CHECKOUT,
        must_not_contain=_MARCADORES_DE_OUTRA_DEFINICAO,
        context="BPMN da arvore",
    )


def test_marcadores_discriminam_dos_dois_lados() -> None:
    """Os dois conjuntos sao DISJUNTOS e nao-vazios — um guard so de presenca nao discriminaria."""
    assert _MARCADORES_DO_CHECKOUT
    assert _MARCADORES_DE_OUTRA_DEFINICAO
    assert not set(_MARCADORES_DO_CHECKOUT) & set(_MARCADORES_DE_OUTRA_DEFINICAO)


def test_marcador_do_checkout_ausente_falha_alto() -> None:
    """Uma definicao alheia (sem os ids desta arvore) e recusada, nomeando o que faltou."""
    with pytest.raises(EngineDefinitionProvenanceError) as exc:
        assert_definition_provenance(
            "<bpmn:process id='SP-OP-RECURSO-001'/>",
            must_contain=_MARCADORES_DO_CHECKOUT,
            must_not_contain=_MARCADORES_DE_OUTRA_DEFINICAO,
            context="definicao sintetica",
        )
    assert "Start_RecursoRecebido" in str(exc.value)
    assert "definicao sintetica" in str(exc.value)


def test_marcador_de_outra_definicao_presente_falha_alto() -> None:
    """O caso REAL da janela 6: um XML que tem os ids desta arvore E os da definicao antiga
    (deploy alheio no meio da sessao) tambem e recusado — o guard checa os DOIS lados."""
    hibrido = " ".join([*_MARCADORES_DO_CHECKOUT, "ST_SubmitAppeal"])
    with pytest.raises(EngineDefinitionProvenanceError) as exc:
        assert_definition_provenance(
            hibrido,
            must_contain=_MARCADORES_DO_CHECKOUT,
            must_not_contain=_MARCADORES_DE_OUTRA_DEFINICAO,
            context="definicao hibrida",
        )
    assert "ST_SubmitAppeal" in str(exc.value)


def test_erro_de_proveniencia_e_um_engine_rest_error() -> None:
    """Subclasse de `EngineRestError`: quem ja capturava o erro do harness continua capturando."""
    assert issubclass(EngineDefinitionProvenanceError, EngineRestError)

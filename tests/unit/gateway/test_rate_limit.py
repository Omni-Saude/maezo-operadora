"""Provas do limitador de taxa do chokepoint de efeitos (gap D6-01).

O que estas provas cobrem, e por que cada uma existe:

  * a ARITMETICA do balde (capacidade, recarga por tempo decorrido, teto, recusa que nao gasta
    token) — com o relogio INJETADO, porque um limitador cuja unica prova e' um `sleep` e' um
    limitador que ninguem re-executa;
  * o ISOLAMENTO POR CHAVE — um agente em loop nao pode gastar o orcamento de outro, nem de outro
    tenant (I-8 aplicado a vazao);
  * a RECUSA ESTRUTURADA em `gate()` — nunca um descarte silencioso: excecao na forma de recusa
    DECLARADA pela classe de acao, contador incrementado, log so' com tokens limitados (I-3);
  * os DEFAULTS das settings, derivados em `rate_limit.py` a partir do proprio
    `worker_runtime/settings.py` (`max_tasks_per_poll`/`poll_interval_ms`), e a recusa fail-closed
    de valores que desligariam o controle.

  * a FORMA TIPADA da recusa em TODAS as operacoes do catalogo, e nao so' em `dmn.evaluate`
    (D6-01-F1) — mais a guarda de A-12 que so' e' alcancavel com uma decisao de PERMISSAO, e que
    por isso precisa de prova direta: com o manifesto em `status: DRAFT` nenhuma chamada real
    produz um ALLOW, entao um teste ponta-a-ponta nunca exercita aquela linha.

MUTACAO QUE FICA VERMELHA (registrada no relatorio da tarefa): remover o bloco
`if not within_rate: ... raise _rate_limited_denial(...)` de `gateway/seams/_base.py::gate`
derruba `test_gate_recusa_a_chamada_acima_do_limite` e
`test_a_recusa_por_taxa_incrementa_o_contador`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from maezo.gateway import effect_classes, rate_limit
from maezo.gateway.rate_limit import (
    DEFAULT_CAPACITY,
    DEFAULT_REFILL_PER_SECOND,
    RateLimiter,
    TokenBucket,
)
from maezo.gateway.seams import _base
from maezo.gateway.seams._base import EffectDeniedError, SeamContext, configure_rate_limiter, gate
from maezo.gateway.settings import GatewaySettings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def limiter_restaurado() -> Iterator[None]:
    """O limitador e' estado de PROCESSO: instalar um balde pequeno e devolver o anterior.

    Feito pela porta publica (`configure_rate_limiter`) e nao mexendo no global do modulo, para
    que o proprio teste use o caminho que uma raiz de composicao usaria.
    """
    anterior = _base._RATE_LIMITER
    yield
    configure_rate_limiter(anterior)


def _seam(principal: str = "helena", tenant: str = "amh") -> SeamContext:
    """Contexto real, do construtor sancionado — nunca um stub local (mesma regra de seams/)."""
    from maezo.gateway.tool_registry import build_agent_seam_context

    return build_agent_seam_context(tenant=tenant, agent_id=principal)


# =================================================================================================
# Aritmetica do balde
# =================================================================================================


def test_o_balde_comeca_cheio_e_gasta_um_token_por_chamada() -> None:
    balde = TokenBucket(capacity=3, refill_per_second=1.0)
    assert balde.tokens == 3.0
    assert [balde.consume(now=0.0) for _ in range(3)] == [True, True, True]
    assert balde.consume(now=0.0) is False


def test_a_recusa_nao_gasta_token() -> None:
    """Uma chamada recusada nao pode cavar o balde e matar de fome as chamadas seguintes."""
    balde = TokenBucket(capacity=1, refill_per_second=1.0)
    assert balde.consume(now=0.0) is True
    assert balde.consume(now=0.0) is False
    assert balde.tokens == 0.0
    assert balde.consume(now=0.0) is False
    assert balde.tokens == 0.0
    # Meio segundo a 2 tokens/s ainda nao basta; um segundo basta.
    assert balde.consume(now=0.5) is False
    assert balde.consume(now=1.0) is True


def test_a_recarga_e_proporcional_ao_tempo_decorrido() -> None:
    balde = TokenBucket(capacity=10, refill_per_second=4.0)
    for _ in range(10):
        assert balde.consume(now=0.0) is True
    assert balde.consume(now=0.0) is False
    # 0,5 s * 4 tokens/s = 2 tokens.
    assert balde.consume(now=0.5) is True
    assert balde.consume(now=0.5) is True
    assert balde.consume(now=0.5) is False


def test_a_recarga_nunca_passa_da_capacidade() -> None:
    """Sem o teto, um processo ocioso acumularia uma rajada ilimitada."""
    balde = TokenBucket(capacity=5, refill_per_second=1000.0)
    for _ in range(5):
        assert balde.consume(now=0.0) is True
    assert balde.consume(now=3600.0) is True  # uma hora ociosa
    assert balde.tokens == 4.0  # recarregou ate' 5 e gastou 1 — nao 3.6 milhoes


def test_um_relogio_que_anda_para_tras_nao_vira_reembolso() -> None:
    balde = TokenBucket(capacity=2, refill_per_second=1.0)
    assert balde.consume(now=100.0) is True
    assert balde.consume(now=100.0) is True
    assert balde.consume(now=99.0) is False


@pytest.mark.parametrize(
    ("capacity", "refill"),
    [(0, 1.0), (-1, 1.0), (1, 0.0), (1, -2.0)],
)
def test_parametros_que_desligariam_o_controle_sao_recusados(capacity: int, refill: float) -> None:
    """Capacidade 0 recusa tudo; recarga 0 estrangula para sempre apos a primeira rajada."""
    with pytest.raises(ValueError):
        TokenBucket(capacity=capacity, refill_per_second=refill)
    with pytest.raises(ValueError):
        RateLimiter(capacity=capacity, refill_per_second=refill)


# =================================================================================================
# Isolamento por chave (tenant, principal)
# =================================================================================================


def test_cada_par_tenant_principal_tem_seu_proprio_balde() -> None:
    limitador = RateLimiter(capacity=2, refill_per_second=1.0)
    assert limitador.allow(tenant="amh", principal="helena", now=0.0) is True
    assert limitador.allow(tenant="amh", principal="helena", now=0.0) is True
    assert limitador.allow(tenant="amh", principal="helena", now=0.0) is False
    # Outro principal, MESMO tenant: orcamento intacto.
    assert limitador.allow(tenant="amh", principal="rafael", now=0.0) is True
    # Outro tenant, MESMO principal: idem (I-8 aplicado a vazao).
    assert limitador.allow(tenant="outro", principal="helena", now=0.0) is True
    assert limitador.keys() == {("amh", "helena"), ("amh", "rafael"), ("outro", "helena")}


# =================================================================================================
# Settings
# =================================================================================================


def test_os_defaults_das_settings_sao_os_derivados_do_modulo() -> None:
    settings = GatewaySettings()
    assert settings.rate_limit_capacity == DEFAULT_CAPACITY == 120
    assert settings.rate_limit_refill_per_second == DEFAULT_REFILL_PER_SECOND == 20.0
    # A derivacao (rate_limit.py, secao "Defaults"): 10 tarefas / 5 s * 6 chamadas gated = 12/s de
    # pior caso legitimo do worker daemon; a recarga tem de ficar ACIMA disso.
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    worker = WorkerRuntimeSettings()
    pior_caso = worker.max_tasks_per_poll / (worker.poll_interval_ms / 1000.0) * 6
    assert settings.rate_limit_refill_per_second > pior_caso
    # E, de vazio, o balde enche dentro do lock da tarefa externa — uma tarefa estrangulada
    # atrasa, nao morre por lock expirado.
    segundos_para_encher = settings.rate_limit_capacity / settings.rate_limit_refill_per_second
    assert segundos_para_encher < worker.lock_duration_ms / 1000.0


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("rate_limit_capacity", 0),
        ("rate_limit_capacity", -5),
        ("rate_limit_refill_per_second", 0.0),
        ("rate_limit_refill_per_second", -1.0),
    ],
)
def test_settings_recusam_valores_que_neutralizariam_o_limitador(campo: str, valor: Any) -> None:
    """Fail-closed e LOUD: nao ha' clamp silencioso para um valor que alguem digitou."""
    with pytest.raises(ValueError):
        GatewaySettings(**{campo: valor})


# =================================================================================================
# O chokepoint: `gate()` recusa, de forma estruturada, acima do limite
# =================================================================================================


async def test_gate_permite_dentro_do_limite(limiter_restaurado: None) -> None:
    configure_rate_limiter(RateLimiter(capacity=2, refill_per_second=1.0))
    seam = _seam()
    decisao = await gate(seam, "dmn.evaluate")
    assert decisao.operation == "dmn.evaluate"


async def test_gate_recusa_a_chamada_acima_do_limite(limiter_restaurado: None) -> None:
    """A prova central de D6-01: acima do limite o efeito NAO acontece.

    Esta e' a prova que fica VERMELHA se o bloco `if not within_rate: ... raise` sair de `gate`.
    """
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    await gate(seam, "dmn.evaluate")
    with pytest.raises(EffectDeniedError) as excinfo:
        await gate(seam, "dmn.evaluate")
    assert excinfo.value.decision.reason == rate_limit.REASON_RATE_LIMITED
    assert excinfo.value.decision.layer == rate_limit.LAYER_RATE_LIMIT
    assert excinfo.value.decision.allow is False
    assert excinfo.value.decision.enforced is True


async def test_a_recusa_por_taxa_chega_no_caminho_declarado_do_no(limiter_restaurado: None) -> None:
    """Forma de recusa = a DECLARADA pela classe, nunca um `RuntimeError` cru (adversario A-12).

    `_evaluate_dmn` captura `(DmnEvaluationError, DmnNoResultError)` e nada mais; uma recusa por
    taxa que escapasse desse `except` derrubaria o turno inteiro — o PEP prejudicando o paciente.
    """
    from maezo.tools.workers.dmn_transport import DmnEvaluationError

    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    await gate(seam, "dmn.evaluate")
    with pytest.raises(DmnEvaluationError):
        await gate(seam, "dmn.evaluate")


async def test_a_recusa_por_taxa_nao_carrega_phi_nem_argumento(limiter_restaurado: None) -> None:
    """I-3: so' tokens limitados na mensagem — operacao, classe, camada, motivo, forma."""
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    segredo = "SP-OP-" + "9" * 6
    await gate(seam, "cibseven.start_process", process_key=segredo)
    with pytest.raises(EffectDeniedError) as excinfo:
        await gate(seam, "cibseven.start_process", process_key=segredo)
    mensagem = str(excinfo.value)
    assert segredo not in mensagem
    assert rate_limit.REASON_RATE_LIMITED in mensagem
    assert "operation=cibseven.start_process" in mensagem


async def test_a_recusa_por_taxa_incrementa_o_contador(limiter_restaurado: None) -> None:
    """Nunca um descarte silencioso: `maezo_effect_rate_limited_total` conta a recusa."""
    from maezo.platform.observability import get_metrics_collector

    contador = get_metrics_collector().effect_rate_limited
    antes = contador.labels(tenant="amh", principal="helena")._value.get()
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    await gate(seam, "dmn.evaluate")
    with pytest.raises(EffectDeniedError):
        await gate(seam, "dmn.evaluate")
    depois = contador.labels(tenant="amh", principal="helena")._value.get()
    assert depois == antes + 1


async def test_o_balde_e_por_principal_tambem_atraves_de_gate(limiter_restaurado: None) -> None:
    """Um agente estrangulado nao estrangula o outro — isolamento visto do chokepoint."""
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    helena = _seam("helena")
    rafael = _seam("rafael")
    await gate(helena, "dmn.evaluate")
    with pytest.raises(EffectDeniedError):
        await gate(helena, "dmn.evaluate")
    # rafael nunca chamou: orcamento intacto.
    await gate(rafael, "dmn.evaluate")


async def test_uma_chamada_que_a_politica_negaria_tambem_gasta_token(
    limiter_restaurado: None,
) -> None:
    """Um loop martelando uma operacao NEGADA e' exatamente a fuga que o limite existe para conter.

    `gateway.desconhecida` nao esta no catalogo -> DENY em L-0. Como toda classe hoje esta em
    `shadow`, o DENY nao levanta; o token, porem, e' gasto — senao um loop de recusas ficaria
    sem qualquer limite.
    """
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    decisao = await gate(seam, "gateway.desconhecida")
    assert decisao.allow is False
    assert decisao.enforced is False
    with pytest.raises(EffectDeniedError) as excinfo:
        await gate(seam, "dmn.evaluate")
    assert excinfo.value.decision.reason == rate_limit.REASON_RATE_LIMITED


async def test_o_limitador_do_processo_sai_das_settings(limiter_restaurado: None) -> None:
    """Sem limitador instalado, a primeira chamada gated constroi um a partir das settings."""
    configure_rate_limiter(None)
    seam = _seam()
    await gate(seam, "dmn.evaluate")
    limitador = _base._RATE_LIMITER
    assert isinstance(limitador, RateLimiter)
    settings = GatewaySettings()
    assert limitador.capacity == settings.rate_limit_capacity
    assert limitador.refill_per_second == settings.rate_limit_refill_per_second


# =================================================================================================
# D6-01-F1 — a recusa por taxa e' TIPADA em toda a superficie gated, nao so' em `dmn.evaluate`
# =================================================================================================

#: Forma declarada -> o tipo que a recusa TEM de ter. Escrito a mao a proposito: ler a resposta de
#: `_DENIAL_FACTORIES` seria pedir a tabela que se confirme sozinha. As duas primeiras existem para
#: serem capturadas pelo `except` especifico do no'; as quatro ultimas sao tipos NOMEADOS (antes de
#: D6-01-F1 eram a base crua `EffectDeniedError`, em 10 das 16 operacoes do catalogo).
_TIPO_POR_FORMA: dict[str, str] = {
    "ROTA_DMN_INDISPONIVEL": "DmnEffectDeniedError",
    "LEITURA_INCONCLUSIVA": "CibSevenEffectDeniedError",
    "INCIDENTE_FALHA_FECHADA": "CibSevenEffectDeniedError",
    "ESCALONAMENTO_HUMANO": "EscalonamentoHumanoDeniedError",
    "LACUNA_DECLARADA": "LacunaDeclaradaDeniedError",
    "ROTA_LLM_INDISPONIVEL": "RotaLlmIndisponivelDeniedError",
    "DEGRADACAO_SEM_DOSSIE": "DegradacaoSemDossieDeniedError",
}


def test_a_tabela_de_formas_cobre_todas_as_formas_declaradas_e_nenhuma_cai_na_base() -> None:
    """Nenhuma forma pode resolver para `EffectDeniedError` cru — a base e' o estado FAIL-CLOSED.

    `denial_for` cai na base quando o catalogo e o manifesto discordam; e' uma condicao de erro
    registrada em ERROR, nunca a forma NORMAL de uma classe declarada. Antes de D6-01-F1 quatro das
    sete formas resolviam para ela por projeto.
    """
    from maezo.gateway.effect_classes import DENIAL_SHAPES

    assert set(_TIPO_POR_FORMA) == set(DENIAL_SHAPES)
    for forma in sorted(DENIAL_SHAPES):
        fabrica = _base._DENIAL_FACTORIES[forma]
        tipo = fabrica()
        assert tipo is not EffectDeniedError, f"{forma} recusa com a base crua"
        assert issubclass(tipo, EffectDeniedError)
        assert tipo.__name__ == _TIPO_POR_FORMA[forma]


@pytest.mark.parametrize("operacao", sorted(effect_classes.OPERATIONS))
async def test_toda_operacao_gated_recusa_por_taxa_na_sua_forma_tipada(
    limiter_restaurado: None, operacao: str
) -> None:
    """A prova de D6-01-F1: as 16 operacoes do catalogo, nao a unica que tinha teste.

    O relatorio original generalizava de `dmn.evaluate` ("cai no handler ja' declarado do no',
    exatamente como qualquer outra recusa do chokepoint") e 10 das 16 operacoes recusavam com
    `EffectDeniedError` cru — um `RuntimeError` num caminho que o modo sombra NUNCA fizera levantar.
    Aqui cada operacao e' dirigida acima do limite e a recusa tem de ser o tipo que a forma
    DECLARADA da classe manda, com o payload de taxa.
    """
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    await gate(seam, operacao)
    with pytest.raises(EffectDeniedError) as excinfo:
        await gate(seam, operacao)

    classe = effect_classes.OPERATIONS[operacao].action_class
    forma = effect_classes.denial_shape_for(classe)
    assert forma is not None
    assert type(excinfo.value).__name__ == _TIPO_POR_FORMA[forma]
    assert type(excinfo.value) is not EffectDeniedError
    decisao = excinfo.value.decision
    assert decisao.reason == rate_limit.REASON_RATE_LIMITED
    assert decisao.layer == rate_limit.LAYER_RATE_LIMIT
    assert decisao.denial_shape == forma
    assert decisao.action_class == classe
    assert decisao.operation == operacao
    assert decisao.allow is False
    assert decisao.enforced is True


@pytest.mark.parametrize(
    ("operacao", "handler"),
    [
        ("fhir.read_patient", "LacunaDeclaradaDeniedError"),
        ("whatsapp.send_message", "EscalonamentoHumanoDeniedError"),
        ("inference.generate", "RotaLlmIndisponivelDeniedError"),
        ("population.actuarial_risk", "LacunaDeclaradaDeniedError"),
        ("a2a.delegate", "DegradacaoSemDossieDeniedError"),
    ],
)
async def test_as_quatro_formas_de_no_amplo_tambem_sao_capturaveis_pelo_nome(
    limiter_restaurado: None, operacao: str, handler: str
) -> None:
    """Um handler que queira SO' esta recusa consegue escreve-la — sem alargar para `Exception`.

    Os nos destas quatro formas capturam `except Exception` e dobram a excecao numa nota de lacuna;
    isso continua funcionando (os tipos novos sao subclasses). O que muda e' que um consumidor pode
    nomear a recusa em vez de depender do `except` largo de cada chamador, e o
    `type(exc).__name__` que os grafos gravam em estado (CC-10) deixa de ser generico.
    """
    from maezo.gateway import seams as seams_pkg

    esperado = getattr(seams_pkg, handler)
    configure_rate_limiter(RateLimiter(capacity=1, refill_per_second=0.001))
    seam = _seam()
    await gate(seam, operacao)
    with pytest.raises(esperado) as excinfo:
        await gate(seam, operacao)
    assert excinfo.value.decision.reason == rate_limit.REASON_RATE_LIMITED


def test_a_forma_da_recusa_por_taxa_vem_da_classe_e_nao_da_decisao() -> None:
    """A guarda de A-12, provada DIRETAMENTE — nenhum teste ponta-a-ponta a alcanca hoje.

    Com o manifesto em `status: DRAFT` toda classe nega, entao `decide_effect` nunca devolve um
    ALLOW e `decision.denial_shape` esta sempre preenchido: mutar
    `denial_shape_for(...) or decision.denial_shape` para `decision.denial_shape` deixava a suite
    inteira VERDE (achado D6-01-F1). Aqui a decisao de PERMISSAO — a unica em que a linha importa —
    e' construida a mao. Mutante `shape = decision.denial_shape` -> VERMELHO neste teste.
    """
    from maezo.gateway.effect_pep import EffectDecision

    permissao = EffectDecision(
        allow=True,
        enforced=False,
        reason="PERMITIDO",
        layer="L5_APROVACAO",
        action_class="avaliacao_dmn",
        denial_shape=None,
        operation="dmn.evaluate",
    )
    recusa = _base._rate_limited_denial(permissao, "dmn.evaluate")
    assert type(recusa).__name__ == "DmnEffectDeniedError"
    assert recusa.decision.denial_shape == "ROTA_DMN_INDISPONIVEL"
    assert recusa.decision.reason == rate_limit.REASON_RATE_LIMITED
    assert recusa.decision.layer == rate_limit.LAYER_RATE_LIMIT
    assert recusa.decision.enforced is True

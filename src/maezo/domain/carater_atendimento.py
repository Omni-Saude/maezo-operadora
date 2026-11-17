"""`carater_atendimento` da autorizacao previa: dominio FECHADO, sem default (DL-0170).

POR QUE ESTE MODULO EXISTE. A regra do carater precisa valer IGUAL nos tres pontos por onde um
pedido de autorizacao chega ao Rafael:

  * a rota de ingresso `POST /v1/autorizacoes` (`runtime/agent_runtime/ingress.py`);
  * a ORIGEM da delegacao A2A (`agents/helena/delegation.py::delegate_auth_analysis`), que valida
    ANTES de montar o envelope — sem isso a recusa do Rafael ficaria selada no `task_id`
    `auth-{tenant}-{guia}` e um reenvio com o carater correto so' repetiria a rejeicao;
  * o proprio Rafael (`agents/rafael/delegation.py::state_from_envelope` e o no' `receive` de
    `agents/rafael/graph.py`), como defesa em profundidade.

Mora em `maezo.domain` (folha: so' biblioteca padrao, nenhuma dependencia de outro pacote `maezo`)
porque e' vocabulario de valor compartilhado: `maezo.domain` esta' no SDK dos agentes
(`contratos/sdk.py::MODULOS_DO_SDK`), entao Helena e Rafael o importam sem cruzar a fronteira do
ADR-0067 (`scripts/ci/check_fronteira_agentes.py`), e a plataforma (rota de ingresso) tambem, sem um
agente importar outro.

A REGRA. Depois de `strip()`, o valor precisa ser EXATAMENTE `"urgencia"` ou `"eletivo"` — o
dominio do contrato SP-OP-AUTH-001 (`docs/processes/contracts/SP-OP-AUTH-001.md`, variavel
`carater_atendimento`) e as duas chaves que a `auth_sla.dmn` reconhece (r1 e r2/r3). Qualquer outro
valor (`"emergencia"`, `"Urgencia"`, `"URGENCIA"`, `"2"`, `"urgência"`) cairia na linha r4 da
`auth_sla` e ganharia P5D — o prazo LONGO — em silencio. Por isso NAO ha mapeamento de sinonimo:
traduzir valor e' escolher prazo regulatorio no lugar de quem informou.
"""

from __future__ import annotations

from typing import Any, Final

#: Nome do campo — um lugar, para a cerca AST reconhecer o alias (`tests/unit/agents/
#: test_carater_atendimento_sem_default.py`).
CAMPO_CARATER_ATENDIMENTO: Final[str] = "carater_atendimento"

#: O dominio fechado. Comparacao EXATA apos `strip()`; sem `lower()`, sem acento, sem sinonimo.
CARATERES_CANONICOS: Final[frozenset[str]] = frozenset({"urgencia", "eletivo"})


class CaraterAtendimentoRecusadoError(ValueError):
    """Base: o carater nao serve para decidir prazo. Nunca carrega o valor recebido.

    `ValueError` de proposito: na delegacao A2A o dispatcher trata essa familia como falha
    TERMINAL de produtor (`a2a/dispatcher.py::_TERMINAL_HANDLER_ERROR_CLASSES`) e devolve uma
    rejeicao selada cujo `detail` carrega so' o nome da classe. Selada quer dizer: o MESMO
    `task_id` nao roda de novo. Por isso a origem valida antes (docstring do modulo).
    """


class CaraterAtendimentoAusenteError(CaraterAtendimentoRecusadoError):
    """O carater faltou: chave ausente, `None`, vazio ou so' espacos."""

    def __init__(self) -> None:
        super().__init__(
            "carater_atendimento ausente ou vazio: o carater nao e' presumido (DL-0170). "
            "Quem origina o pedido precisa informar 'urgencia' ou 'eletivo' ANTES de enviar; "
            "na delegacao A2A a recusa e' terminal para este task_id."
        )


class CaraterAtendimentoInvalidoError(CaraterAtendimentoRecusadoError):
    """O carater veio, mas fora do dominio fechado. O valor NAO entra na mensagem."""

    def __init__(self) -> None:
        super().__init__(
            "carater_atendimento fora do dominio: aceito so' 'urgencia' ou 'eletivo', exatos "
            "(DL-0170). Sinonimo, maiuscula, acento ou codigo TISS nao sao traduzidos, porque "
            "traduzir escolheria o prazo regulatorio; na delegacao A2A a recusa e' terminal "
            "para este task_id."
        )


def carater_atendimento_exigido(valor: Any) -> str:
    """O carater canonico (sem espacos nas pontas) ou uma recusa tipada.

    Ausente = o MESMO criterio da rota de ingresso para campo obrigatorio
    (`str(valor or "").strip()` vazio). Presente fora de `CARATERES_CANONICOS` = invalido.
    """
    texto = str(valor or "").strip()
    if not texto:
        raise CaraterAtendimentoAusenteError()
    if texto not in CARATERES_CANONICOS:
        raise CaraterAtendimentoInvalidoError()
    return texto

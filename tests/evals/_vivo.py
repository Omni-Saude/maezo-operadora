"""O provedor AO VIVO dos evals Tier B, com gravador de chamadas — fonte unica da configuracao.

Por que existe (21/11/2026)
---------------------------
Os grafos ENGOLEM a falha do provedor (degradam para `falha_tecnica`, pedido de reenvio ou "narrativa
indisponivel"), entao um teste vivo que so' olha o resultado nao distingue "o modelo respondeu mal" de "o
modelo nem foi chamado". Medido: com o provedor `anthropic` atual, TODA chamada dos 21 casos vivos sai
com `phi=True` e e' recusada pela fachada (`PhiZoneRoutingError`, ADR-0006) ANTES da rede — e os testes
de nao-vazamento passavam trivialmente com o dossie de indisponivel.

Contrato
--------
* `provedor_vivo()` e' o UNICO jeito de um teste vivo obter o provedor; `scripts/ci/tier_b_preflight.py`
  usa o mesmo, pela mesma rota (`phi=True`) — a configuracao nao e' duplicada.
* `exigir_chamada_viva(...)` roda ANTES de qualquer limiar ou checagem de vazamento: falha com a
  mensagem estavel `LIVE CALL FAILED: <ClasseDoErro>` se alguma chamada ao provedor levantou, ou com
  `LIVE CALL FAILED: NenhumaChamadaAoProvedor` se nenhuma chamada aconteceu.
* Quando um caso vivo roda: `tests/evals/_regra_viva.py` (opt-in `TIER_B_EXECUCAO` em {noturno, manual}, e
  credencial pretendida). Provedor real NOMEADO sem credencial utilizavel => `provedor_vivo()` falha com
  `LIVE CALL FAILED: <ClasseDoErro>` (QUEBRADO), nunca "NAO RODOU".
* `exigir_narrativa(...)`: os testes de nao-vazamento so' checam vazamento sobre narrativa GERADA.
O resumo do noturno (`scripts/ci/tier_b_resumo.py`) le essas mensagens: elas nunca contam como "limiar".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.inference.settings import InferenceSettings

# A REGRA de roda/pula (opt-in de execucao + credencial pretendida) e' de `_regra_viva` (so' stdlib): aqui
# so' se reexporta, nunca se copia.
from ._regra_viva import (
    EXECUCOES_VIVAS,
    PROVEDOR_PADRAO,
    PROVEDORES_DE_TESTE,
    PROVEDORES_VIVOS,
    PROVEDORES_ZONA_PHI,
    VARIAVEL_EXECUCAO,
    VARIAVEL_PROVEDOR,
    credencial_viva_presente,
    execucao_viva,
    motivo_do_pulo,
    provedor_configurado,
)

LIVE_CALL_FAILED = "LIVE CALL FAILED"
SEM_CHAMADA = "NenhumaChamadaAoProvedor"
NARRATIVA_AUSENTE = "NARRATIVA AUSENTE"
#: Classe que indica que o provedor configurado nao pode receber PHI (fachada de `runtime.inference`).
ERRO_ZONA_PHI = "PhiZoneRoutingError"

PROVEDOR_NAO_VIVO = "ProvedorNaoVivo"
#: `provedor_vivo()` sem o opt-in de execucao (TIER_B_EXECUCAO): recusa ANTES de construir o provedor.
SEM_OPT_IN = "SemOptInDeExecucao"


def configuracao_viva() -> InferenceSettings:
    """A configuracao do provedor real dos evals vivos. O provedor vem de `MAEZO_INFERENCE_PROVIDER`
    (como no runtime); sem ela, `anthropic`. Um provedor de teste (`noop`, `phi_zone_mock`) e' RECUSADO:
    um eval vivo nunca "passa" contra resposta enlatada."""
    provedor = provedor_configurado()
    if provedor not in PROVEDORES_VIVOS:
        raise AssertionError(f"{LIVE_CALL_FAILED}: {PROVEDOR_NAO_VIVO} ({VARIAVEL_PROVEDOR}={provedor})")
    return InferenceSettings(provider=provedor)


class GravadorDeChamadas(InferenceProvider):
    """`InferenceProvider` real que registra o desfecho de cada `generate`: `None` (respondeu) ou a
    classe da excecao. Nao muda nada da chamada; so' observa — e repropaga a excecao."""

    def __init__(self) -> None:
        # Defesa em profundidade: um caso vivo sem o `live_key_skip` (esquecido) e sem opt-in de execucao
        # FALHA antes de construir o provedor — zero chamadas, nunca uma chamada paga nao pedida.
        if execucao_viva() is None:
            raise AssertionError(f"{LIVE_CALL_FAILED}: {SEM_OPT_IN} ({VARIAVEL_EXECUCAO} ausente)")
        configuracao = configuracao_viva()  # provedor de teste: ja' levanta LIVE CALL FAILED: ProvedorNaoVivo
        try:
            super().__init__(settings=configuracao)
        except Exception as erro:
            # Provedor real NOMEADO sem credencial utilizavel (ex.: `bedrock_br` sem os portoes da zona PHI ou
            # sem sessao AWS): quem nomeou quis rodar => LIVE CALL FAILED (QUEBRADO), nunca "NAO RODOU".
            # So' a classe do erro sai; a mensagem do provedor fica na causa encadeada.
            raise AssertionError(f"{LIVE_CALL_FAILED}: {type(erro).__name__}") from erro
        self.chamadas: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        try:
            resposta = await super().generate(
                prompt, phi=phi, agent_id=agent_id, tenant_id=tenant_id, task_kind=task_kind
            )
        except BaseException as erro:
            self.chamadas.append(type(erro).__name__)
            raise
        self.chamadas.append(None)
        return resposta


def provedor_vivo() -> GravadorDeChamadas:
    return GravadorDeChamadas()


def exigir_chamada_viva(*gravadores: GravadorDeChamadas) -> None:
    """Falha (mensagem estavel) se nenhuma chamada aconteceu ou se alguma levantou."""
    chamadas = [c for g in gravadores for c in g.chamadas]
    if not chamadas:
        raise AssertionError(f"{LIVE_CALL_FAILED}: {SEM_CHAMADA}")
    for desfecho in chamadas:
        if desfecho is not None:
            raise AssertionError(f"{LIVE_CALL_FAILED}: {desfecho}")


def exigir_narrativa(dossie: Mapping[str, Any] | None) -> None:
    """O teste de nao-vazamento so' vale sobre uma narrativa gerada e nao vazia."""
    narrativa = (dossie or {}).get("narrativa")
    if not isinstance(narrativa, str) or not narrativa.strip():
        raise AssertionError(f"{NARRATIVA_AUSENTE}: o dossie nao tem narrativa gerada")


__all__ = [
    "ERRO_ZONA_PHI",
    "EXECUCOES_VIVAS",
    "LIVE_CALL_FAILED",
    "NARRATIVA_AUSENTE",
    "PROVEDORES_DE_TESTE",
    "PROVEDORES_VIVOS",
    "PROVEDORES_ZONA_PHI",
    "PROVEDOR_NAO_VIVO",
    "PROVEDOR_PADRAO",
    "SEM_CHAMADA",
    "SEM_OPT_IN",
    "VARIAVEL_EXECUCAO",
    "VARIAVEL_PROVEDOR",
    "GravadorDeChamadas",
    "configuracao_viva",
    "credencial_viva_presente",
    "execucao_viva",
    "exigir_chamada_viva",
    "exigir_narrativa",
    "motivo_do_pulo",
    "provedor_configurado",
    "provedor_vivo",
]

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
* Provedor real NOMEADO alem de `anthropic` (`bedrock`, `bedrock_br`, `br_resident`) conta como credencial
  pretendida: o caso RODA, e sem credencial utilizavel `provedor_vivo()` falha com `LIVE CALL FAILED:
  <ClasseDoErro>` (QUEBRADO), nunca "NAO RODOU" — "NAO RODOU" e' so' sem chave E sem provedor nomeado.
* `exigir_narrativa(...)`: os testes de nao-vazamento so' checam vazamento sobre narrativa GERADA.
O resumo do noturno (`scripts/ci/tier_b_resumo.py`) le essas mensagens: elas nunca contam como "limiar".
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from maezo.runtime.inference import InferenceProvider
from maezo.runtime.inference.settings import InferenceSettings

LIVE_CALL_FAILED = "LIVE CALL FAILED"
SEM_CHAMADA = "NenhumaChamadaAoProvedor"
NARRATIVA_AUSENTE = "NARRATIVA AUSENTE"
#: Classe que indica que o provedor configurado nao pode receber PHI (fachada de `runtime.inference`).
ERRO_ZONA_PHI = "PhiZoneRoutingError"
#: A MESMA variavel que o runtime usa para escolher o provedor (`InferenceSettings`, MAEZO_INFERENCE_*).
VARIAVEL_PROVEDOR = "MAEZO_INFERENCE_PROVIDER"
#: Sem a variavel: o comportamento de antes (`anthropic`, nao phi_capable => LIVE CALL FAILED visivel).
PROVEDOR_PADRAO = "anthropic"
#: Provedores REAIS aceitos num eval vivo; os da zona PHI nao usam chave de API (SigV4/sessao do dev).
PROVEDORES_VIVOS = frozenset({"anthropic", "bedrock", "bedrock_br", "br_resident"})
PROVEDORES_ZONA_PHI = frozenset({"bedrock_br", "br_resident"})
#: Provedores de TESTE do runtime (`_PROVIDER_FACTORIES`): nomeados (ex.: `noop` num `.env` local) seguem
#: pulados; num caso que roda (com chave) sao recusados (`ProvedorNaoVivo`), nunca pontuados.
PROVEDORES_DE_TESTE = frozenset({"noop", "phi_zone_mock"})
PROVEDOR_NAO_VIVO = "ProvedorNaoVivo"


def provedor_configurado() -> str:
    return os.environ.get(VARIAVEL_PROVEDOR, "").strip() or PROVEDOR_PADRAO


def credencial_viva_presente() -> bool:
    """Ha' credencial PRETENDIDA para um eval vivo: chave Anthropic, ou qualquer provedor nomeado alem do
    padrao que nao seja de teste. "NAO RODOU" (pulo) fica so' para sem chave E sem provedor nomeado."""
    chave = os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    # Quem nomeou `bedrock`/`bedrock_br`/`br_resident` quis rodar: o caso roda, e sem credencial utilizavel
    # (sessao AWS, portoes `MAEZO_PHI_*`) falha visivel — LIVE CALL FAILED. Um nome desconhecido (ex.: erro de
    # digitacao) tambem roda e falha (`ProvedorNaoVivo`). So' um provedor de TESTE nomeado segue pulado.
    return bool(chave) or provedor_configurado() not in PROVEDORES_DE_TESTE | {PROVEDOR_PADRAO}


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

    def ultima_falhou(self) -> str | None:
        return self.chamadas[-1] if self.chamadas else None


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

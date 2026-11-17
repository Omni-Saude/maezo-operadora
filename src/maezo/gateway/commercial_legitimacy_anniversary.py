"""GTM-E-66 (E-SF-21) — gatilho de aniversário da revalidação de legitimidade coletiva.

**O que é.** No aniversário do contrato (RN 557/2022, art. 10 — reconfirmação anual, que o
substrato materializa em `reconfirmation_due_at`), a tela de legitimidade da PJ contratante
ganha uma NOVA `screen_revision`: linha nova da mesma instância (mesmo `screen_ref` e
`opportunity_ref`), nunca UPDATE de revisão (substrato, migração 0030). A linha copia o
recibo vigente (critério→resultado→referência, status, regime, vencimento) e registra
`would_block=True`, que é o que a postura enforcing decidiria sobre um recibo com a
reconfirmação vencida. É só registro: nenhuma postura aplica efeito a partir dela.

**O que não é.**

- Não é revalidação: revalidar é a staff declarar de novo o checklist completo
  (`LegitimacyScreenRevalidation`, rota da tela). O gatilho não inventa critério, não muda
  resultado nem status e não muda o vencimento: ele só torna o aniversário visível como
  revisão própria, à espera da revalidação humana.
- Não sinaliza o art. 39: o aniversário não é ingresso de beneficiário. O sinal do art. 39
  continua sendo da postura do guard, sobre um comando do caminho coletivo: a idempotência
  do guard é a cabeça já carregar o par `would_block` e `art39_signaled`, então a linha do
  aniversário (`would_block` sem art. 39) não suprime a linha do guard.
- O eixo é a PJ (como no guard: "tela verde" é atributo da PJ): o aniversário registrado é
  o da última revisão da PJ, não o de um contrato específico que tenha disparado o evento.
- Não tem efeito adverso: não chama gateway comercial, comunicação, processo nem efeito.
  Rescisão (`contract_termination`) é item hard L0 e nunca nasce aqui.
- Não está montado: nenhuma composição de `src/` instancia este gatilho; quem o agenda é ato
  sucessor (fonte do evento de aniversário). Sem recibo, sem vencimento ou antes dele, não
  escreve nada; com a cabeça já carregando `would_block`, não escreve de novo (idempotente).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, final

from maezo.gateway.commercial_legitimacy_screen import (
    LegitimacyScreenRecord,
    LegitimacyScreenStore,
    LegitimacyScreenStoreError,
)

__all__ = [
    "ANNIVERSARY_ACTOR",
    "LegitimacyAnniversaryTrigger",
    "anniversary_revision",
]

#: Autor das linhas do gatilho (distinto da staff e do guard).
ANNIVERSARY_ACTOR: Final = "legitimacy_screen_anniversary"


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("anniversary clock must be timezone-aware")
    return now


def anniversary_revision(head: LegitimacyScreenRecord, *, now: datetime) -> LegitimacyScreenRecord | None:
    """A revisão de aniversário que a cabeça pede, ou `None` quando não há o que registrar.

    `None` quando não há vencimento, quando ele ainda não chegou ou quando a cabeça já
    registra `would_block` (aniversário ou bloqueio já visível — idempotente).
    """
    now = _aware(now)
    due = head.reconfirmation_due_at
    if due is None or due > now or head.would_block:
        return None
    return LegitimacyScreenRecord(
        tenant=head.tenant,
        screen_ref=head.screen_ref,
        screen_revision=head.screen_revision + 1,
        legal_entity_ref=head.legal_entity_ref,
        opportunity_ref=head.opportunity_ref,
        contract_kind=head.contract_kind,
        contracting_form=head.contracting_form,
        criteria=head.criteria,
        status=head.status,
        would_block=True,
        art39_signaled=False,
        art39_anchor=None,
        reconfirmation_due_at=head.reconfirmation_due_at,
        created_by=ANNIVERSARY_ACTOR,
        created_at=now,
        updated_at=now,
    )


@final
class LegitimacyAnniversaryTrigger:
    """Lê a última revisão da PJ e, no aniversário, salva a revisão nova. Só isso."""

    def __init__(
        self,
        store: LegitimacyScreenStore,
        *,
        tenant: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._tenant = tenant
        self._clock = clock or (lambda: datetime.now(UTC))

    async def on_anniversary(self, legal_entity_ref: str) -> LegitimacyScreenRecord | None:
        """Registra o aniversário da PJ; devolve a linha escrita, ou `None` se nada foi escrito.

        Corrida (`revision_taken`: guard ou staff gravou a mesma revisão antes): nada deste
        gatilho foi gravado; relê a cabeça UMA vez e decide sobre a revisão vigente (no
        máximo uma escrita). Nova colisão propaga o erro do store: sem laço, sem escrita
        por cima; o próximo disparo relê.
        """
        now = self._clock()
        head = await self._store.latest_for_legal_entity(self._tenant, legal_entity_ref)
        revision = None if head is None else anniversary_revision(head, now=now)
        if revision is None:
            return None
        try:
            return await self._store.save(revision)
        except LegitimacyScreenStoreError as exc:
            if exc.code != "revision_taken":
                raise
        head = await self._store.latest_for_legal_entity(self._tenant, legal_entity_ref)
        revision = None if head is None else anniversary_revision(head, now=now)
        if revision is None:
            return None
        return await self._store.save(revision)

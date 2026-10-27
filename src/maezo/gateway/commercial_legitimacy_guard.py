"""OP21 `legitimacy.screen` — o guard do caminho coletivo (postura shadow; REG-1.1, W1 REG).

**Por que existe.** O parecer do Diretor Jurídico (2026-10-26, snapshot canônico
`docs/compliance/legal/textos-base/PARECER-OP21-2026-10-26-LUCAS.txt` sha256 `d80740cc…`,
registrado em `docs/compliance/legal/PARECER-OP21-RESPOSTA-LUCAS-2026-10-26.md` sha256
`56b8887d…`) orientou "bloquear e registrar … além de também sinalizar o efeito do art. 39";
o desenho `OP21-LEGITIMACY-SCREEN-DESIGN-V1.md` (sha256 `5639f41d…`, §2 DECISÃO 1 e §4
DECISÃO 3) fixou a mecânica: o guard é uma SUBCLASSE de `CommercialGateway` que sobrepõe
`execute` e avalia o comando ANTES de `super().execute(...)` — o `isinstance(gateway,
CommercialGateway)` do router comercial permanece verdadeiro sem editar arquivo congelado
nenhum, e o bind acontece na composition root.

**As três pernas do art. 39, sempre juntas (desenho §6 DECISÃO 5).** Sobre cada comando do
bound set com a PJ contratante SEM recibo vigente, a postura: (1) registraria o BLOQUEIO —
em shadow como `would_block=True` na linha persistida, em enforcing como recusa ANTES de
qualquer passo de fonte/efeito; (2) REGISTRA a verificação — a linha do substrato (#1022)
leva critério→resultado→referência; (3) SINALIZA o efeito — `legitimacy.art39_signaled`
carrega o PAR estrutural {âncora, consequência} verbatim do art. 39. O controle MODELA a
sinalização — nunca constitui o vínculo individual, que é fato jurídico externo.

**Postura shadow (desenho §4 DECISÃO 3): avalia, registra, sinaliza — e NUNCA altera a
resposta comercial.** O resultado que o chamador observa é byte-idêntico ao de flag OFF,
comando legítimo ou ilegítimo. Todo defeito do lado do guard (store indisponível, sink
falhando, relógio ingênuo) é engolido pela postura shadow: o que a shadow não pode fazer é
mudar a venda nem pendurá-la. A postura enforcing (PR 4, classe sucessora com bind explícito
em composition root — nunca por ambiente) é a única que recusa, fail-closed. O gatilho da
recusa sai por `CommercialRefusalError` e a base a renderiza pelo codec — o mesmo caminho
fechado de tokens que o router usa para recusas de admissão.

**Persistência do guard (idempotente; "tela verde" é atributo da PJ).** O guard lê a
ÚLTIMA revisão da PJ pelo port do substrato (`latest_for_legal_entity`) e julga a VIGÊNCIA
(status × `reconfirmation_due_at` × relógio — julgamento da postura, nunca do storage). Ele
persiste SOMENTE a observação postural que a cabeça ainda não registra: uma NOVA revisão da
MESMA instância (mesmo `screen_ref`/`opportunity_ref`, revisão+1) copiando os critérios do
recibo e gravando `would_block`/`art39_signaled` — reenvio do mesmo comando não cria segunda
linha (a cabeça já carrega o sinal), a revalidação da tela pela staff segue sendo revisão
dela, e o guard NUNCA fabrica instância sem oportunidade (o modelo é opportunity-bound; quem
registra a verificação de checklist é a tela staff, parecer §5). PJ sem tela nenhuma: nada a
persistir — o sinal vai pelo evento, e o enforcing bloqueia pela ausência do recibo.

**PHI firewall camada 2 (registry Q7 §3).** O guard avalia exatamente dois fatos
contratacionais — `scope.legal_entity_ref` e o recibo fechado do substrato — e NUNCA abre o
corpo do comando (payload opaco ao guard; o parse é do codec, dentro do
`super().execute`). Nenhum modelo deste módulo tem campo onde dado clínico/de saúde caiba
(fechado por `extra="forbid"`); o token `PHI_IN_COMMERCIAL_INPUT` é o vocabulário da recusa
da camada de admissão (prova 8, PR 4).

**Citação (parecer §1/§4).** Registros e eventos citam a contratação por artigo PRÓPRIO da
RN 557/2022 e a penalidade pela forma canônica "RN 489/2022, art. 23-A (redação dada pela
RN 659/2025)". Nenhuma caracterização jurídica do canal existe em texto deste módulo
(parecer §3) — o guard não fala do canal, fala da PJ contratante.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Final, Literal, Protocol, Self, final

from pydantic import BaseModel, ConfigDict, model_validator

from maezo.gateway.commercial import CommercialGateway
from maezo.gateway.commercial_legitimacy_screen import (
    ART39_ANCHOR,
    ContractKind,
    LegitimacyScreenRecord,
    LegitimacyScreenStore,
)
from maezo.portal.contracts.models import OpaqueRef
from maezo.ports.commercial import (
    CommercialCommand,
    CommercialRefusalError,
    CommercialScope,
    OperationalResult,
)

__all__ = [
    "ART39_SIGNAL_ANCHOR",
    "ART39_SIGNAL_CONSEQUENCE",
    "ART39_TWIN_ANCHOR",
    "COLLECTIVE_BOUND_SET",
    "GUARD_ACTOR",
    "LEGITIMACY_UNPROVEN",
    "PHI_IN_COMMERCIAL_INPUT",
    "SOURCE_UNAVAILABLE",
    "LegitimacyArt39Signal",
    "LegitimacyEventKind",
    "LegitimacyScreenEvaluation",
    "LegitimacyScreenEvent",
    "LegitimacyScreenEventSink",
    "LegitimacyScreenGateway",
    "LegitimacyScreenGuardFactory",
    "LegitimacyScreenShadow",
    "collective_legitimacy_shadow",
]

# ----------------------------------------------------------------------------------
# O bound set FECHADO (desenho §2 DECISÃO 1: o arco PJ-contratante→proposta→aceite→
# emissão). Tudo fora dele (work.*, catalog.*, payment.*, mandate.*, support.*,
# operator.*, journey.*, receivable.*, intake.*, prospect.*, contact.*,
# financial_action.*, operation.*) passa intocado: nenhum comando fora do set avalia,
# persiste, emite evento ou atrasa.
# ----------------------------------------------------------------------------------

COLLECTIVE_BOUND_SET: Final[frozenset[str]] = frozenset(
    {
        "party.prepare",
        "party.submit",
        "party.link",
        "offer.quote",
        "offer.terms",
        "acceptance.record",
        "contract.issue",
    }
)

# ----------------------------------------------------------------------------------
# O vocabulário de recusa das posturas (tokens D4 do registry §1.4 — definidos UMA vez,
# aqui; o substrato #1022 não os antecipou). Valores na forma de wire. `source_unavailable`
# já existe no enum FECHADO de `reason_code` do codec C6-4; `legitimacy_unproven` e
# `phi_in_commercial_input` NÃO estão nele — a ida a wire de um token novo é negociação de
# schema sucessor (a recusa byte-exata do codec é prova do PR de enforcing), não decisão
# local deste módulo.
# ----------------------------------------------------------------------------------

LEGITIMACY_UNPROVEN: Final = "legitimacy_unproven"
SOURCE_UNAVAILABLE: Final = "source_unavailable"
PHI_IN_COMMERCIAL_INPUT: Final = "phi_in_commercial_input"

# ----------------------------------------------------------------------------------
# O PAR estrutural da sinalização do art. 39 (desenho §6, verbatim) e o gêmeo do regime
# do empresário individual (RN 557/2022, art. 10, §§ 2º e 3º — "equipara-se … conforme
# prevê o artigo 39 desta resolução": mesma consequência, âncora própria). A linha
# persistida carrega sempre a âncora canônica do art. 39 (Literal fechado do substrato);
# o gêmeo via no EVENTO, pelo par.
# ----------------------------------------------------------------------------------

ART39_SIGNAL_ANCHOR: Final = ART39_ANCHOR
ART39_TWIN_ANCHOR: Final = "RN 557/2022, art. 10, §§ 2º e 3º"
ART39_SIGNAL_CONSEQUENCE: Final = (
    "vinculo_direto_e_individual_com_a_operadora_equiparado_ao_plano_individual_ou_familiar"
)

#: Autor das linhas que o guard persiste — token interno fixo, nunca um principal de
#: sessão (o guard observa; não é pessoa).
GUARD_ACTOR: Final = "legitimacy_screen_guard"

#: Molde das classes fechadas deste módulo (mesmo do substrato #1022).
_MODEL: Final = ConfigDict(
    frozen=True,
    strict=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
)


class _Closed(BaseModel):
    model_config = _MODEL


#: Espécies de evento do OP21 — os três do registry (aprovação/bloqueio/expiração) mais a
#: sinalização do efeito do art. 39 (desenho §3, R-7 do registry).
LegitimacyEventKind = Literal[
    "legitimacy.approved",
    "legitimacy.blocked",
    "legitimacy.expired",
    "legitimacy.art39_signaled",
]


@final
class LegitimacyArt39Signal(_Closed):
    """O PAR estrutural {âncora, consequência} — fechado em exatamente dois valores.

    A âncora é a do art. 39 OU a do gêmeo do empresário individual (art. 10 §§ 2º-3º); a
    consequência é o efeito verbatim comum aos dois. Nenhum terceiro valor, nenhum texto
    livre, nenhum dado de pessoa (G-PHI: o par é fato de CONTROLE, não de beneficiário).
    """

    anchor: Literal["RN 557/2022, art. 39", "RN 557/2022, art. 10, §§ 2º e 3º"]
    consequence: Literal[
        "vinculo_direto_e_individual_com_a_operadora_equiparado_ao_plano_individual_ou_familiar"
    ]


@final
class LegitimacyScreenEvent(_Closed):
    """Um fato de postura emitido por avaliação de comando do bound set.

    FECHADO por construção (G-PHI): exatamente estes campos e nenhum outro — o wire do
    comando nunca é aberto pelo guard, então não existe campo onde dado de beneficiário
    caiba. O par do art. 39 é EXCLUSIVO da espécie `legitimacy.art39_signaled` (validador
    abaixo); os eventos de resultado o carregam como `None`.
    """

    schema_version: Literal["legitimacy-screen-event.v1"] = "legitimacy-screen-event.v1"
    kind: LegitimacyEventKind
    tenant: OpaqueRef
    contract_kind: ContractKind | None
    would_block: bool
    art39: LegitimacyArt39Signal | None = None

    @model_validator(mode="after")
    def _par_exclusivo(self) -> Self:
        if (self.art39 is not None) != (self.kind == "legitimacy.art39_signaled"):
            raise ValueError("the art. 39 signal pairs only with the art39_signaled kind")
        return self


class LegitimacyScreenEventSink(Protocol):
    """Coletor dos eventos de postura — a composition root é quem o liga (ou não).

    Síncrono e best-effort POR CONTRATO: uma falha do coletor é engolida pela postura
    shadow (nada dela altera o caminho comercial). Sem coletor ligado, os eventos não
    saem do processo; a persistência segue.
    """

    def emit(self, event: LegitimacyScreenEvent) -> None: ...


@final
class LegitimacyScreenEvaluation(_Closed):
    """O veredito da avaliação de um comando do bound set — valor, nunca efeito.

    `receipt` é a ÚLTIMA revisão da PJ (ou `None` — ausência, nunca tela fabricada);
    `outcome` é a espécie do evento de resultado; `art39` é o par da sinalização quando a
    postura registraria bloqueio (`would_block=True`), `None` caso contrário.
    """

    outcome: Literal["approved", "blocked", "expired"]
    would_block: bool
    receipt: LegitimacyScreenRecord | None
    art39: LegitimacyArt39Signal | None


#: A espécie de evento de cada resultado da avaliação (a sinalização do art. 39 é emitida
#: À PARTE, pela perna SINALIZAR — nunca por este mapa).
_OUTCOME_EVENT_KINDS: Final[dict[str, LegitimacyEventKind]] = {
    "approved": "legitimacy.approved",
    "blocked": "legitimacy.blocked",
    "expired": "legitimacy.expired",
}


LegitimacyScreenGuardFactory = Callable[[CommercialGateway], "LegitimacyScreenGateway"]


# ----------------------------------------------------------------------------------
# O guard: SUBCLASSE de `CommercialGateway` (desenho §2 item 3). O wrap é por herança —
# o objeto composto É o gateway do caminho comercial (o `isinstance` do router congelado
# permanece verdadeiro) e `super().execute(...)` é o caminho integral de sempre.
# ----------------------------------------------------------------------------------


class LegitimacyScreenGateway(CommercialGateway):
    """Base do guard do caminho coletivo: bound set + avaliação + o quadro de postura.

    Esta base NÃO é composable por si (`_posture` é aberto — só uma CLASSE DE POSTURA a
    implementa; a composition root valida a classe exata). Para comandos FORA do bound
    set o `execute` desta base é o de `CommercialGateway`, byte a byte — a única diferença
    observável é uma adesão a frozenset.
    """

    def __init__(
        self,
        *,
        store: LegitimacyScreenStore,
        tenant: str,
        events: LegitimacyScreenEventSink | None = None,
        **gateway: Any,
    ) -> None:
        super().__init__(**gateway)
        if not isinstance(tenant, str) or not tenant:
            raise ValueError("legitimacy_screen_tenant_invalid")
        if not callable(getattr(store, "latest_for_legal_entity", None)) or not callable(
            getattr(store, "save", None)
        ):
            raise ValueError("legitimacy_screen_store_invalid")
        self.store = store
        self.tenant = tenant
        self.events = events

    async def execute(self, command: CommercialCommand, scope: CommercialScope) -> OperationalResult:
        if command.command in COLLECTIVE_BOUND_SET:
            try:
                await self._posture(command, scope)
            except CommercialRefusalError as exc:
                # Recusa DECLARADA de postura (enforcing): renderiza pelo codec — os
                # mesmos tokens fechados que o router renderiza para recusas de admissão.
                # A shadow nunca a levanta (engole os próprios defeitos dentro de
                # `_posture`), então este ramo é inerte nela por construção.
                return self._posture_refusal(exc)
            except NotImplementedError:
                # A base NÃO é composable: posture ausente é defeito de composição LOUD
                # (fail-closed), nunca passagem silenciosa sem triagem.
                raise
            except Exception:
                # Defeito NÃO declarado da postura: nunca altera o caminho por acidente.
                # O enforcing é fail-closed traduzindo os próprios defeitos em recusa
                # declarada DENTRO de `_posture`; aqui nada é engolido duas vezes.
                pass
        return await super().execute(command, scope)

    async def _posture(self, command: CommercialCommand, scope: CommercialScope) -> None:
        """A consequência da postura sobre a avaliação — aberto por desenho (§4).

        Shadow: registra + sinaliza, nunca recusa. Enforcing (PR 4): recusa fail-closed
        ANTES de qualquer passo de fonte/efeito, por `CommercialRefusalError`.
        """
        raise NotImplementedError("legitimacy_screen_posture_required")

    def _posture_refusal(self, exc: CommercialRefusalError) -> OperationalResult:
        try:
            return self.codec.refusal(exc.status, exc.reason_code)
        except Exception:
            return self.codec.refusal("unavailable", SOURCE_UNAVAILABLE)

    async def _evaluate(self, scope: CommercialScope) -> LegitimacyScreenEvaluation:
        """A avaliação da PJ contratante — os DOIS fatos contratacionais do guard.

        Vigência (julgamento da postura, port do substrato é só leitura): recibo `valid`
        com `reconfirmation_due_at` ausente ou futura = vigente; `valid` com reconfirmação
        vencida = expirado (art. 10); `pending`/`invalid`/ausência = não comprovado —
        PENDENTE ≠ APROVADO por omissão (registry OP21). `would_block` é o que o
        enforcing decidiria: recibo vigente único por PJ é pré-condição do bound set.
        """
        receipt = await self.store.latest_for_legal_entity(self.tenant, scope.legal_entity_ref)
        if receipt is None:
            return LegitimacyScreenEvaluation(
                outcome="blocked",
                would_block=True,
                receipt=None,
                art39=self._art39_signal(None),
            )
        if receipt.status == "valid" and (
            receipt.reconfirmation_due_at is None or receipt.reconfirmation_due_at > self._clock()
        ):
            return LegitimacyScreenEvaluation(
                outcome="approved", would_block=False, receipt=receipt, art39=None
            )
        if receipt.status == "valid":
            return LegitimacyScreenEvaluation(
                outcome="expired",
                would_block=True,
                receipt=receipt,
                art39=self._art39_signal(receipt.contract_kind),
            )
        return LegitimacyScreenEvaluation(
            outcome="blocked",
            would_block=True,
            receipt=receipt,
            art39=self._art39_signal(receipt.contract_kind),
        )

    def _art39_signal(self, contract_kind: ContractKind | None) -> LegitimacyArt39Signal:
        # O gêmeo do empresário individual sinaliza com a ÂNCORA PRÓPRIA (art. 10 §§ 2º-3º);
        # sem recibo (regime desconhecido) a âncora é a canônica do art. 39 — que é o efeito
        # que o próprio §2º do art. 10 invoca. A consequência é a mesma, verbatim.
        anchor = ART39_TWIN_ANCHOR if contract_kind == "empresario_individual" else ART39_SIGNAL_ANCHOR
        return LegitimacyArt39Signal(anchor=anchor, consequence=ART39_SIGNAL_CONSEQUENCE)  # type: ignore[arg-type]


@final
class LegitimacyScreenShadow(LegitimacyScreenGateway):
    """A postura SHADOW (desenho §4): avalia, registra, sinaliza — nunca altera a venda.

    Toda a consequência desta postura acontece DENTRO de `_posture`, com cada perna
    blindada: falha de persistência não impede o evento, falha de evento não altera a
    resposta, e o TODO é executado sob o mesmo teto de tempo do gateway — a shadow nunca
    pendura o comando no store. O resultado comercial é sempre o de `super().execute`.
    """

    async def _posture(self, command: CommercialCommand, scope: CommercialScope) -> None:
        # O corpo INTEIRO é blindado — inclusive a LEITURA em `_evaluate` (OBS-1 do gate
        # cego do PR): um store não-conforme ao Protocol do substrato que levante até
        # `CommercialRefusalError` na leitura não pode acionar o ramo de recusa da base —
        # o invariante byte-idêntico vale contra QUALQUER defeito do lado do guard.
        with suppress(Exception):
            async with asyncio.timeout(self.timeout_seconds):
                evaluation = await self._evaluate(scope)
                await self._register(evaluation)
                self._signal(evaluation)

    async def _register(self, evaluation: LegitimacyScreenEvaluation) -> None:
        """A perna REGISTRAR: só a observação que a cabeça AINDA não registra.

        Nova revisão da MESMA instância (mesmo `screen_ref`/`opportunity_ref`), critérios
        do recibo copiados (critério→resultado→referência — o guard não inventa critério),
        `would_block`/`art39_signaled` da postura, âncora canônica do art. 39. Cabeça que
        já carrega o sinal, aprovação, e PJ sem recibo: nada a escrever — idempotente.
        """
        receipt = evaluation.receipt
        if receipt is None or not evaluation.would_block or receipt.would_block:
            return
        now = self._clock()
        await self.store.save(
            LegitimacyScreenRecord(
                tenant=receipt.tenant,
                screen_ref=receipt.screen_ref,
                screen_revision=receipt.screen_revision + 1,
                legal_entity_ref=receipt.legal_entity_ref,
                opportunity_ref=receipt.opportunity_ref,
                contract_kind=receipt.contract_kind,
                contracting_form=receipt.contracting_form,
                criteria=receipt.criteria,
                status=receipt.status,
                would_block=True,
                art39_signaled=True,
                # Literal inline (mypy não aceita variável em Literal) — byte-igual a
                # ART39_ANCHOR/ART39_SIGNAL_ANCHOR, igualdade que o teste pina.
                art39_anchor="RN 557/2022, art. 39",
                reconfirmation_due_at=receipt.reconfirmation_due_at,
                created_by=GUARD_ACTOR,
                created_at=now,
                updated_at=now,
            )
        )

    def _signal(self, evaluation: LegitimacyScreenEvaluation) -> None:
        """A perna SINALIZAR: o evento de resultado + o par do art. 39 quando bloquearia."""
        if self.events is None:
            return
        outcome_kind = _OUTCOME_EVENT_KINDS[evaluation.outcome]
        self.events.emit(
            LegitimacyScreenEvent(
                kind=outcome_kind,
                tenant=self.tenant,
                contract_kind=evaluation.receipt.contract_kind if evaluation.receipt else None,
                would_block=evaluation.would_block,
            )
        )
        if evaluation.art39 is not None:
            self.events.emit(
                LegitimacyScreenEvent(
                    kind="legitimacy.art39_signaled",
                    tenant=self.tenant,
                    contract_kind=evaluation.receipt.contract_kind if evaluation.receipt else None,
                    would_block=True,
                    art39=evaluation.art39,
                )
            )


def collective_legitimacy_shadow(
    gateway: CommercialGateway | None,
    *,
    enabled: bool,
    guard_factory: LegitimacyScreenGuardFactory | None = None,
) -> CommercialGateway | None:
    """A composition root da postura — a flag é o ÚNICO caminho até aqui, e só até a shadow.

    OFF (shipped): devolve o objeto RECEBIDO, sem wrap — o caminho comercial é byte a byte
    o de hoje, e nenhum componente do guard existe na composição. ON: exige a fábrica do
    guard SEM default (dois portões, molde do lembrete de cobrança — virar a chave sem o
    bind explícito é recusa de boot, não shadow silenciosa) e aceita EXCLUSIVAMENTE a
    classe `LegitimacyScreenShadow` exata: nenhuma subclasse, nenhuma outra postura, e a
    enforcing (sucessora, PR 4) nunca é alcançável por ambiente — prova 4 do desenho §7.
    """
    if not enabled or gateway is None:
        return gateway
    if guard_factory is None:
        raise ValueError("legitimacy_screen_guard_unbound")
    wrapped = guard_factory(gateway)
    # Portão de postura: `type(...) is` — nenhuma subclasse passa. A enforcing nunca é
    # montável por esta porta, nem por fábrica que a devolva.
    if type(wrapped) is not LegitimacyScreenShadow:
        raise ValueError("legitimacy_screen_posture_invalid")
    return wrapped

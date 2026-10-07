"""Telemetria presence-only da atividade vendor — contadores agregados, ZERO conteúdo (VW5+).

**Contrato (WAVES §2.9-i + §5).** A atividade da audiência `vendor` é observável apenas como
PRESENÇA: contadores inteiros agregados por SINAL de catálogo fechado e booleanos de presença
("o sinal ocorreu na janela"), nunca medida, nunca duração, nunca valor. O titular aqui é o
VENDEDOR COMO CATEGORIA (a audiência), nunca um vendedor individual — nenhum campo deste módulo
aceita, carrega ou deriva identificador de pessoa, referência opaca de canal, payload ou
qualquer campo clínico (G-PHI, VW0-D23). A asserção estrutural de PHI não é uma promessa de
disciplina: é a FORMA das assinaturas. `record` recebe exatamente um argumento posicional, um
membro do enum fechado, e NÃO existe parâmetro `**kwargs`/`*args`/payload em nenhuma superfície
pública — um payload não tem onde entrar, logo não tem onde sair.

**Por que módulo folha.** Este arquivo não importa NADA de `maezo.*` — nem gateway, nem portal,
nem runtime. Um tipo que possa carregar conteúdo (`ChannelSubmissionCommand`, `MembershipRecord`,
`bytes` de negócio) não é sequer NOMEÁVEL aqui. A cerca que trava essa propriedade é
`tests/unit/platform/test_vendor_activity_telemetry.py` (import em interpretador NOVO medindo
`sys.modules`, espelho da cerca da observabilidade). O acoplamento com as fontes reais dos
eventos (OP16 `submission.*`, job de publicação VW1-P0, sessão vendor do portal) é papel do
CALLER, que traduz o evento já existente num `record(SINAL)` — este módulo nunca vai buscar o
evento nem lê o comando que o originou.

**KPI moldável (WAVES §5).** `phi_egress_violations` é COMPUTADO, não hardcoded: é a varredura
do estado interno que conta entradas cuja chave foge ao catálogo fechado ou cujo valor não é
`int` exato. Com a superfície atual ele é estruturalmente 0 — e o teste prova que continua 0
DEPOIS de tentativas de contrabando (string, kwarg, posicional extra) e prova que vira positivo
se o estado interno for corrompido à mão. Se um código futuro fizer `phi_egress_violations == 0`
virar constante, este módulo deixa de cumprir o contrato.

**Não é isto.** Não é medidor de SLA (nenhum timer — RATIFY-LATER), não é evento de domínio
(nada é publicado), não liga flag nem decide go/no-go — é leitor de presença para o dashboard de
contagens (`docs/audits/VENDOR-FASE-B-2026-10/VW5-METRICS.md`), e o go/no-go de deploy
permanece HUMANO.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PHI_EGRESS_VIOLATIONS_KPI",
    "SNAPSHOT_SCHEMA",
    "ContentSmugglingError",
    "VendorActivityCounters",
    "VendorActivitySignal",
    "VendorActivitySnapshot",
]


class ContentSmugglingError(TypeError):
    """Um argumento além do sinal fechado foi apresentado a um contador presence-only."""


class VendorActivitySignal(StrEnum):
    """Catálogo FECHADO de sinais de presença — cada membro cita a fonte já existente.

    Nenhum sinal novo é inventado aqui: cada um é a tradução presence-only de um evento que já
    existe no código (VW1-P0 job, OP16 máquina, sessão vendor do portal). Um sinal novo exige
    fonte nomeada e edição revisada deste catálogo — nunca um `record("string solta")`.
    """

    #: Sessão com perfil de capacidades vendor aberta no portal (audiência, não usuário).
    SESSION_OPENED = "session_opened"
    #: OP16 `submission.received` — comando ADMITIDO pela máquina (ALLOW `received`).
    SUBMISSION_RECEIVED = "submission_received"
    #: OP16 reenvio idempotente da MESMA business key (ALLOW `replayed_active_instance`).
    SUBMISSION_REPLAYED = "submission_replayed"
    #: OP16 recusa tipada (DENY: `STALE_REVISION`, `PHI_IN_COMMERCIAL_INPUT`, fonte ausente...).
    SUBMISSION_REFUSED = "submission_refused"
    #: OP16 resposta de formalização concluída (`formalization_response`).
    SUBMISSION_RESPONDED = "submission_responded"
    #: Cadência VW1-P0 rodou (o desfecho da rodada vem nos dois sinais seguintes).
    PUBLICATION_ATTEMPTED = "publication_attempted"
    #: Cadência VW1-P0 recusou (fonte ausente/vazia = UNKNOWN — `ReadRefusalError`).
    PUBLICATION_REFUSED = "publication_refused"
    #: Cadência VW1-P0 publicou >= 1 projeção de acesso no access plane.
    PUBLICATION_DELIVERED = "publication_delivered"


#: O KPI que o dashboard de contagens lê. `== 0` é o invariante verificável por teste (WAVES §5).
PHI_EGRESS_VIOLATIONS_KPI: Final = "phi_egress_violations"

#: Schema do snapshot serializado — versão FECHADA; mudou o shape, muda o schema.
SNAPSHOT_SCHEMA: Final = "maezo-vendor-activity-presence/v1"


class VendorActivitySnapshot(BaseModel):
    """Forma fechada do agregado — só `str` de catálogo, `int` exato e `bool` exato.

    `strict=True` + `extra="forbid"` + frozen: nenhum campo heterogêneo sobrevive aqui e o
    snapshot de uma janela não é mutável por quem o lê. `presence` é DERIVADO (`count > 0`) —
    presença, não medida.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    #: `schema_` com alias `"schema"` (precedente `VendorLedgerState`): o nome público no JSON é
    #: `"schema"`, mas o atributo não sombreia o método homônimo (deprecado) do pydantic.
    schema_: str = Field(default=SNAPSHOT_SCHEMA, alias="schema", frozen=True)
    counts: Mapping[str, int]
    presence: Mapping[str, bool]
    phi_egress_violations: int

    def as_json(self) -> str:
        """JSON determinístico (chaves ordenadas) para o dashboard de contagens."""
        return json.dumps(
            {
                "schema": self.schema_,
                "counts": dict(sorted(self.counts.items())),
                "presence": dict(sorted(self.presence.items())),
                PHI_EGRESS_VIOLATIONS_KPI: self.phi_egress_violations,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class VendorActivityCounters:
    """Contadores agregados presence-only. Processo único, thread-ingênuo por contrato.

    A superfície INTEIRA desta classe se resume a `record(signal)` + leituras. Não existe método
    que aceite payload, referência de pessoa, conteúdo ou `**kwargs` — a ausência é o firewall.
    """

    __slots__ = ("_counts",)

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record(self, signal: VendorActivitySignal, /) -> None:
        """Conta UMA ocorrência do sinal. Este é o ÚNICO caminho de escrita que existe.

        `type(...) is not` (e não `isinstance`): a string solta `"session_opened"` é um `str`,
        não é o membro do catálogo — e a string solta é exatamente o canal por onde conteúdo
        disfarçado entraria. Recusa com `ContentSmugglingError` (subtipo de `TypeError`).
        """
        if type(signal) is not VendorActivitySignal:  # noqa: E721 - recusa de str solta É o objetivo
            raise ContentSmugglingError(
                "contador presence-only aceita apenas um membro do catálogo fechado "
                "VendorActivitySignal; nenhum payload, conteúdo ou string solta tem entrada aqui"
            )
        key = signal.value
        self._counts[key] = self._counts.get(key, 0) + 1

    def snapshot(self) -> VendorActivitySnapshot:
        """Agregado imutável da janela corrente, catálogo completo (zeros explícitos).

        A FORMA do snapshot nunca herpa: entrada irregular do estado interno (chave fora do
        catálogo, valor não-`int` exato, negativo) NÃO entra no merge — ela é acusada no KPI
        (`phi_egress_violations`), que é computado na MESMA varredura. O snapshot fica sempre
        bem-formado; a irregularidade nunca vira dado.
        """
        counts: dict[str, int] = {signal.value: 0 for signal in VendorActivitySignal}
        catalog = frozenset(counts)
        for key, value in self._counts.items():
            if key not in catalog or type(value) is not int or value < 0:
                continue
            counts[key] += value
        return VendorActivitySnapshot(
            counts=counts,
            presence={key: value > 0 for key, value in counts.items()},
            phi_egress_violations=self._phi_egress_violations(),
        )

    def reset(self) -> None:
        """Zera a janela (rotação do dashboard). Não preserva histórico — aqui não há histórico."""
        self._counts.clear()

    def _phi_egress_violations(self) -> int:
        """Varredura do estado interno: entrada fora do catálogo ou com valor não-`int` exato.

        É o KPI COMPUTADO do contrato. Sob a superfície atual ele só pode ser 0; se um código
        futuro abrir caminho de conteúdo, esta varredura é a que acusa.
        """
        catalog = {signal.value for signal in VendorActivitySignal}
        return sum(
            1
            for key, value in self._counts.items()
            if key not in catalog or type(value) is not int or value < 0
        )

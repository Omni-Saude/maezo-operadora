#!/usr/bin/env python3
"""Dashboard de desvios shadow — W7 7.5 (muro 2027-02-09; SÓ DADOS RASTREADOS).

Purpose
-------
O plano (`docs/plans/maezo-completion-program.md:118`, item 7.5) pede um "dashboard de desvios
shadow (muro 2027-02-09; só de dados rastreados)". Este script é esse dashboard: um GERADOR
DETERMINÍSTICO que lê as fontes RASTREADAS do repositório e emite um artefato citável
(`docs/delivery/w7/shadow-deviation-dashboard.md`) com, por item shadow:

    flag/estado · prazo · dias-restantes-hoje · fonte arquivo:linha · status

onde status ∈ {`NO-PRAZO`, `VENCIDO`, `SEM-DATA`} (ver "Semântica de status" abaixo).

A fronteira do dashboard é a fronteira do rastreável. O registry vendor OP-REGISTRY é
gitignored — se um desvio só existir lá, ele NÃO entra como linha de dados; entra como lacuna
declarada no relatório do pacote. Nenhuma data é inventada para fechar contagem.

Fontes rastreadas (as ÚNICAS; cada linha do artefato cita arquivo:linha)
-----------------------------------------------------------------------
1. `spec/policies/autonomy/action-approvals.yaml` — LEITO PELO LOADER TIPADO
   (`maezo.gateway.action_execution.load_action_approvals`), nunca por YAML cru: `modo`,
   `default_enforcement`, `class_enforcement`, `deviations` (blocos `deviation:` ratificados
   2026-08-13, renovados 2026-09-20) e `deviation_defects`. Mesma superfície que o runtime e o
   gate `scripts/ci/check_deviation_expiry.py` usam — o dashboard não pode divergir da leitura
   que o gateway faz do próprio registro.
2. `docs/plans/w5-5-1-flags-reancoragem.md` §3 — inventário rastreado das flags de
   habilitação dark (6 grupos; 33 interruptores ENUMERADOS linha a linha — o headline do doc
   diz 31, a soma dos parciais declarados por ele é 5+3+3+1+19+2 = 33; a divergência é
   registrada no painel, não reproduzida). Cada linha cita a linha da tabela do doc E a âncora
   de código da flag (verificada viva contra a árvore).
3. `docs/review-queue.md` — as linhas da fila que nomeiam interruptor + revisor + janela
   (OP21 `legitimacy.screen` :33, pin TISS :690, `REEMBOLSO-AUTO-OVERPAY` :1750, candidatos
   shadow M-4..M-7 :640-643, matriz auth-criteria :22).
4. `docs/plans/maezo-completion-program.md` — os prazos de verdade externa (§0 :17), o D-10
   (:135), o GOV-0.2 (:36) e o W8 (:120). As DATAS são EXTRAÍDAS da linha viva por regex, não
   congeladas no script — uma renovação futura é refletida na regeneração.
5. `deploy/helm/maezo-tenant/values.yaml` — `lifecycle.expectedFailUntil` (:898), lido VIVO.
6. `docs/Tarefas_Pendentes.md:1140` — R-131 (legal-hold), prazo monitorável fora do escopo
   shadow, registrado na seção de prazos com essa qualificação explícita.
7. `docs/design/wave1-effect-chokepoint.md:874/:892` — anotações históricas (ADD-only,
   "ANSWERED 2026-08-13") que citam os valores PRÉ-renovação (2026-11-11); registradas na
   seção "Derivas declaradas", com âncora que espera o valor antigo — se alguém atualizar o
   doc, a âncora quebra e força a atualização da linha (fail-closed nas duas direções).

Semântica de status
-------------------
    prazo_efetivo = prazo_próprio (quando o item tem um) senão o muro (quando o item é
                    alcançado pelo muro shadow 2027-02-09); senão nada.
    SEM-DATA      não há prazo próprio nem muro herdado — LACUNA declarada, nunca data inventada.
    VENCIDO       hoje > prazo_efetivo (e o item não foi resolvido — p.ex. flip a `enforcing`).
    NO-PRAZO     hoje <= prazo_efetivo. `aviso = "≤14 dias"` dentro da mesma janela do gate
                  `check_deviation_expiry.py`. Um item RESOLVIDO (flip) é NO-PRAZO com
                  observação "desvio encerrado" — virou histórico, não violação.

Dois feltros que NÃO viram VENCIDO (e por quê): o `checkpoint` de um bloco `deviation:` vencido
é "conversa, não gate" (docstring do `DeviationRecord` no loader) — vira anotação
`checkpoint-vencido` na linha; e o D-52 (W8 antecipada para dez-2026) não tem dia exato
rastreado — o alvo formal 2027-01-26 segue sendo o prazo da linha, com a antecipação anotada.

Âncoras
-------
Cada linha de inventário congelado (flags, candidatos, prazos, derivas) carrega uma âncora
`(caminho, linha, token)` que é RE-VERIFICADA contra a árvore a cada execução. Token ausente ou
linha movida ⇒ exit 2 com o nome da âncora — o dashboard não pode ficar verde citando uma
fonte que se moveu por debaixo dele (mesma postura das cercas de citação da casa: conteúdo
nomeado, não silêncio). Linhas alimentadas pelo loader não têm âncora de linha: a fonte citada
é o próprio loader tipado.

Determinismo
------------
Sem relógio de parede: a data de execução é `--today` ou a data do sistema (dia, não segundo);
ordem de linhas fixa; nenhuma leitura de ambiente. Dois `--today` iguais ⇒ bytes iguais.

Uso
---
    uv run python scripts/w7/shadow_deviation_dashboard.py
    uv run python scripts/w7/shadow_deviation_dashboard.py --today 2027-02-10
    uv run python scripts/w7/shadow_deviation_dashboard.py --format json --out /tmp/dash.json
    uv run python scripts/w7/shadow_deviation_dashboard.py --self-check

Exit codes
----------
    0  artefato emitido; todas as âncoras de pé; manifesto sem defeitos
    2  âncora desalinhada (fonte movida/renomeada) — regenerar com as linhas novas
    3  manifesto de autonomia degradado ou bloco `deviation:` malformado (fail-closed, igual ao
       gate `check_deviation_expiry.py`: registrar defeito é a função, engoli-lo é a falha)
    4  self-check reprovado (o comparador não provou saber ficar vermelho)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only; runtime import é deferido em main()
    from maezo.gateway.action_execution import ActionApprovals, DeviationRecord

# scripts/w7/<file> -> parents[2] == repo root (mesma convenção de scripts/ci/).
REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------
# OS PRAZOS-ÂNCORA DO PROGRAMA. Datas ratificadas pelo dono e renovadas UMA vez (2026-09-20,
# PR fix/d7-exception-renewal-2027) — "sem segunda renovação" (`docs/plans/
# maezo-completion-program.md:135`, D-10). O muro não é deste script: é o teto que este script
# EXISTE para medir. O lineage do Q-2 (original 2026-11-11 → renovado) é citado na linha dele.
# ---------------------------------------------------------------------------------------------
MURO_SHADOW: Final[date] = date(2027, 2, 9)
WARNING_WINDOW_DAYS: Final[int] = 14  # mesma janela de `scripts/ci/check_deviation_expiry.py`

STATUS_NO_PRAZO: Final[str] = "NO-PRAZO"
STATUS_VENCIDO: Final[str] = "VENCIDO"
STATUS_SEM_DATA: Final[str] = "SEM-DATA"

PLAN_DOC: Final[str] = "docs/plans/maezo-completion-program.md"
FLAGS_DOC: Final[str] = "docs/plans/w5-5-1-flags-reancoragem.md"
QUEUE_DOC: Final[str] = "docs/review-queue.md"
MANIFEST: Final[str] = "spec/policies/autonomy/action-approvals.yaml"
WA_SETTINGS: Final[str] = "src/maezo/platform/webhooks/whatsapp/settings.py"
VALUES_YAML: Final[str] = "deploy/helm/maezo-tenant/values.yaml"
TAREFAS_DOC: Final[str] = "docs/Tarefas_Pendentes.md"
CHOKEPOINT_DOC: Final[str] = "docs/design/wave1-effect-chokepoint.md"

MURO_BASIS: Final[str] = f"muro-{MURO_SHADOW.isoformat()} (herdado — D-10, sem 2ª renovação)"

# Saídas padrão (o artefato markdown entra no PR; o JSON é regenerável sob demanda).
DEFAULT_OUT_MD: Final[Path] = Path("docs/delivery/w7/shadow-deviation-dashboard.md")
DEFAULT_OUT_JSON: Final[Path] = Path("docs/delivery/w7/shadow-deviation-dashboard.json")

EXIT_OK: Final[int] = 0
EXIT_ANCHOR_DRIFT: Final[int] = 2
EXIT_MANIFEST_DEFECT: Final[int] = 3
EXIT_SELF_CHECK: Final[int] = 4


# ---------------------------------------------------------------------------------------------
# Âncoras — a citação arquivo:linha é verificada, não decorada.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Anchor:
    """`(caminho, linha, token)` que DEVE continuar de pé para a linha do dashboard valer.

    `line` é a dica (1-based) capturada na fonte na data do pacote; a verificação tolera a linha
    ter se movido SÓ para reportar o movimento (exit 2) — nunca para seguir o token em silêncio.
    `line=None` significa "primeira ocorrência do token" (para arquivos cuja forma é estável).
    """

    path: str
    token: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class AnchorVerdict:
    """Resultado da re-verificação de uma âncora contra a árvore viva."""

    anchor: Anchor
    ok: bool
    found_line: int | None  # 1-based quando o token foi encontrado (na dica ou alhures)
    note: str  # "" quando ok; o motivo nomeado quando desalinhada

    @property
    def fonte(self) -> str:
        """A citação `arquivo:linha` renderizada — com a linha ENCONTRADA, não a decorada."""
        if self.found_line is None:
            return self.anchor.path
        return f"{self.anchor.path}:{self.found_line}"


def verify_anchor(repo_root: Path, anchor: Anchor) -> AnchorVerdict:
    """Re-verifica a âncora contra a árvore. Falha NOMEADA, nunca silenciosa."""
    path = repo_root / anchor.path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return AnchorVerdict(anchor, False, None, f"arquivo ilegível: {exc}")
    if anchor.line is not None and 1 <= anchor.line <= len(lines) and anchor.token in lines[anchor.line - 1]:
        return AnchorVerdict(anchor, True, anchor.line, "")
    for idx, text in enumerate(lines, start=1):
        if anchor.token in text:
            if anchor.line is not None:
                note = f"token na linha {idx}, esperado {anchor.line} — fonte movida"
            else:
                note = ""
            return AnchorVerdict(anchor, not note, idx, note)
    return AnchorVerdict(anchor, False, None, "token ausente no arquivo — fonte mudou de forma")


# ---------------------------------------------------------------------------------------------
# O modelo de linha do dashboard.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShadowItem:
    """Uma linha do dashboard, já resolvida (prazo efetivo, dias, status)."""

    item_id: str
    secao: str
    rotulo: str
    estado: str
    owner: str
    own_deadline: date | None
    deadline_field: str  # `expires` | `review_by` | `—` | o nome do campo de origem
    muro_bound: bool
    deadline_basis: str  # `própria` | MURO_BASIS | `—` (lacuna declarada)
    effective_deadline: date | None
    dias_restantes: int | None
    status: str
    aviso: str
    checkpoint: date | None
    checkpoint_vencido: bool
    fonte: str
    fonte_prazo: str
    obs: str


def classify_deadline(
    own_deadline: date | None,
    muro_bound: bool,
    hoje: date,
    resolvido: bool = False,
) -> tuple[date | None, int | None, str, str]:
    """A função PURA de status — todo o dashboard passa por aqui (e o self-check a prova).

    Devolve `(prazo_efetivo, dias_restantes, status, aviso)`.
    """
    if own_deadline is None and not muro_bound:
        return None, None, STATUS_SEM_DATA, ""
    effective = own_deadline if own_deadline is not None else MURO_SHADOW
    dias = (effective - hoje).days
    aviso = "≤14 dias" if 0 <= dias <= WARNING_WINDOW_DAYS else ""
    if resolvido:
        return effective, dias, STATUS_NO_PRAZO, aviso  # desvio encerrado — histórico
    if hoje > effective:
        return effective, dias, STATUS_VENCIDO, aviso
    return effective, dias, STATUS_NO_PRAZO, aviso


def _md_cell(text: str) -> str:
    """Escapa `|` para a célula não quebrar a tabela markdown."""
    return text.replace("|", "\\|")


# ---------------------------------------------------------------------------------------------
# Seção A — o registro MÁQUINA-LEGÍVEL (`action-approvals.yaml`, pelo loader tipado).
# ---------------------------------------------------------------------------------------------

_SLOT_Q2: Final[str] = "enforcement_padrao_nao_mapeado"
_CLASS_Q10: Final[str] = "leitura_phi_clinica"
_SLOT_Q10: Final[str] = f"acoes.{_CLASS_Q10}"

#: Rótulos humanos das classes declaradas — NOMES da casa, não conteúdo inventado: cada rótulo é
#: a própria chave da classe no manifesto (a `descricao` rica vive no YAML e o loader não a
#: expõe; duplicá-la aqui criaria uma segunda cópia para divergir).
_MANIFEST_FONTE: Final[str] = f"{MANIFEST} (loader tipado `load_action_approvals`)"


def _deviation_row(
    slot: str,
    record: DeviationRecord,
    live_value: str,
    hoje: date,
    rotulo: str,
    obs_extra: str = "",
) -> ShadowItem:
    """Uma linha por bloco `deviation:` ratificado — prazo PRÓPRIO, lido do record."""
    effective, dias, status, aviso = classify_deadline(
        record.deadline, muro_bound=False, hoje=hoje, resolvido=live_value != "shadow"
    )
    lineage = (
        " · prazo original 2026-11-11, renovado pelo dono 2026-09-20 (sem 2ª renovação)"
        if slot == _SLOT_Q2
        else ""
    )
    return ShadowItem(
        item_id=f"A-{slot}",
        secao="A",
        rotulo=rotulo,
        estado=f"valor vivo: {live_value}",
        owner=record.owner_role,
        own_deadline=record.deadline,
        deadline_field=record.deadline_field,
        muro_bound=False,
        deadline_basis="própria (bloco `deviation:` ratificado)",
        effective_deadline=effective,
        dias_restantes=dias,
        status=status,
        aviso=aviso,
        checkpoint=record.checkpoint,
        checkpoint_vencido=record.checkpoint < hoje,
        fonte=_MANIFEST_FONTE,
        fonte_prazo=f"{_MANIFEST_FONTE} · bloco `deviation:` do slot `{slot}`",
        obs=(
            f"ratificado {record.ratified_on.isoformat()} por {record.ratified_by}; "
            f"critérios: {record.criteria_ref}{lineage}"
            + (" · desvio ENCERRADO (flip a enforcing) — histórico" if live_value != "shadow" else "")
            + (f" · {obs_extra}" if obs_extra else "")
        ).strip(" ·"),
    )


def build_secao_gateway(approvals: ActionApprovals, hoje: date) -> tuple[list[ShadowItem], list[str]]:
    """Seção A: `modo` + desvios ratificados + as 17 posturas por classe.

    TUDO sai do loader tipado. Defeitos de bloco viram problema nomeado (exit 3 no chamador) —
    registrar é a função, engolir é a falha (mesma disciplina do gate).
    """
    items: list[ShadowItem] = []
    problems: list[str] = []

    # A.0 — o teto global. Nada enforça enquanto `modo: shadow`; a virada é ato terminal
    # (design §9.4 passo 7). Sem prazo próprio; o muro é o bound do programa.
    modo, _, _, _ = classify_deadline(None, muro_bound=True, hoje=hoje)
    items.append(
        ShadowItem(
            item_id="A-modo",
            secao="A",
            rotulo="modo global do gateway de autonomia (`modo`)",
            estado=f"modo={approvals.mode}",
            owner="dono (virada é ato terminal do rollout)",
            own_deadline=None,
            deadline_field="—",
            muro_bound=True,
            deadline_basis=MURO_BASIS,
            effective_deadline=modo,
            dias_restantes=(modo - hoje).days if modo else None,
            status=STATUS_NO_PRAZO if modo else STATUS_SEM_DATA,
            aviso="",
            checkpoint=None,
            checkpoint_vencido=False,
            fonte=_MANIFEST_FONTE,
            fonte_prazo=f"{PLAN_DOC}:17 (muro) · {PLAN_DOC}:135 (D-10)",
            obs="teto: `modo: shadow` significa que NADA enforça, jamais — o default de classe não muda isso",
        )
    )

    # A.1..A.n — blocos `deviation:` ratificados, varridos GENERICAMENTE (o loader varre a raiz
    # e toda classe declarada; o dashboard não conhece a lista de cor — lê o que está lá).
    rotulos = {
        _SLOT_Q2: (
            "Q-2 — `enforcement_padrao_nao_mapeado: shadow` (default de refs NÃO mapeadas; "
            "desvio explícito do literal XRD-09 do ADR-0037)"
        ),
        _SLOT_Q10: ("Q-10 — classe C2 `leitura_phi_clinica: shadow` (leituras PHI via FHIR)"),
    }
    valores_vivos: dict[str, str] = {
        _SLOT_Q2: approvals.default_enforcement,
        **{f"acoes.{cls}": valor for cls, valor in sorted(approvals.class_enforcement.items())},
    }
    for slot in sorted(approvals.deviations):
        record = approvals.deviations[slot]
        live = valores_vivos.get(slot, "shadow")
        items.append(
            _deviation_row(
                slot,
                record,
                live,
                hoje,
                rotulos.get(
                    slot,
                    f"desvio ratificado do slot `{slot}` (classe declarada no manifesto)",
                ),
                obs_extra=(
                    "checkpoint intermediário do dono — vencido sem registro rastreado de "
                    "revisão intermediária (conversa, não gate — anotado, não VENCIDO)"
                    if record.checkpoint < hoje
                    else ""
                ),
            )
        )
    for slot, defects in sorted(approvals.deviation_defects.items()):
        problems.append(f"bloco `deviation:` do slot `{slot}` malformado: {'; '.join(defects)}")

    # A.x — as posturas POR CLASSE. Declarar classe não é aprová-la; todas seguem shadow e o
    # prazo delas é o muro do programa (a não ser que tenham bloco próprio — a linha de bloco
    # próprio já está acima; aqui o bound herdado é dito como herdado).
    q10_tem_bloco = _SLOT_Q10 in approvals.deviations
    for cls in sorted(approvals.class_enforcement):
        if cls == _CLASS_Q10 and q10_tem_bloco:
            continue  # a linha Q-10 (com prazo próprio) já representa a classe
        effective, dias, status, aviso = classify_deadline(
            None, muro_bound=True, hoje=hoje, resolvido=approvals.class_enforcement[cls] != "shadow"
        )
        obs = "declarar classe NÃO é aprová-la — aprovações `PENDENTE` por domínio"
        if approvals.class_enforcement[cls] != "shadow":
            obs = "classe em enforcing — o muro não mais a alcança (linha histórica)"
        items.append(
            ShadowItem(
                item_id=f"A-classe-{cls}",
                secao="A",
                rotulo=f"classe `{cls}`",
                estado=f"enforcement: {approvals.class_enforcement[cls]}",
                owner="dono (flip por classe é ato humano ratificado, Q-1)",
                own_deadline=None,
                deadline_field="—",
                muro_bound=True,
                deadline_basis=MURO_BASIS,
                effective_deadline=effective,
                dias_restantes=dias,
                status=status,
                aviso=aviso,
                checkpoint=None,
                checkpoint_vencido=False,
                fonte=_MANIFEST_FONTE,
                fonte_prazo=f"{PLAN_DOC}:17 (muro) · {PLAN_DOC}:135 (D-10)",
                obs=obs,
            )
        )
    if _CLASS_Q10 in approvals.class_enforcement and not q10_tem_bloco and not problems:
        problems.append(
            f"classe `{_CLASS_Q10}` está em shadow sem bloco `deviation:` — a cerca "
            "`check_deviation_expiry.py` deveria ter pego isso; dashboard não assume o risco"
        )
    return items, problems


# ---------------------------------------------------------------------------------------------
# Seção B — as flags de habilitação dark (inventário rastreado, `w5-5-1-flags-reancoragem.md`
# §3, base `dc544a0e`): 33 interruptores ENUMERADOS em 6 grupos. Cada linha: âncora na tabela
# do doc + âncora de código da flag. (O headline do doc diz 31 — divergência registrada viva.)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlagRow:
    item_id: str
    grupo: str
    rotulo: str
    estado: str
    code: Anchor
    doc: Anchor | None
    pr: str
    obs: str


FLAG_ROWS: Final[tuple[FlagRow, ...]] = (
    # Grupo A — flags dark do Programa de Completude W5 (doc :30-38)
    FlagRow(
        "B-A1",
        "A",
        "`lgpd_dsr_channel` (`MAEZO_PORTAL_LGPD_DSR_CHANNEL`)",
        "default `False`; OFF responde 403 antes de qualquer efeito",
        Anchor("src/maezo/portal/api/config.py", "lgpd_dsr_channel: bool = False", 52),
        Anchor(FLAGS_DOC, "lgpd_dsr_channel", 34),
        "#955 (5.2)",
        "flip = ato da ROLL com revisor + janela nomeados no ato",
    ),
    FlagRow(
        "B-A2",
        "A",
        "`staff_documents_panel` (`MAEZO_PORTAL_STAFF_DOCUMENTS_PANEL`)",
        "default `False`; OFF = stub byte-a-byte + sessão sem sinal",
        Anchor("src/maezo/portal/api/config.py", "staff_documents_panel: bool = False", 61),
        Anchor(FLAGS_DOC, "staff_documents_panel", 35),
        "#960 (5.5-p1)",
        "flip = ato da ROLL",
    ),
    FlagRow(
        "B-A3",
        "A",
        "`staff_operations_panel` (`MAEZO_PORTAL_STAFF_OPERATIONS_PANEL`)",
        "default `False`; idem A2",
        Anchor("src/maezo/portal/api/config.py", "staff_operations_panel: bool = False", 71),
        Anchor(FLAGS_DOC, "staff_operations_panel", 36),
        "#963 (5.5-p2)",
        "flip = ato da ROLL",
    ),
    FlagRow(
        "B-A4",
        "A",
        "`MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED`",
        "OFF — só o literal `true` habilita (`bridge_flag_enabled`)",
        Anchor(
            "src/maezo/platform/identity_bridges.py",
            'TITULAR_BRIDGE_FLAG_ENV: Final = "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED"',
            92,
        ),
        Anchor(FLAGS_DOC, "MAEZO_ERASURE_BRIDGE_TITULAR_ENABLED", 37),
        "#965 (5.4)",
        "OFF = recusa `flag_off` antes de qualquer travessia; zero caller de produção pinado",
    ),
    FlagRow(
        "B-A5",
        "A",
        "`MAEZO_ERASURE_BRIDGE_THREAD_ENABLED`",
        "OFF (idem A4)",
        Anchor(
            "src/maezo/platform/identity_bridges.py",
            'THREAD_BRIDGE_FLAG_ENV: Final = "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED"',
            95,
        ),
        Anchor(FLAGS_DOC, "MAEZO_ERASURE_BRIDGE_THREAD_ENABLED", 38),
        "#965 (5.4)",
        "idem A4",
    ),
    # Grupo B — portões interinos DL-0049 do portal (doc :42-48; end-state = REMOÇÃO)
    FlagRow(
        "B-B1",
        "B",
        "`direct_completion` (`MAEZO_PORTAL_DIRECT_COMPLETION`)",
        "default `False`; cercado por `scripts/ci/check_portal_direct_completion.py` (7 cercas)",
        Anchor("src/maezo/portal/api/config.py", "direct_completion: bool = False", 37),
        Anchor(FLAGS_DOC, "MAEZO_PORTAL_DIRECT_COMPLETION", 46),
        "DL-0049 (relay #427)",
        "end-state = REMOÇÃO com o relay, não flip",
    ),
    FlagRow(
        "B-B2",
        "B",
        "`direct_completion` do perfil de produção (`PortalProductionSettings`)",
        "default `False`; portão INDEPENDENTE por desenho",
        Anchor("src/maezo/gateway/staff_cases/production_config.py", "direct_completion: bool = False", 176),
        Anchor(FLAGS_DOC, "PortalProductionSettings", 47),
        "DL-0049",
        "mesma cerca de B1",
    ),
    FlagRow(
        "B-B3",
        "B",
        "`cors_origins` (`MAEZO_PORTAL_CORS_ORIGINS`)",
        'default `""` (nenhuma origem cross-origin); curinga proibido',
        Anchor("src/maezo/portal/api/config.py", 'cors_origins: str = ""', 45),
        Anchor(FLAGS_DOC, "MAEZO_PORTAL_CORS_ORIGINS", 48),
        "DL-0049",
        "mesma cerca de B1",
    ),
    # Grupo C — runtime de workers/agentes (doc :52-58)
    FlagRow(
        "B-C1",
        "C",
        "`denial_notice_owner` (`MAEZO_DENIAL_NOTICE_OWNER`)",
        "default `False`; liga o dono nativo de `operadora.auth.send_denial_notice`",
        Anchor(
            "src/maezo/runtime/worker_runtime/settings.py",
            'Field(default=False, alias="MAEZO_DENIAL_NOTICE_OWNER")',
            83,
        ),
        Anchor(FLAGS_DOC, "MAEZO_DENIAL_NOTICE_OWNER", 56),
        "WP-J1-06",
        "fail-closed quando on; flip é a ativação inteira",
    ),
    FlagRow(
        "B-C2",
        "C",
        "`document_request_host_installed` (`MAEZO_AUTH_DOCUMENT_REQUEST_HOST`)",
        "default `False`; exclusividade do tópico de documentos (decisão #18 do dono)",
        Anchor(
            "src/maezo/runtime/worker_runtime/settings.py",
            'Field(default=False, alias="MAEZO_AUTH_DOCUMENT_REQUEST_HOST")',
            141,
        ),
        Anchor(FLAGS_DOC, "MAEZO_AUTH_DOCUMENT_REQUEST_HOST", 57),
        "WP-J1-03",
        "ligar sem instalar a ponte deixa tópico sem consumidor — visível em `/readyz`",
    ),
    FlagRow(
        "B-C3",
        "C",
        "`agent_ingress_enabled` (`MAEZO_AGENT_INGRESS_ENABLED`)",
        "default `False`; declarada ON em deploy dev SÓ para o agente `rafael`",
        Anchor(
            "src/maezo/runtime/agent_runtime/settings.py",
            "agent_ingress_enabled: bool = Field(default=False",
            71,
        ),
        Anchor(FLAGS_DOC, "MAEZO_AGENT_INGRESS_ENABLED", 58),
        "postura própria",
        "deploy dev: `deploy/aws-ecs/envs/dev-sa-east-1/service-agents.tf:244`",
    ),
    # Grupo D — lembretes (doc :62-66)
    FlagRow(
        "B-D1",
        "D",
        "`habilitado` (`MAEZO_LEMBRETE_COBRANCA`)",
        "default `False` (+ `dry_run=True`); duplo portão habilitado+dry_run",
        Anchor("src/maezo/platform/lembretes/config.py", 'AliasChoices("MAEZO_LEMBRETE_COBRANCA")', 33),
        Anchor(FLAGS_DOC, "MAEZO_LEMBRETE_COBRANCA", 66),
        "postura própria",
        "declarada em deploy dev com default false (`task-lembrete-cobranca.tf:7,81`)",
    ),
    # Grupo E — Helena/Lucas WhatsApp (doc :70-86; 17 default-False + 2 exceções default-True)
    FlagRow(
        "B-E1",
        "E",
        "`ack_then_queue`",
        "default `False`",
        Anchor(WA_SETTINGS, "ack_then_queue: bool = Field(", 179),
        None,
        "DL-*",
        "base de habilitação = registro DL-* do campo (`docs/decisions-log.md`)",
    ),
    FlagRow(
        "B-E2",
        "E",
        "`devolve_turno`",
        "default `False`",
        Anchor(WA_SETTINGS, "devolve_turno: bool = Field(", 258),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E3",
        "E",
        "`roteador_lucas_enabled`",
        "default `False`",
        Anchor(WA_SETTINGS, "roteador_lucas_enabled: bool = Field(", 270),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E4",
        "E",
        "`helena_identidade_amh`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_identidade_amh: bool = Field(", 314),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E5",
        "E",
        "`helena_historico`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_historico: bool = Field(", 341),
        None,
        "DL-0080",
        "idem",
    ),
    FlagRow(
        "B-E6",
        "E",
        "`helena_triagem_v2`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_triagem_v2: bool = Field(", 350),
        None,
        "DL-0104",
        "idem",
    ),
    FlagRow(
        "B-E7",
        "E",
        "`helena_v4`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_v4: bool = Field(", 359),
        None,
        "DL-0115",
        "idem",
    ),
    FlagRow(
        "B-E8",
        "E",
        "`helena_carteirinha_completa`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_carteirinha_completa: bool = Field(", 368),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E9",
        "E",
        "`helena_dependentes_nome`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_dependentes_nome: bool = Field(", 377),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E10",
        "E",
        "`helena_base_saude`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_base_saude: bool = Field(", 387),
        None,
        "DL-*",
        "completa os 17 default-False (nota do doc :86)",
    ),
    FlagRow(
        "B-E11",
        "E",
        "`helena_saude_gerativa`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_saude_gerativa: bool = Field(", 441),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E12",
        "E",
        "`helena_busca_semantica`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_busca_semantica: bool = Field(", 455),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E13",
        "E",
        "`helena_consultas_amh`",
        "default `False`",
        Anchor(WA_SETTINGS, "helena_consultas_amh: bool = Field(", 486),
        None,
        "DL-*",
        "dependência: `_consultas_exigem_identidade` (`settings.py:565-599`)",
    ),
    FlagRow(
        "B-E14",
        "E",
        "`acesso_beneficiario`",
        "default `False`",
        Anchor(WA_SETTINGS, "acesso_beneficiario: bool = Field(", 497),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E15",
        "E",
        "`lucas_consulta_valores`",
        "default `False`",
        Anchor(WA_SETTINGS, "lucas_consulta_valores: bool = Field(", 535),
        None,
        "DL-*",
        "dependências: `_lucas_v4_exige_valores`/`_valores_exigem_verificacao`",
    ),
    FlagRow(
        "B-E16",
        "E",
        "`cancel_pf_v1`",
        "default `False`",
        Anchor(WA_SETTINGS, "cancel_pf_v1: bool = Field(", 545),
        None,
        "DL-*",
        "idem",
    ),
    FlagRow(
        "B-E17",
        "E",
        "`lucas_v4`",
        "default `False`",
        Anchor(WA_SETTINGS, "lucas_v4: bool = Field(", 558),
        None,
        "DL-0114",
        "idem",
    ),
    FlagRow(
        "B-E18",
        "E",
        "`memoria_clinica_enabled`",
        "default `True` — EXCEÇÃO por decisão (ratificações pendentes: médico-auditor/DPO)",
        Anchor(WA_SETTINGS, "memoria_clinica_enabled: bool = Field(", 195),
        None,
        "DL-*",
        "uma das 2 exceções default-True declaradas no inventário",
    ),
    FlagRow(
        "B-E19",
        "E",
        "`acesso_limite_cpf`",
        "default `True` — EXCEÇÃO por decisão (ratificações pendentes: médico-auditor/DPO)",
        Anchor(WA_SETTINGS, "acesso_limite_cpf: bool = Field(", 513),
        None,
        "DL-*",
        "a outra exceção default-True",
    ),
    # Grupo F — par (flag, revisor, janela) COMPLETO na review-queue (doc :90-95)
    FlagRow(
        "B-F1",
        "F",
        "`REEMBOLSO_AUTO_PAGAMENTO_LIBERADO`",
        "default `False`; build dark merged (bloqueio fail-closed no `Flow_GW_AutoAprovar`)",
        Anchor(
            "src/maezo/tools/workers/reembolso.py", "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO: bool = False", 214
        ),
        Anchor(FLAGS_DOC, "REEMBOLSO-AUTO-OVERPAY", 94),
        "R-058/R-059",
        "revisor/janela COMPLETOS em `docs/review-queue.md:1750`; ordem R-157: assinatura "
        "de atuária ANTES do teto D-07 subir de zero",
    ),
    FlagRow(
        "B-F2",
        "F",
        "pin TISS T2.6-2 (`spec/policies/ans/tiss-schema-pin.yaml`)",
        "`status: DRAFT`, `ratificado: false`; data-gated, NÃO wired em `ans_submit.py`",
        Anchor("spec/policies/ans/tiss-schema-pin.yaml", "status: DRAFT", 95),
        Anchor(FLAGS_DOC, "Ratificação do pin TISS", 95),
        "T2.6-2",
        "revisor/janela COMPLETOS em `docs/review-queue.md:690`; ativação = 4 campos "
        "independentes (loader recusa ratificação forjada)",
    ),
)

_FLAGS_DOC_GRUPO_ANCHORS: Final[tuple[Anchor, ...]] = (
    Anchor(FLAGS_DOC, "Grupo A — Flags dark do Programa", 30),
    Anchor(FLAGS_DOC, "Grupo B — Portões interinos DL-0049", 42),
    Anchor(FLAGS_DOC, "Grupo C — Runtime de workers/agentes", 52),
    Anchor(FLAGS_DOC, "Grupo D — Lembretes", 62),
    Anchor(FLAGS_DOC, "Grupo E — Helena/Lucas WhatsApp", 70),
    Anchor(FLAGS_DOC, "Grupo F — Interruptores com par", 90),
)

#: O headline do doc §3 ("soma checada: 31 interruptores em 6 grupos") diverge da SOMA DOS
#: PRÓPRIOS PARCIAIS que ele declara no fechamento (`TOTAL GERAL: 5+3+3+1+19+2`), que dá 33:
#: A(5) + B(3) + C(3) + D(1) + E(19 = 17 default-False + 2 exceções default-True) + F(2) = 33.
#: O `31` do headline é o subtotal A..E (5+3+3+1+19) que ficou congelado no fechamento. O
#: dashboard segue o INVENTÁRIO ENUMERADO (33 — é o que existe linha a linha no doc) e
#: REGISTRA a divergência no título da seção com o número extraído VIVO do headline — sem
#: editar `docs/plans` (ato do orquestrador) e sem reproduzir o número que não fecha.
_FLAGS_DOC_HEADLINE_ANCHOR: Final[Anchor] = Anchor(FLAGS_DOC, "interruptores em 6 grupos", 28)


def flags_doc_headline() -> tuple[str, AnchorVerdict]:
    """Lê vivo o número do headline do doc §3 (para citá-lo ao divergir, não para segui-lo)."""
    verdict = verify_anchor(REPO_ROOT, _FLAGS_DOC_HEADLINE_ANCHOR)
    if verdict.found_line is None:
        return "?", verdict
    text = (REPO_ROOT / FLAGS_DOC).read_text(encoding="utf-8").splitlines()[verdict.found_line - 1]
    m = re.search(r"soma checada:\s*(\d+)\s+interruptores em\s*(\d+)\s+grupos", text)
    if not m:
        return "?", AnchorVerdict(
            verdict.anchor,
            False,
            verdict.found_line,
            "headline presente mas sem o padrão `soma checada: N interruptores em M grupos`",
        )
    return f"{m.group(1)} interruptores em {m.group(2)} grupos", verdict


def build_secao_flags(hoje: date) -> tuple[list[ShadowItem], list[AnchorVerdict]]:
    """Seção B: o inventário ENUMERADO de flags dark (33 linhas, 6 grupos).

    Prazo = muro herdado (D-10: pacotes de flip até 2027-01-26; muro 2027-02-09 sem 2ª
    renovação). O headline do doc diz 31; a soma dos parciais declarados é 33 — a divergência
    é registrada no título da seção com o número lido VIVO do doc.
    """
    items: list[ShadowItem] = []
    verdicts: list[AnchorVerdict] = []
    headline, headline_verdict = flags_doc_headline()
    divergencia = (
        f"divergência de soma REGISTRADA: doc headline `{headline}` × enumerados aqui "
        f"{len(FLAG_ROWS)} (parciais do doc: 5+3+3+1+19+2)"
    )
    for row in FLAG_ROWS:
        effective, dias, status, aviso = classify_deadline(None, muro_bound=True, hoje=hoje)
        fonte = row.code.path
        obs = row.obs
        if row.doc is not None:
            verdict = verify_anchor(REPO_ROOT, row.doc)
            verdicts.append(verdict)
            fonte = f"{row.doc.path} (linha da tabela §3)"
            if not verdict.ok:
                obs = f"ÂNCORA DESALINHADA ({verdict.note}) · {obs}"
        verdict_code = verify_anchor(REPO_ROOT, row.code)
        verdicts.append(verdict_code)
        fonte = f"{fonte} + {verdict_code.fonte}"
        if not verdict_code.ok:
            obs = f"ÂNCORA DESALINHADA ({verdict_code.note}) · {obs}"
        items.append(
            ShadowItem(
                item_id=row.item_id,
                secao="B",
                rotulo=f"[grupo {row.grupo}] {row.rotulo}",
                estado=row.estado,
                owner=f"ROLL / revisor da habilitação ({row.pr})",
                own_deadline=None,
                deadline_field="—",
                muro_bound=True,
                deadline_basis=MURO_BASIS,
                effective_deadline=effective,
                dias_restantes=dias,
                status=status,
                aviso=aviso,
                checkpoint=None,
                checkpoint_vencido=False,
                fonte=fonte,
                fonte_prazo=f"{PLAN_DOC}:135 (D-10) · {PLAN_DOC}:17 (muro)",
                obs=obs,
            )
        )
    verdicts.append(headline_verdict)
    return items, verdicts, divergencia


# ---------------------------------------------------------------------------------------------
# Seção C — sombras de regras/tabelas (manifestos candidatos DRAFT + matriz auth-criteria).
# TODAS sem data em qualquer fonte rastreada → SEM-DATA declarado (lacuna, não data inventada).
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateRow:
    item_id: str
    rotulo: str
    estado: str
    reviewer: str
    anchor: Anchor
    queue_anchor: Anchor
    obs: str


CANDIDATE_ROWS: Final[tuple[CandidateRow, ...]] = (
    CandidateRow(
        "C-adequacao",
        "candidato RN 259 `adequacao-gap-shadow-candidate.yaml` (W3, precedente mergeado #204)",
        "DRAFT — candidato não ratificado; avaliador shadow PURO sem engine "
        "(`src/maezo/tools/workers/adequacao_shadow.py`); tabela viva `adequacao_gap.dmn` "
        "conhecida-errada (ordem FIRST `r_eletivo_leve` → `r_conforme` sem teto)",
        "regulatório (RN 259) — ato do dono regulatorio/clinico (ADR-0028 §7)",
        Anchor("spec/processes/dmn/adequacao-gap-shadow-candidate.yaml", "regras_candidatas:", 309),
        Anchor(QUEUE_DOC, "adequacao-gap-shadow-candidate.yaml", 618),
        "mitigação runtime owner-ratificada em `adequacao.route_remediation` preserva o veredito "
        "e recusa agir sobre `CONFORME` contraditado",
    ),
    CandidateRow(
        "C-M4",
        "candidato M-4 `glosa-triage-shadow-candidate.yaml` + `glosa_triage.dmn`",
        "DRAFT — candidato não ratificado",
        "auditoria-contas + compliance",
        Anchor("spec/processes/dmn/glosa-triage-shadow-candidate.yaml", "regras_candidatas:"),
        Anchor(QUEUE_DOC, "glosa-triage-shadow-candidate.yaml", 640),
        'conjunto negativo `not("tecnica","clinica")` admite `"desconhecida"` — sob FIRST '
        "o fail-safe `r_catchall` fica inalcançável no subespaço favorável",
    ),
    CandidateRow(
        "C-M5",
        "candidato M-5 `carencia-check-shadow-candidate.yaml` + `carencia_check.dmn`",
        "DRAFT — candidato não ratificado; contradiz premissa declarada da tabela viva",
        "juridico/regulatorio + medico auditor",
        Anchor("spec/processes/dmn/carencia-check-shadow-candidate.yaml", "regras_candidatas:"),
        Anchor(QUEUE_DOC, "carencia-check-shadow-candidate.yaml", 641),
        "sob CPT, a escada de urgência (art. 12, V — 24h) e o catch-all L0 ficam inalcançáveis",
    ),
    CandidateRow(
        "C-M6",
        "candidato M-6 `upcoding-complexity-ceiling-shadow-candidate.yaml` + tabela",
        "DRAFT — candidato não ratificado; scores seguem SINTÉTICOS",
        "medico-auditor + auditoria especial + financas",
        Anchor("spec/processes/dmn/upcoding-complexity-ceiling-shadow-candidate.yaml", "regras_candidatas:"),
        Anchor(QUEUE_DOC, "upcoding-complexity-ceiling-shadow-candidate.yaml", 642),
        'única das 7 `fraude_scoring/*` cujo catch-all emite 0/`"none"` — classe não mapeada '
        "contribui ZERO e fica indistinguível de caso conforme",
    ),
    CandidateRow(
        "C-M7",
        "candidatos M-7 `triage-redflag-gestante` + `triage-redflag-pediatric` (CLÍNICO)",
        "DRAFT — achados registrados, NENHUMA regra proposta (lacunas de banda)",
        "medico obstetra / medico pediatra",
        Anchor("spec/processes/dmn/triage-redflag-gestante-shadow-candidate.yaml", "regras_candidatas:"),
        Anchor(QUEUE_DOC, "triage-redflag-gestante-shadow-candidate.yaml", 643),
        "ratificar aqui = clínico registra que leu e dispositionou os achados; fechar exige "
        "conteúdo clínico que só ele pode escrever",
    ),
    CandidateRow(
        "C-auth-criteria",
        "matriz `auth-criteria-ratification.yaml` (GAP-AUTH-4) — portão de segurança do critério",
        "DRAFT — NADA ratificado; enquanto `ratificado: false`, o critério é FALSE com "
        "`*_FONTE_NAO_RATIFICADA` e o modo SOMBRA registra o que a tabela TERIA decidido",
        "medico auditor + juridico/regulatorio + financas (por fonte)",
        Anchor("spec/processes/dmn/auth-criteria-ratification.yaml", "ratificado: false"),
        Anchor(QUEUE_DOC, "auth-criteria-ratification.yaml", 22),
        "`mapeamento_dut_criteria` deliberadamente incompleto — julgamento clínico é do SME",
    ),
)


def build_secao_candidates(hoje: date) -> tuple[list[ShadowItem], list[AnchorVerdict]]:
    items: list[ShadowItem] = []
    verdicts: list[AnchorVerdict] = []
    for row in CANDIDATE_ROWS:
        # Lacuna declarada de propósito: nenhuma fonte rastreada dá data a estes itens e o muro
        # da autonomia não os alcança (são sombras de REGRA, não do gateway) — SEM-DATA.
        _, dias, status, aviso = classify_deadline(None, muro_bound=False, hoje=hoje)
        v_manifest = verify_anchor(REPO_ROOT, row.anchor)
        v_queue = verify_anchor(REPO_ROOT, row.queue_anchor)
        verdicts.extend([v_manifest, v_queue])
        obs = row.obs
        bad = [v for v in (v_manifest, v_queue) if not v.ok]
        if bad:
            obs = "ÂNCORA DESALINHADA (" + "; ".join(v.note for v in bad) + f") · {obs}"
        items.append(
            ShadowItem(
                item_id=row.item_id,
                secao="C",
                rotulo=row.rotulo,
                estado=row.estado,
                owner=row.reviewer,
                own_deadline=None,
                deadline_field="—",
                muro_bound=False,
                deadline_basis="— (lacuna declarada: nenhuma fonte rastreada dá prazo)",
                effective_deadline=None,
                dias_restantes=dias,
                status=status,
                aviso=aviso,
                checkpoint=None,
                checkpoint_vencido=False,
                fonte=f"{v_manifest.fonte} + {v_queue.fonte}",
                fonte_prazo="—",
                obs=obs,
            )
        )
    return items, verdicts


# ---------------------------------------------------------------------------------------------
# Seção D — OP21 `legitimacy.screen` (sombra REG-1.1, merged #1039/#1040).
# ---------------------------------------------------------------------------------------------


def build_secao_op21(hoje: date) -> tuple[list[ShadowItem], list[AnchorVerdict]]:
    """Seção D: a sombra OP21 — prazo PRÓPRIO 1º/1/2027 (escalonamento, RN 489/2022 art. 23-A).

    A data é EXTRAÍDA viva da linha :17 do plan doc (`1º/1/2027 escalonamento OP21`) — se a
    renovação mudar o dia, a regeneração segue a fonte; se o texto sumir, a âncora quebra.
    """
    verdicts: list[AnchorVerdict] = []
    prazos_anchor = Anchor(PLAN_DOC, "escalonamento OP21", 17)
    v_prazo = verify_anchor(REPO_ROOT, prazos_anchor)
    verdicts.append(v_prazo)
    prazo: date | None = None
    if v_prazo.found_line is not None:
        m = re.search(
            r"1º/1/(\d{4})",
            (REPO_ROOT / PLAN_DOC).read_text(encoding="utf-8").splitlines()[v_prazo.found_line - 1],
        )
        if m:
            prazo = date(int(m.group(1)), 1, 1)
    flag_anchor = Anchor("src/maezo/portal/api/config.py", "collective_legitimacy_screen: bool = False", 110)
    queue_anchor = Anchor(QUEUE_DOC, "collective_legitimacy_screen", 33)
    guard_anchor = Anchor(
        "src/maezo/gateway/commercial_legitimacy_guard.py", "def collective_legitimacy_shadow(", 494
    )
    v_flag = verify_anchor(REPO_ROOT, flag_anchor)
    v_queue = verify_anchor(REPO_ROOT, queue_anchor)
    v_guard = verify_anchor(REPO_ROOT, guard_anchor)
    verdicts.extend([v_flag, v_queue, v_guard])

    items: list[ShadowItem] = []
    effective, dias, status, aviso = classify_deadline(prazo, muro_bound=False, hoje=hoje)
    items.append(
        ShadowItem(
            item_id="D-flag",
            secao="D",
            rotulo=(
                "OP21 `legitimacy.screen` — flag dark `collective_legitimacy_screen` "
                "(`MAEZO_PORTAL_COLLECTIVE_LEGITIMACY_SCREEN`), postura shadow do guard"
            ),
            estado=(
                "DARK — flag OFF em prod (passa o objeto através, byte-idêntico); ON wrap "
                "`LegitimacyScreenGateway(CommercialGateway)` (#1039: bound set fechado de 7 "
                "operações; eventos art. 39) — avalia/registra/sinaliza sem nunca alterar a "
                "resposta comercial; cerca CI de 8 envs ativa"
            ),
            owner="ROLL (revisor a nomear) · jurídico (parecer 2026-10-26, RN 489/2022 art. 23-A)",
            own_deadline=prazo,
            deadline_field="prazo regulatório" if prazo else "—",
            muro_bound=False,
            deadline_basis=(
                f"própria (escalonamento OP21 — {PLAN_DOC}:17, RN 489/2022 art. 23-A "
                "(redação dada pela RN 659/2025))"
                if prazo
                else "— (âncora do prazo quebrou — regenerar)"
            ),
            effective_deadline=effective,
            dias_restantes=dias,
            status=status,
            aviso=aviso,
            checkpoint=None,
            checkpoint_vencido=False,
            fonte=f"{v_flag.fonte} + {v_queue.fonte}",
            fonte_prazo=v_prazo.fonte,
            obs=(
                "merged #1039 (shadow) + #1040 (enforcing provado, SEM bind de produção — a "
                "porta da flag recusa a classe em boot); elevação a enforcing = classe "
                "sucessora com bind PRÓPRIO em composition root, nunca por ambiente; recusa "
                "byte-exata `legitimacy_unproven` depende de vocabulário no codec C6-4 "
                "(sucessor do freeze)"
            ),
        )
    )
    eff2, dias2, status2, aviso2 = classify_deadline(prazo, muro_bound=False, hoje=hoje)
    items.append(
        ShadowItem(
            item_id="D-eventos",
            secao="D",
            rotulo="OP21 — eventos art. 39 + linha persistida da sombra (telemetria do guard)",
            estado=(
                "evento `collective_legitimacy_shadow` com `would_block=True` na linha "
                "persistida (em shadow NUNCA altera a resposta; em enforcing a recusa vai "
                "ANTES do efeito, pelo recibo — código e provas rastreados em "
                "`src/maezo/gateway/commercial_legitimacy_guard.py` + "
                "`tests/unit/gateway/test_commercial_legitimacy_enforcing.py`)"
            ),
            owner="idem D-flag",
            own_deadline=prazo,
            deadline_field="prazo regulatório" if prazo else "—",
            muro_bound=False,
            deadline_basis="própria (idem D-flag)" if prazo else "—",
            effective_deadline=eff2,
            dias_restantes=dias2,
            status=status2,
            aviso=aviso2,
            checkpoint=None,
            checkpoint_vencido=False,
            fonte=v_guard.fonte,
            fonte_prazo=v_prazo.fonte,
            obs=(
                "a sombra só produz linha quando a flag estiver ON — OFF hoje, a telemetria é "
                "a prova preparada, não a medição em curso"
            ),
        )
    )
    return items, verdicts


# ---------------------------------------------------------------------------------------------
# Seção E — prazos de governo (datas EXTRAÍDAS vivas das linhas-fonte).
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GovernanceRow:
    item_id: str
    rotulo: str
    anchor: Anchor
    pattern: str  # regex com UM grupo de captura `YYYY-MM-DD` (ou `1º/1/YYYY`)
    kind: str  # `iso` | `primeiro_de_janeiro`
    escopo: str  # `shadow` | `governo`
    obs: str


GOVERNANCE_ROWS: Final[tuple[GovernanceRow, ...]] = (
    GovernanceRow(
        "E-muro",
        "muro shadow + revisão C2 (renovado pelo dono 2026-09-20; sem segunda renovação)",
        Anchor(PLAN_DOC, "muro shadow", 17),
        r"(\d{4}-\d{2}-\d{2}) muro shadow",
        "iso",
        "shadow",
        f"é o teto que as seções A/B usam como bound herdado; D-10 ({PLAN_DOC}:135)",
    ),
    GovernanceRow(
        "E-w8-flip",
        "W8 CLOSE — pacotes de flip entregues (alvo formal; D-52 registra antecipação "
        "para dez-2026, sem dia exato rastreado)",
        Anchor(PLAN_DOC, "W8 CLOSE (M; alvo", 120),
        r"alvo (\d{4}-\d{2}-\d{2})",
        "iso",
        "governo",
        "o muro chega completo ou com a lista de corte pré-acordada "
        f"({PLAN_DOC}:296, risco 5): só stubs W5 e profundidade 6.7 são cortáveis",
    ),
    GovernanceRow(
        "E-c2-janela",
        "fim da janela de evidências C2",
        Anchor(PLAN_DOC, "fim da janela de evidências C2", 17),
        r"(\d{4}-\d{2}-\d{2}) fim da janela de evidências C2",
        "iso",
        "governo",
        "evidência para a revisão C2 do médico-auditor (o Q-10 é a classe dessa revisão)",
    ),
    GovernanceRow(
        "E-gov02",
        "GOV-0.2 — emenda F-P04 (2026-11-11 → renovado 2027-02-09)",
        Anchor(PLAN_DOC, "renovado", 36),
        r"renovado (\d{4}-\d{2}-\d{2})",
        "iso",
        "shadow",
        "verificar linhas de dados do gate de expiração — `check_deviation_expiry.py`",
    ),
    GovernanceRow(
        "E-op21",
        "escalonamento OP21 (RN 489/2022, art. 23-A; multa-base R$ 506.250)",
        Anchor(PLAN_DOC, "escalonamento OP21", 17),
        r"1º/1/(\d{4})",
        "primeiro_de_janeiro",
        "governo",
        "o prazo próprio da Seção D",
    ),
    GovernanceRow(
        "E-lifecycle",
        "lifecycle `expectedFailUntil` (tolerância de falha esperada dos 3 CronJobs LGPD; "
        "teto, não dispensa — R-040)",
        Anchor(VALUES_YAML, "expectedFailUntil", 898),
        r'expectedFailUntil:\s*"(\d{4}-\d{2}-\d{2})"',
        "iso",
        "shadow",
        "renovado 2026-09-20 de 2026-11-11 para 2027-02-09 "
        "(`docs/runbooks/portal-dev-provisionamento.md:363`); a exclusão condicional exige "
        "kube-state-metrics com `--metric-annotations-allowlist` (fail-open honesto declarado)",
    ),
    GovernanceRow(
        "E-r131",
        "R-131 — ratificação registro legal-hold + re-anchor checkpoint assinado",
        Anchor(TAREFAS_DOC, "LEGAL-HOLD-REGISTRY-REANCHOR", 1140),
        r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*$",
        "iso",
        "governo",
        "FORA do escopo shadow — registrado por ser prazo rastreado e monitorável da mesma "
        "janela de revisão do dono",
    ),
)


def build_secao_governance(hoje: date) -> tuple[list[ShadowItem], list[AnchorVerdict], list[str]]:
    items: list[ShadowItem] = []
    verdicts: list[AnchorVerdict] = []
    problems: list[str] = []
    for row in GOVERNANCE_ROWS:
        v = verify_anchor(REPO_ROOT, row.anchor)
        verdicts.append(v)
        prazo: date | None = None
        if v.found_line is not None:
            line_text = (
                (REPO_ROOT / row.anchor.path).read_text(encoding="utf-8").splitlines()[v.found_line - 1]
            )
            m = re.search(row.pattern, line_text)
            if m:
                if row.kind == "primeiro_de_janeiro":
                    prazo = date(int(m.group(1)), 1, 1)
                else:
                    prazo = date.fromisoformat(m.group(1))
        if prazo is None:
            problems.append(
                f"prazo não extraível de {v.fonte} (padrão `{row.pattern}`) — linha do painel "
                f"`{row.item_id}` exige regeneração após revisar a fonte"
            )
        effective, dias, status, aviso = classify_deadline(prazo, muro_bound=False, hoje=hoje)
        items.append(
            ShadowItem(
                item_id=row.item_id,
                secao="E",
                rotulo=row.rotulo,
                estado=f"escopo: {row.escopo}",
                owner="dono",
                own_deadline=prazo,
                deadline_field="data extraída da fonte viva",
                muro_bound=False,
                deadline_basis="própria (extraída da linha-fonte)" if prazo else "— (extração falhou)",
                effective_deadline=effective,
                dias_restantes=dias,
                status=status,
                aviso=aviso,
                checkpoint=None,
                checkpoint_vencido=False,
                fonte=v.fonte,
                fonte_prazo=v.fonte,
                obs=row.obs,
            )
        )
    return items, verdicts, problems


# ---------------------------------------------------------------------------------------------
# Seção F — derivas declaradas entre fontes rastreadas (anotações históricas pré-renovação).
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriftRow:
    item_id: str
    rotulo: str
    anchor: Anchor  # espera o valor ANTIGO — a atualização do doc quebra a âncora de propósito
    vivo: str
    obs: str


DRIFT_ROWS: Final[tuple[DriftRow, ...]] = (
    DriftRow(
        "F-q2-quote",
        "anotação Q-2 (ADD-only, ANSWERED 2026-08-13) cita `expires: 2026-11-11` — valor PRÉ-renovação",
        Anchor(CHOKEPOINT_DOC, "expires: 2026-11-11", 874),
        "manifesto vivo: `expires: 2027-02-09` (renovado 2026-09-20)",
        "anotação histórica de design; a leitura VIVA é do loader — atualizar o doc exige "
        "revisão (doc de design, não deste pacote); a âncora quebra se o doc for emendado, "
        "forçando a atualização desta linha",
    ),
    DriftRow(
        "F-q10-quote",
        "anotação Q-10 cita `checkpoint: 2026-11-11` — valor PRÉ-renovação "
        "(o `review_by: 2027-02-09` citado segue vivo)",
        Anchor(CHOKEPOINT_DOC, "checkpoint: 2026-11-11", 892),
        "manifesto vivo: `checkpoint: 2027-02-09` (= review_by, renovado 2026-09-20)",
        "idem F-q2-quote",
    ),
)


def build_secao_drifts(hoje: date) -> tuple[list[ShadowItem], list[AnchorVerdict]]:
    items: list[ShadowItem] = []
    verdicts: list[AnchorVerdict] = []
    for row in DRIFT_ROWS:
        v = verify_anchor(REPO_ROOT, row.anchor)
        verdicts.append(v)
        _, _, status, _ = classify_deadline(None, muro_bound=False, hoje=hoje)
        obs = row.obs
        if not v.ok:
            obs = f"ÂNCORA DESALINHADA ({v.note}) — o doc citado foi emendado? Atualize esta linha. · {obs}"
        items.append(
            ShadowItem(
                item_id=row.item_id,
                secao="F",
                rotulo=row.rotulo,
                estado=f"deriva declarada · {row.vivo}",
                owner="dono do doc de design (CODEOWNED)",
                own_deadline=None,
                deadline_field="—",
                muro_bound=False,
                deadline_basis="—",
                effective_deadline=None,
                dias_restantes=None,
                status=status,
                aviso="",
                checkpoint=None,
                checkpoint_vencido=False,
                fonte=v.fonte if v.ok else row.anchor.path,
                fonte_prazo="—",
                obs=obs,
            )
        )
    return items, verdicts


# ---------------------------------------------------------------------------------------------
# Renderização — markdown (artefato citável) e JSON (consumo mecanizado).
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dashboard:
    hoje: date
    secoes: list[tuple[str, str, list[ShadowItem]]] = field(default_factory=list)

    def add(self, secao: str, titulo: str, items: list[ShadowItem]) -> None:
        self.secoes.append((secao, titulo, items))

    def all_items(self) -> list[ShadowItem]:
        return [item for _, _, items in self.secoes for item in items]

    def counts(self) -> dict[str, int]:
        contagem = {STATUS_NO_PRAZO: 0, STATUS_VENCIDO: 0, STATUS_SEM_DATA: 0}
        for item in self.all_items():
            contagem[item.status] += 1
        return contagem

    def to_markdown(self) -> str:
        total = len(self.all_items())
        contagem = self.counts()
        out: list[str] = []
        out.append("# Dashboard de desvios shadow — W7 7.5 (muro 2027-02-09)")
        out.append("")
        out.append(f"- **Gerado em:** {self.hoje.isoformat()} (data de execução; sem relógio de parede)")
        out.append("- **Gerador:** `scripts/w7/shadow_deviation_dashboard.py` (determinístico; regenerável)")
        out.append(
            "- **Fontes:** SÓ dados rastreados — `spec/policies/autonomy/action-approvals.yaml` "
            "(pelo loader tipado), `docs/plans/w5-5-1-flags-reancoragem.md` §3, "
            "`docs/review-queue.md`, `docs/plans/maezo-completion-program.md`, "
            "`deploy/helm/maezo-tenant/values.yaml`, `docs/Tarefas_Pendentes.md`, "
            "`docs/design/wave1-effect-chokepoint.md`. Nenhuma fonte gitignored alimenta linha de dado."
        )
        out.append(
            f"- **Muro:** {MURO_SHADOW.isoformat()} (renovado pelo dono 2026-09-20; **sem segunda "
            "renovação** — D-10). Itens sem prazo próprio e alcançados pelo programa shadow "
            "herdam o muro; itens sem prazo em qualquer fonte rastreada ficam `SEM-DATA` "
            "(lacuna declarada, nunca data inventada)."
        )
        out.append("")
        out.append("## Resumo")
        out.append("")
        out.append("| Status | Itens |")
        out.append("|---|---|")
        for status in (STATUS_NO_PRAZO, STATUS_VENCIDO, STATUS_SEM_DATA):
            out.append(f"| `{status}` | {contagem[status]} |")
        out.append(f"| **Total** | **{total}** |")
        out.append("")
        out.append(
            "Legenda: `NO-PRAZO` = dentro do prazo (coluna `Aviso` marca `≤14 dias` na janela do gate "
            "`check_deviation_expiry.py`) · `VENCIDO` = hoje > prazo · `SEM-DATA` = sem prazo próprio e sem "
            "muro herdado. `Checkpoint vencido` é anotação, não gate (conversa, não expiração — "
            "docstring do `DeviationRecord`)."
        )
        out.append("")
        for secao, titulo, items in self.secoes:
            out.append(f"## Seção {secao} — {titulo}")
            out.append("")
            out.append(
                "| ID | Item | Flag/Estado | Prazo | Campo | Base do prazo | Dias | Status "
                "| Aviso | Checkpoint | Fonte | Dono | Obs |"
            )
            out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for item in items:
                checkpoint = item.checkpoint.isoformat() if item.checkpoint else "—"
                if item.checkpoint_vencido:
                    checkpoint += " (vencido)"
                out.append(
                    "| "
                    + " | ".join(
                        _md_cell(cell)
                        for cell in (
                            item.item_id,
                            item.rotulo,
                            item.estado,
                            item.effective_deadline.isoformat() if item.effective_deadline else "—",
                            item.deadline_field,
                            item.deadline_basis,
                            str(item.dias_restantes) if item.dias_restantes is not None else "—",
                            f"`{item.status}`",
                            item.aviso or "—",
                            checkpoint,
                            item.fonte,
                            item.owner,
                            item.obs or "—",
                        )
                    )
                    + " |"
                )
            out.append("")
        out.append("## O que este dashboard NÃO cobre (declarado, não escondido)")
        out.append("")
        out.append(
            "- **Registry vendor OP-REGISTRY (gitignored):** nenhuma linha de dado vem dele. Se um desvio "
            "só existir lá, a linha correspondente não existe aqui — a lacuna fica no relatório do pacote "
            "(`docs/audits/HARDENING-2026-10/W7-75-SHADOW-DASHBOARD-v1.md`, gitignored, sha256 no PR)."
        )
        out.append(
            "- **Ambiente deployado em execução:** as cercas provam a ÁRVORE rastreada (defaults, "
            "`deploy/**`, manifestos); o comportamento do ambiente vivo é dos coletores de "
            "telemetria, não deste painel."
        )
        out.append(
            "- **`docs/plans` não é editado por este pacote** — novos prazos/decisões são do "
            "orquestrador; regenere o dashboard depois de qualquer mudança neles (as datas da "
            "Seção E são extraídas da linha viva)."
        )
        out.append("")
        return "\n".join(out)

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "gerado_em": self.hoje.isoformat(),
                    "muro_shadow": MURO_SHADOW.isoformat(),
                    "gerador": "scripts/w7/shadow_deviation_dashboard.py",
                    "fontes": "somente rastreadas (ver markdown)",
                    "contagens": self.counts(),
                    "secoes": [
                        {
                            "id": secao,
                            "titulo": titulo,
                            "itens": [
                                {
                                    "id": i.item_id,
                                    "rotulo": i.rotulo,
                                    "estado": i.estado,
                                    "owner": i.owner,
                                    "prazo_proprio": i.own_deadline.isoformat() if i.own_deadline else None,
                                    "campo_prazo": i.deadline_field,
                                    "muro_herdado": i.muro_bound,
                                    "base_prazo": i.deadline_basis,
                                    "prazo_efetivo": i.effective_deadline.isoformat()
                                    if i.effective_deadline
                                    else None,
                                    "dias_restantes": i.dias_restantes,
                                    "status": i.status,
                                    "aviso": i.aviso,
                                    "checkpoint": i.checkpoint.isoformat() if i.checkpoint else None,
                                    "checkpoint_vencido": i.checkpoint_vencido,
                                    "fonte": i.fonte,
                                    "fonte_prazo": i.fonte_prazo,
                                    "obs": i.obs,
                                }
                                for i in items
                            ],
                        }
                        for secao, titulo, items in self.secoes
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
            + "\n"
        )


# ---------------------------------------------------------------------------------------------
# Self-check — o gerador não emite painel que ele não prove saber ficar vermelho.
# ---------------------------------------------------------------------------------------------


def _synthetic_manifest(tmp: Path, *, root_expires: str, class_review_by: str, class_value: str) -> Path:
    """Manifesto SINTÉTICO mínimo (mesma forma aceita pelo loader tipado)."""
    import yaml

    deviation_root = {
        "owner_role": "dono sintético",
        "ratified_on": "2026-01-01",
        "ratified_by": "self-check",
        "expires": root_expires,
        "checkpoint": "2026-03-01",
        "criteria_ref": "self-check",
    }
    deviation_class = {
        "owner_role": "dono sintético",
        "ratified_on": "2026-01-01",
        "ratified_by": "self-check",
        "review_by": class_review_by,
        "checkpoint": "2027-01-01",
        "criteria_ref": "self-check",
    }
    manifest = {
        "status": "DRAFT",
        "modo": "shadow",
        "enforcement_padrao_nao_mapeado": "shadow",
        "deviation": deviation_root,
        "acoes": {
            "leitura_phi_clinica": {
                "enforcement": class_value,
                "deviation": deviation_class,
            },
        },
    }
    path = tmp / "action-approvals.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def self_check() -> list[str]:
    """Prova de não-vacuidade: o comparador SABE falhar e passar. Devolve defeitos nomeados."""
    import tempfile

    from maezo.gateway.action_execution import load_action_approvals

    defects: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="w7-dash-selfcheck-"))

    # (1) A função pura distingue os três status e a janela de aviso.
    d = date(2026, 10, 27)
    if classify_deadline(date(2026, 10, 26), False, d)[2] != STATUS_VENCIDO:
        defects.append("classify_deadline não marca VENCIDO no dia seguinte ao prazo")
    if classify_deadline(date(2026, 10, 27), False, d)[2] != STATUS_NO_PRAZO:
        defects.append("classify_deadline não marca NO-PRAZO no próprio dia")
    if classify_deadline(None, False, d)[2] != STATUS_SEM_DATA:
        defects.append("classify_deadline não marca SEM-DATA sem prazo e sem muro")
    if classify_deadline(None, True, d)[2] != STATUS_NO_PRAZO:
        defects.append("classify_deadline não herda o muro")
    if classify_deadline(date(2026, 11, 10), False, d)[3] != "≤14 dias":
        defects.append("janela de aviso ≤14 dias não acende no 14º dia")
    if classify_deadline(date(2026, 11, 11), False, d)[3] != "":
        defects.append("janela de aviso acende fora da janela (dia 15)")
    if classify_deadline(date(2026, 10, 26), False, d, resolvido=True)[2] != STATUS_NO_PRAZO:
        defects.append("item resolvido (flip) não é isento do VENCIDO")

    # (2) O loader, num manifesto sintético, produz os mesmos status que a cerca juraria.
    vencido = build_secao_gateway(
        load_action_approvals(
            _synthetic_manifest(
                tmp / "a", root_expires="2026-01-31", class_review_by="2099-01-01", class_value="shadow"
            )
        ),
        date(2026, 2, 1),
    )[0]
    if not any(i.status == STATUS_VENCIDO for i in vencido):
        defects.append("manifesto sintético com `expires` passado não rendeu linha VENCIDA")

    vivo = build_secao_gateway(
        load_action_approvals(
            _synthetic_manifest(
                tmp / "b", root_expires="2099-01-31", class_review_by="2099-06-01", class_value="shadow"
            )
        ),
        date(2026, 2, 1),
    )[0]
    q2 = next((i for i in vivo if i.item_id.endswith(_SLOT_Q2)), None)
    if q2 is None or q2.status != STATUS_NO_PRAZO or q2.deadline_field != "expires":
        defects.append("manifesto sintético vivo não rendeu linha Q-2 NO-PRAZO com campo `expires`")
    q10 = next((i for i in vivo if i.item_id.endswith(_SLOT_Q10)), None)
    if q10 is None or q10.status != STATUS_NO_PRAZO or q10.deadline_field != "review_by":
        defects.append("manifesto sintético vivo não rendeu linha Q-10 NO-PRAZO com campo `review_by`")

    # (3) Flip a enforcing = desvio encerrado (isento), e a linha por classe some do painel
    # de sombra (a classe deixa de ser shadow; o bloco vira histórico).
    flipped = build_secao_gateway(
        load_action_approvals(
            _synthetic_manifest(
                tmp / "c", root_expires="2020-01-01", class_review_by="2020-06-01", class_value="enforcing"
            )
        ),
        date(2026, 2, 1),
    )[0]
    q10_flip = next((i for i in flipped if i.item_id.endswith(_SLOT_Q10)), None)
    if q10_flip is None or q10_flip.status != STATUS_NO_PRAZO:
        defects.append("classe flipada a enforcing não virou isenta (deveria ser NO-PRAZO/histórico)")
    if any(i.item_id == "A-classe-leitura_phi_clinica" for i in flipped):
        defects.append("classe flipada ainda aparece como postura shadow por classe")

    # (4) Âncora: drift é detectado e NOMEADO (linha movida e token ausente).
    probe = tmp / "fonte.txt"
    probe.write_text("linha um\nlinha dois com o token\n", encoding="utf-8")
    moved = verify_anchor(tmp, Anchor("fonte.txt", "token", 5))
    if moved.ok or "movida" not in moved.note:
        defects.append("âncora com linha movida não foi reportada como drift")
    missing = verify_anchor(tmp, Anchor("fonte.txt", "token-inexistente", 1))
    if missing.ok or "ausente" not in missing.note:
        defects.append("âncora com token ausente não foi reportada como drift")
    ok = verify_anchor(tmp, Anchor("fonte.txt", "token", 2))
    if not ok.ok or ok.found_line != 2:
        defects.append("âncora íntegra não verificou")

    # (5) O inventário congelado de flags: soma dos parciais declarados pelo doc §3
    # (A5+B3+C3+D1+E19+F2 = 33) — e a DIVERGÊNCIA registrada com o headline do doc (31).
    soma_esperada = {"A": 5, "B": 3, "C": 3, "D": 1, "E": 19, "F": 2}
    soma = {g: sum(1 for r in FLAG_ROWS if r.grupo == g) for g in soma_esperada}
    if soma != soma_esperada:
        defects.append(f"inventário de flags divergiu do doc §3: {soma} ≠ {soma_esperada}")
    if sum(soma.values()) != 33:
        defects.append(f"inventário enumerado deveria ser 33 (5+3+3+1+19+2), é {sum(soma.values())}")
    headline, headline_verdict = flags_doc_headline()
    if headline == "31 interruptores em 6 grupos" and not headline_verdict.ok:
        defects.append("headline do doc §3 não pôde ser lido vivo para registrar a divergência")

    # (6) Renderização determinística: mesmas entradas ⇒ mesmos bytes (markdown e JSON).
    def _demo_dashboard() -> Dashboard:
        dash = Dashboard(hoje=d)
        dash.add(
            "X",
            "demo",
            [
                ShadowItem(
                    item_id="X-1",
                    secao="X",
                    rotulo="demo | pipe",
                    estado="e",
                    owner="o",
                    own_deadline=None,
                    deadline_field="—",
                    muro_bound=True,
                    deadline_basis=MURO_BASIS,
                    effective_deadline=MURO_SHADOW,
                    dias_restantes=(MURO_SHADOW - d).days,
                    status=STATUS_NO_PRAZO,
                    aviso="",
                    checkpoint=None,
                    checkpoint_vencido=False,
                    fonte="f:1",
                    fonte_prazo="p:1",
                    obs="o",
                )
            ],
        )
        return dash

    if _demo_dashboard().to_markdown() != _demo_dashboard().to_markdown():
        defects.append("markdown não é byte-estável para a mesma entrada")
    if _demo_dashboard().to_json() != _demo_dashboard().to_json():
        defects.append("json não é byte-estável para a mesma entrada")
    if "| demo \\| pipe |" not in _demo_dashboard().to_markdown():
        defects.append("célula com pipe não foi escapada na tabela markdown")
    return defects


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def build_dashboard(
    hoje: date, manifest_path: Path | None
) -> tuple[Dashboard, list[AnchorVerdict], list[str]]:
    """Monta o dashboard completo + vereditos de âncora + problemas (defeitos de manifesto)."""
    from maezo.gateway.action_execution import load_action_approvals

    problems: list[str] = []
    verdicts: list[AnchorVerdict] = []
    dash = Dashboard(hoje=hoje)

    approvals = load_action_approvals(manifest_path)
    if approvals.degraded:
        problems.append("manifesto de autonomia DEGRADADO (falha de carga) — painel não confiável")
    gateway_items, gateway_problems = build_secao_gateway(approvals, hoje)
    problems.extend(gateway_problems)
    dash.add("A", "Gateway de autonomia — `action-approvals.yaml` (registro máquina-legível)", gateway_items)

    flags_items, flags_verdicts, flags_divergence = build_secao_flags(hoje)
    verdicts.extend(flags_verdicts)
    for anchor in _FLAGS_DOC_GRUPO_ANCHORS:
        verdicts.append(verify_anchor(REPO_ROOT, anchor))
    dash.add(
        "B",
        "Flags de habilitação dark — inventário ENUMERADO: 33 interruptores / 6 grupos "
        "(`w5-5-1-flags-reancoragem.md` §3; " + flags_divergence + ")",
        flags_items,
    )

    candidate_items, candidate_verdicts = build_secao_candidates(hoje)
    verdicts.extend(candidate_verdicts)
    dash.add(
        "C", "Sombras de regras/tabelas — manifestos candidatos DRAFT + matriz auth-criteria", candidate_items
    )

    op21_items, op21_verdicts = build_secao_op21(hoje)
    verdicts.extend(op21_verdicts)
    dash.add("D", "OP21 `legitimacy.screen` — sombra REG-1.1 (merged #1039/#1040)", op21_items)

    gov_items, gov_verdicts, gov_problems = build_secao_governance(hoje)
    verdicts.extend(gov_verdicts)
    problems.extend(gov_problems)
    dash.add("E", "Prazos de governo (datas extraídas das fontes vivas)", gov_items)

    drift_items, drift_verdicts = build_secao_drifts(hoje)
    verdicts.extend(drift_verdicts)
    dash.add("F", "Derivas declaradas entre fontes rastreadas", drift_items)

    return dash, verdicts, problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="data de execução (ISO; default = hoje do sistema) — determinismo",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="manifesto de autonomia alternativo (default = o rastreado)",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="arquivo de saída (default: docs/delivery/w7/shadow-deviation-dashboard.<ext>)",
    )
    parser.add_argument("--stdout", action="store_true", help="imprime o artefato em vez de gravar")
    parser.add_argument("--self-check", action="store_true", help="roda só a prova de não-vacuidade e sai")
    args = parser.parse_args(argv)

    if args.self_check:
        defects = self_check()
        for d in defects:
            print(f"SELF-CHECK DEFECT: {d}", file=sys.stderr)
        return EXIT_OK if not defects else EXIT_SELF_CHECK

    hoje = args.today or date.today()
    dash, verdicts, problems = build_dashboard(hoje, args.manifest)

    exit_code = EXIT_OK
    drifts = [v for v in verdicts if not v.ok]
    if drifts:
        exit_code = EXIT_ANCHOR_DRIFT
        print(f"{len(drifts)} âncora(s) desalinhada(s):", file=sys.stderr)
        for v in drifts:
            print(f"  - {v.anchor.path}:{v.anchor.line} token `{v.anchor.token}` → {v.note}", file=sys.stderr)
    if problems:
        if exit_code == EXIT_OK:
            exit_code = EXIT_MANIFEST_DEFECT
        print(f"{len(problems)} problema(s) no registro:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)

    payload = dash.to_markdown() if args.format == "markdown" else dash.to_json()
    if args.stdout:
        sys.stdout.write(payload)
    else:
        out = args.out or (DEFAULT_OUT_MD if args.format == "markdown" else DEFAULT_OUT_JSON)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"dashboard gravado em {out} ({dash.counts()})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

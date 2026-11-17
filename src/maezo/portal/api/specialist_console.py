"""Console do especialista comercial (Faixa Assistida) e compromisso de primeira resposta (E-SF-ADD01-02).

Modulo puro, variante fechada: NAO cria router FastAPI e NAO e importado por
`app.py`; nada e montado. Define apenas (a) os papeis separados de conducao e de
cadastro, (b) a projecao da jornada sem campo clinico e (c) o registro nao
ratificado do compromisso de primeira resposta.

Nenhum prazo (numero de minutos) nem texto normativo e exibido ao cliente. A
politica do compromisso (N) nao esta ratificada: depende de ato do dono.
"""

from __future__ import annotations

import re
import typing
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from maezo.gateway.communications.whatsapp_templates import contem_dado_de_saude
from maezo.ports.commercial import CommercialOperation

PAPEL_CONDUCAO: Final = "especialista_comercial_faixa_assistida"
PAPEL_CADASTRO: Final = "back_office_cadastro"

OPERACOES_DO_CONSOLE: Final[frozenset[str]] = frozenset(
    {"work.list", "work.claim", "work.assign", "work.handoff", "work.reconcile", "journey.get"}
)
# Definicao do back-office de cadastro e ato do dono (GAP): sem operacao no console.
OPERACOES_DO_CADASTRO: Final[frozenset[str]] = frozenset()

OPERACOES_VEDADAS_AO_CONSOLE: Final[frozenset[str]] = frozenset(
    {
        "acceptance.record",
        "enrollment.request",
        "offer.terms",
        "party.submit",
        "prospect.save",
        "contact.request",
    }
)


class SpecialistConsoleError(ValueError):
    """Recusa com codigo fixo; nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _assert_console_fechado() -> None:
    conhecidas = set(typing.get_args(CommercialOperation))
    for op in OPERACOES_DO_CONSOLE:
        if op not in conhecidas or op in OPERACOES_VEDADAS_AO_CONSOLE:
            raise RuntimeError("specialist_console_aberto_demais")


_assert_console_fechado()


@dataclass(frozen=True, slots=True)
class AtorDoConsole:
    ator_ref: str
    papeis: frozenset[str]


def operacoes_permitidas(ator: AtorDoConsole) -> frozenset[str]:
    papeis = ator.papeis
    if PAPEL_CONDUCAO in papeis and PAPEL_CADASTRO in papeis:
        raise SpecialistConsoleError("console_papel_acumulado")
    if not papeis <= {PAPEL_CONDUCAO, PAPEL_CADASTRO}:
        raise SpecialistConsoleError("console_papel_desconhecido")
    if PAPEL_CONDUCAO in papeis:
        return OPERACOES_DO_CONSOLE
    return OPERACOES_DO_CADASTRO


def exigir_operacao(ator: AtorDoConsole, operacao: str) -> None:
    if operacao not in operacoes_permitidas(ator):
        raise SpecialistConsoleError("console_operacao_vedada")


CAMPOS_DA_VISAO: Final = (
    "journey_ref",
    "revision",
    "observed_at",
    "prospect_ref",
    "account_ref",
    "party_ref",
)
CAMPOS_DO_SNAPSHOT: Final[frozenset[str]] = frozenset(
    {
        "journey_ref",
        "revision",
        "prospect_ref",
        "account_ref",
        "party_ref",
        "payer_ref",
        "quote_contexts",
        "acceptance",
        "contract",
        "enrollment",
        "coverage",
        "documents",
        "pending_items",
        "pending_operations",
        "next_actions",
        "support",
        "support_topics",
        "observed_at",
        "profile_ref",
        "party_linkage",
        "commercial_acceptance",
        "receivable_refs",
        "payment_attempt_refs",
        "mandate_refs",
        "financial_action_refs",
    }
)


@dataclass(frozen=True, slots=True)
class VisaoDaJornada:
    journey_ref: str | None
    revision: int
    observed_at: str
    prospect_ref: str | None
    account_ref: str | None
    party_ref: str | None


_PROFUNDIDADE_MAXIMA: Final = 32


def _tem_saude(node: object, profundidade: int = 0) -> bool:
    if profundidade > _PROFUNDIDADE_MAXIMA:
        raise SpecialistConsoleError("console_snapshot_contract_mismatch")
    if node is None or type(node) in (bool, int, float):
        return False
    if isinstance(node, str):
        return contem_dado_de_saude(node)
    if isinstance(node, Mapping):
        return any(
            _tem_saude(k, profundidade + 1) or _tem_saude(v, profundidade + 1) for k, v in node.items()
        )
    if isinstance(node, list | tuple):
        return any(_tem_saude(v, profundidade + 1) for v in node)
    raise SpecialistConsoleError("console_snapshot_contract_mismatch")


#: Classe fechada dos VALORES da visao (CD-94 item 2): a lista de campos ja' garante por construcao
#: que nenhum CAMPO clinico sai; estes validadores garantem que o VALOR de cada campo nao carrega texto.
#: Referencia opaca estrita: UUID canonico ou hexadecimal minusculo de 16 a 64 caracteres, com prefixo
#: opcional de um conjunto fechado (jrn_, pro_, acc_, pty_). Nesse alfabeto nao se escreve palavra clinica.
#: GAP: o formato das referencias do contrato comercial nao e' fixado (o tipo e' texto livre); uma
#: referencia real fora deste formato e' recusada, o que e' o lado seguro com o console nao montado.
_REF_OPACA: Final[re.Pattern[str]] = re.compile(
    r"(?:(?:jrn|pro|acc|pty)_)?(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,64})"
)
#: Instante ISO-8601 com fuso obrigatorio ("Z" ou deslocamento), segundos e fracao opcional.
_INSTANTE_COM_FUSO: Final[re.Pattern[str]] = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _ref(valor: object) -> str | None:
    if valor is None:
        return None
    if type(valor) is str and _REF_OPACA.fullmatch(valor) is not None:
        return valor
    raise SpecialistConsoleError("console_snapshot_value_class_refused")


def _instante(valor: object) -> str:
    if type(valor) is not str or _INSTANTE_COM_FUSO.fullmatch(valor) is None:
        raise SpecialistConsoleError("console_snapshot_value_class_refused")
    try:
        momento = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise SpecialistConsoleError("console_snapshot_value_class_refused") from None
    if momento.utcoffset() is None:
        raise SpecialistConsoleError("console_snapshot_value_class_refused")
    return valor


def projetar_jornada(snapshot: object) -> VisaoDaJornada:
    if not isinstance(snapshot, Mapping) or set(snapshot) != CAMPOS_DO_SNAPSHOT:
        raise SpecialistConsoleError("console_snapshot_contract_mismatch")
    # 1) A GARANTIA: cada valor que sai na visao passa no validador da sua classe fechada.
    revision = snapshot["revision"]
    if type(revision) is not int or revision < 0:
        raise SpecialistConsoleError("console_snapshot_value_class_refused")
    visao = VisaoDaJornada(
        journey_ref=_ref(snapshot["journey_ref"]),
        revision=revision,
        observed_at=_instante(snapshot["observed_at"]),
        prospect_ref=_ref(snapshot["prospect_ref"]),
        account_ref=_ref(snapshot["account_ref"]),
        party_ref=_ref(snapshot["party_ref"]),
    )
    # 2) Defesa em profundidade: o resto do snapshot (o que nao sai na visao) e' varrido pelo detector
    # por lista. Os seis valores ja' provados pela classe ficam fora da varredura: o alfabeto deles nao
    # escreve palavra, e o padrao de CID do detector acusaria hexadecimal legitimo (ex.: "e89b"). A
    # correcao de falso positivo e' o validador da classe, nunca excecao no detector.
    resto = {chave: valor for chave, valor in snapshot.items() if chave not in CAMPOS_DA_VISAO}
    if _tem_saude(resto):
        raise SpecialistConsoleError("PHI_IN_COMMERCIAL_INPUT")
    return visao


@dataclass(frozen=True, slots=True)
class RegistroDoCompromisso:
    n_minutos: int | None = field(repr=False)
    assinaturas_de_dono: tuple[str, ...]
    ato_ref: str | None
    estado: Literal["NAO_RATIFICADO"]


# Sem aprovador, data ou evidencia: politica nao ratificada.
REGISTRO_DO_COMPROMISSO: Final = RegistroDoCompromisso(
    n_minutos=None, assinaturas_de_dono=(), ato_ref=None, estado="NAO_RATIFICADO"
)


def compromisso_vigente(registro: RegistroDoCompromisso) -> int | None:
    """N so vale com ato registrado e assinatura de 2 donos distintos (ato do dono, Q-SC-1 pendente)."""
    n = registro.n_minutos
    if type(n) is not int or n <= 0:
        return None
    if not isinstance(registro.ato_ref, str) or not registro.ato_ref:
        return None
    ass = registro.assinaturas_de_dono
    if any(not isinstance(a, str) or not a for a in ass) or len(set(ass)) < 2 or len(set(ass)) != len(ass):
        return None
    return n


ESTADOS_DO_RECIBO: Final[frozenset[str]] = frozenset({"requested", "suppressed", "delivered", "failed"})


def visao_do_compromisso(
    registro: RegistroDoCompromisso, *, estado_do_recibo: str | None
) -> dict[str, object]:
    """Nunca exibe N (Q-SC-1); horario de atendimento nao configurado (GAP)."""
    del registro
    if estado_do_recibo is not None and estado_do_recibo not in ESTADOS_DO_RECIBO:
        raise SpecialistConsoleError("console_recibo_estado_invalido")
    return {"horario_de_atendimento": None, "estado_do_recibo": estado_do_recibo}

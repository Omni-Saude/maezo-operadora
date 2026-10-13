"""O que o simulador registra de cada conversa (em memoria; so' sai do processo MASCARADO).

`Turno.texto` e o `Sujeito` da conversa carregam dado real (CPF digitado, nome). Eles existem so'
na memoria do processo do simulador: tudo o que vai ao S3 passa por `mascaramento.Mascarador`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

#: De quem e' uma mensagem enviada ao beneficiario. `acesso` = texto fixo do fluxo de acesso (DL-0083).
Agente = Literal["helena", "lucas", "acesso"]
#: `acesso` = turno do fluxo de acesso conduzido pelo simulador; `persona` = mensagem da persona.
Fase = Literal["acesso", "persona"]

#: O vocabulario FECHADO que a persona declara para cada mensagem (insumo das checagens D5/D11).
TEMAS: tuple[str, ...] = (
    "cobranca",
    "saude",
    "emergencia",
    "cadastro",
    "plano",
    "humano",
    "acesso",
    "terceiro",
    "outro",
    "despedida",
)


@dataclass(frozen=True)
class Sujeito:
    """Uma pessoa do roster. `repr` oculto: CPF, nascimento e nome nunca vao a log nem a excecao."""

    rotulo: str
    cpf: str = field(repr=False)
    nascimento: date = field(repr=False)
    nome: str = field(repr=False)
    dono: bool = False
    #: Referencia portavel da AMH, preenchida pelo preflight (pseudonima; nao e' PHI).
    ref: str | None = field(default=None, repr=False)


@dataclass
class MensagemEnviada:
    agente: Agente
    texto: str


@dataclass
class EscalonamentoSimulado:
    """Um start de SP-OP-ESCALATION-001 que o interceptador NAO deixou chegar ao motor."""

    agente: str
    motivo: str | None
    severidade: str | None
    business_key: str
    resumo: str = ""


@dataclass
class Turno:
    indice: int
    fase: Fase
    #: Texto EXATO enviado ao despachante (pode ser CPF/nascimento na fase de acesso).
    texto: str
    tema: str
    mensagens: list[MensagemEnviada] = field(default_factory=list)
    escalonamentos: list[EscalonamentoSimulado] = field(default_factory=list)
    #: `FatosCobranca` (como dicionario) que a fonte do Lucas devolveu NESTE turno — o gabarito do D2.
    fatos: list[dict[str, Any]] = field(default_factory=list)
    #: Motivos de `Indisponivel` da fonte neste turno.
    fonte_indisponivel: list[str] = field(default_factory=list)
    #: Desfechos fechados da identidade da Helena neste turno.
    identidade: list[str] = field(default_factory=list)
    latencia_s: float = 0.0
    erro: str | None = None
    #: So' tokens fechados do estado final (nunca texto): intent, next_kind, response_kind...
    resultado: dict[str, Any] = field(default_factory=dict)

    def textos(self, agente: Agente | None = None) -> list[str]:
        return [m.texto for m in self.mensagens if agente is None or m.agente == agente]


@dataclass
class Conversa:
    id: str
    persona_id: str
    sujeito: Sujeito
    telefone: str = field(repr=False)
    conversation_id: str
    semente: int
    turnos: list[Turno] = field(default_factory=list)
    erro_preparo: str | None = None
    encerrada_por: str = ""

    def turnos_da_persona(self) -> list[Turno]:
        return [t for t in self.turnos if t.fase == "persona"]

    def escalonamentos(self) -> list[EscalonamentoSimulado]:
        return [e for t in self.turnos for e in t.escalonamentos]


@dataclass(frozen=True)
class Achado:
    """Uma falha de checagem. `motivo` e' texto do simulador (passa pelo mascarador antes de sair)."""

    checagem: str
    motivo: str
    turno: int | None = None
    agente: str | None = None


@dataclass
class ResultadoChecagem:
    checagem: str
    aplicavel: bool
    achados: list[Achado] = field(default_factory=list)

    @property
    def passou(self) -> bool:
        return not self.achados


@dataclass
class Avaliacao:
    """O veredito do juiz para uma conversa (escala 1..5)."""

    respondeu: int
    esclarecimento: int
    tom: int
    seguranca_clinica: int
    escalacao_adequada: int
    loop: bool
    contradicao: bool
    falha_principal: str
    turno: int | None
    motivo: str
    erro: str | None = None

    def nota_minima(self) -> int:
        return min(
            self.respondeu, self.esclarecimento, self.tom, self.seguranca_clinica, self.escalacao_adequada
        )

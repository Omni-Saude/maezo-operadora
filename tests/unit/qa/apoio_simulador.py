"""Construtores de dado SINTETICO para os testes do simulador (nenhum CPF real)."""

from __future__ import annotations

from datetime import date
from typing import Any

from maezo.qa.simulador.registro import (
    Conversa,
    EscalonamentoSimulado,
    MensagemEnviada,
    Sujeito,
    Turno,
)

#: CPF sintetico valido (digitos verificadores corretos), usado em todo teste.
CPF_SINTETICO = "52998224725"


def sujeito(*, nome: str = "Joana Pereira da Silva", dono: bool = False, rotulo: str = "s001") -> Sujeito:
    return Sujeito(
        rotulo=rotulo,
        cpf=CPF_SINTETICO,
        nascimento=date(1980, 3, 14),
        nome=nome,
        dono=dono,
        ref="ref-sintetica",
    )


def turno(
    indice: int,
    *,
    texto: str = "oi",
    tema: str = "outro",
    fase: str = "persona",
    helena: list[str] | None = None,
    lucas: list[str] | None = None,
    acesso: list[str] | None = None,
    fatos: list[dict[str, Any]] | None = None,
    escalonamentos: list[EscalonamentoSimulado] | None = None,
    erro: str | None = None,
    latencia_s: float = 1.0,
    resultado: dict[str, Any] | None = None,
) -> Turno:
    mensagens = (
        [MensagemEnviada("acesso", t) for t in acesso or []]
        + [MensagemEnviada("helena", t) for t in helena or []]
        + [MensagemEnviada("lucas", t) for t in lucas or []]
    )
    return Turno(
        indice=indice,
        fase=fase,  # type: ignore[arg-type]
        texto=texto,
        tema=tema,
        mensagens=mensagens,
        escalonamentos=list(escalonamentos or []),
        fatos=list(fatos or []),
        erro=erro,
        latencia_s=latencia_s,
        resultado=dict(resultado or {}),
    )


def conversa(*turnos: Turno, persona_id: str = "apressado", dono: bool = False) -> Conversa:
    return Conversa(
        id="c0001",
        persona_id=persona_id,
        sujeito=sujeito(dono=dono),
        telefone="5511900000500",
        conversation_id="wa:amh:hk1_abc",
        semente=1,
        turnos=list(turnos),
    )


def fatos_de_julho() -> dict[str, Any]:
    """Uma competencia paga, com valor e data de pagamento, como a `FonteGravada` grava."""
    return {
        "status_conciliado": True,
        "ciclos_sem_conciliacao": 0,
        "numero_boleto": "****0037",
        "cnab_ref": "amh-billing:2026-10-08",
        "valor_em_aberto": "0.00",
        "vencimento_referencia": "2026-07-10",
        "competencias": [
            {
                "competencia": "2026-07",
                "situacao": "paga",
                "vencimento": "2026-07-10",
                "valor_total": "8389.53",
                "valor_coparticipacao": "120.00",
                "liquidado_em": "2026-07-29",
                "boleto": "****0037",
            }
        ],
    }


def escalonamento(agente: str = "lucas", motivo: str = "cobranca") -> EscalonamentoSimulado:
    return EscalonamentoSimulado(agente=agente, motivo=motivo, severidade=None, business_key="ESC-amh-x")

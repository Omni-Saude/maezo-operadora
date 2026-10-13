"""O beneficiario simulado: o MESMO modelo dos agentes (Bedrock sa-east-1), no papel da persona.

O que o modelo ve': a persona (descricao, objetivo, estilo) e a conversa ate' aqui, MASCARADA (as
respostas dos agentes passam pelo `Mascarador`; as mensagens da propria persona ja' sao marcadores
ou texto dela). O que ele NUNCA ve': CPF, data de nascimento, nome — ele escreve `{{CPF}}` /
`{{NASCIMENTO}}` e o simulador troca na hora de enviar.

A chamada vai com `phi=True`: o provedor recusa se nao for o da zona de saude (in-region). E' a
garantia de que a conversa (que tem dado real de cobranca nas respostas) nao sai do Brasil.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .personas import MARCADOR_CPF, MARCADOR_NASCIMENTO, Persona
from .registro import TEMAS

Gerar = Callable[[str], Awaitable[str]]

MAX_CARACTERES_MENSAGEM = 400
_JSON = re.compile(r"\{.*\}", re.DOTALL)
#: Uma sequencia com cara de CPF NUNCA sai do modelo: vira o marcador (o modelo nao conhece o dado,
#: mas pode inventar um numero que coincida com o CPF de alguem quando o atendimento o pede).
_PARECE_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")


@dataclass(frozen=True)
class FalaDaPersona:
    mensagem: str
    tema: str
    encerrar: bool


class RespostaDaPersonaInvalidaError(ValueError):
    """O modelo nao devolveu o JSON combinado. Nunca ecoa a resposta."""


def montar_prompt(persona: Persona, historico: Sequence[tuple[str, str]], turno: int) -> str:
    """`historico`: pares (quem, texto) JA' MASCARADOS; quem e' `voce` ou `atendimento`."""
    linhas = "\n".join(f"[{quem}] {texto}" for quem, texto in historico) or "(a conversa ainda nao comecou)"
    pode_encerrar = turno >= persona.turnos_min
    deve_encerrar = turno >= persona.turnos_max
    return f"""Voce esta' fazendo o papel de um beneficiario de plano de saude conversando pelo WhatsApp
com o atendimento automatico da operadora. Isto e' um TESTE do atendimento; escreva SO' a proxima
mensagem do beneficiario.

PERSONA: {persona.nome}. {persona.descricao}
OBJETIVO NA CONVERSA: {persona.objetivo}
ESTILO: {persona.estilo}

REGRAS
1. Portugues do Brasil, como uma pessoa real escreve no WhatsApp.
   No maximo {MAX_CARACTERES_MENSAGEM} caracteres.
2. Voce NAO sabe CPF, data de nascimento, nome completo, numero de carteirinha nem de boleto. Se o
   atendimento pedir o CPF, escreva exatamente {MARCADOR_CPF}; se pedir a data de nascimento, escreva
   exatamente {MARCADOR_NASCIMENTO}. Nunca invente esses numeros.
3. Siga o objetivo e o estilo. Reaja ao que o atendimento respondeu (se nao entendeu, pergunte; se a
   resposta nao serviu, insista do seu jeito).
4. Textos entre colchetes como [NOME] ou [BOLETO] sao dados ocultados: trate como se voce os tivesse lido.
5. Classifique a SUA mensagem em "tema", um de: {", ".join(TEMAS)}.
6. "encerrar": true quando o objetivo foi atendido ou a conversa nao tem mais para onde ir.
   {"Voce JA pode encerrar se fizer sentido." if pode_encerrar else "Ainda NAO encerre."}
   {"Este e' o ultimo turno: despeca-se e use encerrar=true." if deve_encerrar else ""}

CONVERSA ATE' AGORA (voce = beneficiario):
{linhas}

Responda SO' com um JSON numa linha, sem texto antes ou depois:
{{"mensagem": "...", "tema": "...", "encerrar": false}}"""


def interpretar(resposta: str) -> FalaDaPersona:
    achado = _JSON.search(resposta or "")
    if achado is None:
        raise RespostaDaPersonaInvalidaError("persona: resposta sem JSON")
    try:
        dados = json.loads(achado.group(0))
    except json.JSONDecodeError:
        raise RespostaDaPersonaInvalidaError("persona: JSON invalido") from None
    if not isinstance(dados, dict):
        raise RespostaDaPersonaInvalidaError("persona: JSON nao e' objeto")
    mensagem = str(dados.get("mensagem") or "").strip()
    if not mensagem:
        raise RespostaDaPersonaInvalidaError("persona: mensagem vazia")
    mensagem = _PARECE_CPF.sub(MARCADOR_CPF, mensagem)[:MAX_CARACTERES_MENSAGEM]
    tema = str(dados.get("tema") or "outro").strip().lower()
    return FalaDaPersona(
        mensagem=mensagem,
        tema=tema if tema in TEMAS else "outro",
        encerrar=bool(dados.get("encerrar")),
    )


class BeneficiarioLLM:
    """Gera a proxima fala da persona. Uma nova tentativa quando o JSON vem quebrado."""

    def __init__(self, gerar: Gerar, *, tentativas: int = 2) -> None:
        self._gerar = gerar
        self._tentativas = max(1, tentativas)

    async def proxima(
        self, persona: Persona, historico: Sequence[tuple[str, str]], turno: int
    ) -> FalaDaPersona:
        prompt = montar_prompt(persona, historico, turno)
        ultimo: Exception | None = None
        for _ in range(self._tentativas):
            try:
                return interpretar(await self._gerar(prompt))
            except RespostaDaPersonaInvalidaError as exc:
                ultimo = exc
        raise RespostaDaPersonaInvalidaError(str(ultimo))


def gerador_da_inferencia(inferencia: object, *, tenant_id: str, papel: str) -> Gerar:
    """Adapta o provedor real (`InferenceProvider.generate`) ao `Gerar`, SEMPRE com `phi=True`."""

    async def gerar(prompt: str) -> str:
        texto: str = await inferencia.generate(  # type: ignore[attr-defined]
            prompt, phi=True, agent_id=papel, tenant_id=tenant_id
        )
        return texto

    return gerar


__all__ = [
    "BeneficiarioLLM",
    "FalaDaPersona",
    "Gerar",
    "RespostaDaPersonaInvalidaError",
    "gerador_da_inferencia",
    "interpretar",
    "montar_prompt",
]

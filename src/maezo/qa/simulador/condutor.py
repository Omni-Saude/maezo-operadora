"""O condutor: planeja as conversas, conduz cada uma contra o despachante real e registra tudo.

TELEFONES. Cada pessoa do roster ganha UM numero sintetico fixo da faixa reservada ao simulador
(`5511900000500`..`5511900000759`, nunca a do canal de teste) e e' verificada UMA vez por execucao
pelo fluxo REAL (ACEITO -> CPF -> nascimento; o numero esta' fora do cadastro, entao o documento
resolve a pessoa — DL-0084). As personas do FLUXO DE ACESSO (recusar, revogar, errar o CPF) usam a
faixa `5511900000760`..`5511900000799`, e a linha do acesso e' apagada antes de cada uma: elas
testam o fluxo inteiro, do zero.

UMA CONVERSA POR TELEFONE DE CADA VEZ. As conversas de um mesmo telefone formam uma fila; filas
diferentes rodam em paralelo ate' `concorrencia`. Entre duas conversas do mesmo telefone,
`Limpeza.reiniciar_conversa` (checkpoint, roteador, cache de identidade, casos simulados).

RITMO. O receptor tem teto de mensagens por conversa por minuto (`LimitadorDeVolume`, 6/min no dev);
o condutor espera `intervalo_min_s` entre duas mensagens da mesma conversa para medir o atendimento,
nao o teto.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from maezo.platform.webhooks.whatsapp import acesso as acesso_mod
from maezo.platform.webhooks.whatsapp.dispatch import InboundMessage
from maezo.platform.webhooks.whatsapp.security import hash_phone

from .beneficiario_llm import BeneficiarioLLM, RespostaDaPersonaInvalidaError
from .composicao import ContextoDoTurno, no_turno
from .mascaramento import Mascarador
from .personas import MARCADOR_CPF, MARCADOR_LLM, MARCADOR_NASCIMENTO, Persona, substituir
from .registro import Conversa, Fase, Sujeito, Turno

logger = structlog.get_logger(__name__)

PREFIXO_FAIXA = "5511900000"
FAIXA_PRINCIPAL = range(500, 760)
FAIXA_ACESSO = range(760, 800)
#: Chaves do estado final do turno que o registro guarda. So' tokens fechados (e `error`, curto).
TOKENS_DO_RESULTADO = (
    "next_kind",
    "intent",
    "response_kind",
    "escalation_motivo",
    "escalation_started",
    "acesso_estado",
    "acesso_cadastro",
    "population",
    "desfecho",
    "limite_excedido",
    "error",
)
ROTEIRO_DE_VERIFICACAO: tuple[str, ...] = ("Oi", "ACEITO", MARCADOR_CPF, MARCADOR_NASCIMENTO)
TENTATIVAS_INDISPONIVEL = 2
ESPERA_INDISPONIVEL_S = 15.0


@dataclass(frozen=True)
class PlanoDeConversa:
    id: str
    persona: Persona
    sujeito: Sujeito
    telefone: str
    semente: int

    def como_manifesto(self, conversation_id: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "persona": self.persona.id,
            "sujeito": self.sujeito.rotulo,
            "semente": self.semente,
            "conversation_id": conversation_id,
            "fluxo_de_acesso": self.persona.roteiro_acesso is not None,
        }


def telefone(numero: int) -> str:
    return f"{PREFIXO_FAIXA}{numero:03d}"


def planejar_conversas(
    personas: Sequence[Persona], sujeitos: Sequence[Sujeito], *, semente: int
) -> list[PlanoDeConversa]:
    """Uma conversa por persona da lista (ja' na ordem do plano). O dono, se presente, fica com a
    primeira conversa; as demais sorteiam a pessoa pela semente."""
    if not sujeitos:
        raise ValueError("planejar: nenhum sujeito resolvido no roster")
    if len(sujeitos) > len(FAIXA_PRINCIPAL):
        sujeitos = list(sujeitos)[: len(FAIXA_PRINCIPAL)]
    rng = random.Random(semente)
    indice_do_dono = next((i for i, s in enumerate(sujeitos) if s.dono), None)
    planos: list[PlanoDeConversa] = []
    acesso_seq = 0
    for i, persona in enumerate(personas):
        idx = indice_do_dono if i == 0 and indice_do_dono is not None else rng.randrange(len(sujeitos))
        if persona.roteiro_acesso is not None:
            numero = FAIXA_ACESSO[acesso_seq % len(FAIXA_ACESSO)]
            acesso_seq += 1
        else:
            numero = FAIXA_PRINCIPAL[idx]
        planos.append(
            PlanoDeConversa(
                id=f"c{i + 1:04d}",
                persona=persona,
                sujeito=sujeitos[idx],
                telefone=telefone(numero),
                semente=semente + i,
            )
        )
    return planos


@dataclass(frozen=True)
class ConfigDoCondutor:
    concorrencia: int = 3
    intervalo_min_s: float = 10.5


class Condutor:
    def __init__(
        self,
        *,
        dispatcher: Any,
        beneficiario: BeneficiarioLLM,
        limpeza: Any,
        run_id: str,
        config: ConfigDoCondutor | None = None,
        relogio: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        config = config or ConfigDoCondutor()
        if config.concorrencia < 1:
            raise ValueError("condutor: concorrencia >= 1")
        self._dispatcher = dispatcher
        self._beneficiario = beneficiario
        self._limpeza = limpeza
        self._run_id = run_id
        self._config = config
        self._relogio = relogio
        self._dormir = dormir
        self._verificados: set[str] = set()
        self._ultimo_envio: dict[str, float] = {}

    def conversation_id(self, numero: str) -> str:
        tenant = self._dispatcher.tenant_id
        return f"wa:{tenant}:{hash_phone(numero, tenant, self._dispatcher.pseudonymizer)}"

    async def executar(self, planos: Sequence[PlanoDeConversa]) -> list[Conversa]:
        filas: OrderedDict[str, list[PlanoDeConversa]] = OrderedDict()
        for plano in planos:
            filas.setdefault(plano.telefone, []).append(plano)
        semaforo = asyncio.Semaphore(self._config.concorrencia)
        conversas: list[Conversa] = []

        async def fila(planos_do_telefone: list[PlanoDeConversa]) -> None:
            async with semaforo:
                for plano in planos_do_telefone:
                    conversas.append(await self.conversar(plano))

        await asyncio.gather(*(fila(f) for f in filas.values()))
        return sorted(conversas, key=lambda c: c.id)

    async def conversar(self, plano: PlanoDeConversa) -> Conversa:
        cid = self.conversation_id(plano.telefone)
        conversa = Conversa(
            id=plano.id,
            persona_id=plano.persona.id,
            sujeito=plano.sujeito,
            telefone=plano.telefone,
            conversation_id=cid,
            semente=plano.semente,
        )
        mascarador = Mascarador(plano.sujeito)
        historico: list[tuple[str, str]] = []
        try:
            await self._limpeza.reiniciar_conversa(cid)
            if plano.persona.roteiro_acesso is not None:
                await self._limpeza.apagar_acesso(cid)
                self._verificados.discard(plano.telefone)
                feitos = await self._roteiro(conversa, plano, historico, mascarador)
            else:
                if plano.telefone not in self._verificados:
                    await self._limpeza.apagar_acesso(cid)
                    if not await self._verificar(conversa, plano):
                        conversa.erro_preparo = "verificacao_nao_concluida"
                        return conversa
                    self._verificados.add(plano.telefone)
                historico.append(("atendimento", acesso_mod.RESPOSTA_VERIFICADO))
                feitos = 0
            await self._dialogo(conversa, plano, historico, mascarador, feitos)
        except Exception as exc:  # uma conversa quebrada nao derruba a execucao
            conversa.erro_preparo = type(exc).__name__
            logger.error("simulador_conversa_falhou", conversa=plano.id, erro=type(exc).__name__)
        logger.info(
            "simulador_conversa_concluida",
            conversa=plano.id,
            persona=plano.persona.id,
            turnos=len(conversa.turnos),
            encerrada_por=conversa.encerrada_por,
        )
        return conversa

    # -- roteiro de aceite (mensagens fixas, sem persona) -------------------------------------------
    async def preparar_conversa(self, plano: PlanoDeConversa) -> Conversa:
        """Conversa NOVA no telefone do plano: limpa o estado, apaga o acesso e refaz a verificacao
        completa (ACEITO -> CPF -> nascimento). `erro_preparo` preenchido = nao concluiu."""
        cid = self.conversation_id(plano.telefone)
        conversa = Conversa(
            id=plano.id,
            persona_id=plano.persona.id,
            sujeito=plano.sujeito,
            telefone=plano.telefone,
            conversation_id=cid,
            semente=plano.semente,
        )
        try:
            await self._limpeza.reiniciar_conversa(cid)
            await self._limpeza.apagar_acesso(cid)
            self._verificados.discard(plano.telefone)
            if await self._verificar(conversa, plano):
                self._verificados.add(plano.telefone)
            else:
                conversa.erro_preparo = "verificacao_nao_concluida"
        except Exception as exc:
            conversa.erro_preparo = type(exc).__name__
            logger.error("simulador_preparo_falhou", conversa=plano.id, erro=type(exc).__name__)
        return conversa

    async def enviar_texto(self, conversa: Conversa, plano: PlanoDeConversa, texto: str) -> Turno:
        """UMA mensagem fixa (fase `persona`, tema `aceite`), com o mesmo ritmo e registro do resto."""
        return await self._enviar(conversa, plano, texto, fase="persona", tema="aceite")

    # -- verificacao (fora da persona) ------------------------------------------------------------
    async def _verificar(self, conversa: Conversa, plano: PlanoDeConversa) -> bool:
        for passo in ROTEIRO_DE_VERIFICACAO:
            turno = await self._enviar_com_retentativa(conversa, plano, passo)
            if acesso_mod.RESPOSTA_VERIFICADO in turno.textos():
                return True
        return False

    async def _enviar_com_retentativa(self, conversa: Conversa, plano: PlanoDeConversa, passo: str) -> Turno:
        turno = await self._enviar(
            conversa, plano, substituir(passo, plano.sujeito), fase="acesso", tema="acesso"
        )
        for _ in range(TENTATIVAS_INDISPONIVEL):
            if acesso_mod.INDISPONIVEL not in turno.textos():
                break
            await self._dormir(ESPERA_INDISPONIVEL_S)
            turno = await self._enviar(
                conversa, plano, substituir(passo, plano.sujeito), fase="acesso", tema="acesso"
            )
        return turno

    # -- roteiro do fluxo de acesso --------------------------------------------------------------
    async def _roteiro(
        self,
        conversa: Conversa,
        plano: PlanoDeConversa,
        historico: list[tuple[str, str]],
        mascarador: Mascarador,
    ) -> int:
        """As mensagens fixas da persona de acesso; `{{LLM}}` = uma fala do modelo. Devolve quantas
        falas do modelo ja' foram usadas (contam no limite de turnos da persona)."""
        assert plano.persona.roteiro_acesso is not None
        falas = 0
        for passo in plano.persona.roteiro_acesso:
            if passo == MARCADOR_LLM:
                falas += 1
                if not await self._fala_da_persona(conversa, plano, historico, mascarador, falas):
                    break
                if conversa.encerrada_por == "persona":
                    conversa.encerrada_por = ""  # o roteiro vai ate' o fim; a persona encerra depois dele
                continue
            turno = await self._enviar(
                conversa, plano, substituir(passo, plano.sujeito), fase="acesso", tema="acesso"
            )
            historico.append(("voce", passo))
            historico.extend(("atendimento", mascarador.texto(t)) for t in turno.textos())
        return falas

    # -- dialogo com a persona -------------------------------------------------------------------
    async def _dialogo(
        self,
        conversa: Conversa,
        plano: PlanoDeConversa,
        historico: list[tuple[str, str]],
        mascarador: Mascarador,
        feitos: int,
    ) -> None:
        if conversa.encerrada_por:
            return
        for n in range(feitos + 1, plano.persona.turnos_max + 1):
            if not await self._fala_da_persona(conversa, plano, historico, mascarador, n):
                return
            if conversa.encerrada_por:
                return
        conversa.encerrada_por = "limite_de_turnos"

    async def _fala_da_persona(
        self,
        conversa: Conversa,
        plano: PlanoDeConversa,
        historico: list[tuple[str, str]],
        mascarador: Mascarador,
        n: int,
    ) -> bool:
        """Uma fala do modelo enviada ao despachante. `False` = a conversa terminou."""
        try:
            fala = await self._beneficiario.proxima(plano.persona, historico, n)
        except RespostaDaPersonaInvalidaError:
            conversa.encerrada_por = "persona_invalida"
            return False
        except Exception as exc:
            conversa.encerrada_por = f"persona_indisponivel:{type(exc).__name__}"
            return False
        turno = await self._enviar(
            conversa, plano, substituir(fala.mensagem, plano.sujeito), fase="persona", tema=fala.tema
        )
        historico.append(("voce", fala.mensagem))
        historico.extend(("atendimento", mascarador.texto(t)) for t in turno.textos())
        if fala.encerrar and n >= plano.persona.turnos_min:
            conversa.encerrada_por = "persona"
        return True

    # -- envio -----------------------------------------------------------------------------------
    async def _ritmo(self, telefone_: str) -> None:
        ultimo = self._ultimo_envio.get(telefone_)
        if ultimo is not None:
            espera = self._config.intervalo_min_s - (self._relogio() - ultimo)
            if espera > 0:
                await self._dormir(espera)
        self._ultimo_envio[telefone_] = self._relogio()

    async def _enviar(
        self, conversa: Conversa, plano: PlanoDeConversa, texto: str, *, fase: Fase, tema: str
    ) -> Turno:
        await self._ritmo(plano.telefone)
        indice = len(conversa.turnos) + 1
        turno = Turno(indice=indice, fase=fase, texto=texto, tema=tema)
        message_id = f"wamid.simulador.{self._run_id}.{conversa.id}.{indice}"
        inicio = self._relogio()
        with no_turno(
            ContextoDoTurno(turno=turno, message_id=message_id, conversation_id=conversa.conversation_id)
        ):
            try:
                resultado = await self._dispatcher.dispatch(
                    InboundMessage(from_number=plano.telefone, text=texto, message_id=message_id)
                )
                turno.resultado = {
                    k: (
                        str(resultado[k])[:120] if k == "error" and resultado[k] is not None else resultado[k]
                    )
                    for k in TOKENS_DO_RESULTADO
                    if k in resultado
                }
            except Exception as exc:
                turno.erro = type(exc).__name__
            finally:
                turno.latencia_s = self._relogio() - inicio
        conversa.turnos.append(turno)
        return turno


__all__ = [
    "FAIXA_ACESSO",
    "FAIXA_PRINCIPAL",
    "Condutor",
    "ConfigDoCondutor",
    "PlanoDeConversa",
    "planejar_conversas",
    "telefone",
]

"""Teto de volume do canal: por conversa e por tenant, em janela deslizante.

FRENTE 7.1 (14/09/2026). O receptor nao tinha protecao de volume nenhuma — nem por beneficiario,
nem por janela. Um numero em laco, ou um incidente que faca mil pessoas escreverem ao mesmo tempo,
entrava inteiro: cada mensagem vira uma chamada de modelo, uma avaliacao de DMN e, quando o
modelo cai, um escalonamento. A fila humana recebe tudo isso no pior momento possivel.

O QUE ESTE MODULO NAO FAZ, e precisa estar dito antes de alguem confiar demais nele:

  E' MEMORIA DE PROCESSO, NAO ESTADO COMPARTILHADO. Cada replica do receptor conta o seu proprio
  trafego. Com N replicas, o teto global efetivo e' N vezes o configurado, e um beneficiario
  cujas mensagens caiam em replicas diferentes e' contado em cada uma. Hoje o servico roda com
  UMA replica (`webhook_receiver_desired_count`), entao a conta fecha; no dia em que subir para
  duas, este numero passa a ser um piso, nao um teto. Um limitador exato exigiria estado
  compartilhado (o mesmo Postgres do registro de dedup seria o lugar), e isso e' uma decisao de
  infraestrutura que esta frente nao toma — o que ela entrega e' a protecao que existe hoje
  contra o caso que realmente acontece: UM numero em laco.

  ELE NAO PERSISTE. Reiniciar a task zera as janelas. Um reinicio durante um pico reabre o teto,
  o que e' aceitavel: o pior caso e' o comportamento de hoje, que e' nenhum limite.

JANELA DESLIZANTE, e nao balde por minuto de relogio: um balde por minuto deixa passar o dobro do
teto na virada (o fim de um minuto e o comeco do seguinte). A janela deslizante custa guardar os
carimbos de tempo, que sao poucos por definicao — se fossem muitos, o limite ja teria recusado.

O RELOGIO E' INJETADO (`agora`) para o teste nao depender de dormir. Monotonico por padrao: um
ajuste de relogio do sistema no meio de um pico nao pode abrir nem fechar o teto por acidente.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

#: Escopo do teto que recusou a mensagem. Vocabulario FECHADO: vira rotulo de metrica.
ESCOPO_CONVERSA: Final[str] = "conversa"
ESCOPO_TENANT: Final[str] = "tenant"

#: A janela. Um minuto e' a unidade em que os dois tetos sao pensados e falados.
JANELA_SEGUNDOS: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class Veredito:
    """Resultado de uma consulta ao limitador."""

    permitido: bool
    escopo: str | None = None
    #: Quantas mensagens ja' havia na janela quando a decisao foi tomada. So' para log.
    observadas: int = 0


class LimitadorDeVolume:
    """Teto por conversa e por tenant, em janela deslizante de um minuto.

    `por_conversa` e `por_tenant` em zero DESLIGAM aquele teto — e desligar e' uma escolha que a
    task definition declara, nao um efeito colateral de esquecer a variavel: os defaults em
    `settings.py` sao numeros de verdade.

    A CONVERSA e' identificada pelo `conversation_id` keyed (`wa:{tenant}:{hk1_...}`), nunca pelo
    telefone: o limitador guarda essa chave em memoria, e um telefone bruto em estrutura de
    processo seria PHI onde nao precisa haver.
    """

    def __init__(
        self,
        *,
        por_conversa: int,
        por_tenant: int,
        agora: Callable[[], float] = time.monotonic,
    ) -> None:
        self._por_conversa = max(0, por_conversa)
        self._por_tenant = max(0, por_tenant)
        self._agora = agora
        self._conversas: defaultdict[str, deque[float]] = defaultdict(deque)
        self._tenants: defaultdict[str, deque[float]] = defaultdict(deque)

    def _podar(self, janela: deque[float], agora: float) -> None:
        limite = agora - JANELA_SEGUNDOS
        while janela and janela[0] <= limite:
            janela.popleft()

    def registrar(self, *, tenant_id: str, conversation_id: str) -> Veredito:
        """Conta esta mensagem e diz se ela pode seguir.

        A ORDEM E' DELIBERADA: o teto da conversa e' avaliado primeiro. Quando os dois estouram ao
        mesmo tempo, o motivo reportado e' o da conversa — que e' o acionavel (um numero em laco),
        enquanto o do tenant e' consequencia. E uma mensagem RECUSADA nao entra em nenhuma das duas
        janelas: contar o que foi barrado faria uma rajada prolongar o proprio bloqueio.
        """
        agora = self._agora()

        conversa = self._conversas[conversation_id]
        self._podar(conversa, agora)
        if self._por_conversa and len(conversa) >= self._por_conversa:
            return Veredito(permitido=False, escopo=ESCOPO_CONVERSA, observadas=len(conversa))

        tenant = self._tenants[tenant_id]
        self._podar(tenant, agora)
        if self._por_tenant and len(tenant) >= self._por_tenant:
            return Veredito(permitido=False, escopo=ESCOPO_TENANT, observadas=len(tenant))

        conversa.append(agora)
        tenant.append(agora)
        return Veredito(permitido=True, observadas=len(conversa))

    def esquecer_conversas_ociosas(self) -> int:
        """Descarta janelas vazias. Devolve quantas saiu.

        Sem isto o dicionario cresce com uma entrada por conversa VISTA, para sempre — um
        vazamento lento que so' aparece semanas depois, em memoria de container. Chamado pelo
        receptor de tempos em tempos, nunca no caminho da mensagem.
        """
        agora = self._agora()
        mortas = []
        for chave, janela in self._conversas.items():
            self._podar(janela, agora)
            if not janela:
                mortas.append(chave)
        for chave in mortas:
            del self._conversas[chave]
        return len(mortas)

"""Dublê em memoria de `AgenteAtivoStore` (ADR-0062).

Mora aqui, e nao em `src/`, pela cerca §8.4: nenhum `Fake*` alcancavel de raiz de producao. Ele
implementa a MESMA semantica de compare-and-set do adaptador Postgres (insert perde se a linha
existe; update so' com a revisao esperada, e a revisao nova e' `esperada + 1`), e expoe um ponto
de sincronizacao (`barreira_de_leitura`) para provar a corrida entre duas escritas.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime

from maezo.platform.webhooks.whatsapp.roteamento import LinhaAgenteAtivo


class FakeAgenteAtivoStore:
    def __init__(self, *, tenant: str = "amh") -> None:
        self.tenant = tenant
        self.linhas: dict[str, LinhaAgenteAtivo] = {}
        self.gravacoes = 0
        self.cas_perdidos = 0
        self.purgas: list[tuple[datetime, int]] = []
        #: Quando presente, a PRIMEIRA leitura de cada chamador espera aqui: com `Barrier(2)`,
        #: duas escritas leem a mesma revisao antes de qualquer uma gravar.
        self.barreira_de_leitura: asyncio.Barrier | None = None
        self._ja_esperou: set[int] = set()

    async def ler(self, conversation_id: str) -> LinhaAgenteAtivo | None:
        linha = self.linhas.get(conversation_id)
        barreira = self.barreira_de_leitura
        tarefa = id(asyncio.current_task())
        if barreira is not None and tarefa not in self._ja_esperou:
            self._ja_esperou.add(tarefa)
            await barreira.wait()
        return linha

    async def gravar(self, linha: LinhaAgenteAtivo, *, revisao_esperada: int | None) -> bool:
        atual = self.linhas.get(linha.conversation_id)
        if revisao_esperada is None:
            if atual is not None:
                self.cas_perdidos += 1
                return False
            self.linhas[linha.conversation_id] = replace(linha, revisao=0)
        else:
            if atual is None or atual.revisao != revisao_esperada:
                self.cas_perdidos += 1
                return False
            self.linhas[linha.conversation_id] = replace(linha, revisao=revisao_esperada + 1)
        self.gravacoes += 1
        return True

    async def purgar_antigas(self, *, antes_de: datetime, limite: int) -> int:
        self.purgas.append((antes_de, limite))
        velhas = [c for c, linha in self.linhas.items() if linha.ultimo_turno_em < antes_de][:limite]
        for conversa in velhas:
            del self.linhas[conversa]
        return len(velhas)

"""O agente de ENTRADA em servico proprio (ADR-0067 fase 4b).

    cliente.py        `AgenteDeEntradaRemoto` — a porta `AgenteDeEntrada` por HTTP interno (lado do receptor)
                      e `RemetenteRemoto` (lado do agente: o envio volta ao receptor com o token do turno)
    rota_de_envio.py  `POST /interno/agentes/envio` no receptor
    servidor.py       o app interno do servico do agente (as operacoes da porta, em processo)
    servico.py        a partida do servico (`python -m maezo.platform.agente_remoto`)

O protocolo de fio (codec, rotas, assinatura) mora em `maezo.contratos.conversa_remota`. Sem
`MAEZO_AGENTE_DE_ENTRADA_URL` no receptor nada disto e' usado: o turno roda em processo, como sempre.
"""

from __future__ import annotations

__all__: list[str] = []

"""`maezo.contratos` — o que a plataforma e os agentes combinam entre si (ADR-0067).

A plataforma (receptor, gateway, runtime, worker, portal) nao importa `maezo.agents.<id>`, e um agente nao
importa outro. Quando os dois lados precisam de um nome em comum — a forma de um turno, o vocabulario da
identidade, a porta de uma fonte de dados —, ele mora aqui, e os dois lados importam daqui. A implementacao
de um agente e' resolvida pelo framework de agentes a partir do id
(`maezo.agents.carregar_agente_de_entrada`).

    maezo.contratos.conversa                o turno do agente de entrada do numero unico
    maezo.contratos.identidade_beneficiario o vocabulario fechado da identidade (DL-0077/DL-0078)
    maezo.contratos.fatos_do_plano          a porta das consultas do plano na AMH (contrato TINA)

Mudanca num contrato e' mudanca dos DOIS lados: roda a suite da plataforma e a do agente.
"""

from __future__ import annotations

__all__: list[str] = []

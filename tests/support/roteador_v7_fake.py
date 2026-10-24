"""A decisao do roteador v7 (JSON-in-text) para os modelos falsos dos testes da Helena.

Os testes escrevem a decisao no vocabulario INTERNO de sempre (`cobertura`, `equipe`, `sintoma`...) e este
ajudante a traduz para o contrato v7 (`roteador_v7`), com os demais campos no valor neutro.
"""

from __future__ import annotations

import json
from typing import Any

_PARA_V7: dict[str, tuple[str, str]] = {
    "cobertura": ("cobertura_fora", "nenhum"),
    "equipe": ("atendimento_humano", "nenhum"),
    "sintoma": ("saude", "sintoma_proprio"),
    "saude": ("saude", "duvida_geral"),
    "cobranca": ("lucas_cobranca", "nenhum"),
    "pedidos": ("lucas_pedidos", "nenhum"),
    "plano": ("plano", "nenhum"),
    "conversa": ("conversa", "nenhum"),
}


def decisao_v7(
    habilidade: str,
    *,
    responde: bool = False,
    sim_nao: str = "nao_se_aplica",
    opcao: str = "nenhuma",
    sinais: list[str] | None = None,
    alarme: str = "uncertain",
    historia: bool = False,
    tipo: str | None = None,
    recusa: bool = False,
    correcao: bool = False,
    habilidade_provavel: str | None = None,
    alternativa: str | None = None,
    **_: Any,
) -> str:
    """O JSON v7. `habilidade="duvida"` vira dois pedidos com `needs_clarifying_question`."""
    pedidos: list[str] = []
    esclarecer = False
    if habilidade == "duvida":
        a, b = _PARA_V7[habilidade_provavel or "conversa"][0], _PARA_V7[alternativa or "conversa"][0]
        pedidos, esclarecer = [a, b], True
        habilidade = habilidade_provavel or "conversa"
    skill, tipo_padrao = _PARA_V7[habilidade]
    return json.dumps(
        {
            "skill": skill,
            "is_continuation_of_topic": responde,
            "resposta_sim_nao": sim_nao,
            "chosen_option": opcao,
            "alarm_signs_reported": [{"sinal": s, "de_quem": "pessoa"} for s in (sinais or [])],
            "alarm_confidence": alarme,
            "is_story_or_past": historia,
            "is_personal_symptom_vs_general_question": tipo or tipo_padrao,
            "topic_events": [],
            "multiple_requests": pedidos,
            "refusal_of_team": recusa,
            "correction": correcao,
            "needs_clarifying_question": esclarecer,
        },
        ensure_ascii=False,
    )

"""O TEXTO FIXO que `classify` deixa pronto (sem modelo) e as falas anteriores da pessoa. Helpers de conversa.

Limpeza da saude, etapa 1 (dono, 02/11/2026): MOVIDO SEM MUDANCA de `habilidades/saude/informar.py`
(`_base_texto_fixo`, agora `texto_fixo`; `_falas_da_pessoa`, agora `falas_da_pessoa`). O roteador, o
encerramento do caso, a oferta da equipe e a porta unica usam; a base de saude pode sair sem eles. O grafo
aponta `_texto_fixo` e `_falas_da_pessoa` para ca'.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def texto_fixo(update: dict[str, Any], texto: str, *, pendente: str | None = None) -> dict[str, Any]:
    """O turno vira `information` com o texto fixo pronto (e a pergunta pendente, quando ha')."""
    from maezo.agents.helena.graph import _rota_informativa

    update["intent"] = "information"
    update["sintoma_codigo"] = None
    update.pop("consulta_subtipo", None)
    update["response_text"] = texto
    if pendente is not None:
        update["coleta_pendente"] = pendente
    return _rota_informativa(update)


def falas_da_pessoa(state: Mapping[str, Any]) -> list[str]:
    """As mensagens ANTERIORES da pessoa no historico curto (DL-0080), da mais antiga para a mais recente.
    As falas da Helena NUNCA entram (podem ter dado do plano/carteirinha)."""
    historico = state.get("historico_conversa")
    if not isinstance(historico, list):
        return []
    return [
        str(e.get("texto") or "")
        for e in historico
        if isinstance(e, Mapping) and e.get("papel") == "beneficiario" and e.get("texto")
    ]


__all__ = ["falas_da_pessoa", "texto_fixo"]

"""A leitura DETERMINISTICA (sem modelo) das respostas OBVIAS as perguntas de alerta do protocolo.

Teste do dono de 26/10/2026 (logs `/ecs/maezo-operadora-dev/agent-helena`, 16:39-17:12 UTC): com o modelo em
`ReadTimeout` em quase metade das chamadas, a leitura da resposta falhou (`helena_protocolo_modelo_falhou
etapa=leitura`) e "nehuma", "nao, eu respondi NAO para todas" e "eu ja' respondi nao para todas as perguntas
acima" viraram re-pergunta e depois a oferta da equipe. A resposta obvia nao precisa do modelo:

- TODAS NAO: so' negativas ("nao", "nenhuma", "nada", erro leve de digitacao em "nenhuma"/"nenhum") e palavras
  de ligacao de um vocabulario FECHADO ("eu ja' respondi", "para todas", "as suas perguntas acima", "pqp");
- SIM EM ALGUMAS: "a 2 sim", "so' a 3", "sim na 1", "1 e 3 sim", "a primeira sim" (as outras = nao);
- TODAS SIM: "todas", "sim para todas"; o "sim" sozinho so' quando ha' UMA pergunta.

Qualquer palavra FORA do vocabulario fechado ("nao, mas to' com falta de ar agora", "nao sei", "acho que nao")
= `None`: o modelo le' (e o sinal de alarme afirmado ja' passou pela trava de entrada antes daqui). Na duvida,
nunca se adivinha.
"""

from __future__ import annotations

import re
from typing import Final

from maezo.agents.helena.habilidades.conversa.leitura_sem_modelo import (
    TODAS_NAO as _TODAS_NAO,
)
from maezo.agents.helena.habilidades.conversa.leitura_sem_modelo import nega_tudo
from maezo.agents.helena.habilidades.conversa.leitura_sem_modelo import simbolos as _simbolos

#: "so' a 3" / "sim na 1" / "a 2 sim" / "1 e 3 sim", com o "as outras nao" opcional no fim.
_SIM_EM_ALGUMAS: Final[re.Pattern[str]] = re.compile(r"^(?:[SO]+N+|N+S+|ON+S+)(?:RX|XR|RT?X|XRT?)?$")
_TODAS_SIM: Final[re.Pattern[str]] = re.compile(r"^S*TS*$")


def ler_sem_modelo(texto: str, quantas: int) -> tuple[str, ...] | None:
    """As respostas (`sim`/`nao`, uma por pergunta, na ordem) quando a mensagem e' obvia; senao `None`."""
    if quantas < 1:
        return None
    lido = _simbolos(texto, quantas)
    if lido is None or not lido[0]:
        return None
    simbolos, numeros = lido
    if _TODAS_NAO.match(simbolos):
        return ("nao",) * quantas
    if _SIM_EM_ALGUMAS.match(simbolos):
        return tuple("sim" if i in numeros else "nao" for i in range(1, quantas + 1))
    if _TODAS_SIM.match(simbolos) or (simbolos == "S" and quantas == 1):
        return ("sim",) * quantas
    return None


__all__ = ["ler_sem_modelo", "nega_tudo"]

"""maezo.runtime.guards — pre-resolved worker/DMN fact validators (fleet hardening ciclo 2,
familia NONE-GUARDRAIL-MISSING: BEA-04/FER-08). Um valor invalido ou ausente e' um sinal de
QUALIDADE DE DADO, nunca uma decisao de negocio (C3): os validadores aqui SO checam forma/tipo,
nunca um limiar ou uma politica. O defeito que este modulo fecha e' sempre o mesmo, em campos
diferentes de agentes diferentes: um valor pre-resolvido por worker/DMN que o grafo apenas
CONSOME (nunca deriva) colapsava, quando ausente ou do tipo errado, para um default que tem a
MESMA CARA de um fato real (`0`, `False`, string vazia sem marca), tornando a corrupcao invisivel
no dossie/telemetria que um humano le. Cada helper aqui devolve `None` nesse caso -- nunca o
default mascarado -- para que o chamador registre a lacuna como um token de CLASSE, nunca o
valor cru (que pode carregar PHI).

REUSO PRIMEIRO -- antes de adicionar um validador aqui, confira se um dos dois abaixo ja cobre a
forma do seu campo: `require_number` para um fato PRE-RESOLVIDO numerico de worker (contagem/
score, `int`, `bool` excluido -- ver `agents/beatriz/graph.py::_score_consumed`); e
`require_iso8601_duration` para um prazo/SLA de saida de DMN `typeRef="string"` (ADR-0018
§4-bis-A: "dias/SLA -> string ISO 8601" -- as DMNs deste repo emitem PERIODOS em dias, ex.
`"P10D"`, nunca uma data/hora de calendario; nao ha gramatica ISO-8601 de DURACAO no `datetime`
da stdlib, so' `date`/`datetime`, por isso este modulo tem seu proprio regex em vez de tentar
`datetime.fromisoformat`, que rejeitaria `"P10D"` mesmo sendo um valor perfeitamente valido -- ver
`agents/fernando/graph.py::{_build_message,_build_dossier}`).
"""

from __future__ import annotations

import re
from typing import Any

#: Gramatica ISO-8601 de DURACAO (`PnYnMnWnDTnHnMnS`, pelo menos um designador apos o `P`;
#: exige um digito imediatamente apos `T` quando presente, para nao casar o `"PT"` vazio). Esta
#: e' a forma que as saidas `typeRef="string"` de SLA/prazo das DMNs da frota usam de fato --
#: `spec/processes/dmn/inadimplencia_purga.dmn`/`inadimplencia_sla.dmn`: `"P10D"`, `"P15D"`,
#: `"P60D"`, `"P7D"` -- uma DURACAO, nunca uma data/hora de calendario.
_ISO8601_DURATION: re.Pattern[str] = re.compile(
    r"^P(?!$)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?$"
)


def require_number(value: Any, *, field: str, notes: list[str]) -> int | None:
    """Consome defensivamente um fato NUMERICO pre-resolvido por worker -- nunca derivado, nunca
    recalculado pelo chamador. So' um `int` genuino e' admitido (`bool` excluido -- um `bool` e'
    subclasse de `int`, nunca um score/contagem legitimo); qualquer outra coisa (`float`, string
    numerica, `None`, uma estrutura) e' REJEITADA para `None`, anexando um token de classe
    limitado a `notes`: `f"{field}_ausente"` quando o valor e' `None`/omitido,
    `f"{field}_invalido"` caso contrario -- a rejeicao vira uma lacuna visivel, nunca um `0`
    silencioso que se parece com uma contagem real.
    """
    if value is None:
        notes.append(f"{field}_ausente")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        notes.append(f"{field}_invalido")
        return None
    return int(value)  # narrows `Any` explicitly -- `value` is already a genuine, non-bool int


def require_iso8601_duration(value: Any, *, field: str, notes: list[str] | None = None) -> str | None:
    """Consome defensivamente um fato de DURACAO ISO-8601 pre-resolvido por worker/DMN --
    checagem SO' DE FORMA (nunca uma regra de negocio, C3: aqui se checa FORMATO, nunca um
    limiar/politica). Uma string nao-vazia que casa a gramatica ISO-8601 de duracao passa
    INALTERADA; qualquer outra coisa (ausente, tipo errado, malformada) e' REJEITADA para `None`.
    `notes`, quando fornecido, recebe o mesmo par de tokens de `require_number`
    (`f"{field}_ausente"` / `f"{field}_invalido"`); omitido, o chamador decide como registrar a
    lacuna (parametro opcional para nao forcar um canal de lacunas em grafos que ainda nao tem
    um, ex.: `fernando/graph.py`, que sinaliza via log estruturado em vez de uma lista de estado).
    """
    if not value:
        if notes is not None:
            notes.append(f"{field}_ausente")
        return None
    if not isinstance(value, str) or not _ISO8601_DURATION.match(value):
        if notes is not None:
            notes.append(f"{field}_invalido")
        return None
    return value

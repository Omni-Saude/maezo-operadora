"""O FOCO de uma pergunta de valor de cobranca, decidido pelo TEXTO do beneficiario (DL-0087, 10/10/2026).

Decisao do dono (tom humanizado): o Lucas responde SO' o que foi perguntado. "Quanto foi a mensalidade de
julho?" nao pode virar um extrato com coparticipacao, saldo, vencimento, pagamento e boleto. O Lucas nao
ve' texto (ADR-0062, `lucas_turno.py`), entao quem decide o campo perguntado e' a Helena, onde o texto
existe, por um lexico PEQUENO e deterministico — o mesmo lugar e o mesmo desenho do ano da competencia
(`competencia.py::resolver_ano_da_competencia`, #721). O valor viaja no handoff como enum fechado.

Precedencia (a primeira que casa vence): coparticipacao > saldo > "quanto paguei" (valor) > pagamento >
vencimento > valor > geral. "Quanto paguei em maio?" pergunta o VALOR, nao a data; "quando paguei?"
pergunta a data. `geral` = valor + situacao numa frase.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal, get_args

CobrancaFoco = Literal[
    "valor",
    "coparticipacao",
    "saldo",
    "pagamento",
    "vencimento",
    "geral",
    "ultima",
    "esclarecimento",
    "extrato",
    "pagamento_ao_prestador",
    "como_pagar",
    "ja_paguei",
]

#: O dominio FECHADO do foco. `geral` e' o default (nenhum termo do lexico no texto).
COBRANCA_FOCOS: Final[frozenset[str]] = frozenset(get_args(CobrancaFoco))
FOCO_GERAL: Final[str] = "geral"

#: `ultima` (simulador, 13/10/2026): pergunta de CONFIRMACAO sobre a mensalidade mais recente ("Essa e' a
#: ultima? Nao e' novembro?"). `esclarecimento` NAO sai do lexico: o despachante o poe quando reaproveita a
#: resposta anterior ("de qual ano?", `runtime/cobranca_esclarecimento.py`) — a resposta e' a curta.
FOCO_ULTIMA: Final[str] = "ultima"
FOCO_ESCLARECIMENTO: Final[str] = "esclarecimento"
#: Rodada 5 (13/10/2026): "manda o extrato detalhado" (o Lucas nao envia extrato) e "paguei a consulta
#: direto ao medico e nao aparece" (pagamento ao prestador nao e' coparticipacao cobrada pelo plano).
FOCO_EXTRATO: Final[str] = "extrato"
FOCO_PAGAMENTO_AO_PRESTADOR: Final[str] = "pagamento_ao_prestador"
#: Rodada 6 (13/10/2026): "como faco pra pagar depois do vencimento?" e "ja' paguei dia 06, e o comprovante?".
FOCO_COMO_PAGAR: Final[str] = "como_pagar"
FOCO_JA_PAGUEI: Final[str] = "ja_paguei"

_MESES_DO_ANO: Final[str] = (
    "janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
)

#: Lexico na ordem de precedencia, sobre o texto sem acento e minusculo.
_LEXICO: Final[tuple[tuple[CobrancaFoco, re.Pattern[str]], ...]] = (
    ("extrato", re.compile(r"\bextrato\b|\bdemonstrativo\b|\bdetalhad")),
    ("ja_paguei", re.compile(r"\bpaguei (?:no )?dia\b|\bcomprovante\b|\bpaguei ontem\b|\bpaguei hoje\b")),
    (
        "como_pagar",
        re.compile(
            r"\bdepois do vencimento\b|\bapos o vencimento\b|\bpagar atrasad|\bpagar em atraso\b"
            r"|\bcomo (?:faco|posso|eu faco|que eu faco) (?:pra |para )?pagar\b"
        ),
    ),
    (
        "pagamento_ao_prestador",
        re.compile(
            r"\bdireto (?:ao|pro|para o|na|no|a) "
            r"(?:medico|clinica|consultorio|laboratorio|dentista|hospital)\b"
            r"|\bpaguei (?:a|o) (?:consulta|exame|procedimento)\b"
            r"|\bpaguei (?:ao|pro|para o|na|no|a) (?:medico|clinica|consultorio|laboratorio|dentista)\b"
            r"|\bfui (?:no|ao) (?:medico|dentista)\b"
        ),
    ),
    (
        "ultima",
        re.compile(
            r"\b(?:essa|esta)\s+(?:e\s+)?a\s+(?:ultima|mais recente)\b|^\s*e a (?:ultima|mais recente)\s*\?"
            r"|\bnao e (?:a )?(?:de )?(?:" + _MESES_DO_ANO + r")\b"
        ),
    ),
    ("coparticipacao", re.compile(r"copart")),
    # 13/10/2026 (simulador): "o TOTAL em aberto, tudo junto?" e' saldo em toda forma de perguntar o total.
    (
        "saldo",
        re.compile(
            r"\bsaldo\b|\bdevo\b|\bem aberto\b|\bfalta pagar\b|\btudo junto\b|\bao todo\b|\bno total\b"
            r"|\bsomand|\bdevendo\b"
        ),
    ),
    ("valor", re.compile(r"\bquanto paguei\b")),
    # 13/10/2026 (simulador): "a mensalidade de julho foi paga?" pergunta do PAGAMENTO daquele mes.
    ("pagamento", re.compile(r"\bpaguei\b|\bpagamento\b|\b(?:foi|esta|ta|ja foi) pag[oa]\b")),
    ("vencimento", re.compile(r"\bvence|\bvencimento\b")),
    ("valor", re.compile(r"\bvalor\b|\bquanto foi\b|\bquanto e\b")),
)

_ESPACOS: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", plano)


def decidir_foco_da_cobranca(texto: object) -> CobrancaFoco:
    """O campo perguntado, pelo lexico; `geral` quando nenhum termo casa (ou o texto nao e' `str`)."""
    if not isinstance(texto, str):
        return "geral"
    plano = _normalizar(texto)
    for foco, padrao in _LEXICO:
        if padrao.search(plano):
            return foco
    return "geral"


def foco_valido(valor: object) -> bool:
    return isinstance(valor, str) and valor in COBRANCA_FOCOS


__all__ = [
    "COBRANCA_FOCOS",
    "FOCO_COMO_PAGAR",
    "FOCO_ESCLARECIMENTO",
    "FOCO_EXTRATO",
    "FOCO_GERAL",
    "FOCO_JA_PAGUEI",
    "FOCO_PAGAMENTO_AO_PRESTADOR",
    "FOCO_ULTIMA",
    "CobrancaFoco",
    "decidir_foco_da_cobranca",
    "foco_valido",
]

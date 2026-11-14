"""O caso clinico PARALELO: a chave de negocio de um alerta clinico grave com caso aberto na conversa.

O PROBLEMA (bateria do Lucas, 02/10/2026, caso LU090). A chave `ESC-{tenant}-{conversation_id}` e' UMA
por conversa ("maximo uma instancia ativa por conversa"). Com um caso de COBRANCA aberto, "estou com dor
no peito e falta de ar" voltava `ja_ativo`: a Helena respondia "seu atendimento ja esta aberto", o caso
seguia P2 em atendimento-humano e o alerta clinico P1 nao chegava ao plantao.

A REGRA (decisao do diretor de tecnologia, 04/10/2026, DL-0072): um alerta clinico GRAVE nunca e'
suprimido por um caso que nao e' clinico. Se a chave da conversa ja' esta ocupada por um caso de outra
classe, a Helena abre um caso clinico PROPRIO, com a chave `ESC-{tenant}-{conversation_id}-clin`, e o
caso de cobranca segue como esta. O contrario nao muda: com caso clinico aberto, a cobranca nao abre
outro. Um alerta clinico novo com o caso clinico ja' aberto tambem nao abre outro.

AMPLIACAO (DL-0097 B, rodada 10, 14/10/2026), que e' o comportamento VIGENTE em `agents/helena/graph.py`
(`_MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA`, `_caso_clinico_cobre_o_motivo`): o caso paralelo abre para
QUALQUER severidade e tambem para `intencao_clinica`, e uma pergunta clinica aberta (`intencao_clinica`)
nao segura a chave contra um alerta clinico novo. O contrato SP-OP-ESCALATION-001 ainda descreve so' a
regra GRAVE; a emenda que o alinha ao codigo aguarda o medico-auditor e o regulatorio.

Este modulo e' so' a forma da chave: quem decide quando abrir e' `agents/helena/graph.py`, e quem
precisa reconhecer a chave (o `agent-resume`, que confere a ancora do evento) importa daqui.
"""

from __future__ import annotations

from typing import Final

SUFIXO_CASO_CLINICO: Final[str] = "-clin"

#: Os `motivo_categoria` que sao alerta clinico. Os outros (cobranca, solicitacao_humano,
#: falha_tecnica, intencao_clinica, outro) NAO seguram a chave contra um alerta clinico grave.
MOTIVOS_DE_ALERTA_CLINICO: Final[frozenset[str]] = frozenset({"red_flag_clinico", "risco_psicossocial"})


def chave_do_escalonamento(tenant_id: str, conversation_id: str, *, clinico: bool = False) -> str:
    """`ESC-{tenant}-{conversation_id}`, ou a mesma com o sufixo do caso clinico paralelo."""
    base = f"ESC-{tenant_id}-{conversation_id}"
    return base + SUFIXO_CASO_CLINICO if clinico else base


def chave_e_da_conversa(chave: str, tenant_id: str, conversation_id: str) -> bool:
    """A chave e' a do escalonamento desta conversa — a principal ou a do caso clinico paralelo."""
    return chave in (
        chave_do_escalonamento(tenant_id, conversation_id),
        chave_do_escalonamento(tenant_id, conversation_id, clinico=True),
    )

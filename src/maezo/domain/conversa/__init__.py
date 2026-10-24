"""`maezo.domain.conversa` — regras LEXICAS e de calendario da conversa com o beneficiario (ADR-0067).

Funcoes puras (so' biblioteca padrao) que a Helena, o Lucas e o despachante do numero unico usam ao mesmo
tempo: o foco, a lista, a intencao e a reclamacao de cobranca, a competencia (mes/ano) do texto, o caso
clinico aberto, o aviso de terceiro e a normalizacao do texto. Moravam em `maezo.runtime`, que nao e' lugar
de regra de conversa; um agente as importa daqui sem importar a plataforma.

    maezo.domain.conversa.normalizacao       texto sem acento, caixa baixa, pontuacao como espaco
    maezo.domain.conversa.competencia        o mes/ano de que o texto fala
    maezo.domain.conversa.cobranca_*         foco, lista, esclarecimento, intencao e reclamacao de cobranca
    maezo.domain.conversa.caso_clinico       a chave do escalonamento e os motivos de alerta clinico
    maezo.domain.conversa.terceiro_no_texto  o aviso de que so' o proprio cadastro e' mostrado
"""

from __future__ import annotations

__all__: list[str] = []

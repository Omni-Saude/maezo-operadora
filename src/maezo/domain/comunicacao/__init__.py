"""`maezo.domain.comunicacao` — o que pode e o que nao pode ser dito ao beneficiario (ADR-0067 §3).

As cercas de saida valem para QUALQUER texto que sai para o beneficiario, nao so' para o da Helena: a
Helena as aplica ao que redige, o Lucas e o gateway as aplicam a retomada escrita pela equipe. Por isso
moram no dominio (folha: so' biblioteca padrao), e nao dentro de um agente.

    maezo.domain.comunicacao.cerca_de_saida   recusas de canal, negativa clinica e promessas
    maezo.domain.comunicacao.retomada_saida   composicao e checagem da mensagem de retomada
"""

from __future__ import annotations

__all__: list[str] = []

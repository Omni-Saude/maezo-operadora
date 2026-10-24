"""Os textos fixos da habilidade `saude` na OPCAO C (decisao do dono de 23/10/2026, DL-0154), com a chave
de cada um no pacote de comportamento. Modulo FOLHA (sem import do grafo)."""

from __future__ import annotations

from typing import Final

#: O fecho da duvida geral respondida UMA vez (no lugar do aviso que oferecia a equipe a cada resposta).
CHAVE_FECHO: Final[str] = "saude_fecho_da_duvida"
TEXTO_FECHO: Final[str] = (
    "Esta informação é geral e não substitui avaliação médica. Posso ajudar em mais alguma coisa?"
)
#: A pessoa insiste no MESMO assunto: o medico ou a equipe (opcoes: onde marcar / equipe de saude).
CHAVE_OFERTA_MEDICO: Final[str] = "saude_oferta_medico"
TEXTO_OFERTA_MEDICO: Final[str] = (
    "Para ir além disso, quem pode te orientar é o médico. Quer que eu te mostre onde marcar a consulta ou "
    "prefere falar com a equipe de saúde?"
)

PADRAO: Final[dict[str, str]] = {CHAVE_FECHO: TEXTO_FECHO, CHAVE_OFERTA_MEDICO: TEXTO_OFERTA_MEDICO}

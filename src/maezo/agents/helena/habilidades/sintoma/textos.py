"""Os textos fixos da habilidade `sintoma` (decisoes do dono de 23/10/2026, DL-0154), com a chave de cada
um no pacote de comportamento (`spec/agents/helena/pacote/textos.yaml`). Modulo FOLHA (sem import do
grafo)."""

from __future__ import annotations

from typing import Final

from maezo.agents.helena.helena_v4 import TEXTO_DA_REPERGUNTA

#: Resposta sem clareza a pergunta de alerta: a pergunta de novo, UMA vez, com outras palavras (`{sinais}`).
CHAVE_REPERGUNTA: Final[str] = "sintoma_repergunta"
#: Continuou sem clareza: a oferta da equipe de saude, sem 192 e sem caso.
CHAVE_OFERTA_EQUIPE: Final[str] = "sintoma_oferta_equipe"
TEXTO_OFERTA_EQUIPE: Final[str] = (
    "Não consegui entender bem a sua resposta. Para não arriscar, posso passar o seu caso para a nossa "
    "equipe de saúde avaliar com você. Quer? Responda sim ou não."
)
#: "Nao" a oferta (ou as opcoes depois do fecho): fecha sem caso.
CHAVE_OFERTA_RECUSADA: Final[str] = "sintoma_oferta_recusada"
TEXTO_OFERTA_RECUSADA: Final[str] = "Tudo bem. Se o sintoma mudar ou piorar, é só me chamar."
#: Seguimento sobre o mesmo sintoma depois de fechar (texto do dono, palavra por palavra).
CHAVE_AVALIAR_CAUSA: Final[str] = "sintoma_avaliar_causa"
TEXTO_AVALIAR_CAUSA: Final[str] = (
    "Quem pode avaliar a causa é o médico. Quer que eu te mostre onde marcar a consulta ou prefere falar "
    "com a equipe de saúde?"
)
#: Alarme JA' tratado e a pessoa diz que foi engano / nao sente aquilo (texto do dono, palavra por palavra).
CHAVE_CASO_MANTIDO: Final[str] = "caso_mantido_para_a_equipe"
TEXTO_CASO_MANTIDO: Final[str] = "Entendi. Mantive o seu caso para a equipe de saúde confirmar com você."

PADRAO: Final[dict[str, str]] = {
    CHAVE_REPERGUNTA: TEXTO_DA_REPERGUNTA,
    CHAVE_OFERTA_EQUIPE: TEXTO_OFERTA_EQUIPE,
    CHAVE_OFERTA_RECUSADA: TEXTO_OFERTA_RECUSADA,
    CHAVE_AVALIAR_CAUSA: TEXTO_AVALIAR_CAUSA,
    CHAVE_CASO_MANTIDO: TEXTO_CASO_MANTIDO,
}

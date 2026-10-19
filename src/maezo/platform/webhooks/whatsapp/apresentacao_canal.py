"""Mensagem de apresentacao do canal (`apresentacao-canal-v1`, decisao do dono em 14/10/2026).

Em DOIS momentos unicos o beneficiario ve UMA mensagem curta (no maximo 6 linhas) que diz o que o canal
resolve, para saber que assuntos pode tratar por aqui. Nenhum agente e' nomeado.

  1. A primeira vez que ele conclui a verificacao no canal.
  2. A volta depois de um silencio longo (`MAEZO_ACESSO_APRESENTACAO_DIAS`, 30 dias por padrao): a conversa
     volta a "apresentacao devida" e ela sai ao fim da PROXIMA verificacao (uma vez so', nunca duas).

NUNCA na reverificacao diaria. Quem decide e quando e' o `acesso.py` (coluna `apresentado_em`, migration
0026); aqui so' ficam os textos FIXOS (constantes versionadas, revisadas contra
`docs/guia-de-tom-dos-agentes.md`: frases de ate' 20 palavras, so' os canais permitidos, nenhuma promessa).

O texto vai em mensagem PROPRIA, depois de "Pronto, ja' confirmei que e' voce": o emoji do guia e' so' de
mensagem positiva, e esta fala de emergencia (192).

O item "dependentes" so' aparece quando a fonte de dependentes esta ligada (OpenAPI TINA de dependentes
configurado); sem ela o canal nao consegue consultar dependentes e o texto nao os promete.
"""

from __future__ import annotations

from typing import Final

VERSAO_APRESENTACAO_CANAL: Final[str] = "apresentacao-canal-v2"

#: Primeira mensagem do fecho da verificacao quando a apresentacao vai a seguir (positiva: leva o emoji).
TEXTO_VERIFICADO_APRESENTACAO: Final[str] = "Pronto, já confirmei que é você 😊"

_ABERTURA: Final[str] = "Por aqui você pode:\n• Consultar mensalidade, vencimento e coparticipação.\n"
_PLANO_COM_DEPENDENTES: Final[str] = "• Ver os dados do seu plano: carteirinha, carências e dependentes.\n"
_PLANO_SEM_DEPENDENTES: Final[str] = "• Ver os dados do seu plano: carteirinha e carências.\n"
#: FASE 4 (texto do dono, 18/10/2026): "tirar duvidas de saude" entra; sai o tom de "relatar sintoma e ser
#: encaminhado".
_FECHO: Final[str] = (
    "• Tirar dúvidas de saúde e saber onde buscar atendimento.\n"
    "• Falar com a nossa equipe sobre saúde ou cobrança, quando precisar.\n"
    "Em emergência, ligue 192. Como posso ajudar?"
)

TEXTO_APRESENTACAO_CANAL: Final[str] = _ABERTURA + _PLANO_COM_DEPENDENTES + _FECHO
TEXTO_APRESENTACAO_CANAL_SEM_DEPENDENTES: Final[str] = _ABERTURA + _PLANO_SEM_DEPENDENTES + _FECHO


def texto_apresentacao(*, com_dependentes: bool) -> str:
    """O texto fixo da apresentacao; "dependentes" so' com a fonte de dependentes disponivel."""
    return TEXTO_APRESENTACAO_CANAL if com_dependentes else TEXTO_APRESENTACAO_CANAL_SEM_DEPENDENTES


__all__ = [
    "TEXTO_APRESENTACAO_CANAL",
    "TEXTO_APRESENTACAO_CANAL_SEM_DEPENDENTES",
    "TEXTO_VERIFICADO_APRESENTACAO",
    "VERSAO_APRESENTACAO_CANAL",
    "texto_apresentacao",
]

""" "NAO ENTENDI" REEXPLICA A ULTIMA RESPOSTA (teste do dono de 25/10/2026).

O DEFEITO MEDIDO. Logo depois da resposta do plano, "Nao entendi" foi ao roteador (`plano`), ao classificador
(`information`) e a REDACAO do modelo. O historico curto guarda a resposta do plano so' como o marcador
`[resposta com dados do plano]` (DL-0080: dado do plano nao fica na memoria), entao o modelo nao tinha o que
reexplicar e escreveu uma frase sem sentido ("Sobre a situacao do plano para uso em consulta. Os detalhes
ficam...").

A REGRA. "Nao entendi", "como assim?", "nao compreendi" (SO' isso, sem pedido novo e sem pergunta pendente)
reexplicam a ULTIMA resposta da Helena em palavras simples (1 a 2 frases), sem mudar de assunto e SEM modelo:
- resposta do plano: a pergunta anterior da pessoa diz o assunto; a de "esta' apto?" e' lida de novo na fonte
  (a mesma consulta) e sai em palavras simples; as outras, o que aquela resposta mostrou, sem fato novo;
- aviso da cobertura e o do aplicativo: a versao simples fixa;
- outra resposta (saude, conversa): a primeira frase dela, sem os fechos padrao (nada novo, nenhum link: o
  historico guarda o texto ja' enviado, depois da trava `sem_link`);
- a propria reexplicacao de novo: desculpa e a oferta de uma pessoa (sem laco).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from maezo.agents.helena.consultas_plano import literais_dos_fatos
from maezo.agents.helena.habilidades.cobertura_fora import CHAVE_TEXTO as CHAVE_TEXTO_COBERTURA_FORA
from maezo.agents.helena.habilidades.cobertura_fora import TEXTO_COBERTURA_FORA
from maezo.agents.helena.habilidades.conversa import app as _app
from maezo.agents.helena.habilidades.conversa.leitura_curta import TEXTO_DESCULPA_SEM_ASSUNTO, normalizar
from maezo.agents.helena.habilidades.plano import apto as _apto
from maezo.agents.helena.historico import (
    MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO,
    MARCADOR_RESPOSTA_DE_COBERTURA,
)
from maezo.domain.conversa.competencia import hoje_em_brasilia

if TYPE_CHECKING:
    from maezo.agents.helena.graph import HelenaGraph

PREFIXO: Final[str] = "Em outras palavras: "
#: A reexplicacao tambem nao ficou clara: sem terceira tentativa, a oferta de uma pessoa (sem caso sozinha).
TEXTO_SEM_MAIS: Final[str] = (
    "Desculpe não ter conseguido explicar melhor. Se quiser falar com uma pessoa da equipe, é só me pedir."
)
TEXTO_COBERTURA_SIMPLES: Final[str] = (
    f"{PREFIXO}eu não consigo confirmar cobertura por aqui. Quem confirma é o aplicativo Austa Clínicas, o "
    "portal do plano ou a central de atendimento do plano."
)
TEXTO_APP_SIMPLES: Final[str] = (
    f"{PREFIXO}abra a loja de aplicativos do seu celular, procure por 'Austa Clínicas' e instale o "
    "aplicativo."
)
#: Resposta do plano sem releitura: o que aquela resposta mostrou, por assunto (nenhum fato do cadastro).
_DO_PLANO: Final[Mapping[str, str]] = {
    "carencia": (
        "as carências que constam no seu cadastro. Carência é o tempo de espera para poder usar alguns "
        "atendimentos do plano."
    ),
    "carteirinha": "os dados da sua carteirinha que constam no cadastro do plano.",
    "autorizacao": "como estão os seus pedidos de autorização, pelo que consta no cadastro do plano.",
    "dependentes": "quem está no seu plano como dependente, pelo que consta no cadastro.",
    "elegibilidade": (
        "os dados do seu plano que estão no cadastro, como o tipo do plano, desde quando ele vale e se está "
        "liberado para atendimento."
    ),
}
_FIM_DO_PLANO: Final[str] = " Se uma parte ficou confusa, me diga qual que eu explico."

#: O pedido de reexplicacao, e as UNICAS palavras que a mensagem pode ter alem dele (senao e' pedido novo:
#: "como assim nao tem cobertura?" segue o caminho de sempre).
_PEDIDO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao entendi|nao entendo|nao compreendi|nao compreendo|nao ficou claro|nao captei|como assim"
    r"|expli(?:ca|que|car) (?:melhor|de novo|direito|outra vez)|(?:pode|poderia) (?:me )?explicar"
    r"|o que (?:voce|vc) quis dizer|o que (?:isso|isto) (?:quer dizer|significa)|o que significa)\b"
)
_VOCABULARIO: Final[frozenset[str]] = frozenset(
    {"nao", "entendi", "entendo", "compreendi", "compreendo", "ficou", "claro", "captei", "como", "assim",
     "explica", "explique", "explicar", "melhor", "de", "novo", "direito", "outra", "vez", "pode", "poderia",
     "me", "o", "que", "voce", "vc", "quis", "dizer", "isso", "isto", "quer", "significa", "eu", "ainda",
     "mas", "nada", "muito", "bem", "ue", "uai", "hein", "oi", "hum", "ah", "desculpa", "desculpe", "por",
     "favor", "moca", "helena", "ta", "entao", "pra", "para", "mim", "a", "sua", "resposta", "mensagem",
     "essa", "ultima", "sei"}
)  # fmt: skip
_MAX_PALAVRAS: Final[int] = 12

#: Os fechos padrao das respostas (nao sao o conteudo a reexplicar).
_FECHOS: Final[re.Pattern[str]] = re.compile(
    r"(?:e so me pedir|posso ajudar em mais alguma coisa|nao substitui avaliacao medica|ficou alguma duvida"
    r"|se precisar e so chamar|em que posso ajudar)"
)
_PREFIXOS_DE_VARIACAO: Final[tuple[str, ...]] = ("Entendi. ", "Certo. ")
_FRASE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")
_MINUSCULA: Final[frozenset[str]] = frozenset(
    {"o", "a", "os", "as", "um", "uma", "para", "pelo", "pela", "no", "na", "nos", "nas", "se", "quando",
     "isso", "esse", "essa", "este", "esta", "e", "voce", "sim", "nao", "em", "de", "do", "da", "com", "por",
     "quem"}
)  # fmt: skip
_TETO_DO_RESUMO: Final[int] = 240


def pede_reexplicacao(texto: object) -> bool:
    """A mensagem e' SO' um pedido de reexplicacao ("nao entendi", "como assim?", "nao compreendi")?"""
    if not isinstance(texto, str):
        return False
    plano = normalizar(texto)
    palavras = plano.split()
    return (
        bool(palavras)
        and len(palavras) <= _MAX_PALAVRAS
        and bool(_PEDIDO.search(plano))
        and all(p in _VOCABULARIO for p in palavras)
    )


def e_reexplicacao(texto: object) -> bool:
    """O texto e' uma reexplicacao deste modulo (o `inform` o envia sem modelo)?"""
    return isinstance(texto, str) and (texto.startswith(PREFIXO) or texto == TEXTO_SEM_MAIS)


def ultima_troca(historico: object) -> tuple[str | None, str | None]:
    """`(a fala da pessoa ANTES da ultima resposta, a ultima resposta da Helena)` no historico curto."""
    if not isinstance(historico, Sequence):
        return None, None
    itens = [e for e in historico if isinstance(e, Mapping)]
    for i in range(len(itens) - 1, -1, -1):
        if itens[i].get("papel") == "helena" and str(itens[i].get("texto") or "").strip():
            anterior = next(
                (
                    str(itens[j].get("texto") or "")
                    for j in range(i - 1, -1, -1)
                    if itens[j].get("papel") == "beneficiario"
                ),
                None,
            )
            return anterior, str(itens[i].get("texto")).strip()
    return None, None


def _assunto_do_plano(pergunta: str | None) -> str:
    plano = normalizar(pergunta or "")
    if re.search(r"\bcarencias?\b", plano):
        return "carencia"
    if re.search(r"\bcarteir\w*|\bcartao\b", plano):
        return "carteirinha"
    if re.search(r"\bautoriza\w*|\bguias?\b|\bsenha\b", plano):
        return "autorizacao"
    if re.search(r"\bdependentes?\b", plano):
        return "dependentes"
    return "elegibilidade"


def _sem_prefixo(texto: str) -> str:
    for prefixo in _PREFIXOS_DE_VARIACAO:
        if texto.startswith(prefixo):
            return texto[len(prefixo) :]
    return texto


def resumo_simples(resposta: str) -> str | None:
    """A primeira frase de conteudo da resposta (e a segunda, se couber), sem os fechos padrao. PURA."""
    frases = [f.strip() for f in _FRASE.split(_sem_prefixo(resposta.strip())) if f.strip()]
    conteudo = [f for f in frases if not _FECHOS.search(normalizar(f))]
    if not conteudo:
        return None
    resumo = conteudo[0]
    if len(conteudo) > 1 and len(resumo) + 1 + len(conteudo[1]) <= _TETO_DO_RESUMO:
        resumo = f"{resumo} {conteudo[1]}"
    if len(resumo) > _TETO_DO_RESUMO:
        return None
    primeira = resumo.split(" ", 1)[0]
    if normalizar(primeira) in _MINUSCULA:
        resumo = resumo[:1].lower() + resumo[1:]
    return f"{PREFIXO}{resumo}"


async def reexplicar(self: HelenaGraph, state: Mapping[str, Any]) -> dict[str, Any] | None:
    """O update do turno com a reexplicacao da ULTIMA resposta, ou `None` (sem resposta anterior: o caminho de
    sempre)."""
    pergunta, ultima = ultima_troca(state.get("historico_conversa"))
    if ultima is None:
        return None
    ultima = _sem_prefixo(ultima)
    if e_reexplicacao(ultima) or (
        ultima == MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO and pede_reexplicacao(pergunta)
    ):
        # A reexplicacao (a do plano fica guardada como o marcador) tambem nao ficou clara.
        return {"response_text": TEXTO_SEM_MAIS, "tipo": "de_novo"}
    if ultima == MARCADOR_RESPOSTA_COM_DADOS_DO_PLANO:
        if _apto.pergunta_se_esta_apto(pergunta):
            ref = self._ref_verificada(state)
            view = await self._ler_consulta(ref, "elegibilidade") if ref is not None else None
            hoje = hoje_em_brasilia(self._relogio())
            miolo = _apto.resposta_simples_de_apto(view, hoje)
            if miolo is not None:
                return {
                    "response_text": f"{PREFIXO}{miolo}",
                    "tipo": "plano_apto",
                    "resposta_com_dados_do_plano": True,
                    "consulta_literais": literais_dos_fatos(_apto.fatos_de_apto(view, hoje)),
                }
        assunto = _assunto_do_plano(pergunta)
        return {
            "response_text": f"{PREFIXO}a mensagem anterior mostrou {_DO_PLANO[assunto]}{_FIM_DO_PLANO}",
            "tipo": "plano",
        }
    if ultima == MARCADOR_RESPOSTA_DE_COBERTURA or ultima.startswith(
        (TEXTO_COBERTURA_FORA[:60], self._textos[CHAVE_TEXTO_COBERTURA_FORA][:60])
    ):
        return {"response_text": TEXTO_COBERTURA_SIMPLES, "tipo": "cobertura"}
    if ultima in (_app.TEXTO_APP_ONDE_BAIXAR, self._textos[_app.CHAVE_APP_ONDE_BAIXAR]):
        return {"response_text": TEXTO_APP_SIMPLES, "tipo": "app"}
    resumo = resumo_simples(ultima)
    return {
        "response_text": resumo or TEXTO_DESCULPA_SEM_ASSUNTO,
        "tipo": "resumo" if resumo else "sem_assunto",
    }


__all__ = [
    "PREFIXO",
    "TEXTO_SEM_MAIS",
    "e_reexplicacao",
    "pede_reexplicacao",
    "reexplicar",
    "resumo_simples",
    "ultima_troca",
]

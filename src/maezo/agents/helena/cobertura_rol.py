"""COBERTURA DE PROCEDIMENTO pelo Rol da ANS x segmentacao do plano (fase 3.5; DECISAO D1 do dono, 18/10/2026:
"pode").

O QUE E'. Com a pergunta de cobertura de um procedimento ("o plano cobre vasectomia?", "a laqueadura consta no
Rol
da ANS?"), a Helena diz se o procedimento CONSTA no Rol da ANS vigente para a SEGMENTACAO do plano do proprio
beneficiario verificado, com os FATOS do contrato AMH TINA "cobertura" (manifest v1.9): `situacao`, `no_rol`,
`na_segmentacao`, `rol_edicao`, diretriz de utilizacao (DUT) e carencias. Sempre com o aviso do dono: "a
cobertura final depende de carencia, autorizacao e indicacao medica; confirme pela central ou pelo
aplicativo".

O QUE NAO E'. Nunca promete cobertura, nunca diz "nao cobre" (`fora_do_rol`/`indeterminado`/lista vazia = "nao
consegui confirmar pelo cadastro", como manda o contrato), nunca escolhe um item quando o termo e' generico
(`truncada` ou varios itens diferentes: pede o nome do pedido medico). Texto DETERMINISTICO, sem modelo.

So' com `MAEZO_HELENA_COBERTURA_ROL` e so' na allowlist da saude gerativa (quem decide e' o grafo).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Final

AVISO_DA_COBERTURA: Final[str] = (
    "A cobertura final depende de carência, autorização e indicação médica; confirme pela central de "
    "atendimento ou pelo aplicativo Austa Clínicas."
)
RESPOSTA_SEM_ITEM: Final[str] = (
    "Não encontrei esse procedimento com esse nome no cadastro de cobertura do seu plano. "
    "Se puder, me mande o nome como está no pedido médico. " + AVISO_DA_COBERTURA
)
RESPOSTA_GENERICA: Final[str] = (
    "Encontrei mais de um procedimento parecido no cadastro do seu plano. Me diga o nome exato, como está no "
    "pedido médico, que eu confiro. " + AVISO_DA_COBERTURA
)
_MAX_ITENS_NO_TEXTO: Final[int] = 3

#: O assunto do procedimento na mensagem, quando a base nao o reconhece: o que vem depois de
#: "cobre"/"cobertura
#: de"/"consta no Rol" (ou antes de "consta no Rol"). So' letras, numeros e espaco (o contrato aceita 3 a 80).
_DEPOIS_DE_COBRE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:cobre|cobrem|cobertura (?:de|da|do|para|pra)|coberto|coberta)\s+(?:a |o |as |os |uma |um )?"
    r"(?P<termo>[a-z0-9][a-z0-9 ]{2,60}?)\s*(?:\?|$|\bpelo\b|\bno meu\b|\bna minha\b|\bno plano\b)"
)
_ANTES_DO_ROL: Final[re.Pattern[str]] = re.compile(
    r"^(?:a |o |as |os )?(?P<termo>[a-z0-9][a-z0-9 ]{2,60}?)\s+"
    r"(?:consta|esta|entra|faz parte) (?:no|do) rol\b"
)
_PALAVRAS_VAZIAS: Final[frozenset[str]] = frozenset(
    {
        "meu",
        "minha",
        "plano",
        "o",
        "a",
        "de",
        "do",
        "da",
        "isso",
        "esse",
        "essa",
        "procedimento",
        "exame",
        "cirurgia",
    }
)


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9 ?]", " ", sem_acento).split())


def termo_da_mensagem(texto: str) -> str | None:
    """O termo de busca tirado da propria mensagem (sem a base), ou `None`."""
    plano = _normalizar(texto)
    for padrao in (_DEPOIS_DE_COBRE, _ANTES_DO_ROL):
        m = padrao.search(plano)
        if m is not None:
            termo = " ".join(p for p in m.group("termo").split() if p not in _PALAVRAS_VAZIAS)
            if 3 <= len(termo) <= 80:
                return termo
    return None


#: RODADA DO DONO (19/10/2026): CONTINUIDADE — depois de uma resposta de cobertura, a mensagem que e' so' o
#: nome
#: do procedimento ("Mamografia", "e vasectomia?", "o nome correto e' mamografia") continua sendo cobertura.
_ENCHIMENTO_DA_CONTINUACAO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao|sim|e|eh|ok|entao|isso|e (?:a|o|as|os)|e sobre|e quanto a|a minha duvida|minha duvida|duvida"
    r"|nao (?:e|eh) sobre (?:a |o )?(?:mensalidade|boleto|cobranca|pagamento)s?|sobre (?:a |o )?"
    r"|o nome (?:correto |certo )?(?:e|eh)|o nome|chama|se chama|quero saber|queria saber|do|da|de|o|a|os|as"
    r"|exame|procedimento|cirurgia|por favor|obrigad[oa]|cobre|cobertura|consta no rol|meu plano|plano)\b"
)
_MAX_PALAVRAS_DA_CONTINUACAO: Final[int] = 16


def termo_da_continuacao(texto: str) -> str | None:
    """O termo de busca de uma mensagem que CONTINUA a cobertura (so' o procedimento, sem as palavras de
    conversa), ou `None` quando nao sobra termo valido."""
    plano = _normalizar(texto)
    if not plano or len(plano.split()) > _MAX_PALAVRAS_DA_CONTINUACAO:
        return None
    resto = " ".join(_ENCHIMENTO_DA_CONTINUACAO.sub(" ", plano.replace("?", " ")).split())
    return termo_valido(resto) if resto else None


def termo_valido(termo: str) -> str | None:
    """O termo no formato do contrato (letras, numeros e espaco; 3 a 80), ou `None`."""
    plano = " ".join(re.sub(r"[^a-z0-9 ]", " ", _normalizar(termo)).split())
    return plano if 3 <= len(plano) <= 80 else None


def _br(iso: object) -> str | None:
    if not isinstance(iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", iso):
        return None
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def _rol(edicao: object) -> str:
    texto = " ".join(str(edicao or "").split())
    m = re.search(r"RN\s*N?º?\s*(\d+)\s*/\s*(\d{4})", texto, re.IGNORECASE)
    return f"Rol da ANS (RN nº {m.group(1)}/{m.group(2)})" if m else "Rol da ANS"


def _frase_do_item(item: Any, segmentacao: str | None) -> str:
    descricao = " ".join(str(getattr(item, "descricao", "") or "esse procedimento").split())
    situacao = getattr(item, "situacao", "")
    seg = f" ({segmentacao})" if segmentacao else ""
    if situacao == "coberto_rol":
        rol = _rol(getattr(item, "rol_edicao", None))
        frase = f"{descricao}: consta no {rol} para a segmentação do seu plano{seg}."
    elif situacao == "coberto_extra":
        frase = f"{descricao}: consta no cadastro do seu plano como cobertura além do Rol da ANS."
    elif situacao == "fora_da_segmentacao":
        frase = f"{descricao}: está no Rol da ANS, mas não na segmentação do seu plano{seg}."
    else:
        frase = f"{descricao}: não consegui confirmar pelo cadastro do seu plano."
    if getattr(item, "tem_diretriz", False) and situacao in ("coberto_rol", "fora_da_segmentacao"):
        frase += " Ele tem diretriz de utilização da ANS (critérios para a cobertura)."
    pendentes = [c for c in getattr(item, "carencias", ()) or () if getattr(c, "cumprida", None) is False]
    if pendentes:
        fim = _br(getattr(pendentes[0], "termina_em", None))
        frase += f" Há carência em andamento{' até ' + fim if fim else ''}."
    return frase


def texto_da_cobertura(view: Any) -> str:
    """A resposta deterministica a partir da `CoberturaView` (fatos do contrato TINA cobertura)."""
    itens: Sequence[Any] = tuple(getattr(view, "itens", ()) or ())
    if not itens:
        return RESPOSTA_SEM_ITEM
    distintos = {(getattr(i, "descricao", None), getattr(i, "situacao", None)) for i in itens}
    if getattr(view, "truncada", False) is True or len(distintos) > _MAX_ITENS_NO_TEXTO:
        return RESPOSTA_GENERICA
    segmentacao = getattr(view, "segmentacao", None)
    vistos: dict[tuple[Any, Any], Any] = {}
    for i in itens:
        vistos.setdefault((getattr(i, "descricao", None), getattr(i, "situacao", None)), i)
    frases = [_frase_do_item(i, segmentacao) for i in vistos.values()]
    return "Pelo cadastro do seu plano: " + " ".join(frases) + " " + AVISO_DA_COBERTURA


#: RODADA DO DONO (19/10/2026): PARA QUEM e' o procedimento, quando o plano tem dependentes. O contrato
#: publicado
#: de dependentes (tina-dependentes v1.5) e' ANONIMO (sem nome): cada dependente aparece pelo grau
#: ("dependente
#: 1 (filho ou filha)"). O nome completo depende de mudanca de contrato da AMH (proposta no DL-0126).
PENDENTE_COBERTURA_PARA_QUEM: Final[str] = "BS_COBERTURA_PARA_QUEM"
PREFIXO_COBERTURA_QUEM: Final[str] = "BS_COBERTURA_QUEM:"
QUEM_TITULAR: Final[str] = "titular"
_RESPOSTA_TITULAR: Final[re.Pattern[str]] = re.compile(
    r"^(?:1|um|opcao 1|para mim|pra mim|eu|e para mim|e pra mim|mim|titular|o titular|a titular"
    r"|para o titular"
    r"|pra eu|para eu|e para eu|para voce saber|meu|minha|e meu|e minha)$"
)
_RESPOSTA_DEPENDENTE: Final[re.Pattern[str]] = re.compile(
    r"^(?:opcao )?(?P<n>[2-9])$|\b(?:dependente|filh[oa]|esposa|marido|conjuge|companheir[oa]|net[oa]|enteado"
    r"|enteada)\b"
)


#: O inicio da pergunta "para quem e'?" (o grafo reconhece a pergunta no historico).
INICIO_DA_PERGUNTA_PARA_QUEM: Final[str] = "Antes de eu consultar: é para quem?"


def pergunta_para_quem(graus: list[str]) -> str:
    """A pergunta "para quem e'?" com cada dependente pelo grau (o contrato nao traz nome)."""
    opcoes = ["1) para você (titular)"]
    for i, grau in enumerate(graus, start=1):
        opcoes.append(f"{i + 1}) para o dependente {i} ({grau})")
    return f"{INICIO_DA_PERGUNTA_PARA_QUEM} Responda " + "; ".join(opcoes) + "."


def quem_da_resposta(texto: str, quantos: int) -> str | None:
    """`"titular"`, `"dependente:N"` ou `None` (a resposta nao e' a escolha)."""
    plano = " ".join(_normalizar(texto).replace("?", " ").split())
    if _RESPOSTA_TITULAR.match(plano):
        return QUEM_TITULAR
    m = _RESPOSTA_DEPENDENTE.search(plano)
    if m is None:
        return None
    n = int(m.group("n")) - 1 if m.group("n") else 1
    return f"dependente:{n}" if 1 <= n <= max(quantos, 1) else None


AVISO_DO_DEPENDENTE: Final[str] = (
    "Consultei pelo contrato do plano, que tem a mesma segmentação para o titular e os dependentes. "
    "A carência do dependente pode ser diferente: confirme pela central de atendimento ou pelo aplicativo."
)


def literais_da_cobertura(view: Any) -> tuple[str, ...]:
    """Os literais que o texto pode citar (para a cerca TEXTO x FATO): descricoes, edicao do Rol, datas."""
    saida: list[str] = []
    seg = getattr(view, "segmentacao", None)
    if seg:
        saida.append(str(seg))
    for i in getattr(view, "itens", ()) or ():
        for valor in (getattr(i, "descricao", None), getattr(i, "rol_edicao", None)):
            if valor:
                saida.append(str(valor))
        for c in getattr(i, "carencias", ()) or ():
            fim = _br(getattr(c, "termina_em", None))
            if fim:
                saida.append(fim)
    return tuple(dict.fromkeys(saida))


__all__ = [
    "AVISO_DO_DEPENDENTE",
    "INICIO_DA_PERGUNTA_PARA_QUEM",
    "PENDENTE_COBERTURA_PARA_QUEM",
    "PREFIXO_COBERTURA_QUEM",
    "QUEM_TITULAR",
    "pergunta_para_quem",
    "quem_da_resposta",
    "AVISO_DA_COBERTURA",
    "RESPOSTA_GENERICA",
    "RESPOSTA_SEM_ITEM",
    "literais_da_cobertura",
    "termo_da_continuacao",
    "termo_da_mensagem",
    "termo_valido",
    "texto_da_cobertura",
]

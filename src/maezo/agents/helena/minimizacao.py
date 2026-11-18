"""A MASCARA do texto que sai pela rota global da Helena (DL-0192): `minimizar_prompt`.

O texto da rota global (`runtime/inference/experimento_global.py`) pode ser processado fora do Brasil. Antes
dela, os dois chamadores (`modelo_allowlist.ProvedorHaikuGlobal` e a leitura da porta unica,
`habilidades/saude_original/fluxo.py`) passam o texto por esta mascara. E' DETERMINISTICA (so' expressoes
regulares e regras de contagem, nada de modelo) e CONSERVADORA (na duvida entre um dado de saude e um
identificador, fica o dado de saude). NAO e' um anonimizador: o que nao casa com uma das regras abaixo sai
como esta'; o docstring de `minimizar_prompt` lista o que fica de fora.

Tempo LINEAR no tamanho do texto: toda expressao tem um ponto de partida ancorado (lookbehind) ou e' uma
corrida maxima de uma classe so', e a decisao por token e' uma passada (`_e_identificador_curto`). Prova:
`tests/unit/agents/test_helena_minimizar_prompt.py` (entradas adversariais de 100 mil caracteres).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Final

DADO_REMOVIDO: Final[str] = "[dado removido]"

# --- Numeros ----
#: 8+ digitos com no maximo um separador (espaco, ponto, hifen, barra) entre eles: CPF, telefone, carteirinha,
#: protocolo ANS, CEP, data. O segundo lookbehind impede recomecar no meio de uma corrida (tempo linear).
_NUMERO_LONGO: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(?<!\d[\s./-])\d(?:[\s./-]?\d){7,}(?!\d)")
#: Unidades clinicas: um numero de 5 a 7 digitos seguido de uma delas e' medida, nao identificador.
_UNIDADES: Final[str] = (
    r"ui|u|unidades?|mg|mcg|ml|kg|g|mm|cm|mm3|mmhg|bpm|mg/dl|ng|pg|c[eé]lulas?|passos|c[oó]pias|"
    r"plaquetas|leuc[oó]citos|calorias|kcal"
)
#: Numero solto de 5 a 7 digitos (carteirinha parcial, protocolo, matricula), fora de decimal, data, faixa,
#: numero colado a letra (o token decide) e medida com unidade.
_NUMERO_CURTO: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w/-])(?<![0-9][.,])[0-9]{5,7}(?![\w/-]|[.,][0-9]|\s?(?i:" + _UNIDADES + r")\b)"
)

# --- E-mail ----
#: Qualquer `algo@algo` (com ou sem dominio de topo). O comeco e' ancorado (inicio, espaco, abre-parentese
#: ou aspas) e o local nao contem esses caracteres: cada caractere e' lido no maximo duas vezes. A pontuacao
#: final (`.`, `?`, `)`...) fica fora.
_EMAIL: Final[re.Pattern[str]] = re.compile(r"(?<![^\s(<\[{\"'])[^\s@(<\[{\"']+@[^\s@]*[^\s@.,;:!?)\]}>\"']")

# --- Identificador curto ----
#: Um token e' a corrida MAXIMA de letras (qualquer escrita), digitos, `_` e `-`; o ponto separa (`J45.9`).
_TOKEN: Final[re.Pattern[str]] = re.compile(r"(?<![\w-])[\w-]+")
#: Formatos de identificador DESTE repositorio, mascarados com qualquer quantidade de digitos: protocolo
#: `REE-1A2B-3C4D`/`CAN-...` e o pseudonimo keyed `hk1_<hex>`.
_FORMATO_DE_PROTOCOLO: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]{2,5}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}")
_PREFIXO_PSEUDONIMO: Final[str] = "hk1_"
#: Medida `NxN` (pressao `140x90`, `2x500mg`, `140x90mmHg`): nunca identificador.
_MEDIDA_NXN: Final[re.Pattern[str]] = re.compile(r"[0-9]+[xX][0-9]+[A-Za-z/]*")
#: Referencia de documento do repo nas INSTRUCOES dos prompts (`ADR-0005`, `DL-0101`): fica.
_REFERENCIA_DO_REPO: Final[re.Pattern[str]] = re.compile(r"(?:ADR|DL|SP-OP|AGJ|HEL)-[0-9]")
#: Identificador generico: 5+ digitos no token (marcador e remedio com dose tem ate' 4: `CA125`, `CA19-9`,
#: `AAS100`, `XR500`).
_MINIMO_DE_DIGITOS_DO_ID: Final[int] = 5
#: Token maior que isto nao e' palavra: mascarado se tiver 5+ digitos, sem as outras regras.
_MAXIMO_DO_TOKEN: Final[int] = 128

# --- Nomes ----
_MAIUSCULA: Final[str] = "A-ZÀ-ÖØ-Þ"
#: Uma palavra de nome: letras, com apostrofo ou hifen DENTRO (`D'Ávila`, `O'Neil`, `Ana-Clara`).
_PALAVRA: Final[str] = r"[^\W\d_]+(?:['’-][^\W\d_]+)*"
_PALAVRA_MAIUSCULA: Final[str] = r"[" + _MAIUSCULA + r"][^\W\d_]*(?:['’-][^\W\d_]+)*"
#: As palavras seguintes do nome: na MESMA linha, com inicial maiuscula, com particula entre elas.
_CONTINUACAO: Final[str] = r"(?:[ \t]+(?:(?:de|da|do|das|dos|e)[ \t]+)?" + _PALAVRA_MAIUSCULA + r")*"
#: Tratamento antes do nome: fica no texto; o nome depois dele e' mascarado.
_TRATAMENTO: Final[str] = r"(?:(?i:dr|dra|sr|sra|srta)\.?[ \t]+)"
#: Parentesco depois de "o nome do/da ...": so' pessoa, para "o nome do remedio e' X" ficar.
_PARENTESCO: Final[str] = (
    r"filh[oa]|pai|m[aã]e|marido|espos[oa]|irm[aã]o?|av[oóô]|net[oa]|beb[eê]|dependente|titular|paciente|"
    r"namorad[oa]|companheir[oa]|sobrinh[oa]|ti[oa]|entead[oa]|crian[cç]a|menin[oa]"
)
#: Deixa FORTE: o que vem depois e' nome com alta probabilidade; a primeira palavra e' mascarada em qualquer
#: caixa (no WhatsApp "meu nome e' ana" e' comum).
_APRESENTACAO_FORTE: Final[re.Pattern[str]] = re.compile(
    r"(?P<deixa>(?i:\bmeu\s+nome(?:\s+completo)?|\bo\s+nome\s+(?:dele|dela|d[oa]\s+(?:meu\s+|minha\s+)?"
    r"(?:" + _PARENTESCO + r")))(?i:[ \t]+(?:é|e|eh)\b[ \t]*:?|[ \t]*:)\s*" + _TRATAMENTO + r"?"
    r"|(?i:\bme\s+chamo\b)\s*:?\s*" + _TRATAMENTO + r"?)"
    r"(?P<nome>" + _PALAVRA + _CONTINUACAO + r")"
)
#: Deixa FRACA ("sou o/a X", "aqui e' o/a X"): so' com a primeira palavra em MAIUSCULA ("sou a mae dela"
#: fica).
_APRESENTACAO_FRACA: Final[re.Pattern[str]] = re.compile(
    r"(?P<deixa>(?i:\b(?:eu\s+)?sou\s+[oa]|\baqui\s+(?:é|e|eh)\s+[oa])[ \t]+" + _TRATAMENTO + r"?)"
    r"(?P<nome>" + _PALAVRA_MAIUSCULA + _CONTINUACAO + r")"
)
#: "Dr. Fulano", "Dra. Fulana": o tratamento com maiuscula e o nome com maiuscula, em qualquer lugar.
_TRATAMENTO_E_NOME: Final[re.Pattern[str]] = re.compile(
    r"(?P<deixa>\b(?:Dr|Dra|Sr|Sra|Srta|DR|DRA)\.?[ \t]+)(?P<nome>" + _PALAVRA_MAIUSCULA + _CONTINUACAO + r")"
)
_PARTICULAS: Final[frozenset[str]] = frozenset({"de", "da", "do", "das", "dos", "e"})
_MINIMO_DE_LETRAS_DO_NOME: Final[int] = 3
#: Sobrenomes que sao tambem palavras comuns: so' mascarados com inicial MAIUSCULA no texto ("ha' 3 dias",
#: "tomei leite" ficam).
SOBRENOMES_COMUNS: Final[frozenset[str]] = frozenset(
    {
        "dias", "leite", "rocha", "costa", "campos", "flores", "ramos", "rosa", "pereira", "lima", "cruz",
        "matos", "pinto", "branco", "prado", "porto", "sales", "vale", "mota", "neves", "reis", "paz", "luz",
        "bastos", "freitas", "vieira", "moura",
    }
)  # fmt: skip
#: A vogal (ou o c) sem acento -> a classe com as variantes acentuadas.
_VARIANTES: Final[Mapping[str, str]] = {
    "a": "aáàâãä",
    "e": "eéèêë",
    "i": "iíìîï",
    "o": "oóòôõö",
    "u": "uúùûü",
    "c": "cç",
}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c)).lower()


def _classe(letra: str, *, maiuscula: bool = False) -> str:
    variantes = _VARIANTES.get(letra)
    if variantes is None:
        return re.escape(letra.upper() if maiuscula else letra)
    return f"[{variantes.upper() if maiuscula else variantes}]"


def _padrao_de_nomes(nomes: Iterable[str]) -> re.Pattern[str] | None:
    """Cada palavra (3+ letras, fora particulas) dos nomes conhecidos, palavra inteira e sem acento; sem
    caixa, exceto `SOBRENOMES_COMUNS` (so' com inicial maiuscula). `None` sem palavra nenhuma."""
    partes: set[str] = set()
    for nome in nomes:
        for palavra in re.findall(r"[^\W\d_]+", _sem_acento(str(nome or ""))):
            if len(palavra) >= _MINIMO_DE_LETRAS_DO_NOME and palavra not in _PARTICULAS:
                partes.add(palavra)
    if not partes:
        return None
    alternativas = []
    for palavra in sorted(partes, key=len, reverse=True):
        if palavra in SOBRENOMES_COMUNS:
            resto = "".join(_classe(c) for c in palavra[1:])
            alternativas.append(_classe(palavra[0], maiuscula=True) + f"(?i:{resto})")
        else:
            alternativas.append("(?i:" + "".join(_classe(c) for c in palavra) + ")")
    return re.compile(r"(?<!\w)(?:" + "|".join(alternativas) + r")(?!\w)")


def _e_identificador_curto(token: str) -> bool:
    """Uma passada: formato do repo, ou 5+ digitos com uma letra antes de um digito, fora medida `NxN` e
    referencia de documento do repo."""
    digitos = sum(c.isdigit() for c in token)
    if len(token) > _MAXIMO_DO_TOKEN:
        return digitos >= _MINIMO_DE_DIGITOS_DO_ID
    nucleo = token.strip("_-")
    if _FORMATO_DE_PROTOCOLO.fullmatch(nucleo) or nucleo.lower().startswith(_PREFIXO_PSEUDONIMO):
        return True
    if digitos < _MINIMO_DE_DIGITOS_DO_ID:
        return False
    primeira_letra = next((i for i, c in enumerate(token) if c.isalpha()), None)
    ultimo_digito = max(i for i, c in enumerate(token) if c.isdigit())
    return (
        primeira_letra is not None
        and primeira_letra < ultimo_digito
        and _MEDIDA_NXN.fullmatch(nucleo) is None
        and _REFERENCIA_DO_REPO.match(nucleo) is None
    )


def _mascarar_nome(m: re.Match[str]) -> str:
    return m.group("deixa") + DADO_REMOVIDO


def minimizar_prompt(prompt: str, *, nomes: Iterable[str] = ()) -> str:
    """O texto que sai pela rota global, com dados de identificacao trocados por `DADO_REMOVIDO`.

    MASCARA, NESTA ORDEM:
    (1) e-mail: qualquer `algo@algo`, com ou sem dominio de topo;
    (2) cada palavra dos `nomes` conhecidos (os nomes dos dependentes que o grafo ja' leu,
        `modelo_allowlist.nomes_conhecidos`): palavra inteira de 3+ letras, sem acento, fora particulas; sem
        caixa, exceto os `SOBRENOMES_COMUNS` (Dias, Leite, Rocha...), so' com inicial maiuscula no texto;
    (3) o nome depois de uma apresentacao FORTE: "meu nome (completo) e'"/":", "me chamo", "o nome dele/dela
        e'", "o nome do meu filho/da minha mae... e'" (so' parentesco). A primeira palavra em qualquer caixa
        (pode estar na linha seguinte), as seguintes na mesma linha com inicial maiuscula; palavras com
        apostrofo ou hifen inteiras (`D'Ávila`, `Ana-Clara`); um tratamento (Dr./Sra.) fica e o nome depois
        dele sai;
    (4) o nome depois de uma apresentacao FRACA ("sou o/a", "aqui e' o/a"), so' com inicial maiuscula;
    (5) "Dr./Dra./Sr./Sra. Nome" em qualquer lugar (tratamento e nome com maiuscula);
    (6) identificador curto, por token (corrida de letras, digitos, `_` e `-`): os formatos do repo
        (`XXX-1A2B-3C4D`, `hk1_...`) com qualquer quantidade de digitos; senao 5+ digitos e uma letra antes de
        um digito (`AB12345`, `ÁB12345`, `_AB12345`, `nº12345`), fora medida `NxN` (`140x90`, `2x500mg`) e
        referencia de documento do repo (`ADR-0005`); token de mais de 128 caracteres, se tiver 5+ digitos;
    (7) numero de 8+ digitos com no maximo um separador entre eles (CPF, telefone, carteirinha, protocolo ANS,
        CEP, data);
    (8) numero solto de 5 a 7 digitos que nao e' decimal, data, faixa nem medida com unidade (`UI`,
        `unidades`, `mg`, `mmHg`, `bpm`, `celulas`, `passos`...).

    NAO COBRE (sai como esta'): nome sem apresentacao e fora dos `nomes` ("fui com a Maria"); nome em
    minusculas depois de deixa fraca ou de tratamento ("dr. joao"); sobrenome em minusculas depois do primeiro
    nome ("me chamo ana souza" mascara so' "ana"); nome de 1 ou 2 letras na lista de `nomes`; o nome do
    proprio beneficiario quando ele nao se apresenta (a Helena nao o tem); endereco, cidade, nome de hospital;
    idade, sintoma, dose; numero de ate' 4 digitos; codigo com ate' 4 digitos fora dos formatos do repo
    (`AB1234`, `CA125`); e-mail por extenso ("arroba"). EXCESSO CONHECIDO (dado de saude que pode sair
    mascarado): depois de deixa forte, palavras com maiuscula na mesma linha ("me chamo Ana Febre Alta"
    mascara as tres); depois de deixa fraca, palavra com maiuscula que nao e' nome ("sou a Mae dela"); palavra
    comum com maiuscula que e' sobrenome conhecido ("Dias atras" com um dependente Dias); numero de 5 a 7
    digitos sem unidade."""
    texto = _EMAIL.sub(DADO_REMOVIDO, prompt) if "@" in prompt else prompt
    padrao = _padrao_de_nomes(nomes)
    if padrao is not None:
        texto = padrao.sub(DADO_REMOVIDO, texto)
    texto = _APRESENTACAO_FORTE.sub(_mascarar_nome, texto)
    texto = _APRESENTACAO_FRACA.sub(_mascarar_nome, texto)
    texto = _TRATAMENTO_E_NOME.sub(_mascarar_nome, texto)
    # O token antes do numero longo: `FICT-0000-0000` sai inteiro, nao como "FICT-" mais a mascara.
    texto = _TOKEN.sub(lambda m: DADO_REMOVIDO if _e_identificador_curto(m.group(0)) else m.group(0), texto)
    texto = _NUMERO_LONGO.sub(DADO_REMOVIDO, texto)
    return _NUMERO_CURTO.sub(DADO_REMOVIDO, texto)


__all__ = ["DADO_REMOVIDO", "SOBRENOMES_COMUNS", "minimizar_prompt"]

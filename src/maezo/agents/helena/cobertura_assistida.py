"""COBERTURA ASSISTIDA (rodada do dono de 19/10/2026, "FACA e RESOLVA"; DL-0128) — so' na allowlist da saude
gerativa.

O QUE MUDA. A pergunta de cobertura deixa de depender de a pessoa escrever o nome do procedimento DEPOIS de
"cobre" e com o nome do cadastro:

  1. EXTRACAO pelo GLM-5 (zona PHI, sa-east-1; nunca o Haiku): os procedimentos pedidos (em qualquer ordem,
     um ou varios), se e' remedio/material, a urgencia, para quem e', se e' outro assunto (andamento de
     autorizacao, rede, coparticipacao, reembolso), exclusao legal, discordancia. So' extrai; quem diz se
     cobre e' o cadastro. Sem o modelo, uma extracao deterministica de reserva.
  2. TRADUCAO para o nome oficial (`conhecimento/catalogo_procedimentos.py`) e consulta da cobertura pelo
     CODIGO; varios candidatos = ate' 3 OPCOES numeradas (nunca "me diga o nome exato").
  3. LIMITE DE 2 TENTATIVAS (decisao do dono): 1a falha -> opcoes ou o nome como esta' no pedido medico; 2a
     falha -> "nao consegui localizar por aqui; quer que eu passe para a nossa equipe?" — o caso SO' abre com
     "sim", com o resumo (pedido, para quem, termos tentados; sem CPF). Falha da AMH nunca vira "nao
     encontrei".
  4. Textos do dono (dev; JURIDICO antes de producao) para fora da segmentacao, fora do Rol/indeterminado,
     carencia pendente e DUT. Nunca termina em "nao cobre".

Este modulo e' PURO (sem rede): textos, extracao (parse do JSON do modelo e a reserva deterministica), o
contexto da conversa (codificado em `coleta_contexto`) e a leitura das respostas da pessoa. Quem chama a AMH,
o catalogo e o modelo e' o grafo.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

# --- Pendencias (`coleta_pendente`) ----------------------------------------------------------------------
PREFIXO: Final[str] = "COB2:"
PEND_QUEM: Final[str] = "COB2:QUEM"
PEND_OPCOES: Final[str] = "COB2:OPCOES"
PEND_FORMA: Final[str] = "COB2:FORMA"
PEND_EQUIPE: Final[str] = "COB2:EQUIPE"
PEND_FALHA: Final[str] = "COB2:FALHA"
PENDENCIAS: Final[frozenset[str]] = frozenset({PEND_QUEM, PEND_OPCOES, PEND_FORMA, PEND_EQUIPE, PEND_FALHA})
MAX_TENTATIVAS: Final[int] = 2
MAX_PROCEDIMENTOS: Final[int] = 4
_MAX_CONTEXTO: Final[int] = 3500

ASSUNTOS: Final[frozenset[str]] = frozenset(
    {"cobertura", "autorizacao", "rede", "coparticipacao", "reembolso", "outro"}
)
TIPOS: Final[frozenset[str]] = frozenset({"procedimento", "remedio", "material", "nenhum"})
PARA_QUEM: Final[frozenset[str]] = frozenset({"titular", "dependente", "terceiro", "nao_disse"})
EXCLUSOES: Final[frozenset[str]] = frozenset({"estetica", "inseminacao", "experimental", "nenhuma"})


def normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento).split())


# --- Extracao
# ------------------------------------------------------------------------------------------------
PROMPT_EXTRACAO: Final[str] = """\
Voce extrai dados de UMA mensagem de beneficiario de plano de saude sobre cobertura. NAO responda a pessoa e
NAO diga se cobre. Devolva SO' um JSON, sem texto fora dele, com estas chaves:
- "procedimentos": lista (0 a 4) com o NOME de cada exame, procedimento, cirurgia, consulta ou terapia pedido,
  do jeito que a pessoa escreveu, so' o nome do procedimento: sem "meu medico pediu", sem "o plano cobre", sem
  nome de pessoa, sem o que a pessoa NEGA ou usa de contexto ("nao e' sobre a mensalidade"). Ex.: para
  "o medico pediu ultrassom da tireoide e um hemograma, cobre?" -> ["ultrassom da tireoide", "hemograma"].
- "tipo": "procedimento" | "remedio" (medicamento, injecao, insulina, quimioterapia oral) | "material"
(protese,
  orteses, stent avulso, material cirurgico) | "nenhum".
- "assunto": "cobertura" | "autorizacao" (andamento ou status de uma autorizacao/guia ja' pedida) | "rede"
  (onde fazer, qual hospital/laboratorio/medico atende) | "coparticipacao" (quanto vai pagar de
  coparticipacao)
  | "reembolso" | "outro".
- "urgencia": true se a pessoa diz que e' urgente, emergencia, que esta passando mal agora; senao false.
- "para_quem": "titular" (so' se a mensagem DIZ que e' para a propria pessoa: "para mim", "eu preciso fazer")
  | "dependente" (filho, filha, conjuge, esposa, marido,
  enteado, neto, bebe ou alguem que a pessoa diz que esta no plano) | "terceiro" (mae, pai, irmao, amigo ou
  alguem que nao parece estar no plano) | "nao_disse".
- "quem_texto": a palavra de parentesco ou o primeiro nome que a pessoa usou para quem e' (ex.: "filho",
  "Ana"), ate' 30 letras, ou "".
- "recem_nascido": true se fala de bebe recem-nascido ainda nao incluido no plano; senao false.
- "exclusao_legal": SO' quando a PROPRIA mensagem diz: "estetica" (a pessoa diz que e' so' por estetica ou
  rejuvenescimento) | "inseminacao" (inseminacao artificial, fertilizacao in vitro) | "experimental" (a pessoa
  diz que o tratamento e' experimental). Cirurgias, esterilizacao, planejamento familiar, exames e terapias
  comuns: "nenhuma". Na duvida, "nenhuma".
- "discorda": true se a pessoa diz que o medico, a clinica ou alguem afirmou que o plano cobre ou que ja' foi
  negado e ela discorda; senao false.
"""


@dataclass(frozen=True)
class Pedido:
    procedimentos: tuple[str, ...] = ()
    tipo: str = "procedimento"
    assunto: str = "cobertura"
    urgencia: bool = False
    para_quem: str = "nao_disse"
    quem_texto: str = ""
    recem_nascido: bool = False
    exclusao_legal: str = "nenhuma"
    discorda: bool = False
    pelo_modelo: bool = False


#: Palavras de CONTEXTO que nunca sao parte do nome de um procedimento (cobranca, conversa, pedido,
#: cobertura).
#: Regra geral: o modelo as vezes cola a parte negada ou o contexto no termo ("mensalidade se mamografia").
_CONTEXTO_DO_TERMO: Final[frozenset[str]] = frozenset(
    {
        "mensalidade", "mensalidades", "boleto", "boletos", "cobranca", "pagamento", "fatura", "financeiro",
        "duvida", "pergunta", "sobre", "se", "nao", "sim", "e", "eh", "minha", "meu", "plano", "convenio",
        "cobre", "cobrem", "cobertura", "coberto", "coberta", "rol", "ans", "pedido", "pediu", "solicitou",
        "medico", "medica", "doutor", "doutora", "quero", "saber", "queria", "preciso", "fazer",
    }
)  # fmt: skip
_CONECTORES: Final[frozenset[str]] = frozenset(
    {"de", "da", "do", "das", "dos", "e", "a", "o", "para", "com", "em"}
)


def _termo_limpo(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    termo = " ".join(re.sub(r"[^\w\s/+-]", " ", valor).split())
    palavras = termo.split()
    # Tira as palavras de contexto e os conectores que sobram nas pontas.
    palavras = [p for p in palavras if normalizar(p) not in _CONTEXTO_DO_TERMO]
    while palavras and normalizar(palavras[0]) in _CONECTORES:
        palavras.pop(0)
    while palavras and normalizar(palavras[-1]) in _CONECTORES:
        palavras.pop()
    termo = " ".join(palavras)
    plano = normalizar(termo)
    if not 3 <= len(plano) <= 80:
        return None
    return termo[:80]


def pedido_do_modelo(bruto: object) -> Pedido | None:
    """O `Pedido` a partir da resposta do modelo (JSON, eventualmente cercado de texto), ou `None`."""
    if not isinstance(bruto, str):
        return None
    m = re.search(r"\{.*\}", bruto, re.DOTALL)
    if m is None:
        return None
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, Mapping):
        return None
    procedimentos: list[str] = []
    for p in dados.get("procedimentos") or []:
        termo = _termo_limpo(p)
        if termo is not None and normalizar(termo) not in {normalizar(x) for x in procedimentos}:
            procedimentos.append(termo)
    tipo = str(dados.get("tipo") or "procedimento")
    assunto = str(dados.get("assunto") or "cobertura")
    para_quem = str(dados.get("para_quem") or "nao_disse")
    exclusao = str(dados.get("exclusao_legal") or "nenhuma")
    quem = dados.get("quem_texto")
    return Pedido(
        procedimentos=tuple(procedimentos[:MAX_PROCEDIMENTOS]),
        tipo=tipo if tipo in TIPOS else "procedimento",
        assunto=assunto if assunto in ASSUNTOS else "cobertura",
        urgencia=dados.get("urgencia") is True,
        para_quem=para_quem if para_quem in PARA_QUEM else "nao_disse",
        quem_texto=" ".join(str(quem).split())[:30] if isinstance(quem, str) else "",
        recem_nascido=dados.get("recem_nascido") is True,
        exclusao_legal=exclusao if exclusao in EXCLUSOES else "nenhuma",
        discorda=dados.get("discorda") is True,
        pelo_modelo=True,
    )


_PARENTESCO_NO_PLANO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:filh[oa]s?|esposa|marido|conjuge|companheir[oa]|entead[oa]|net[oa]|bebe|meu menino|minha menina)\b"
)
_TERCEIRO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:minha mae|meu pai|minha avo|meu avo|meu irmao|minha irma|minha tia|meu tio|minha sogra|meu sogro"
    r"|um amigo|uma amiga|meu amigo|minha amiga|minha vizinha|meu vizinho)\b"
)
_URGENCIA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:urgente|urgencia|emergencia|passando mal|agora mesmo)\b"
)
_DISCORDA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(?:o |a )?(?:medic[oa]|clinica|hospital|laboratorio) (?:disse|falou|garantiu) que (?:o plano "
    r")?cobre"
    r"|foi negad[oa]|negaram|nao concordo)\b"
)
_REMEDIO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:remedio|medicamento|insulina|injecao|comprimido|quimioterapia oral|quimio oral|vacina importada)\b"
)
_MATERIAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:protese|ortese|opme|material cirurgico|marca[- ]passo)\b"
)
_ESTETICA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:estetic[oa]|botox|preenchimento|lipo(?:aspiracao)?|harmonizacao|clareamento)\b"
)
_INSEMINACAO: Final[re.Pattern[str]] = re.compile(r"\b(?:inseminacao|fertilizacao in vitro|fiv)\b")
_EXPERIMENTAL: Final[re.Pattern[str]] = re.compile(r"\bexperimenta(?:l|is)\b")
_PARA_MIM: Final[re.Pattern[str]] = re.compile(
    r"\b(?:para mim|pra mim|em mim|eu (?:preciso|vou|tenho|fiz|quero) (?:fazer|operar|realizar)|sou eu|e pra "
    r"mim)\b"
)
_REDE_NO_TEXTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:onde (?:faco|fazer|posso fazer|eu faco|tem)|qual (?:hospital|laboratorio|clinica|medico)"
    r"|quem atende|rede credenciada|perto de mim|endereco)\b"
)


def confirmar_pelo_texto(pedido: Pedido, texto: str) -> Pedido:
    """Os sinais do modelo que mudam o caminho so' valem com APOIO no texto (regra geral, contra falso
    positivo): exclusao legal com a palavra da exclusao; rede com a pergunta de onde/quem atende; titular com
    "para mim"/"eu preciso fazer"."""
    plano = normalizar(texto)
    exclusao = pedido.exclusao_legal
    apoio = {"estetica": _ESTETICA, "inseminacao": _INSEMINACAO, "experimental": _EXPERIMENTAL}.get(exclusao)
    if apoio is None or not apoio.search(plano):
        exclusao = "nenhuma"
    assunto = pedido.assunto
    if assunto == "rede" and not _REDE_NO_TEXTO.search(plano):
        assunto = "cobertura"
    para_quem = pedido.para_quem
    if para_quem == "titular" and not _PARA_MIM.search(plano):
        para_quem = "nao_disse"
    return replace(pedido, exclusao_legal=exclusao, assunto=assunto, para_quem=para_quem)


_ASSUNTO_POR_TEXTO: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\b(?:reembolso|reembolsar|ressarcimento)\b"), "reembolso"),
    (re.compile(r"\bcoparticipacao\b"), "coparticipacao"),
    (
        re.compile(
            r"\b(?:onde (?:faco|posso fazer|fazer)|qual (?:hospital|laboratorio|clinica)|quem atende|rede "
            r"credenciada)\b"
        ),
        "rede",
    ),
    (
        re.compile(
            r"\b(?:andamento|status|situacao) d[ao] (?:minha |meu )?(?:autorizacao|guia|pedido)\b"
            r"|\bja (?:foi )?(?:autorizad[oa]|liberad[oa])\b|\bminha autorizacao\b"
        ),
        "autorizacao",
    ),
)


_VAZIAS_DA_RESERVA: Final[frozenset[str]] = frozenset(
    [
        "que",
        "qual",
        "quais",
        "isso",
        "esse",
        "essa",
        "meu",
        "minha",
        "plano",
        "exame",
        "exames",
        "procedimento",
        "procedimentos",
        "cirurgia",
        "tudo",
        "ele",
        "ela",
    ]
)


def _tem_conteudo(termo: str) -> bool:
    """O termo da reserva tem ao menos uma palavra de 4+ letras que nao e' de conversa."""
    return any(len(p) >= 4 and p not in _VAZIAS_DA_RESERVA for p in normalizar(termo).split())


def pedido_deterministico(texto: str, termos: Sequence[str]) -> Pedido:
    """A RESERVA sem o modelo: o termo achado pelas regras de antes e os sinais por palavra."""
    plano = normalizar(texto)
    assunto = next((a for padrao, a in _ASSUNTO_POR_TEXTO if padrao.search(plano)), "cobertura")
    if _REMEDIO.search(plano):
        tipo = "remedio"
    elif _MATERIAL.search(plano):
        tipo = "material"
    else:
        tipo = "procedimento"
    if _TERCEIRO.search(plano):
        para_quem = "terceiro"
    elif _PARENTESCO_NO_PLANO.search(plano):
        para_quem = "dependente"
    else:
        para_quem = "nao_disse"
    exclusao = (
        "estetica" if _ESTETICA.search(plano) else "inseminacao" if _INSEMINACAO.search(plano) else "nenhuma"
    )
    limpos = [t for t in (_termo_limpo(x) for x in termos) if t and _tem_conteudo(t)]
    return Pedido(
        procedimentos=tuple(limpos[:MAX_PROCEDIMENTOS]),
        tipo=tipo,
        assunto=assunto,
        urgencia=bool(_URGENCIA.search(plano)),
        para_quem=para_quem,
        recem_nascido="recem nascido" in plano or "recem-nascido" in plano,
        exclusao_legal=exclusao,
        discorda=bool(_DISCORDA.search(plano)),
    )


def combinar(modelo: Pedido | None, reserva: Pedido) -> Pedido:
    """O pedido do modelo; sem o modelo (falha, JSON invalido), a reserva deterministica."""
    return reserva if modelo is None else modelo


# --- Contexto da conversa (`coleta_contexto`) ------------------------------------------------------------
@dataclass
class Contexto:
    """O que a conversa de cobertura precisa lembrar entre os turnos. So' fatos do pedido (sem CPF/nome)."""

    termos: list[str] = field(default_factory=list)
    quem: str | None = None
    tentativas: int = 0
    opcoes: list[tuple[str, str]] = field(default_factory=list)  # (codigo, nome) ou ("", descricao)
    tentados: list[str] = field(default_factory=list)
    assunto: str = "cobertura"
    urgencia: bool = False
    respondidos: list[str] = field(default_factory=list)

    def codificar(self) -> str:
        bruto = json.dumps(
            {
                "t": self.termos[:MAX_PROCEDIMENTOS],
                "q": self.quem,
                "a": self.tentativas,
                "o": [list(o) for o in self.opcoes[:3]],
                "h": self.tentados[-6:],
                "s": self.assunto,
                "u": self.urgencia,
                "r": self.respondidos[-4:],
            },
            ensure_ascii=False,
        )
        return bruto[:_MAX_CONTEXTO]

    @classmethod
    def decodificar(cls, bruto: object) -> Contexto:
        if not isinstance(bruto, str) or not bruto.startswith("{"):
            return cls()
        try:
            d = json.loads(bruto)
        except json.JSONDecodeError:
            return cls()
        if not isinstance(d, Mapping):
            return cls()
        opcoes = [
            (str(o[0]), str(o[1]))
            for o in d.get("o") or []
            if isinstance(o, list) and len(o) == 2 and all(isinstance(x, str) for x in o)
        ]
        tentativas = d.get("a")
        return cls(
            termos=[str(t) for t in d.get("t") or [] if isinstance(t, str)][:MAX_PROCEDIMENTOS],
            quem=d.get("q") if isinstance(d.get("q"), str) else None,
            tentativas=tentativas if isinstance(tentativas, int) and 0 <= tentativas <= 5 else 0,
            opcoes=opcoes[:3],
            tentados=[str(t) for t in d.get("h") or [] if isinstance(t, str)][-6:],
            assunto=str(d.get("s") or "cobertura"),
            urgencia=d.get("u") is True,
            respondidos=[str(t) for t in d.get("r") or [] if isinstance(t, str)][-4:],
        )


# --- Leitura das respostas da pessoa
# -------------------------------------------------------------------------
_SIM: Final[re.Pattern[str]] = re.compile(
    r"^(?:sim|s|pode|pode sim|quero|quero sim|ok|claro|por favor|pode passar|passa|pode ser|aceito|isso|"
    r"sim por favor|sim pode|pode passar sim|1 sim)$"
)
_NAO: Final[re.Pattern[str]] = re.compile(
    r"^(?:nao|n|nao obrigad[oa]|agora nao|nao precisa|deixa|deixa pra la)$"
)
_NENHUMA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nenhum|nenhuma|nao e nenhum|nao e nenhuma|nao e esse|nao e isso|outro|outra coisa|nao sei)\b"
)
_ORDINAIS: Final[Mapping[str, int]] = {
    "1": 1, "um": 1, "primeiro": 1, "primeira": 1, "opcao 1": 1,
    "2": 2, "dois": 2, "segundo": 2, "segunda": 2, "opcao 2": 2,
    "3": 3, "tres": 3, "terceiro": 3, "terceira": 3, "opcao 3": 3,
}  # fmt: skip
_TENTAR_DE_NOVO: Final[re.Pattern[str]] = re.compile(
    r"^(?:1|tentar|tenta|tente|tentar de novo|tenta de novo|de novo)$"
)
_FALAR_COM_EQUIPE: Final[re.Pattern[str]] = re.compile(
    r"^(?:2|equipe|falar com a equipe|passar para a equipe|pessoa|atendente)$|\b(?:equipe|atendente|pessoa)\b"
)


def e_sim(texto: str) -> bool:
    return bool(_SIM.match(normalizar(texto)))


def e_nao(texto: str) -> bool:
    return bool(_NAO.match(normalizar(texto)))


def escolha_da_opcao(texto: str, opcoes: Sequence[tuple[str, str]]) -> int | None:
    """O indice (0..) da opcao escolhida pelo numero, ordinal ou NOME; -1 = "nenhuma"; `None` = nao e'
    escolha."""
    plano = normalizar(texto).removeprefix("e ").removeprefix("a ").removeprefix("o ")
    plano = re.sub(r"^(?:opcao|numero|a opcao|o numero)\s+", "", plano)
    if plano in _ORDINAIS:
        n = _ORDINAIS[plano]
        return n - 1 if n <= len(opcoes) else None
    if _NENHUMA.search(plano):
        return -1
    palavras = {p for p in plano.split() if len(p) >= 3}
    if not palavras:
        return None
    melhores: list[tuple[float, int]] = []
    for i, (_, nome) in enumerate(opcoes):
        alvo = {p for p in normalizar(nome).split() if len(p) >= 3}
        if not alvo:
            continue
        comum = len(palavras & alvo) / len(palavras)
        melhores.append((comum, i))
    melhores.sort(reverse=True)
    if melhores and melhores[0][0] >= 0.6 and (len(melhores) == 1 or melhores[0][0] > melhores[1][0]):
        return melhores[0][1]
    return None


def tentar_de_novo(texto: str) -> bool:
    return bool(_TENTAR_DE_NOVO.match(normalizar(texto)))


def falar_com_equipe(texto: str) -> bool:
    return bool(_FALAR_COM_EQUIPE.search(normalizar(texto))) or e_sim(texto)


# --- Textos ------------------------------------------------------------------------------------------------
def _data_br(iso: object) -> str | None:
    if not isinstance(iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", iso):
        return None
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def data_do_dado(view: Any) -> str:
    """E4: a data do dado do cadastro, sempre que existir."""
    data = _data_br(getattr(view, "fonte_atualizada_em", None))
    return f" (dados do cadastro de {data})" if data else ""


TEXTO_FORA_DA_SEGMENTACAO: Final[str] = (
    "Pelo cadastro, esse procedimento não faz parte da segmentação do seu plano{seg}. A confirmação oficial "
    "é "
    "feita quando o médico pede a autorização: a operadora responde por escrito, com o motivo. Se quiser, "
    "passo "
    "para a equipe ver o seu caso."
)
TEXTO_FORA_DO_ROL: Final[str] = (
    "Pelo cadastro, esse procedimento não consta no Rol da ANS. Isso não é uma negativa: em alguns casos "
    "pode "
    "haver cobertura com indicação médica. Quem confirma é a análise da autorização. Quer que eu passe para "
    "a "
    "equipe?"
)
TEXTO_CARENCIA: Final[str] = (
    "Consta para o seu plano, mas há carência até {data}. Antes dessa data, só urgência e emergência têm "
    "regra "
    "própria."
)
TEXTO_DUT: Final[str] = (
    "Consta, com critérios da ANS (diretriz de utilização): a cobertura depende de o pedido médico atender a "
    "esses critérios."
)
AVISO_FINAL: Final[str] = (
    "A cobertura final depende de carência, autorização e indicação médica; confirme pela central de "
    "atendimento ou pelo aplicativo Austa Clínicas."
)


def _rol(edicao: object) -> str:
    texto = " ".join(str(edicao or "").split())
    m = re.search(r"RN\s*N?º?\s*(\d+)\s*/\s*(\d{4})", texto, re.IGNORECASE)
    return f"Rol da ANS (RN nº {m.group(1)}/{m.group(2)})" if m else "Rol da ANS"


def frase_do_item(item: Any, segmentacao: str | None, nome: str | None = None) -> tuple[str, bool]:
    """A frase de UM procedimento e se ela ja' OFERECE a equipe (fora da segmentacao/do Rol)."""
    descricao = " ".join(str(nome or getattr(item, "descricao", "") or "esse procedimento").split())
    situacao = getattr(item, "situacao", "")
    seg = f" ({segmentacao})" if segmentacao else ""
    pendentes = [c for c in getattr(item, "carencias", ()) or () if getattr(c, "cumprida", None) is False]
    if situacao == "fora_da_segmentacao":
        return f"{descricao}: " + TEXTO_FORA_DA_SEGMENTACAO.format(seg=seg), True
    if situacao in ("fora_do_rol", "indeterminado") or situacao not in ("coberto_rol", "coberto_extra"):
        return f"{descricao}: " + TEXTO_FORA_DO_ROL, True
    if situacao == "coberto_rol":
        rol = _rol(getattr(item, "rol_edicao", None))
        frase = f"{descricao}: consta no {rol} para a segmentação do seu plano{seg}."
    else:
        frase = f"{descricao}: consta no cadastro do seu plano como cobertura além do Rol da ANS."
    if getattr(item, "tem_diretriz", False):
        frase += " " + TEXTO_DUT
    if pendentes:
        fim = _data_br(getattr(pendentes[0], "termina_em", None))
        frase += " " + (
            TEXTO_CARENCIA.format(data=fim) if fim else "Há carência em andamento para esse procedimento."
        )
    return frase, False


def item_escolhido(view: Any, nome: str | None) -> Any | None:
    """O item da consulta: o unico, ou o de descricao IGUAL ao nome escolhido; senao `None`."""
    itens = tuple(getattr(view, "itens", ()) or ())
    if not itens:
        return None
    distintos = {
        (normalizar(str(getattr(i, "descricao", "") or "")), getattr(i, "situacao", None)) for i in itens
    }
    if len(distintos) == 1:
        return itens[0]
    if nome:
        alvo = normalizar(nome)
        iguais = [i for i in itens if normalizar(str(getattr(i, "descricao", "") or "")) == alvo]
        if iguais:
            return iguais[0]
    return None


def texto_de_um_resultado(view: Any, item: Any, nome: str | None) -> tuple[str, bool]:
    frase, oferece = frase_do_item(item, getattr(view, "segmentacao", None), nome)
    return frase, oferece


def montar_resposta(frases: Sequence[str], *, view: Any, oferece_equipe: bool, prefixo: str = "") -> str:
    """A resposta final: prefixo (urgencia/discordancia), a(s) frase(s), a data do dado e o aviso."""
    data = data_do_dado(view)
    if len(frases) == 1:
        corpo = f"Pelo cadastro do seu plano{data}: {frases[0]}"
    else:
        corpo = f"Pelo cadastro do seu plano{data}:\n" + "\n".join(
            f"{i}) {f}" for i, f in enumerate(frases, 1)
        )
    fim = "" if oferece_equipe else f" {AVISO_FINAL}"
    return f"{prefixo}{corpo}{fim}".strip()


def nome_legivel(nome: str) -> str:
    """O nome do catalogo para a pessoa ler: sem CAIXA ALTA, ate' 90 letras (corta em palavra)."""
    texto = " ".join(str(nome or "").split())
    if texto.isupper():
        texto = texto.lower()
    texto = texto[:1].upper() + texto[1:]
    if len(texto) > 90:
        texto = texto[:90].rsplit(" ", 1)[0].rstrip(" ,;-(") + "..."
    return texto


def pergunta_das_opcoes(termo: str, nomes: Sequence[str]) -> str:
    linhas = "\n".join(f"{i}) {nome_legivel(n)}" for i, n in enumerate(nomes, 1))
    return (
        f'Encontrei mais de um procedimento parecido com "{termo}" no cadastro:\n{linhas}\n'
        "Qual deles é? Responda com o número ou o nome. Se não for nenhum, me diga como está escrito no "
        "pedido "
        "médico."
    )


def pergunta_da_forma(termo: str) -> str:
    return (
        f'Não localizei "{termo}" no cadastro de procedimentos. Como está escrito no pedido médico? '
        "Pode me mandar o nome do exame ou do procedimento do jeito que aparece lá."
    )


TEXTO_OFERTA_EQUIPE: Final[str] = (
    "Não consegui localizar esse procedimento por aqui. Quer que eu passe para a nossa equipe, que confere "
    "pelo "
    "pedido médico? Responda sim ou não."
)
TEXTO_FALHA_DA_CONSULTA: Final[str] = (
    "Não consegui consultar o cadastro agora. Quer que eu tente de novo ou que eu passe para a nossa equipe? "
    "Responda 1) tentar de novo; 2) falar com a equipe."
)
TEXTO_SEM_EQUIPE: Final[str] = (
    "Tudo bem. Se precisar, é só me mandar o nome do procedimento como está no pedido médico, ou pedir para "
    "falar com a nossa equipe."
)
TEXTO_PEDE_PROCEDIMENTO: Final[str] = (
    "Para eu consultar a cobertura, me diga qual exame ou procedimento é, do jeito que está no pedido médico."
)
PREFIXO_URGENCIA: Final[str] = (
    "Se for urgência ou emergência, a regra é própria: passadas 24 horas da contratação, o plano atende "
    "urgência "
    "e emergência. Se houver sinal de alarme, ligue 192 ou vá ao pronto-socorro. "
)
PREFIXO_DISCORDA: Final[str] = (
    "Entendo. Quem confirma a cobertura de um pedido é a análise da autorização: o médico ou o prestador "
    "envia "
    "o pedido, e a operadora responde por escrito, com o motivo se houver negativa. "
)
TEXTO_TERCEIRO: Final[str] = (
    "Entendo a preocupação. A consulta de cobertura por aqui vale só para quem está no seu plano (você e os "
    "seus "
    "dependentes). Para alguém de fora do plano, quem responde é o plano dessa pessoa. Se quiser, passo para "
    "a "
    "nossa equipe te orientar. Quer?"
)
TEXTO_DEPENDENTE_NAO_ATIVO: Final[str] = (
    "Não encontrei essa pessoa entre os dependentes ATIVOS do seu plano. A consulta por aqui vale para você "
    "e "
    "os dependentes ativos. Se ela estava no plano e saiu, ou se você quer incluí-la, a nossa equipe ajuda. "
    "Quer que eu passe para a equipe?"
)
#: A4 (RASCUNHO; juridico antes de producao): a regra dos 30 dias do recem-nascido (Lei 9.656/1998, art. 12,
#: III).
TEXTO_RECEM_NASCIDO: Final[str] = (
    "Nos planos com obstetrícia, o recém-nascido tem atendimento pelo plano do titular nos primeiros 30 dias "
    "de vida, e pode ser incluído como dependente nesse prazo sem cumprir carência. Para incluir, a nossa "
    "equipe ajuda. Quer que eu passe para a equipe?"
)
TEXTO_REMEDIO: Final[str] = (
    "Remédio tem regra própria, diferente da de exames e procedimentos. Em geral, o plano não cobre remédio "
    "para "
    "usar em casa; há exceções previstas pela ANS, como a quimioterapia oral e alguns tratamentos "
    "específicos, e "
    "o remédio usado durante uma internação ou procedimento faz parte dele. Quem confirma é a análise da "
    "autorização, com o pedido médico. Quer que eu passe para a nossa equipe?"
)
TEXTO_MATERIAL: Final[str] = (
    "Material como prótese, órtese e materiais especiais (OPME) tem regra própria: em geral é coberto quando "
    "está "
    "ligado a um procedimento coberto e pedido junto com ele, com a justificativa do médico. Quem confirma é "
    "a "
    "análise da autorização. Quer que eu passe para a nossa equipe?"
)
TEXTO_EXCLUSAO: Final[Mapping[str, str]] = {
    "estetica": (
        "A lei dos planos de saúde (Lei 9.656/1998, art. 10) deixa de fora da cobertura obrigatória os "
        "procedimentos com finalidade só estética. Quando há indicação médica de reparação ou tratamento, a "
        "análise é outra: quem confirma é a autorização, com o pedido médico. Quer que eu passe para a nossa "
        "equipe?"
    ),
    "inseminacao": (
        "A lei dos planos de saúde (Lei 9.656/1998, art. 10) deixa a inseminação artificial fora da "
        "cobertura "
        "obrigatória. Se quiser entender o que vale para o seu caso, posso passar para a nossa equipe. Quer?"
    ),
    "experimental": (
        "A lei dos planos de saúde (Lei 9.656/1998, art. 10) deixa os tratamentos experimentais fora da "
        "cobertura obrigatória. Se quiser entender o que vale para o seu caso, posso passar para a nossa "
        "equipe. "
        "Quer?"
    ),
}
TEXTO_COPARTICIPACAO: Final[str] = (
    "Por aqui eu não tenho o valor de coparticipação de cada procedimento antes de ele ser feito. Quando o "
    "seu "
    "plano tem coparticipação, ela aparece na mensalidade depois do atendimento, e o Lucas, nosso assistente "
    "financeiro, mostra o que já foi cobrado. Se quiser, também posso passar para a nossa equipe."
)
TEXTO_REEMBOLSO: Final[str] = (
    "Por aqui eu não consigo ver a regra nem pedidos de reembolso do seu plano. Quem orienta sobre reembolso "
    "é "
    "a nossa equipe. Quer que eu passe para a equipe?"
)
TEXTO_PLANO_SEM_ATENDIMENTO: Final[str] = (
    "Pelo cadastro, o atendimento do seu plano não está liberado agora{situacao}. Nessa situação, a "
    "cobertura "
    "depende de regularizar o plano. Para ver a parte financeira, o Lucas, nosso assistente financeiro, "
    "ajuda; "
    "é só me pedir."
)
TEXTO_REPETIDA: Final[str] = (
    "Já te passei o que consta no cadastro para {nome}. Se quiser, posso passar para a nossa equipe conferir "
    "pelo pedido médico. Quer?"
)


def resumo_para_a_equipe(ctx: Contexto) -> str:
    """O resumo do caso (escrito pelo CODIGO, sem CPF): pedido, para quem, termos tentados."""
    termos = "; ".join(dict.fromkeys([*ctx.termos, *ctx.tentados])) or "nao informado"
    quem = {"titular": "o titular"}.get(ctx.quem or "", ctx.quem or "nao informado")
    return (
        f"Duvida de cobertura de procedimento. Pedido: {termos[:300]}. Para quem: {quem}. "
        f"A Helena nao localizou o procedimento no cadastro apos {ctx.tentativas} tentativa(s); conferir "
        f"pelo "
        "pedido medico."
    )


__all__ = [
    "AVISO_FINAL",
    "MAX_TENTATIVAS",
    "PEND_EQUIPE",
    "PEND_FALHA",
    "PEND_FORMA",
    "PEND_OPCOES",
    "PEND_QUEM",
    "PENDENCIAS",
    "PREFIXO",
    "PREFIXO_DISCORDA",
    "PREFIXO_URGENCIA",
    "PROMPT_EXTRACAO",
    "TEXTO_COPARTICIPACAO",
    "TEXTO_DEPENDENTE_NAO_ATIVO",
    "TEXTO_EXCLUSAO",
    "TEXTO_FALHA_DA_CONSULTA",
    "TEXTO_MATERIAL",
    "TEXTO_OFERTA_EQUIPE",
    "TEXTO_PEDE_PROCEDIMENTO",
    "TEXTO_PLANO_SEM_ATENDIMENTO",
    "TEXTO_RECEM_NASCIDO",
    "TEXTO_REEMBOLSO",
    "TEXTO_REMEDIO",
    "TEXTO_REPETIDA",
    "TEXTO_SEM_EQUIPE",
    "TEXTO_TERCEIRO",
    "Contexto",
    "Pedido",
    "combinar",
    "confirmar_pelo_texto",
    "data_do_dado",
    "e_nao",
    "e_sim",
    "escolha_da_opcao",
    "falar_com_equipe",
    "frase_do_item",
    "item_escolhido",
    "montar_resposta",
    "nome_legivel",
    "normalizar",
    "pedido_deterministico",
    "pedido_do_modelo",
    "pergunta_da_forma",
    "pergunta_das_opcoes",
    "resumo_para_a_equipe",
    "tentar_de_novo",
    "texto_de_um_resultado",
]

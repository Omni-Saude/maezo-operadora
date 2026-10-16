"""HELENA V4 (DL-0115) — o catalogo de necessidades v4 do dono, atras da chave `MAEZO_HELENA_V4`.

Este modulo guarda SO' o que e' puro: os lexicos deterministicos, os textos fixos e a leitura do codigo
de "onde a conversa parou". Quem decide a rota e monta o estado e' `graph.py` (`HelenaGraph._v4_*`).
Nada aqui chama modelo, motor ou rede.

CHAVE DESLIGADA (o default) = a Helena de antes, byte a byte: o grafo so' entra aqui com `self._v4`.

O QUE A V4 FAZ (aba "1 Necessidades" do `Catalogo_Necessidades_v4.xlsx`, 16/10/2026; textos aprovados
pelo dono para DEV, revisao juridica/clinica antes de producao):

  1. CONSULTAR UM ESPECIALISTA. A Helena NAO pesquisa especialidade nem agenda neste canal: responde com
     o texto da coluna "HOJE" (segmentacao do cadastro, quando houver; onde marcar — aplicativo Austa
     Clinicas, portal do plano ou central; prazo da ANS; oferta de pessoa). Sem prometer cobertura e sem
     citar nome de medico, clinica ou endereco.
  2. CARTEIRINHA. Plano, segmentacao, vigencia e numero ANS do plano, com o codigo MASCARADO, e o cartao
     completo no aplicativo/portal. Nunca CNS, codigo inteiro, nome ou data de nascimento.
  3. SINTOMA LEVE. Perguntas SO' de alerta (1 ou 2, fechadas, por tipo de queixa, vindas da aba
     "3 Sinais de alarme" do catalogo v1 e das tabelas `triage_redflag_*`) e, com "nao", a resposta no
     formato do dono: "Entendi: <o que a pessoa disse>. ..." com as opcoes 1) onde marcar pelo plano e
     2) falar com a equipe de saude. "Sim" ou sinal de alarme escala como sempre.

TODO O CONTEUDO CLINICO AQUI E' DRAFT — pendente do medico-auditor (ainda nao ha medico). Nenhum sinal
de alarme foi inventado: cada pergunta cita, nos comentarios, as linhas A*/C*/N* do catalogo v1.

PRAZOS DA ANS (RN 566/2022; `docs/compliance/rn-currency-review.md` §2.1):
  * consulta BASICA (pediatria, clinica medica, cirurgia geral, ginecologia e obstetricia): 7 dias uteis —
    ja' conferido no texto-base e usado pela triagem v2;
  * demais ESPECIALIDADES MEDICAS: 14 dias uteis — RASCUNHO / A CONFIRMAR PELO JURIDICO (so' a pagina do
    consumidor da ANS foi lida; ver `PRAZO_ESPECIALISTA_DIAS_UTEIS`);
  * fonoaudiologia, nutricao, psicologia, terapia ocupacional e fisioterapia: 10 dias uteis — OUTRO prazo,
    por isso ficam FORA do lexico (a v4 nao responde 14 para eles).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, NamedTuple

from maezo.ports.tina import ElegibilidadeView

from .triagem_v2 import (
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA,
)

# --- Prazos ----------------------------------------------------------------------------------------
#: RASCUNHO / A CONFIRMAR PELO JURIDICO: demais especialidades medicas, RN 566/2022, em dias uteis
#: (`docs/compliance/rn-currency-review.md`, §2.1, linha 67: "demais especialidades 14 d.u."). Valor lido da
#: pagina do consumidor da ANS; a confirmacao do texto-base da norma esta' pendente.
PRAZO_ESPECIALISTA_DIAS_UTEIS: Final[int] = 14
#: Consulta basica, RN 566/2022 art. 3, I — o valor ja' conferido (usado pela triagem v2).
PRAZO_CONSULTA_BASICA_DIAS_UTEIS: Final[int] = 7

# --- Especialista: o lexico (PEQUENO e FECHADO) -----------------------------------------------------
#: `(nome que a Helena diz, padrao sobre o texto normalizado, e' consulta basica?)`.
#:
#: POR QUE ESTES. A lista do dono (cardiologista, dermatologista, ortopedista, endocrinologista,
#: ginecologista, urologista, oftalmologista, neurologista) mais as especialidades medicas mais pedidas que
#: tem o MESMO prazo (otorrino, gastro, pneumo, reumato). Novas entram por PR, uma a uma.
#:
#: DECISOES, documentadas (16/10/2026):
#:  * GINECOLOGISTA e OBSTETRA sao consulta BASICA na RN 566 (ginecologia e obstetricia): 7 dias uteis, nao
#:    14. O lexico os reconhece, mas o texto diz "consulta basica" com 7 dias — dizer 14 seria falso e
#:    contradiria o texto de gestante da triagem v2. PEDIATRA e CLINICO GERAL idem. A CONFIRMAR com o dono.
#:  * PSIQUIATRA e PSICOLOGO ficam FORA: saude mental pede acolhimento e a tabela de risco psicossocial; o
#:    caminho de hoje continua. Psicologia tem prazo de 10 dias uteis, nao 14.
#:  * FISIOTERAPIA, NUTRICIONISTA e FONOAUDIOLOGO ficam FORA: 10 dias uteis (outro prazo, nao conferido).
#:  * ONCOLOGISTA fica FORA: o assunto pede uma pessoa, e alta complexidade tem prazo proprio.
#:  * "especialista" sozinho fica FORA: sem a especialidade o texto nao teria o que dizer.
_ESPECIALIDADES: Final[tuple[tuple[str, re.Pattern[str], bool], ...]] = (
    ("cardiologista", re.compile(r"\bcardiolog(?:ista|istas|ia)\b"), False),
    ("dermatologista", re.compile(r"\bdermato(?:logistas?|logia)?\b"), False),
    ("ortopedista", re.compile(r"\bortoped(?:ista|istas|ia)\b"), False),
    ("endocrinologista", re.compile(r"\bendocrino(?:logistas?|logia)?\b"), False),
    ("urologista", re.compile(r"\burolog(?:ista|istas|ia)\b"), False),
    ("oftalmologista", re.compile(r"\b(?:oftalmolog(?:ista|istas|ia)|oculista)\b"), False),
    ("neurologista", re.compile(r"\bneurolog(?:ista|istas|ia)\b"), False),
    ("otorrinolaringologista", re.compile(r"\botorrino(?:laringolog(?:ista|istas|ia))?\b"), False),
    ("gastroenterologista", re.compile(r"\bgastroenterolog(?:ista|istas|ia)\b"), False),
    ("pneumologista", re.compile(r"\bpneumolog(?:ista|istas|ia)\b"), False),
    ("reumatologista", re.compile(r"\breumatolog(?:ista|istas|ia)\b"), False),
    # Consulta BASICA (7 dias uteis):
    ("ginecologista", re.compile(r"\bginecolog(?:ista|istas|ia)\b"), True),
    ("obstetra", re.compile(r"\bobstetras?\b"), True),
    ("pediatra", re.compile(r"\bpediatras?\b"), True),
    ("clínico geral", re.compile(r"\bclinicos? gerais\b|\bclinico geral\b"), True),
)
#: O pedido NAO e' de consulta quando fala de outra coisa: procedimento, exame, resultado, papel, cobranca.
#: "cobre ressonancia?" segue o caminho de hoje (cobertura deterministica da consulta do plano).
_NAO_E_PEDIDO_DE_CONSULTA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:exames?|ressonancia|tomografia|ultrassom|ultrassonografia|raio x|biopsia|cirurgias?|procedimentos?"
    r"|internacao|tratamentos?|terapias?|laudos?|resultados?|receitas?|atestados?|encaminhamentos?"
    r"|reembolso|autorizacao|guias?|pedido medico|boletos?|mensalidades?|coparticipacao|faturas?"
    r"|cirurgi\w+|cobranca)\b"
)


def especialidade_pedida(plano: str) -> tuple[str, bool] | None:
    """`(nome, consulta_basica)` da PRIMEIRA especialidade do lexico no texto normalizado, ou `None`.

    `None` tambem quando a mensagem fala de procedimento, exame, resultado, papel ou cobranca (o caminho
    de hoje). So' le' o texto: quem exclui sintoma, pedido de pessoa e portoes de seguranca e' `graph.py`."""
    if _NAO_E_PEDIDO_DE_CONSULTA.search(plano):
        return None
    achado: tuple[int, str, bool] | None = None
    for nome, padrao, basica in _ESPECIALIDADES:
        m = padrao.search(plano)
        if m is not None and (achado is None or m.start() < achado[0]):
            achado = (m.start(), nome, basica)
    return None if achado is None else (achado[1], achado[2])


def texto_do_especialista(
    especialidade: str, *, basica: bool, segmentacao: str | None, frase_cobertura: str | None
) -> str:
    """O texto da coluna "HOJE" da v4 (RASCUNHO aprovado para dev pelo dono, 16/10/2026).

    `segmentacao`: o valor REGISTRADO no cadastro (so' quando o cadastro a traz); entra como "No cadastro,
    seu plano consta com segmentacao X.".
    `frase_cobertura`: a frase de "onde confirmar cobertura" ja' aprovada, so' quando a pessoa PERGUNTOU de
    cobertura. Nenhuma frase promete cobertura; nenhuma cita nome de medico, clinica ou endereco.
    """
    if basica:
        abertura = f"Consulta com {especialidade} é consulta básica."
        prazo = (
            f"Pelas regras da ANS, a consulta básica deve ser oferecida em até "
            f"{PRAZO_CONSULTA_BASICA_DIAS_UTEIS} dias úteis."
        )
    else:
        abertura = f"Consulta com {especialidade} é consulta de especialista."
        prazo = (
            f"Pelas regras da ANS, o plano deve oferecer consulta com especialista em até "
            f"{PRAZO_ESPECIALISTA_DIAS_UTEIS} dias úteis."
        )
    partes = [
        abertura,
        f"No cadastro, seu plano consta com segmentação {segmentacao}." if segmentacao else None,
        "Por aqui eu não pesquiso especialistas nem marco consulta.",
        "Você marca pelo aplicativo Austa Clínicas, pelo portal do plano ou pela central de atendimento "
        "do plano.",
        prazo,
        frase_cobertura,
        "Se quiser falar com uma pessoa da equipe, é só me pedir.",
    ]
    return " ".join(p for p in partes if p)


_ABERTURA_ESPECIALISTA: Final[re.Pattern[str]] = re.compile(
    r"^Consulta com [^.]{3,40} é consulta (?:de especialista|básica)\. "
)


# --- Carteirinha -------------------------------------------------------------------------------------
#: Pede a carteirinha (o numero, os dados, ou o cartao): sem cobranca no texto. A v4 so' ACRESCENTA ao que a
#: Helena ja' reconhecia ("qual o numero da minha carteirinha?").
_PEDE_CARTEIRINHA: Final[re.Pattern[str]] = re.compile(
    r"\bcarteirinha\b|\bcartao do plano\b|\bcarteira do plano\b"
)
_NAO_E_CARTEIRINHA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:boletos?|mensalidades?|faturas?|coparticipacao|pagamentos?|cobranca)\b"
)


def pede_carteirinha(plano: str) -> bool:
    """A mensagem (normalizada) pede a carteirinha, sem falar de cobranca."""
    return bool(_PEDE_CARTEIRINHA.search(plano)) and not _NAO_E_CARTEIRINHA.search(plano)


_MASCARADO: Final[re.Pattern[str]] = re.compile(r"\*{3,}[0-9A-Za-z]{0,4}\Z")
_ANS_REGISTRO: Final[re.Pattern[str]] = re.compile(r"\d{4,9}\Z")
NAO_CONSTA: Final[str] = "não consta no cadastro"
_LIMITE_DE_VINCULOS: Final[int] = 3


def _limpo(valor: object, limite: int = 120) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = " ".join(valor.split())
    return texto[:limite] if texto else None


def _data_br(iso: object) -> str | None:
    """`AAAA-MM-DD` (ou o dia de um date-time) -> `DD/MM/AAAA`; qualquer outra coisa -> `None`."""
    if not isinstance(iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", iso):
        return None
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"


def fatos_da_carteirinha(view: ElegibilidadeView) -> dict[str, object]:
    """Os fatos que PODEM ir ao texto da carteirinha, lista FECHADA: codigo (so' se JA vier mascarado), plano,
    segmentacao, inicio da vigencia, cancelamento e numero ANS do plano. Nome, data de nascimento, CNS,
    CPF, titular e referencias NAO existem aqui: nunca chegam ao texto."""
    vinculos: list[dict[str, str | None]] = []
    for v in view.vinculos[:_LIMITE_DE_VINCULOS]:
        mascarada = v.carteirinha_mascarada
        # O numero ANS so' entra como DIGITOS (4 a 9): qualquer outra coisa e' tratada como ausente.
        ans = re.sub(r"\D", "", _limpo(v.registro_ans_plano, 20) or "")
        vinculos.append(
            {
                "codigo_mascarado": (
                    mascarada if isinstance(mascarada, str) and _MASCARADO.fullmatch(mascarada) else None
                ),
                "plano": _limpo(v.plano),
                "segmentacao": _limpo(v.segmentacao),
                "vigencia_inicio": _data_br(v.vigencia_inicio),
                "cancelamento": _data_br(v.cancelamento),
                "registro_ans": ans if _ANS_REGISTRO.fullmatch(ans) else None,
            }
        )
    return {"vinculos": vinculos}


def texto_da_carteirinha(fatos: Mapping[str, object]) -> str:
    """A resposta da carteirinha: plano, segmentacao, vigencia e numero ANS, com o codigo MASCARADO, e o
    cartao completo no aplicativo/portal. Sem modelo. Campo que o cadastro nao traz diz "nao consta no
    cadastro" (nunca se completa um fato ausente)."""
    vinculos = fatos.get("vinculos")
    blocos: list[str] = []
    if isinstance(vinculos, list):
        for v in vinculos:
            if not isinstance(v, Mapping):
                continue
            vigencia = v.get("vigencia_inicio")
            linhas = [
                f"- Código: {v.get('codigo_mascarado') or 'não consigo mostrar com segurança'}",
                f"- Plano: {v.get('plano') or NAO_CONSTA}",
                f"- Segmentação: {v.get('segmentacao') or NAO_CONSTA}",
                f"- Vigência: {('desde ' + str(vigencia)) if vigencia else NAO_CONSTA}",
                f"- Nº ANS do plano: {v.get('registro_ans') or NAO_CONSTA}",
            ]
            if v.get("cancelamento"):
                linhas.append(f"- Cancelamento: {v['cancelamento']}")
            blocos.append("\n".join(linhas))
    if not blocos:
        return (
            "Não consta nenhum vínculo com o plano neste cadastro. "
            "O cartão completo está no aplicativo Austa Clínicas e no portal do plano. "
            "Se quiser falar com uma pessoa da equipe, é só me pedir."
        )
    return (
        "Pelo cadastro do plano, estes são os dados da sua carteirinha:\n"
        + "\n".join(blocos)
        + "\nO cartão completo está no aplicativo Austa Clínicas e no portal do plano. "
        "Por segurança, não mostro o código inteiro nem outros dados pessoais por aqui. "
        "Se quiser falar com uma pessoa da equipe, é só me pedir."
    )


# --- Sintoma leve: o relato da pessoa ---------------------------------------------------------------
_SAUDACAO: Final[re.Pattern[str]] = re.compile(
    r"^(?:oi|ola|olá|opa|eae|e ai|bom dia|boa tarde|boa noite)\b[\s,!.:;-]*", re.IGNORECASE
)
_LIMITE_DO_RELATO: Final[int] = 160
_LIMITE_DO_ECO: Final[int] = 12


def relato_cru(texto: str) -> str:
    """O relato da pessoa como ela escreveu, sem a saudacao e com tamanho limitado. E' o que vai ao resumo
    do atendente (so' ESTA queixa) e de onde sai o eco."""
    t = " ".join((texto or "").split())
    for _ in range(2):
        t = _SAUDACAO.sub("", t, count=1)
    if len(t) > _LIMITE_DO_RELATO:
        t = t[:_LIMITE_DO_RELATO].rsplit(" ", 1)[0]
    return t.strip()


_POSSESSIVOS: Final[Mapping[str, str]] = {"meu": "seu", "minha": "sua", "meus": "seus", "minhas": "suas"}
_REESCRITAS_INICIAIS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"^(?:eu\s+)?(?:estou|to|tô|tou|estava|ta|tá)\s+(?:com|sentindo)\s+"), ""),
    (re.compile(r"^(?:eu\s+)?(?:tenho|tive|sinto|senti)\s+"), ""),
    (re.compile(r"^(?:eu\s+)?(?:estou|to|tô|tou)\s+"), "você está "),
)
_POSSESSIVO: Final[re.Pattern[str]] = re.compile(r"\b(meu|minha|meus|minhas)\b")
_NAO_SEGURO_NO_ECO: Final[re.Pattern[str]] = re.compile(r"[\[\]@<>{}]|https?:|www\.|\d{6,}")
ECO_GENERICO: Final[str] = "o que você me contou"


def eco_do_relato(cru: str) -> str:
    """O que a pessoa disse, para devolver a ela ("Entendi: dor nas costas desde ontem."): a PRIMEIRA frase,
    em minusculas, com "estou com" / "tenho" tirados e "meu/minha" trocados por "seu/sua" (fala com a
    pessoa); ate' 12 palavras. Texto vazio ou com cara de dado pessoal (colchete, e-mail, link, numero
    longo) volta o generico "o que voce me contou"."""
    primeira = re.split(r"(?<=[.!?])\s+", (cru or "").strip(), maxsplit=1)[0]
    t = primeira.rstrip(".!?… ").strip().lower()
    for padrao, troca in _REESCRITAS_INICIAIS:
        novo = padrao.sub(troca, t, count=1)
        if novo != t:
            t = novo
            break
    t = _POSSESSIVO.sub(lambda m: _POSSESSIVOS[m.group(1)], t)
    t = re.sub(r"\s+(?:por favor|pf|pfv|pfvr)$", "", t)
    palavras = t.split()
    if len(palavras) > _LIMITE_DO_ECO:
        t = " ".join(palavras[:_LIMITE_DO_ECO])
    if len(t) < 2 or _NAO_SEGURO_NO_ECO.search(t):
        return ECO_GENERICO
    return t


# --- Sintoma leve: o tipo de queixa e as perguntas de ALERTA ----------------------------------------
GRUPO_GERAL: Final[str] = "GERAL"
GRUPO_CABECA: Final[str] = "CABECA"
GRUPO_GASTRO: Final[str] = "GASTRO"
GRUPO_PELE: Final[str] = "PELE"
GRUPO_CRIANCA: Final[str] = "CRIANCA"
POPULACAO_ADULTO: Final[str] = "adult"
POPULACAO_CRIANCA: Final[str] = "pediatric"

_CRIANCA_NO_TEXTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:meu|minha) (?:filh[oa]|bebe|neto|neta|sobrinh[oa]|enteado|enteada)\b"
    r"|\bcrianca\b|\bbebe\b|\bnenem\b"
)
_GRUPO_CABECA: Final[re.Pattern[str]] = re.compile(r"\b(?:cabeca|enxaqueca)\b")
_GRUPO_GASTRO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:barriga|abdomen|abdominal|estomago|vomit\w*|diarreia|enjoo|enjoad\w*|nausea\w*|fezes|colica"
    r"|gastrite|azia|refluxo)\b"
)
_GRUPO_PELE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:coceira|cocando|manchas?|bolinh\w+|alergi\w*|urticaria|inchad\w*|inchaco|pele|vermelhid\w+)\b"
)
#: Populacoes que a v4 NAO trata como sintoma leve: gestante e saude mental seguem o caminho de hoje.
_POPULACOES_DA_V4: Final[frozenset[str]] = frozenset({"none", "adult", "pediatric"})
#: Gestacao ou saude mental DITAS no texto, mesmo quando a tabela nao marcou a populacao: caminho de hoje.
_FORA_DA_V4_PELO_TEXTO: Final[re.Pattern[str]] = re.compile(
    r"\b(?:gravida|gestante|gestacao|gravidez|ansiedade|ansios[oa]|panico|depress(?:ao|iv[oa])"
    r"|angusti\w*|triste|tristeza)\b"
)


def populacao_da_v4(population: object, plano: str) -> str | None:
    """`POPULACAO_CRIANCA`/`POPULACAO_ADULTO` ou `None` (gestante, saude mental: caminho de hoje). A
    crianca vem da tabela (`pediatric`) ou das palavras da pessoa ("meu filho", "bebe")."""
    chave = population if isinstance(population, str) and population else "none"
    if chave not in _POPULACOES_DA_V4 or _FORA_DA_V4_PELO_TEXTO.search(plano):
        return None
    if chave == "pediatric" or _CRIANCA_NO_TEXTO.search(plano):
        return POPULACAO_CRIANCA
    return POPULACAO_ADULTO


def grupo_do_sintoma(populacao: str, plano: str) -> str:
    """O tipo de queixa que decide QUAIS perguntas de alerta: crianca; cabeca; barriga/vomito/diarreia; pele;
    senao o conjunto geral. So' o texto — o codigo da allowlist ja' passou pela tabela de alarme."""
    if populacao == POPULACAO_CRIANCA:
        return GRUPO_CRIANCA
    if _GRUPO_CABECA.search(plano):
        return GRUPO_CABECA
    if _GRUPO_GASTRO.search(plano):
        return GRUPO_GASTRO
    if _GRUPO_PELE.search(plano):
        return GRUPO_PELE
    return GRUPO_GERAL


class AlertaDoGrupo(NamedTuple):
    """As perguntas fechadas (1 ou 2) de um grupo e, em palavras, os sinais que elas cobrem (resumo)."""

    perguntas: tuple[str, ...]
    sinais: str


#: AS PERGUNTAS DE ALERTA. RASCUNHO — pendente do medico-auditor. Cada uma lista SO' sinais que ja' existem
#: nas tabelas `triage_redflag_*` / aba "3 Sinais de alarme" do catalogo v1 (NENHUM sinal novo):
#:   GERAL    A1 dor no peito, A2 falta de ar, A7/A8 desmaio, A3 boca torta/fraqueza de um lado/fala alterada
#:   CABECA   A4 dor de cabeca subita e muito forte ("a pior da vida"), A3 deficit neurologico
#:   GASTRO   A10 dor de barriga forte, A5 sangramento (sangue no vomito/nas fezes, fezes pretas)
#:   PELE     A6 reacao alergica grave (inchaco de rosto/boca/lingua, dificuldade para respirar)
#:   CRIANCA  C2 dificuldade para respirar, C5 muito sonolenta, C4 convulsao, N3 mole; C3 febre com
#:            manchinhas vermelhas, C6 sinais de desidratacao (pouco xixi)
#: Redigidas SEM sujeito, porque o sintoma pode ser de outra pessoa ("meu filho"). Frase <= 20 palavras.
ALERTAS: Final[Mapping[str, AlertaDoGrupo]] = {
    GRUPO_GERAL: AlertaDoGrupo(
        (
            "Junto com isso, tem dor no peito, falta de ar, desmaio, boca torta ou fraqueza de um lado "
            "do corpo?",
        ),
        "dor no peito, falta de ar, desmaio, boca torta ou fraqueza de um lado do corpo",
    ),
    GRUPO_CABECA: AlertaDoGrupo(
        (
            "A dor de cabeça começou de repente e é a pior que você já sentiu?",
            "Tem boca torta, fraqueza de um lado do corpo ou fala alterada?",
        ),
        "dor de cabeça de início súbito e muito forte; boca torta, fraqueza de um lado do corpo ou "
        "fala alterada",
    ),
    GRUPO_GASTRO: AlertaDoGrupo(
        ("Tem dor muito forte na barriga, sangue no vômito ou nas fezes, ou fezes pretas?",),
        "dor de barriga muito forte, sangue no vômito ou nas fezes, fezes pretas",
    ),
    GRUPO_PELE: AlertaDoGrupo(
        ("O rosto, a boca ou a língua estão inchados, ou está difícil respirar?",),
        "inchaço do rosto, da boca ou da língua, dificuldade para respirar",
    ),
    GRUPO_CRIANCA: AlertaDoGrupo(
        (
            "Está com dificuldade para respirar, muito mole ou sonolento(a), ou teve convulsão?",
            "Tem febre com manchinhas vermelhas na pele, ou está com pouco xixi?",
        ),
        "dificuldade para respirar, muito mole ou sonolento, convulsão, febre com manchinhas vermelhas, "
        "pouco xixi",
    ),
}
_PEDIDO_DE_RESPOSTA: Final[str] = "Responda sim ou não."
_ABERTURA_DA_REPETICAO: Final[str] = "Só para eu entender:"


def texto_da_pergunta_de_alerta(grupo: str, eco: str, *, repetida: bool = False) -> str:
    """A mensagem com as perguntas de ALERTA do grupo (texto fixo, sem modelo)."""
    perguntas = " ".join(ALERTAS.get(grupo, ALERTAS[GRUPO_GERAL]).perguntas)
    if repetida:
        # A abertura termina em ":", entao a primeira pergunta continua em minuscula.
        return f"{_ABERTURA_DA_REPETICAO} {perguntas[:1].lower()}{perguntas[1:]} {_PEDIDO_DE_RESPOSTA}"
    return (
        f"Entendi: {eco}. Antes de te mostrar o caminho, preciso de uma informação rápida. "
        f"{perguntas} {_PEDIDO_DE_RESPOSTA}"
    )


def texto_final_do_sintoma(eco: str) -> str:
    """A resposta depois do "nao" — NO FORMATO DO DONO (v4, coluna "RECEBERIA", 16/10/2026), palavra por
    palavra. As duas opcoes tem tratamento deterministico (`escolha_da_resposta`)."""
    return (
        f"Entendi: {eco}. Eu não avalio sintomas, mas te ajudo com o caminho. "
        "Você prefere: 1) saber onde marcar consulta pelo plano (clínico geral ou pediatra: "
        "até 7 dias úteis pela ANS); ou 2) falar com a nossa equipe de saúde? "
        "Se piorar, procure um pronto-atendimento; se for grave, ligue 192."
    )


_ABERTURA_FINAL: Final[re.Pattern[str]] = re.compile(
    r"^Entendi: [^.]{1,200}\. Eu não avalio sintomas, mas te ajudo com o caminho\. Você prefere: 1\) "
)


def texto_da_opcao_1(populacao: str) -> str:
    """Opcao 1: como marcar consulta pelo plano, SEM diagnostico — o T05 da triagem v2 (ja' conferido contra
    as cercas): adulto -> clinico geral; crianca -> pediatra."""
    if populacao == POPULACAO_CRIANCA:
        return RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA
    return RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO


# --- O codigo de "onde a conversa parou" (vai em `coleta_pendente`) ---------------------------------
ETAPA_ALERTA: Final[str] = "ALERTA"
ETAPA_ESCOLHA: Final[str] = "ESCOLHA"
ETAPA_CASO_SIM: Final[str] = "CASO_SIM"
ETAPA_CASO_EQUIPE: Final[str] = "CASO_EQUIPE"
ETAPA_CASO_SEM_RESPOSTA: Final[str] = "CASO_SEM_RESPOSTA"
_ETAPAS: Final[frozenset[str]] = frozenset(
    {ETAPA_ALERTA, ETAPA_ESCOLHA, ETAPA_CASO_SIM, ETAPA_CASO_EQUIPE, ETAPA_CASO_SEM_RESPOSTA}
)
PREFIXO_PENDENTE: Final[str] = "V4_"
_SUFIXO_DE_NOVO: Final[str] = "#de_novo"


class Pendente(NamedTuple):
    etapa: str
    grupo: str
    populacao: str
    repetida: bool


def codigo_pendente(etapa: str, grupo: str, populacao: str, *, repetida: bool = False) -> str:
    """`V4_<ETAPA>:<GRUPO>:<populacao>[#de_novo]` — vocabulario fechado, sem texto da pessoa."""
    return f"{PREFIXO_PENDENTE}{etapa}:{grupo}:{populacao}{_SUFIXO_DE_NOVO if repetida else ''}"


def ler_pendente(valor: object) -> Pendente | None:
    """O codigo de `codigo_pendente` lido de volta, ou `None` (nao e' da v4, ou esta' malformado)."""
    if not isinstance(valor, str) or not valor.startswith(PREFIXO_PENDENTE):
        return None
    resto = valor[len(PREFIXO_PENDENTE) :]
    repetida = resto.endswith(_SUFIXO_DE_NOVO)
    if repetida:
        resto = resto[: -len(_SUFIXO_DE_NOVO)]
    partes = resto.split(":")
    if len(partes) != 3:
        return None
    etapa, grupo, populacao = partes
    if etapa not in _ETAPAS or grupo not in ALERTAS or populacao not in (POPULACAO_ADULTO, POPULACAO_CRIANCA):
        return None
    return Pendente(etapa, grupo, populacao, repetida)


# --- Ler a resposta da pessoa -----------------------------------------------------------------------
LEITURA_NAO: Final[str] = "nao"
LEITURA_SIM: Final[str] = "sim"
#: Palavras que dizem "nao" quando a mensagem COMECA por elas.
_NEGATIVAS: Final[frozenset[str]] = frozenset({"nao", "nenhum", "nenhuma", "nada", "negativo", "nem"})
#: Duvida ou meio-termo: lado seguro, vale como "sim" (a equipe olha).
_INCERTEZA: Final[re.Pattern[str]] = re.compile(
    r"\b(?:nao sei|talvez|acho que|as vezes|um pouco|mais ou menos|nao tenho certeza|pode ser)\b"
)
#: "nao" que NAO e' negativa: incapacidade ou agravamento ("nao consigo respirar", "piorou").
_AGRAVANTE: Final[re.Pattern[str]] = re.compile(
    r"\bnao (?:consigo|aguento|da pra|para de|vejo|sinto)\b"
    r"|\b(?:piorou|piorando|piora|insuportavel|desmaiei|desmaiou|convuls\w+|arroxead\w*|roxo|roxa|sangrando"
    r"|sangrou)\b"
)
_RESSALVA: Final[re.Pattern[str]] = re.compile(r"\b(?:mas|porem|so que|entretanto)\b")
_AFIRMATIVAS_INICIAIS: Final[frozenset[str]] = frozenset(
    {"sim", "tenho", "tem", "tive", "teve", "estou", "esta", "sinto", "claro", "isso"}
)
_MAX_PALAVRAS_DA_RESPOSTA: Final[int] = 14


def resposta_ao_alerta(plano: str) -> str | None:
    """`LEITURA_NAO` / `LEITURA_SIM` / `None` (nao deu para entender) sobre a mensagem normalizada.

    DETERMINISTICO e do LADO SEGURO: so' e' "nao" quando a mensagem COMECA por uma negativa, e' curta, nao
    tem ressalva ("nao, mas...") e nao traz agravamento ou incapacidade ("nao consigo respirar", "piorou");
    duvida ("nao sei", "um pouco") vale como "sim"."""
    palavras = plano.split()
    if not palavras or len(palavras) > _MAX_PALAVRAS_DA_RESPOSTA:
        return None
    if _INCERTEZA.search(plano) or _AGRAVANTE.search(plano) or "sim" in palavras:
        return LEITURA_SIM
    if palavras[0] in _NEGATIVAS:
        return None if _RESSALVA.search(plano) else LEITURA_NAO
    if palavras[0] in _AFIRMATIVAS_INICIAIS:
        return LEITURA_SIM
    return None


_PREFIXO_DA_ESCOLHA: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:e|eh)\s+)?(?:(?:a|o)\s+)?(?:(?:opcao|numero|n|item)\s+)?"
)
_CORTESIA_NO_FIM: Final[re.Pattern[str]] = re.compile(r"\s+(?:por favor|pf|pfv|pfvr|obrigad[oa])$")
_NUMEROS_DA_ESCOLHA: Final[Mapping[str, str]] = {
    **dict.fromkeys(("1", "um", "uma", "primeira", "primeiro"), "1"),
    **dict.fromkeys(("2", "dois", "duas", "segunda", "segundo"), "2"),
}
_ROTULO_1: Final[re.Pattern[str]] = re.compile(
    r"^(?:saber )?(?:onde|como) marcar(?: a| uma)?(?: consulta)?(?: pelo plano)?$"
    r"|^marcar(?: a| uma)? consulta(?: pelo plano)?$"
)
_ROTULO_2: Final[re.Pattern[str]] = re.compile(
    r"^(?:quero )?(?:falar )?(?:com )?(?:a |o )?(?:nossa |sua )?equipe(?: de saude)?$"
    r"|^(?:quero )?falar com (?:a |o )?(?:nossa )?equipe(?: de saude)?$"
)


def escolha_da_resposta(plano: str) -> str | None:
    """ "1" ou "2" quando a mensagem (normalizada) e' SO' a escolha de uma opcao da resposta final: o numero
    ("2", "opcao 2", "a 2", "dois", "a segunda"), o numero com o rotulo, ou o rotulo inteiro ("falar com a
    equipe de saude", "onde marcar consulta"). A mensagem inteira: "2 dias de dor" nao e' opcao."""
    texto = _CORTESIA_NO_FIM.sub("", " ".join(plano.split()))
    resto = _PREFIXO_DA_ESCOLHA.sub("", texto, count=1)
    primeira, _, cauda = resto.partition(" ")
    numero = _NUMEROS_DA_ESCOLHA.get(primeira)
    if numero is not None and not cauda:
        return numero
    if numero == "1" and _ROTULO_1.match(cauda):
        return "1"
    if numero == "2" and _ROTULO_2.match(cauda):
        return "2"
    if _ROTULO_1.match(resto):
        return "1"
    if _ROTULO_2.match(resto):
        return "2"
    return None


# --- O resumo do caso para o atendente --------------------------------------------------------------
def resumo_do_caso(pendente: object, relato: object) -> str | None:
    """O resumo LEGIVEL e deterministico de um caso aberto pela v4 (sem modelo, sem asteriscos, sem ficha
    tecnica), ou `None` quando o turno nao e' um caso da v4. Contem SO' o relato da pessoa sobre ESTA queixa
    (nunca o que ela falou antes, sobre outros assuntos), o que a Helena perguntou e o que foi respondido.
    (O resumo ainda passa pelo `redact_free_text` de `_start_escalation`.)"""
    lido = ler_pendente(pendente)
    if lido is None or lido.etapa not in (ETAPA_CASO_SIM, ETAPA_CASO_EQUIPE, ETAPA_CASO_SEM_RESPOSTA):
        return None
    cru = relato_cru(str(relato or "")) if relato else ""
    quem = "do filho(a) ou bebê" if lido.populacao == POPULACAO_CRIANCA else "da própria pessoa"
    queixa = f'A pessoa relatou (queixa {quem}): "{cru}".' if cru else "A pessoa relatou uma queixa de saúde."
    sinais = ALERTAS[lido.grupo].sinais
    if lido.etapa == ETAPA_CASO_SIM:
        corpo = (
            f"A Helena perguntou por sinais de alerta ({sinais}) e a pessoa respondeu sim ou deu a entender "
            "que sim. Caso aberto como alerta de saúde."
        )
    elif lido.etapa == ETAPA_CASO_EQUIPE:
        corpo = (
            f"A Helena perguntou por sinais de alerta ({sinais}) e a resposta foi não. "
            "A pessoa escolheu falar com a equipe de saúde."
        )
    else:
        corpo = (
            f"A Helena perguntou duas vezes por sinais de alerta ({sinais}) e não ficou claro se a pessoa "
            "respondeu. Caso aberto por precaução."
        )
    return f"{queixa} {corpo} A Helena não avaliou sintomas nem deu orientação clínica."


# --- Os textos fixos que `inform` deixa passar ------------------------------------------------------
#: Os textos FIXOS (sem parte variavel) que a v4 deixa prontos para `inform`.
TEXTOS_FIXOS_V4: Final[frozenset[str]] = frozenset(
    {RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO, RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO_CRIANCA}
)


def e_texto_da_v4(texto: object) -> bool:
    """O texto que `classify` deixou pronto e `inform` pode enviar como esta': a resposta do especialista, a
    resposta final do sintoma ou um dos textos fixos da opcao 1."""
    if not isinstance(texto, str):
        return False
    return (
        texto in TEXTOS_FIXOS_V4
        or _ABERTURA_ESPECIALISTA.match(texto) is not None
        or _ABERTURA_FINAL.match(texto) is not None
    )


__all__ = [
    "ALERTAS",
    "ECO_GENERICO",
    "ETAPA_ALERTA",
    "ETAPA_CASO_EQUIPE",
    "ETAPA_CASO_SEM_RESPOSTA",
    "ETAPA_CASO_SIM",
    "ETAPA_ESCOLHA",
    "GRUPO_CABECA",
    "GRUPO_CRIANCA",
    "GRUPO_GASTRO",
    "GRUPO_GERAL",
    "GRUPO_PELE",
    "NAO_CONSTA",
    "POPULACAO_ADULTO",
    "POPULACAO_CRIANCA",
    "PRAZO_CONSULTA_BASICA_DIAS_UTEIS",
    "PRAZO_ESPECIALISTA_DIAS_UTEIS",
    "PREFIXO_PENDENTE",
    "LEITURA_NAO",
    "LEITURA_SIM",
    "TEXTOS_FIXOS_V4",
    "AlertaDoGrupo",
    "Pendente",
    "codigo_pendente",
    "e_texto_da_v4",
    "eco_do_relato",
    "escolha_da_resposta",
    "especialidade_pedida",
    "fatos_da_carteirinha",
    "grupo_do_sintoma",
    "ler_pendente",
    "pede_carteirinha",
    "populacao_da_v4",
    "relato_cru",
    "resposta_ao_alerta",
    "resumo_do_caso",
    "texto_da_carteirinha",
    "texto_da_opcao_1",
    "texto_da_pergunta_de_alerta",
    "texto_do_especialista",
    "texto_final_do_sintoma",
]

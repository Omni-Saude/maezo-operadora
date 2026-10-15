"""NOVA TRIAGEM DA HELENA (DL-0104) — experimento em DEV atras da chave `MAEZO_HELENA_TRIAGEM_V2`.

Este modulo guarda SO' o que e' puro: o vocabulario fechado da tabela nova
(`spec/processes/dmn/helena_triage_admissibility.dmn`), os lexicos deterministicos que apuram a
NECESSIDADE do catalogo a partir do texto, e os textos fixos. Quem consulta a tabela, decide a rota e
monta o estado e' `graph.py` (`HelenaGraph._triagem_v2`). Nada aqui chama modelo, motor ou rede.

CHAVE DESLIGADA (o default) = a Helena de antes, byte a byte: nenhum simbolo deste modulo e' alcancado
por um turno com a chave desligada (`graph.py` so' entra aqui com `self._triagem_v2`).

FONTE DE TUDO: o catalogo `Catalogo_Necessidades_Helena_Lucas.xlsx` (14/10/2026) — aba "1 Necessidades"
(codigos S01..A05), aba "2 Tabela de decisao" (R01..R24), aba "4 Ficha minima" (perguntas por tipo de
queixa) e aba "5 Textos" (T01..T22). CONTEUDO CLINICO DRAFT — pendente de ratificacao do medico-auditor.

O LIMITE QUE NAO MUDA (L0, ADR-0005/0008/0012): nenhum texto daqui diagnostica, diz se e' grave, orienta
conduta ou indica remedio. Os textos mostram o CAMINHO (rede do plano, UBS, pronto-atendimento, 192) e
oferecem uma pessoa (RN 623/2024, art. 9, III).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

#: A chave da decisao no motor — o `id` da `decision` em `helena_triage_admissibility.dmn`.
TRIAGEM_V2_DMN_KEY: Final[str] = "helena_triage_admissibility"

#: FICHA: ate' 3 rodadas de pergunta, SO' com a chave ligada (aba 4: "no maximo 3-4 perguntas"; a
#: decisao D02 do catalogo segue pendente). Com a chave desligada continua `COLETA_MAX_RODADAS = 2`.
#: A tabela nova repete o limite (`rodadas < 3` / `>= 3`); `test_helena_triagem_v2_dmn.py` confere os dois.
COLETA_MAX_RODADAS_TRIAGEM_V2: Final[int] = 3

#: O `coleta_veredito` de um turno de PERGUNTA da nova triagem: e' a DECISAO da tabela (`PERGUNTAR_MAIS`),
#: nao um veredito da `triage_sufficiency`. O backstop de `_route` aceita a rota `collect` com ele (e so'
#: dentro do limite de 3 rodadas); com a chave desligada nenhum turno o emite.
COLETA_VEREDITO_PERGUNTAR_MAIS: Final[str] = "PERGUNTAR_MAIS"
#: Prefixo da PERGUNTA DA FICHA POR TIPO DE QUEIXA (aba 4) em `coleta_pergunta`. As perguntas da
#: `triage_sufficiency` (PERGUNTAR_INTENSIDADE etc.) continuam com o nome de sempre.
PREFIXO_PERGUNTA_DA_FICHA: Final[str] = "PERGUNTAR_FICHA_"

# --- Vocabulario FECHADO da tabela -----------------------------------------------------------------
DECISAO_ABRIR_CASO_EMERGENCIA: Final[str] = "ABRIR_CASO_EMERGENCIA"
DECISAO_ABRIR_CASO: Final[str] = "ABRIR_CASO"
DECISAO_PERGUNTAR_MAIS: Final[str] = "PERGUNTAR_MAIS"
DECISAO_ESCLARECER: Final[str] = "ESCLARECER_NECESSIDADE"
DECISAO_ORIENTAR: Final[str] = "ORIENTAR_CAMINHO_SEM_CASO"
DECISAO_RESPONDER: Final[str] = "RESPONDER_SEM_CASO"
DECISAO_PASSAR_AO_LUCAS: Final[str] = "PASSAR_AO_LUCAS"
DECISOES: Final[frozenset[str]] = frozenset(
    {
        DECISAO_ABRIR_CASO_EMERGENCIA,
        DECISAO_ABRIR_CASO,
        DECISAO_PERGUNTAR_MAIS,
        DECISAO_ESCLARECER,
        DECISAO_ORIENTAR,
        DECISAO_RESPONDER,
        DECISAO_PASSAR_AO_LUCAS,
    }
)
DECISOES_QUE_ABREM_CASO: Final[frozenset[str]] = frozenset(
    {DECISAO_ABRIR_CASO_EMERGENCIA, DECISAO_ABRIR_CASO}
)
DECISOES_COM_TEXTO: Final[frozenset[str]] = frozenset(
    {DECISAO_ESCLARECER, DECISAO_ORIENTAR, DECISAO_RESPONDER}
)
#: Os `motivo_categoria` que a Helena pode abrir (o `MotivoCategoria` de `graph.py`, subconjunto do enum
#: do contrato SP-OP-ESCALATION-001: `cobranca` e `alerta_sla` sao de outros produtores).
MOTIVOS_DA_HELENA: Final[frozenset[str]] = frozenset(
    {
        "red_flag_clinico",
        "risco_psicossocial",
        "intencao_clinica",
        "solicitacao_humano",
        "falha_tecnica",
        "outro",
    }
)
SEVERIDADES: Final[frozenset[str]] = frozenset({"grave", "moderada", "leve"})
#: "-" = a coluna nao se aplica (a linha nao abre caso / o texto vem de outro lugar).
NAO_SE_APLICA: Final[str] = "-"

# --- Necessidades (aba 1 do catalogo) ----------------------------------------------------------------
NECESSIDADE_SINTOMA: Final[str] = "S01"
NECESSIDADE_SINTOMA_GESTANTE: Final[str] = "S04"
NECESSIDADE_SINTOMA_CRIANCA: Final[str] = "S05"
NECESSIDADE_CONDUTA: Final[str] = "S06"
NECESSIDADE_EXAME: Final[str] = "S08"
NECESSIDADE_RECEITA: Final[str] = "S09"
NECESSIDADE_NAVEGACAO: Final[str] = "S10"
NECESSIDADE_NAO_CLARA: Final[str] = "A05"
NECESSIDADE_COBRANCA: Final[str] = "COBRANCA"
NECESSIDADES_DE_SINTOMA: Final[frozenset[str]] = frozenset(
    {NECESSIDADE_SINTOMA, NECESSIDADE_SINTOMA_GESTANTE, NECESSIDADE_SINTOMA_CRIANCA}
)
NECESSIDADES: Final[frozenset[str]] = NECESSIDADES_DE_SINTOMA | frozenset(
    {
        NECESSIDADE_CONDUTA,
        NECESSIDADE_EXAME,
        NECESSIDADE_RECEITA,
        NECESSIDADE_NAVEGACAO,
        NECESSIDADE_NAO_CLARA,
        NECESSIDADE_COBRANCA,
    }
)

# --- Textos (aba "5 Textos") -----------------------------------------------------------------------
#: T05 — orientar o caminho depois da ficha, sem alarme (R13/R14).
#: RASCUNHO — pendente de aprovacao do dono (D14). Adaptado do T05 do catalogo so' para caber na regua de
#: CLAREZA (frase <= 20 palavras, DL-0087) e na cerca de canal (nomes confirmados por inteiro). Prazo da
#: ANS: SO' o valor conferido no texto-base da RN 566/2022 (art. 3, I: consulta basica em 7 dias uteis).
RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO: Final[str] = (
    "Obrigada pelas informações. Eu não avalio sintomas, mas posso te mostrar o caminho. "
    "Você pode marcar consulta com um médico da rede do plano. "
    "Use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento do plano. "
    "Pelas regras da ANS, o plano deve oferecer essa consulta em até 7 dias úteis. "
    "Isso vale para clínico geral, pediatra, ginecologista ou cirurgião geral. "
    "A UBS, pelo SUS, também atende. Se piorar, procure um pronto-atendimento. "
    "Se for uma emergência, ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
#: T08 — recusar remedio / "o que eu tenho?" (R15). RASCUNHO — pendente de aprovacao do dono (D01/D14).
#: Variacao do T07 (`RESPOSTA_DUVIDA_CLINICA`, JA EXISTE): diz que nao diagnostica nem indica remedio,
#: mostra o caminho e oferece uma pessoa.
RESPOSTA_TRIAGEM_SEM_CONDUTA: Final[str] = (
    "Não consigo dizer o que você tem nem qual remédio tomar: isso é feito por um médico, numa consulta. "
    "Você pode marcar consulta na rede do plano. "
    "Use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento do plano. "
    "A UBS, pelo SUS, também atende. Se for uma emergência, ligue agora para o 192 (SAMU). "
    "Se quiser falar com uma pessoa da nossa equipe de saúde, é só me pedir."
)
#: T09 — resultado de exame / receita / atestado / laudo (R16). RASCUNHO — pendente de aprovacao do dono.
RESPOSTA_TRIAGEM_EXAME_RECEITA: Final[str] = (
    "Quem interpreta resultado de exame e faz receita ou atestado é o médico, numa consulta. "
    "Leve o resultado ao médico que pediu o exame, ou marque consulta na rede do plano. "
    "Use o aplicativo Austa Clínicas, o portal do plano ou a central de atendimento do plano. "
    "Se quiser falar com uma pessoa da equipe, é só me pedir."
)
#: T11 — pergunta de esclarecimento com opcoes (R23). RASCUNHO — pendente de aprovacao do dono (D09).
#: Em lista (uma opcao por linha) para caber na regua de CLAREZA.
RESPOSTA_TRIAGEM_ESCLARECER: Final[str] = (
    "Para eu te ajudar melhor, me diga sobre o que é a sua mensagem:\n"
    "1) um sintoma ou dúvida de saúde\n"
    "2) o plano (cobertura, carteirinha, autorização)\n"
    "3) mensalidade ou boleto\n"
    "4) falar com uma pessoa\n"
    "Pode responder com o número."
)
#: Catch-all (R24 com a divergencia do experimento): oferecer uma pessoa, sem abrir caso.
#: RASCUNHO — pendente de aprovacao do dono (D08). Sem ID no catalogo (`OFERTA_PESSOA` na tabela).
RESPOSTA_TRIAGEM_OFERTA_PESSOA: Final[str] = (
    "Ainda não entendi o que você precisa. Se quiser falar com uma pessoa da equipe, é só me pedir. "
    "Se for uma emergência, ligue agora para o 192 (SAMU) ou vá ao pronto-socorro mais próximo."
)
#: Respostas as opcoes 2 e 3 do T11. RASCUNHO — pendente de aprovacao do dono (D09). A opcao 1 reaproveita
#: `RESPOSTA_DUVIDA_DE_SAUDE` (JA EXISTE) e a 4 e' pedido de pessoa (R10, abre caso).
RESPOSTA_TRIAGEM_OPCAO_PLANO: Final[str] = (
    "Certo, é sobre o plano. Me conte o que você quer saber: cobertura, carência, carteirinha ou autorização."
)
RESPOSTA_TRIAGEM_OPCAO_COBRANCA: Final[str] = (
    "Certo, é sobre mensalidade ou boleto. "
    "Me conte o que você quer saber, por exemplo o valor ou o vencimento."
)

#: A ficha minima POR TIPO DE QUEIXA (aba "4 Ficha minima", coluna "Exemplo de pergunta (RASCUNHO)").
#: RASCUNHO — pendente de aprovacao do dono e do medico-auditor (D02). UMA mensagem com as perguntas da
#: queixa, enviada como TEXTO FIXO (sem modelo). Redigidas SEM sujeito ("Está com falta de ar?") porque o
#: sintoma pode ser de outra pessoa ("meu filho"); a da crianca troca "o bebe" por "a crianca" pelo mesmo
#: motivo. Nenhuma orienta conduta: so' perguntam o que alimenta as tabelas de alarme.
PERGUNTAS_DA_FICHA: Final[Mapping[str, str]] = {
    "DOR": "Em que parte do corpo é a dor? Ela está fraca, média ou forte? Começou de repente?",
    "FEBRE": (
        "Quem está com febre e qual a idade? Se for bebê, diga em meses. "
        "Apareceram manchinhas vermelhas na pele?"
    ),
    "RESPIRATORIO": (
        "Está com falta de ar? Consegue falar uma frase inteira sem parar para respirar? Tem febre?"
    ),
    "PELE": "Inchou o rosto, a boca ou a língua? Está difícil respirar? As manchas vieram junto com febre?",
    "GASTRO": (
        "Tem sangue no vômito ou nas fezes, ou as fezes estão pretas? "
        "Está conseguindo beber água e fazer xixi?"
    ),
    "URINARIO": "Tem febre? A dor é forte? Apareceu sangue no xixi?",
    "MENTAL": "Quer me contar um pouco do que está sentindo? Desde quando está assim?",
    "GESTANTE": (
        "De quantas semanas é a gestação? "
        "Está tendo sangramento, perda de líquido ou o bebê está mexendo menos?"
    ),
    "CRIANCA": (
        "Qual é a idade da criança? Se for bebê, diga em meses ou dias. "
        "Está mamando ou comendo e fazendo xixi como de costume? Está muito mole ou sonolenta?"
    ),
}

# --- Lexicos deterministicos (sobre texto JA NORMALIZADO: sem acento, minusculo, sem pontuacao) ---------
#: Relato de SINTOMA numa pergunta clinica ("o que pode ser essa dor ao urinar?"): com ele, a pergunta e'
#: tratada como sintoma (ficha), nunca como pedido de conduta sem sintoma. "urina" sozinha fica de fora de
#: proposito: "o resultado do exame de urina" e' S08, nao sintoma.
_RELATO_DE_SINTOMA = re.compile(
    r"\b(?:dor|dores|doi|doendo|doer|febre|tosse|tossindo|vomit\w*|diarreia|enjoo|enjoad\w*|nausea\w*"
    r"|coceira|cocando|manchas?|bolinh\w+|sangr\w*|ardencia|arde|ardendo|queimacao|inchad\w*|inchaco"
    r"|tontura|tont[oa]|falta de ar|chiado|catarro|coriza|mal estar|passando mal|machuc\w*|ferid[oa]s?"
    r"|alergi\w*|irritacao|colica|desmai\w*|formigamento|dormencia)\b"
)
_RESULTADO_DE_EXAME = re.compile(
    r"\b(?:resultados?|meu exame|meus exames|o exame (?:deu|saiu|veio)"
    r"|exames? (?:deu|deram|saiu|sairam|veio))\b"
)
_RECEITA_ATESTADO = re.compile(r"\b(?:receitas?|atestados?|laudos?|receituario)\b")
#: NECESSIDADE NAO CLARA (A05): a mensagem inteira e' feita destas palavras E tem pelo menos uma palavra de
#: necessidade ("preciso de ajuda", "tenho um problema"). Vocabulario FECHADO como o de
#: `_conversa_sem_relato_clinico`: nenhuma palavra de corpo, idade, remedio, numero ou cobranca cabe nele,
#: entao um sintoma nunca vira "nao clara" por aqui.
_PALAVRAS_DE_NECESSIDADE: Final[frozenset[str]] = frozenset(
    {"ajuda", "ajudar", "ajude", "problema", "problemas", "coisa", "coisas", "aquilo", "resolver", "situacao"}
)
_VOCABULARIO_VAGO: Final[frozenset[str]] = _PALAVRAS_DE_NECESSIDADE | frozenset(
    {
        *("eu", "me", "mim", "minha", "meu", "voce", "vc", "a", "o", "um", "uma", "uns", "umas"),
        *("de", "da", "do", "com", "para", "pra", "por", "e", "ou", "que", "so", "isso", "ai", "la"),
        *("oi", "ola", "opa", "bom", "boa", "dia", "tarde", "noite", "helena", "agora", "favor"),
        *("quero", "queria", "gostaria", "preciso", "precisava", "tenho", "tem", "pode", "poderia"),
        *("consegue", "sei", "nao", "outra", "outro", "algum", "alguma", "uma", "esse", "essa"),
    }
)
_MAX_PALAVRAS_VAGAS: Final[int] = 12
#: Resposta NUMERADA a pergunta de esclarecimento (T11): "4", "opcao 4", "a 2".
_RESPOSTA_NUMERADA = re.compile(r"^(?:(?:a|opcao|numero|n)\s+)?([1-4])$")

#: Tipo de queixa pelo CODIGO da allowlist (quando ha' um) — so' os que a ficha usa.
_GRUPO_DO_CODIGO: Final[Mapping[str, str]] = {
    "febre": "FEBRE",
    "dispneia": "RESPIRATORIO",
    "dificuldade_respiratoria": "RESPIRATORIO",
    "reacao_alergica": "PELE",
    "dor_abdominal": "GASTRO",
    "sinais_desidratacao": "GASTRO",
    "vomitos_persistentes": "GASTRO",
}
#: Tipo de queixa pelo TEXTO, na ORDEM: o especifico antes de "dor" ("dor ao urinar" e' urinario).
_GRUPOS_PELO_TEXTO: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("URINARIO", re.compile(r"\b(?:urin\w*|xixi|bexiga)\b")),
    (
        "RESPIRATORIO",
        re.compile(
            r"\b(?:tosse|tossindo|falta de ar|respir\w*|chiado|catarro|coriza|gripe|gripad[oa]|resfriad\w*"
            r"|garganta)\b"
        ),
    ),
    ("PELE", re.compile(r"\b(?:coceira|cocando|manchas?|bolinh\w+|pele|alergi\w*|urticaria|inchad\w*)\b")),
    (
        "GASTRO",
        re.compile(
            r"\b(?:vomit\w*|diarreia|enjoo|enjoad\w*|nausea\w*|barriga|estomago|fezes|intestin\w*|colica)\b"
        ),
    ),
    ("FEBRE", re.compile(r"\b(?:febre|febril)\b")),
    ("DOR", re.compile(r"\b(?:dor|dores|doi|doendo)\b")),
)
_GRUPO_DA_POPULACAO: Final[Mapping[str, str]] = {
    "gestante": "GESTANTE",
    "pediatric": "CRIANCA",
    "mental_health": "MENTAL",
}


def relata_sintoma(plano: str, intensidade: object = None) -> bool:
    """A mensagem (normalizada) RELATA um sintoma? Uma intensidade dita pelo modelo tambem conta."""
    if isinstance(intensidade, str) and intensidade in SEVERIDADES:
        return True
    return bool(_RELATO_DE_SINTOMA.search(plano))


def necessidade_do_sintoma(population: object) -> str:
    """S04 (gestante), S05 (crianca) ou S01 — o quadro ja' passou pela tabela de alarme da populacao."""
    if population == "gestante":
        return NECESSIDADE_SINTOMA_GESTANTE
    if population == "pediatric":
        return NECESSIDADE_SINTOMA_CRIANCA
    return NECESSIDADE_SINTOMA


def necessidade_da_pergunta_clinica(plano: str, *, navegacao: bool) -> str:
    """S09 (receita/atestado/laudo), S08 (resultado de exame), S10 (onde ir) ou S06 (conduta/diagnostico).

    So' para pergunta clinica SEM sintoma relatado. S07 (duvida geral) cai em S06 ate' a decisao D10.
    """
    if _RECEITA_ATESTADO.search(plano):
        return NECESSIDADE_RECEITA
    if _RESULTADO_DE_EXAME.search(plano):
        return NECESSIDADE_EXAME
    if navegacao:
        return NECESSIDADE_NAVEGACAO
    return NECESSIDADE_CONDUTA


def necessidade_vaga(plano: str) -> bool:
    """A05: a mensagem inteira e' vaga ("preciso de ajuda", "oi, tenho um problema")."""
    palavras = plano.split()
    if not palavras or len(palavras) > _MAX_PALAVRAS_VAGAS:
        return False
    return all(p in _VOCABULARIO_VAGO for p in palavras) and any(
        p in _PALAVRAS_DE_NECESSIDADE for p in palavras
    )


def resposta_numerada(plano: str) -> str | None:
    """ "1".."4" quando a mensagem e' so' o numero de uma opcao do T11; senao `None`."""
    achado = _RESPOSTA_NUMERADA.match(plano.strip())
    return achado.group(1) if achado else None


def grupo_da_queixa(population: object, sintoma_codigo: object, plano: str) -> str | None:
    """O tipo de queixa da aba 4 (chave de `PERGUNTAS_DA_FICHA`), ou `None` quando nao se sabe.

    A POPULACAO vence (gestante, crianca, saude mental): a ficha dela pergunta o que as tabelas de alarme
    daquela populacao mais precisam (semanas de gestacao, idade em meses). Depois o codigo da allowlist,
    depois o texto.
    """
    if isinstance(population, str) and population in _GRUPO_DA_POPULACAO:
        return _GRUPO_DA_POPULACAO[population]
    if isinstance(sintoma_codigo, str) and sintoma_codigo in _GRUPO_DO_CODIGO:
        return _GRUPO_DO_CODIGO[sintoma_codigo]
    for grupo, padrao in _GRUPOS_PELO_TEXTO:
        if padrao.search(plano):
            return grupo
    return None


def pergunta_da_ficha(pergunta: object) -> str | None:
    """O texto fixo de `PERGUNTAR_FICHA_<GRUPO>`, ou `None` se `pergunta` nao e' uma pergunta da ficha."""
    if not isinstance(pergunta, str) or not pergunta.startswith(PREFIXO_PERGUNTA_DA_FICHA):
        return None
    return PERGUNTAS_DA_FICHA.get(pergunta[len(PREFIXO_PERGUNTA_DA_FICHA) :])


class SaidaInvalidaError(ValueError):
    """A linha da tabela nova saiu do vocabulario fechado — o codigo trata como falha tecnica."""


def ler_linha(row: Mapping[str, object]) -> tuple[str, str | None, str | None, str | None]:
    """`(decisao, motivo, severidade, texto)` validados de UMA linha da tabela, ou `SaidaInvalidaError`.

    FAIL-CLOSED: decisao fora de `DECISOES`; caso aberto sem motivo/severidade do dominio; linha SEM caso
    que traga motivo/severidade; ou decisao que precisa de texto sem ID de texto conhecido. Os "-" viram
    `None`. A mensagem de erro so' carrega tokens fechados (nunca valor vindo de fora sem conferir).
    """

    def _valor(chave: str) -> str | None:
        bruto = row.get(chave)
        bruto = bruto.get("value") if isinstance(bruto, Mapping) else bruto
        if bruto is None:
            return None
        texto = str(bruto).strip()
        return None if texto in ("", NAO_SE_APLICA) else texto

    decisao = _valor("decisao")
    if decisao not in DECISOES:
        raise SaidaInvalidaError("decisao fora do vocabulario")
    motivo, severidade, texto = _valor("motivo_categoria"), _valor("severidade"), _valor("texto")
    if decisao in DECISOES_QUE_ABREM_CASO:
        if motivo not in MOTIVOS_DA_HELENA or severidade not in SEVERIDADES:
            raise SaidaInvalidaError("caso sem motivo/severidade do contrato")
    elif motivo is not None or severidade is not None:
        raise SaidaInvalidaError("linha sem caso com motivo/severidade")
    if decisao in DECISOES_COM_TEXTO and texto not in IDS_DE_TEXTO:
        raise SaidaInvalidaError("texto fora do vocabulario")
    return decisao, motivo, severidade, texto


#: Os IDs de texto que a tabela pode emitir (coluna `texto`). T12 = `RESPOSTA_NAVEGACAO` (JA EXISTE), que
#: `graph.py` escolhe com a variante de caso aberto; os outros sao as constantes acima.
IDS_DE_TEXTO: Final[frozenset[str]] = frozenset({"T05", "T08", "T09", "T11", "T12", "OFERTA_PESSOA"})
TEXTOS_POR_ID: Final[Mapping[str, str]] = {
    "T05": RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    "T08": RESPOSTA_TRIAGEM_SEM_CONDUTA,
    "T09": RESPOSTA_TRIAGEM_EXAME_RECEITA,
    "T11": RESPOSTA_TRIAGEM_ESCLARECER,
    "OFERTA_PESSOA": RESPOSTA_TRIAGEM_OFERTA_PESSOA,
}

#: Os textos NOVOS deste modulo que chegam ao beneficiario (as cercas de saida e a regua de CLAREZA sao
#: conferidas sobre todos eles em teste).
TEXTOS_NOVOS: Final[tuple[str, ...]] = (
    RESPOSTA_TRIAGEM_ORIENTAR_CAMINHO,
    RESPOSTA_TRIAGEM_SEM_CONDUTA,
    RESPOSTA_TRIAGEM_EXAME_RECEITA,
    RESPOSTA_TRIAGEM_ESCLARECER,
    RESPOSTA_TRIAGEM_OFERTA_PESSOA,
    RESPOSTA_TRIAGEM_OPCAO_PLANO,
    RESPOSTA_TRIAGEM_OPCAO_COBRANCA,
    *PERGUNTAS_DA_FICHA.values(),
)


def linha_da_ficha(estado: Mapping[str, object]) -> str:
    """A FICHA COLETADA em uma linha para o atendente (vai no `resumo_contexto`, que ja' e' PHI listada e
    passa pelo `redact_free_text` como hoje). So' campos de VOCABULARIO FECHADO e numeros; o texto que a
    pessoa escreveu vai ao resumo pelo modelo (bloco nao confiavel), nunca colado aqui. "" sem nada."""
    partes: list[str] = []
    for rotulo, chave in (
        ("populacao", "population"),
        ("sintoma", "sintoma_codigo"),
        ("intensidade", "intensidade"),
        ("idade_anos", "idade_anos"),
        ("idade_meses", "idade_meses"),
        ("semanas_gestacao", "idade_gestacional_semanas"),
        ("perguntas_feitas", "coleta_rodadas"),
    ):
        valor = estado.get(chave)
        if valor is None or valor in ("", "none", "desconhecida") or isinstance(valor, bool):
            continue
        if not isinstance(valor, (str, int)):
            continue
        partes.append(f"{rotulo}={valor}")
    return f"[ficha da triagem: {', '.join(partes)}]" if partes else ""


__all__ = [
    "COLETA_MAX_RODADAS_TRIAGEM_V2",
    "COLETA_VEREDITO_PERGUNTAR_MAIS",
    "DECISOES",
    "IDS_DE_TEXTO",
    "NECESSIDADES",
    "PERGUNTAS_DA_FICHA",
    "PREFIXO_PERGUNTA_DA_FICHA",
    "TEXTOS_NOVOS",
    "TEXTOS_POR_ID",
    "TRIAGEM_V2_DMN_KEY",
    "SaidaInvalidaError",
    "grupo_da_queixa",
    "ler_linha",
    "linha_da_ficha",
    "necessidade_da_pergunta_clinica",
    "necessidade_do_sintoma",
    "necessidade_vaga",
    "pergunta_da_ficha",
    "relata_sintoma",
    "resposta_numerada",
]

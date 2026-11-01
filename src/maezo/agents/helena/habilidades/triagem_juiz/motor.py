"""MOTOR da triagem com juiz (teste de mesa, rodada 7; DL a registrar): a TABELA e' o piso e o MODELO julga a
conversa inteira e so' SOBE a gravidade. Porta fiel de `roda_proposta.py` v7 do teste de mesa (sem o simulador):
anotador de fatos (modelo, JSON) -> `decidir` (funcao pura sobre `tabela.yaml`) -> perguntas (no maximo 3) ->
juiz (modelo) sobre a conversa inteira. A chamada ao modelo e' INJETADA (`Chamar`): este modulo nao faz rede.

Seis saidas: 192 | PS | HOJE | CONVERSA | EQUIPE | FORA. 192 so' pela tabela (lista de 12 itens); o juiz sobe no
maximo ate' PS. Experimento SO' no dev, atras da chave `MAEZO_HELENA_TRIAGEM_JUIZ` e da allowlist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any, cast

import yaml

#: (sistema, texto, max_tokens) -> texto do modelo. Falha = excecao (quem chama decide o caminho seguro).
Chamar = Callable[[str, str, int], Awaitable[str]]


@cache
def _tabela() -> Any:
    from maezo.agents import resolve_spec_agents_dir  # import tardio: evita ciclo no import do pacote

    caminho = resolve_spec_agents_dir() / "helena" / "triagem_juiz" / "tabela.yaml"
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


TAB: Any = _tabela()
MAX_PERGUNTAS = 3
SEV = {k: int(v) for k, v in TAB["gravidade"].items()}
SINAIS = TAB["sinais"]
LISTA192 = {x["sinal"]: x for x in TAB["lista_192"]}
QUEIXAS = TAB["queixas"]
R = TAB["regras"]
GERAIS = TAB["sinais_gerais"]
ORDEM_GERAIS = TAB.get("ordem_gerais_por_queixa", {})
ADULTOS = ("adulto", "idoso", "gestante")
# Mesmo fato dito com palavras de outro público (o anotador pode usar um nome ou outro): vale o sinal do público.
ALIAS = {
    "crianca": {
        "crianca_sonolenta": ["confusao_ou_sonolencia"],
        "crianca_vomita_tudo": ["vomitos_persistentes"],
        "crianca_respiracao_rapida": ["falta_de_ar", "crianca_dificuldade_respirar"],
        "crianca_tiragem_ou_estridor": ["falta_ar_forte"],
        "crianca_nao_bebe": ["desidratacao"],
        "sangue_fezes_crianca": ["sangue_fezes_ou_vomito"],
        "crianca_manchas_roxas": [],
        "convulsao_recente": ["convulsao_agora"],
    },
    "bebe": {
        "crianca_sonolenta": ["confusao_ou_sonolencia"],
        "crianca_respiracao_rapida": ["falta_de_ar", "crianca_dificuldade_respirar"],
        "crianca_tiragem_ou_estridor": ["falta_ar_forte"],
        "crianca_nao_bebe": ["desidratacao"],
        "bebe_febre_ou_hipotermia": ["febre", "febre_alta"],
        "bebe_pus_ouvido_urina_escura_sangue_fezes": [
            "sangue_fezes_crianca",
            "sangue_fezes_ou_vomito",
            "secrecao_ouvido",
        ],
        "convulsao_recente": ["convulsao_agora"],
    },
    "adulto": {"confusao_ou_sonolencia": ["confusao_subita"], "sinais_choque": []},
}
CRIANCAS = ("crianca", "crianca_5_12")

CONDICOES = TAB["condicoes"]


def _json(txt: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("sem JSON")
    v = json.loads(m.group(0))
    if not isinstance(v, dict):
        raise ValueError("JSON nao e objeto")
    return v


# ------------------------------------------------------------------------------------------------ anotador (extrator)
def _prompt_extrator() -> str:
    sinais = []
    for k, v in SINAIS.items():
        if v.get("derivado"):
            continue
        linha = f"- {k}: {v['desc']}"
        if v.get("fala"):
            linha += f" | jeitos de dizer: {v['fala']}"
        sinais.append(linha)
    queixas = "\n".join(f"- {k}: {v['desc']}" for k, v in QUEIXAS.items())
    return f"""Você é um ANOTADOR de fatos de conversas de saúde no WhatsApp (português do Brasil, escrita informal, com erros).
Você NÃO decide gravidade, NÃO orienta e NÃO diagnostica: só anota o que a pessoa disse, em JSON.

Regras:
- Anote só o que a pessoa ESCREVEU (mensagens e respostas). Não deduza sinais que ela não disse.
- Separe: afirmado (presente AGORA ou nesta doença atual) | negado ("não", "sem", "nenhum", "nada disso") | duvida (não sabe, "acho que",
  "mais ou menos", resposta que não responde). História antiga ou de outra pessoa ("meu avô teve convulsão anos atrás") vai em historia.
- QUANDO (teste do dono, 01/11): desmaio, convulsão, falta de ar forte, dor no peito, sangramento forte, fala enrolada, boca torta,
  fraqueza de um lado e "não acorda" só são afirmado se aconteceram HOJE ou estão acontecendo. Se aconteceram em outro dia e já
  passaram ("semana passada", "há 5 dias", "na segunda"), NÃO anote em sinais: anote em historia (ex.: "desmaio há dias, já passou").
- LEIA A RESPOSTA DO JEITO DA PESSOA. Ela responde com as palavras dela; traduza para o sinal perguntado:
  "nenhuma" = negado; "só a febre" = febre afirmado e os outros perguntados negados; "a 2 sim" = o 2º item afirmado;
  "tá caidinho", "molinho", "dá pra acordar mas volta a dormir", "só quer dormir", "largadinho" = sonolento AFIRMADO
  (sonolento NÃO é "não acorda"); "tá respirando rápido/puxando o ar" = respiração rápida afirmado;
  "acho que não" = negado com dúvida leve -> negado; "não reparei"/"não sei" = duvida.
- Se a assistente perguntou sobre um sinal e a pessoa respondeu, anote a resposta nesse sinal (mesmo que seja "não").
  Pergunta escrita do jeito "Consegue/Está conseguindo X?": resposta "não" = o sinal do problema AFIRMADO (ex.: "Está conseguindo beber?"
  "não" -> crianca_nao_bebe/desidratacao afirmado). "Já voltou ao normal?" "não" -> nao_voltou_ao_normal afirmado.
- Não confunda (os sinais do 192 têm "NÃO inclui" na descrição: respeite). Objeto engolido (moeda, pilha) NÃO é remédio/produto.
  "Muita falta de ar" sem não conseguir falar/lábios roxos é falta_de_ar, não falta_ar_forte. Sangue na urina NÃO é sangramento_forte.
- "paciente" é quem tem o problema (a própria pessoa ou outra: filho, mãe, avó...). paciente_quem: "propria" | "outra_pessoa" | "desconhecido".
- faixa_etaria: "bebe_menos_2m" (recém-nascido, dias ou semanas, até 2 meses), "crianca_2m_5a" (2 meses até 4 anos), "crianca_5_12",
  "adolescente" (13–17), "adulto" (18–59), "idoso" (60+ ou avó/avô), "crianca_idade_desconhecida" (bebê/filho/criança/neném sem idade),
  "adulto_idade_desconhecida" (pai, mãe, marido, esposa, tio sem idade), "desconhecida" (sem pista). Use a idade dita em qualquer resposta.
- gestacao: "gestante" se a paciente está grávida; "pos_parto" se teve bebê há até ~45 dias; "nao" se dito; senão "desconhecido".
- intensidade_0_10: número dito; "insuportável"/"a pior da vida" = 10; "muito forte" = 8; "forte" = 7; "moderada" = 5; "fraca/leve" = 3; senão null.
- tem_dor: true se a queixa envolve dor.
- intencao (uma): "queixa" (relata sintoma/problema de saúde atual, inclusive medição em casa agora: glicemia, pressão, temperatura);
  "duvida_simples" (pergunta geral "o que é X", vacina, cuidado, sem sintoma atual); "qual_profissional" (quer saber que médico/profissional
  procurar); "exame_ou_burocracia" (o que significa resultado de exame de laboratório/imagem, pedido de exame, agendamento, receita,
  carteirinha, plano); "pede_remedio" (pede remédio/pomada/dose); "fora_de_triagem" (assunto não agudo fora do acolhimento).
  Se relata sintoma atual E pede remédio/profissional, use a intenção do pedido e anote também as queixas.
- queixas: lista das queixas ATUAIS do paciente, só com estes ids (pode haver mais de uma):
{queixas}
  Mensagem vaga ("tô fraco", "passando mal", "bebê amuado", "não tá bem") sem outra queixa clara -> ["mal_estar"].
  Queda de pessoa não idosa -> "trauma". Sintoma crônico/antigo (meses/anos, sem piora aguda) -> "fora_da_lista". Lista vazia se não há sintoma.
- risco_suicidio: "imediato" (tentou agora/recente, ou plano + intenção agora, ou sinais de despedida: carta de despedida,
  "já me despedi", doou/organizou as coisas, separou remédios, arma ou corda), "medio" (pensa e tem plano, sem intenção agora),
  "baixo" (pensamentos de morte sem plano), "nao".
- sinais: objeto {{nome: "afirmado"|"negado"|"duvida"}} só com sinais mencionados (ou perguntados), usando SÓ estes nomes:
{chr(10).join(sinais)}
- sangramento_presente: true se há sangramento visível de ferimento/lesão agora.
- condicoes: doenças de base / remédio contínuo do PACIENTE ditos em qualquer mensagem ou resposta, só com estes ids
  (lista vazia se não foi dito nada; ["nenhuma"] se disse que não tem):
{chr(10).join(f"  - {k}: {v}" for k, v in CONDICOES.items())}
- pressao_texto: a pressão medida exatamente como a pessoa escreveu ("15x9", "180 por 110", "16/10"), ou null. NÃO anote pressao_muito_alta.

Responda SÓ com o JSON, neste formato:
{{"intencao": "...", "queixas": [], "queixa_texto": "...", "paciente_quem": "propria|outra_pessoa|desconhecido",
 "faixa_etaria": "...", "idade_texto": null, "gestacao": "gestante|pos_parto|nao|desconhecido", "duracao": null,
 "intensidade_0_10": null, "tem_dor": false, "sangramento_presente": false, "pressao_texto": null, "condicoes": [], "sinais": {{}}, "historia": [], "risco_suicidio": "nao"}}"""


PROMPT_EXTRATOR = _prompt_extrator()


def transcricao(msgs: list[str], qa: list[dict[str, Any]]) -> str:
    linhas = ["Mensagens da pessoa:"] + [f"PESSOA: {m}" for m in msgs]
    for x in qa:
        linhas += [f"ASSISTENTE (pergunta): {x['pergunta']}", f"PESSOA (resposta): {x['resposta']}"]
    return "\n".join(linhas)


def normalizar(f: dict[str, Any]) -> dict[str, Any]:
    qs = f.get("queixas") or []
    f["queixas"] = list(
        dict.fromkeys(
            [q for q in qs if q in QUEIXAS] + (["outra_aguda"] if any(q not in QUEIXAS for q in qs) else [])
        )
    )
    f["sinais"] = {
        k: v
        for k, v in (f.get("sinais") or {}).items()
        if k in SINAIS and v in ("afirmado", "negado", "duvida")
    }
    if f["sinais"].get("crianca_dificuldade_respirar") == "afirmado":
        f["sinais"].setdefault("crianca_respiracao_rapida", "afirmado")
    f["condicoes"] = [c for c in (f.get("condicoes") or []) if c in CONDICOES]
    # pressão muito alta (>= 18 por 11): conta feita aqui, não pelo modelo (rodada 3)
    f["sinais"].pop("pressao_muito_alta", None)
    nums = [int(x) for x in re.findall(r"\d+", str(f.get("pressao_texto") or ""))][:2]
    if len(nums) == 2:
        sis, dia = [n * 10 if n < 30 else n for n in nums]
        f["sinais"]["pressao_muito_alta"] = "afirmado" if (sis >= 180 or dia >= 110) else "negado"
    try:
        f["intensidade_0_10"] = (
            None if f.get("intensidade_0_10") in (None, "") else int(float(f["intensidade_0_10"]))
        )
    except (TypeError, ValueError):
        f["intensidade_0_10"] = None
    return f


# ------------------------------------------------------------------------------------------------ leitura da resposta (rodada 3)
PROMPT_LEITOR = """Você lê UMA resposta de WhatsApp a UMA pergunta de sim ou não feita por uma assistente de saúde.
Diga qual é a resposta LITERAL à pergunta, como se a pessoa respondesse só "sim" ou "não".
- SIM: a resposta à pergunta é sim (em pergunta com "ou", sim para qualquer parte).
  Ex.: "Está com febre?" "tá sim, 38" -> SIM. "Está conseguindo mamar?" "mama sim, normal" -> SIM.
- NAO: a resposta à pergunta é não ("não", "nada disso", "acho que não", "que eu saiba não").
  Ex.: "Está conseguindo mamar?" "não quis as últimas mamadas" -> NAO.
- NAO_SEI: não sabe, não reparou, ou a resposta não responde à pergunta.
- Pergunta se algo foi HOJE (ou é agora): resposta que diz que foi em outro dia ("ontem", "semana passada", "há 5 dias",
  "entre segunda e sexta") -> NAO. Ex.: "Esse desmaio foi hoje?" "isso foi na semana passada, hoje ela está em casa" -> NAO.
Responda só com uma palavra: SIM, NAO ou NAO_SEI."""

# ------------------------------------------------------------------------------------------------ 5 sinais claros
RE5 = {k: re.compile(v, re.I) for k, v in TAB["cinco_sinais_regex"].items()}
RE_ANULA = re.compile(TAB["cinco_sinais_anula"], re.I)


def cinco_sinais(msgs: list[str]) -> str | None:
    for m in msgs:
        if RE_ANULA.search(m):
            continue
        for k, r in RE5.items():
            if r.search(m):
                return str(k)
    return None


# ------------------------------------------------------------------------------------------------ público
FAIXA_PUB = {
    "bebe_menos_2m": ["bebe"],
    "crianca_2m_5a": ["crianca"],
    "crianca_5_12": ["crianca_5_12"],
    "adolescente": ["adulto"],
    "adulto": ["adulto"],
    "idoso": ["idoso"],
    "crianca_idade_desconhecida": ["bebe", "crianca", "crianca_5_12"],
    "adulto_idade_desconhecida": ["adulto", "idoso"],
}


def publicos_possiveis(f: dict[str, Any]) -> list[str]:
    if f.get("gestacao") in ("gestante", "pos_parto"):
        return ["gestante"]
    fx = f.get("faixa_etaria") or "desconhecida"
    if fx in FAIXA_PUB:
        return FAIXA_PUB[fx]
    if f.get("paciente_quem") == "propria":
        return ["adulto", "idoso"]
    return ["adulto", "idoso", "crianca", "crianca_5_12", "bebe"]


# ------------------------------------------------------------------------------------------------ decisão (pura)
def _r(saida: str, regra: str, fonte: Any, prov: Any = False, **extra: Any) -> dict[str, Any]:
    d = {"saida": saida, "regra": regra, "fonte": fonte, "provisoria_lado_seguro": bool(prov)}
    d.update(extra)
    return d


def _grupo(pub: str) -> str:
    return "adulto" if pub in ADULTOS else ("crianca" if pub in CRIANCAS else "bebe")


def blocos(pub: str, q: str) -> list[tuple[str, Any, int]]:
    """Blocos (nome, bloco, camada) da queixa para o público. camada 0 = bloco da queixa."""
    out = []
    if pub in ADULTOS:
        if q in R["adulto"]:
            out.append((f"adulto.{q}", R["adulto"][q], 0))
        if pub in ("idoso", "gestante") and q in R[pub] and not q.startswith("_"):
            out.append((f"{pub}.{q}", R[pub][q], 0))
    elif pub == "crianca":
        if q in R["crianca"]:
            out.append((f"crianca.{q}", R["crianca"][q], 0))
        elif q in R["crianca_usa_adulto"]:
            out.append((f"adulto.{q}(crianca)", R["adulto"][q], 0))
    elif pub == "crianca_5_12":
        if q in R["crianca_5_12"]["adulto"]:
            out.append((f"adulto.{q}(5_12)", R["adulto"][q], 0))
        elif q in R["crianca_5_12"]["crianca"]:
            out.append((f"crianca.{q}(5_12)", R["crianca"][q], 0))
    elif pub == "bebe":
        if q in R["bebe"]:
            out.append((f"bebe.{q}", R["bebe"][q], 0))
        elif q in R["bebe_usa_adulto"]:
            out.append((f"adulto.{q}(bebe)", R["adulto"][q], 0))
    return out


def padroes_ativos(f: dict[str, Any], pub: str) -> list[tuple[str, Any, int]]:
    S = f.get("sinais", {})  # noqa: N806 - nome do teste de mesa
    presentes = (
        set(f["queixas"])
        | {k for k, v in S.items() if v == "afirmado"}
        | {"cond_" + c for c in f.get("condicoes", [])}
    )
    out = []
    for p in TAB.get("padroes", []):
        if any(all(x in presentes for x in comb) for comb in p["gatilho_qualquer"]):
            regras = [r for r in p["regras"] if not r.get("so_publico") or pub in r["so_publico"]]
            out.append((f"padrao.{p['nome']}", {"sinais": regras, "_padrao": True}, 0))
    return out


def gerais_do_publico(pub: str) -> list[Any]:
    g = list(GERAIS[_grupo(pub)])
    if pub == "idoso":
        g += GERAIS["idoso"]
    return g


def avaliar(f: dict[str, Any], estado: dict[str, Any], pub: str) -> tuple[dict[str, Any], list[Any]]:
    """-> (melhor saída, candidatos [(sev_alvo, camada, ordem, sinal, texto, regra)])"""
    S = f.get("sinais", {})  # noqa: N806 - nome do teste de mesa
    duv = estado.get("duvidas", {})
    feitos = [p["sinal"] for p in estado.get("perguntas", [])]
    melhor: dict[str, Any] | None = None
    cands: list[Any] = []
    contador = [0]

    def sobe(r: dict[str, Any]) -> None:
        nonlocal melhor
        if melhor is None or SEV[r["saida"]] > SEV[melhor["saida"]]:
            melhor = r

    def st(sn: str) -> Any:
        if sn == "disuria_grupo_risco" and pub in ("idoso", "gestante", "crianca", "crianca_5_12", "bebe"):
            return "afirmado"
        v = S.get(sn)
        if v != "afirmado":
            for alt in ALIAS.get(_grupo(pub), {}).get(sn, []):
                if S.get(alt) == "afirmado":
                    return "afirmado"
        return v

    def candidato(sev: int, camada: int, ordem: int, sn: str, regra_nome: str) -> None:
        txt = SINAIS[sn].get("pergunta")
        if not txt:
            return
        cands.append((sev, camada, ordem, sn, txt, regra_nome))

    def aplicar(nome_b: str, b: Any, camada: int, q: str, perguntavel: bool = True) -> None:
        for regra in b.get("sinais", []):
            contador[0] += 1
            ordem = contador[0] - (1000 if b.get("_padrao") else 0)  # padrão da fonte vem antes (causa 2)
            if camada == 1 and q in ORDEM_GERAIS:
                lst = ORDEM_GERAIS[q]
                ordem = lst.index(regra.get("sinal")) if regra.get("sinal") in lst else 100 + contador[0]
            if regra.get("so_pos_parto") and f.get("gestacao") != "pos_parto":
                continue
            if regra.get("so_se") and st(regra["so_se"]) != "afirmado":
                continue
            prov = regra.get("provisoria_lado_seguro")
            alvo = regra.get("saida")
            extra = {"ligar_190": True} if regra.get("ligar_190") else {}
            cam = camada
            if regra.get("perguntar_em") is not None:
                if q in regra["perguntar_em"]:
                    cam, ordem = (
                        0,
                        ordem - 500,
                    )  # alerta do público ligado à queixa: vem antes (ex.: gestante + dor de cabeça)
                else:
                    pass
            pode_perguntar = (
                perguntavel
                and not (cam > 0 and alvo == "192")
                and not (regra.get("perguntar_em") is not None and q not in regra["perguntar_em"])
            )
            if "todos" in regra:
                comps = regra["todos"]
                stats = [st(c) for c in comps]
                if all(s == "afirmado" for s in stats):
                    sobe(_r(alvo, f"{nome_b}.{'+'.join(comps)}", regra["fonte"], prov, **extra))
                    continue
                if any(s == "negado" for s in stats):
                    continue
                pend = [c for c, s in zip(comps, stats, strict=True) if s != "afirmado"]
                sn = pend[0]
                n_duv = max(duv.get(sn, 0), 1 if st(sn) == "duvida" else 0)
                if n_duv >= 2:
                    if SEV[alvo] >= SEV["HOJE"]:
                        sobe(_r("EQUIPE", f"duvida_persistente.{sn}", TAB["duvida_persistente"]["fonte"]))
                    continue
                if pode_perguntar:
                    candidato(SEV[alvo], cam, ordem, sn, f"{nome_b}.{'+'.join(comps)}")
                continue
            sn = regra["sinal"]
            if (
                SINAIS[sn].get("tipo") == "intensidade"
                and nome_b.startswith("gerais.")
                and QUEIXAS.get(q, {}).get("sem_intensidade_geral")
            ):
                continue
            if SINAIS[sn].get("tipo") == "intensidade":
                v = f.get("intensidade_0_10")
                faixas = sorted(((int(k), s) for k, s in regra["faixas"].items()), reverse=True)
                if nome_b.startswith("gerais.") and QUEIXAS.get(q, {}).get("dor_local"):
                    faixas = [
                        (lim, "HOJE" if SEV[s] > SEV["HOJE"] else s) for lim, s in faixas
                    ]  # dor forte local: no máximo HOJE (rodada 4)
                if v is not None:
                    for lim, s in faixas:
                        if v >= lim:
                            sobe(_r(s, f"{nome_b}.intensidade>={lim}", regra["fonte"], prov))
                            break
                    continue
                if not f.get("tem_dor"):
                    continue
                alvo = faixas[0][1]
                if duv.get(sn, 0) >= 2:
                    continue
                if pode_perguntar:
                    candidato(SEV[alvo], cam, ordem, sn, f"{nome_b}.intensidade")
                continue
            status = st(sn)
            if status == "negado" and b.get("_padrao") and sn not in estado.get("lidos", {}):
                status = None  # rodada 4: pergunta ligada à condição só vale negada por resposta direta (o anotador deduzia "não")
            if status == "afirmado":
                sobe(_r(alvo, f"{nome_b}.{sn}", regra["fonte"], prov, **extra))
                continue
            if status == "negado":
                continue
            if regra.get("so_perguntar_se") and not f.get(regra["so_perguntar_se"]):
                continue
            if sn == "sangramento_forte" and not f.get("sangramento_presente"):
                continue
            n_duv = max(duv.get(sn, 0), 1 if status == "duvida" else 0)
            if n_duv >= 2:
                if SEV[alvo] >= SEV["HOJE"]:
                    sobe(_r("EQUIPE", f"duvida_persistente.{sn}", TAB["duvida_persistente"]["fonte"]))
                continue
            if pode_perguntar:
                candidato(SEV[alvo], cam, ordem, sn, f"{nome_b}.{sn}")

    # risco de suicídio
    rs = f.get("risco_suicidio")
    if rs == "imediato" or S.get("risco_suicidio_imediato") == "afirmado":
        sobe(_r("192", "lista_192.item8+CVV188", LISTA192["risco_suicidio_imediato"]["fonte"]))
    elif rs == "medio" or S.get("risco_suicidio_medio") == "afirmado":
        sobe(
            _r(
                "HOJE",
                "saude_mental.risco_suicidio_medio+CVV188",
                "CAB28v2 §2.23, p. 231 (médio risco) + CVV 188",
            )
        )
    elif rs in ("baixo", "presente_nao_imediato"):
        x = TAB["risco_suicidio_nao_imediato"]
        sobe(_r(x["saida"], "risco_suicidio_baixo+CVV188", x["fonte"], True))
    # lista do 192 (afirmado agora)
    for nome, it in LISTA192.items():
        if nome == "gestante_convulsao_ou_sangramento_intenso" and pub != "gestante":
            continue
        if nome == "idoso_desmaio_ou_engasgo" and pub != "idoso":
            continue
        if S.get(nome) == "afirmado":
            sobe(_r("192", f"lista_192.item{it['item']}", it["fonte"]))
    if "dor_toracica" in f["queixas"] and any(
        S.get(s) == "afirmado" for s in ("suor_frio", "falta_de_ar", "desmaio")
    ):
        sobe(_r("192", "lista_192.item4", LISTA192["dor_peito_com_sinal_grave"]["fonte"]))
    if pub == "idoso" and S.get("desmaio") == "afirmado":
        sobe(_r("192", "lista_192.item12", LISTA192["idoso_desmaio_ou_engasgo"]["fonte"]))

    queixas = list(f["queixas"])
    it = f.get("intencao") or "duvida_simples"
    if not queixas and it == "queixa":
        queixas = ["mal_estar"]  # mensagem vaga (causa 3)
    if not queixas:
        chave = {
            "pede_remedio": "pede_remedio_sem_queixa",
            "qual_profissional": "qual_profissional_fora",
        }.get(it, it)
        x = TAB["intencoes"].get(chave, TAB["intencoes"]["fora_de_triagem"])
        sobe(_r(x["saida"], f"intencao.{chave}", x["fonte"]))
        # sem queixa: só sinais (gerais) ditos podem subir; sem perguntas
        for g in gerais_do_publico(pub):
            if g.get("sinal") and st(g["sinal"]) == "afirmado":
                sobe(
                    _r(
                        g["saida"],
                        f"gerais.{_grupo(pub)}.{g['sinal']}",
                        g["fonte"],
                        g.get("provisoria_lado_seguro"),
                    )
                )
        # rodada 4: condição de base dita (ex.: dengue em curso) liga as perguntas dela mesmo sem queixa
        for nome_b, b, camada in padroes_ativos(f, pub):
            aplicar(nome_b, b, camada, "_sem_queixa")
        return cast("dict[str, Any]", melhor), cands  # `sobe` acima sempre preenche

    so_lista_fora = True
    for q in queixas:
        if q == "queda" and pub != "idoso":
            q = "trauma"
        info = QUEIXAS.get(q, {"lista": "fora_aguda"})
        bl = blocos(pub, q) + padroes_ativos(f, pub)
        tem_bloco = any(c == 0 and "sem_sinal" in b and not n.startswith("padrao.") for n, b, c in bl)
        for nome_b, b, camada in bl:
            if "sem_sinal" in b:
                ss = b["sem_sinal"]
                sobe(_r(ss["saida"], f"{nome_b}.sem_sinal", ss["fonte"], ss.get("provisoria_lado_seguro")))
            aplicar(nome_b, b, camada, q)
        # camada 1: sinais gerais do público (criança/bebê: sempre perguntáveis; adulto: só sem bloco próprio)
        # assunto não agudo fora da lista: só confere o que foi dito (sem perguntas)
        pergunta_gerais = info["lista"] != "fora" and ((pub not in ADULTOS) or not tem_bloco)
        aplicar(
            f"gerais.{_grupo(pub)}", {"sinais": gerais_do_publico(pub)}, 1, q, perguntavel=pergunta_gerais
        )
        if pub in ("idoso", "gestante"):
            aplicar(f"{pub}._todas", R[pub]["_todas"], 1, q, perguntavel=(pub == "gestante" or not tem_bloco))
        if info.get("vaga") and "_queixa" not in feitos and not estado.get("detalhado"):
            # teste do dono (01/11/2026): "o que está sentindo?" so' quando a pessoa ainda nao contou
            cands.append(
                (
                    SEV["192"],
                    -1,
                    0,
                    "_queixa",
                    "O que você (ou a pessoa) está sentindo? Pode me contar um pouco mais?",
                    "mensagem_vaga",
                )
            )
        if not tem_bloco:
            fs = TAB["fora_sem_bloco"]
            if pub == "bebe":
                x, nome = fs["bebe"], "bebe"
            elif info["lista"] == "secao3":
                x, nome = (
                    (fs["fora_aguda"], "fora_aguda")
                    if pub in ADULTOS
                    else (fs["adulto_lista_sem_bloco_para_idade"], "sem_fonte_para_idade")
                )
            elif info["lista"] == "fora_aguda":
                x, nome = fs["fora_aguda"], "fora_aguda"
            else:
                x, nome = fs["fora"], "fora"
            sobe(_r(x["saida"], f"sem_bloco.{nome}.{q}", x["fonte"], True))
        if info["lista"] != "fora":
            so_lista_fora = False

    # "qual profissional": queixa da lista e leve -> CONVERSA com o profissional; não aguda/exame -> FORA com o profissional
    if (
        it == "qual_profissional"
        and melhor is not None
        and melhor["saida"] in ("CONVERSA", "FORA")
        and not melhor["regra"].startswith(("duvida_persistente", "risco_suicidio"))
    ):
        if melhor["saida"] == "CONVERSA" and not so_lista_fora:
            x = TAB["intencoes"]["qual_profissional"]
            melhor = _r("CONVERSA", "intencao.qual_profissional (" + melhor["regra"] + ")", x["fonte"])
        else:
            x = TAB["intencoes"]["qual_profissional_fora"]
            melhor = _r("FORA", "intencao.qual_profissional_fora (" + melhor["regra"] + ")", x["fonte"])
    if melhor is None:
        x = TAB["duvida_persistente"]
        melhor = _r("EQUIPE", "sem_regra(lado_seguro)", x["fonte"], True)
    if it == "exame_ou_burocracia" and SEV[melhor["saida"]] < SEV["HOJE"]:
        x = TAB["intencoes"]["exame_ou_burocracia"]
        melhor = _r("FORA", "intencao.exame_ou_burocracia (" + melhor["regra"] + ")", x["fonte"])
        cands = []
    return melhor, cands


def _assinatura(m: dict[str, Any], cands: list[Any], sev_atual: int) -> tuple[Any, frozenset[str]]:
    c = [x for x in cands if x[0] > SEV[m["saida"]] and not x[3].startswith("_")]
    return (m["saida"], frozenset(x[3] for x in c))


QUEIXAS_COND = {
    "diarreia_vomito",
    "dor_abdominal",
    "febre",
    "mal_estar",
    "trauma",
    "queda",
    "falta_de_ar",
    "tontura",
    "cefaleia",
    "inchaco_pernas",
    "ferimento",
    "confusao",
}


def _feito(tok: str, feitos: list[str]) -> bool:
    return any(tok in x for x in feitos)


# rodada 6 (ajuste 5): item do 192 que o anotador marcou sem resposta direta -> confirmar com 1 pergunta direta
CONF192 = {
    "falta_ar_forte": None,
    "inchaco_boca_com_falta_ar": None,
    "idoso_desmaio_ou_engasgo": "Esse desmaio (perder os sentidos de verdade) foi hoje, ou ela(e) está engasgada sem conseguir respirar?",
    "ingeriu_remedio_ou_produto": "Engoliu remédio acima da dose, remédio de outra pessoa ou algum produto tóxico?",
}
ITEM_SINAL = {f"lista_192.item{x['item']}": x["sinal"] for x in TAB["lista_192"]}
# rodada 6 (ajuste 3): palavra solta dos 5 sinais vira aviso -> pergunta direta antes do 192 (só os que mais erram)
REGEX_CONFIRMA = {
    "desmaiou": ("desmaio", "Perdeu os sentidos de verdade, e isso foi hoje?"),
    "ficou_roxo": (
        "falta_ar_forte",
        "Está com os lábios roxos ou com tanta falta de ar que não consegue falar uma frase inteira?",
    ),
}


RE_HUMOR = re.compile(
    r"trist|deprimid|depress|desanimad|sem vontade|chorand|choro f|luto|n[aã]o (consig\w*|consegue|t[aá]) dormi|"
    r"sem dormir|ins[oô]nia|n[aã]o dorme|rem[eé]dio (pra|para|de) dormir|calmante|ansios|ang[uú]sti|isolad|"
    r"n[aã]o sai (mais )?do quarto|perdeu o gosto|desesper",
    re.I,
)
PERGUNTA_SUICIDIO = (
    "Uma pergunta importante: tem falado em morrer, em se machucar, ou que seria melhor não estar aqui?"
)


def precisa_pergunta_suicidio(f: dict[str, Any], msgs: list[str], qa: list[dict[str, Any]]) -> bool:
    """Rodada 7 (ajuste 1): humor/sono -> pergunta de pensamento de morte, se ainda não foi dito nem perguntado."""
    if f.get("risco_suicidio") not in (None, "", "nao"):
        return False
    if any(x["sinal"] == "_suicidio" for x in qa):
        return False
    texto = " ".join(msgs)
    return "saude_mental" in f.get("queixas", []) or bool(RE_HUMOR.search(texto))


def decidir(f: dict[str, Any], estado: dict[str, Any]) -> dict[str, Any]:
    """Função pura: fatos + estado -> {'tipo': 'saida'|'pergunta', ...}."""
    feitos = [p["sinal"] for p in estado.get("perguntas", [])]
    pubs = publicos_possiveis(f)
    avals = {p: avaliar(f, estado, p) for p in pubs}
    # público: se a idade é desconhecida, usa o que dá a saída mais grave (lado seguro)
    pub = max(pubs, key=lambda p: (SEV[avals[p][0]["saida"]], -pubs.index(p)))
    melhor, cands = avals[pub]
    if melhor["saida"] == "192" and len(feitos) < MAX_PERGUNTAS:
        sn = ITEM_SINAL.get(melhor["regra"])
        if sn in CONF192 and sn not in estado.get("lidos", {}) and sn not in feitos:
            txt = CONF192[sn] or SINAIS[sn]["pergunta"]
            return {
                "tipo": "pergunta",
                "sinal": sn,
                "texto": txt,
                "motivo": "confirma_192." + melhor["regra"],
                "publico": pub,
            }
    cands = [c for c in cands if c[0] > SEV[melhor["saida"]]] if melhor["saida"] != "192" else []
    # idade/público desconhecido e muda a regra -> 1ª pergunta (causa 4)
    if len(pubs) > 1 and not _feito("_idade", feitos) and SEV[melhor["saida"]] < SEV["192"]:
        assin = {_assinatura(*avals[p], 0) for p in pubs}
        if len(assin) > 1:
            txt = (
                "Para quem é e qual a idade?"
                if f.get("paciente_quem") in (None, "desconhecido")
                else "Qual a idade de quem está com esse problema?"
            )
            cands.append(
                (SEV["192"] + 1, -2, 0, "_idade", txt, "idade->muda a regra (" + ",".join(pubs) + ")")
            )
    # rodada 4: doença de base / remédio contínuo (adulto com dor, vômito, febre, trauma etc.), junto com a idade se ela faltar
    # rodada 5: desligada (a pergunta aberta do juiz cobre doença de base e remédio)
    if (
        not estado.get("sem_condicao")
        and SEV[melhor["saida"]] < SEV["192"]
        and not _feito("_condicao", feitos)
        and not f.get("condicoes")
        and any(p in ADULTOS for p in pubs)
        and (f.get("tem_dor") or set(f["queixas"]) & QUEIXAS_COND)
    ):
        ci = [c for c in cands if c[3] == "_idade"]
        if ci:
            cands.remove(ci[0])
            cands.append(
                (
                    SEV["192"] + 1,
                    -2,
                    0,
                    "_idade_condicao",
                    ci[0][4][:-1] + ", e tem alguma doença ou usa remédio todo dia?",
                    ci[0][5] + "+condicao_de_base",
                )
            )
        else:
            cands.append(
                (
                    SEV["192"] + 1,
                    -2,
                    1,
                    "_condicao",
                    "Tem alguma doença (como diabetes ou do coração) ou usa remédio todo dia, como para afinar o sangue?",
                    "condicao_de_base",
                )
            )
    # um sinal no máximo 2 vezes (pergunta + 1 repergunta); _idade, _condicao e _queixa só 1 vez
    cands = [c for c in cands if feitos.count(c[3]) < 2 and not (c[3].startswith("_") and c[3] in feitos)]
    if cands and len(feitos) < MAX_PERGUNTAS:
        cands.sort(key=lambda c: (-c[0], c[1], c[2]))
        sev, _, _, sn, txt, regra = cands[0]
        if sn in feitos:
            txt = "Desculpe, não entendi bem. " + txt
        return {"tipo": "pergunta", "sinal": sn, "texto": txt, "motivo": regra, "publico": pub}
    out = dict(melhor)
    out["tipo"] = "saida"
    out["publico"] = pub
    out["publicos_possiveis"] = pubs
    if out["saida"] == "CONVERSA":
        out["linha_emergencia"] = (
            "Se aparecer falta de ar forte, desmaio ou piora rápida, ligue 192."  # decisão (b)
        )
    return out


# ------------------------------------------------------------------------------------------------ juiz (rodada 5, opção B)
# O modelo julga a gravidade olhando a conversa inteira; a tabela vale como piso (o juiz só sobe).
LISTA192_TXT = "\n".join(f"  ({x['item']}) {x['texto']}" for x in TAB["lista_192"])

PROMPT_JUIZ_PERGUNTA = """Você é uma enfermeira experiente de classificação de risco que atende pelo WhatsApp de um plano de saúde no Brasil.
Leia a conversa e decida se precisa fazer UMA pergunta antes de classificar a gravidade.
- A pergunta deve ser a que mais pode MUDAR a gravidade do caso. Pense no que um bom profissional checaria para esta queixa:
  idade (se não foi dita e importa), doença de base ou remédio de uso contínuo, remédio novo ou trocado há pouco,
  há quanto tempo começou e se está piorando, e o sinal de alarme mais provável para esta queixa nesta pessoa.
- Em dor de cabeça nova, enjoo, tontura, vômito ou mal-estar sem causa clara, pense em exposição: outras pessoas da casa com o mesmo,
  aquecedor, gás ou fogão em lugar fechado, produto químico, alimento suspeito.
- Uma pergunta só, curta (até 35 palavras), em português simples de WhatsApp, sem termos técnicos. Pode juntar 2 ou 3 pontos numa frase.
- Não oriente, não diagnostique, não dê links.
- Responda null se não há queixa de saúde (administrativo, exame, receita, assunto fora de saúde) ou se a gravidade já está clara.
Responda SÓ com JSON: {"pergunta": "..." ou null}"""

PROMPT_JUIZ = f"""Você é uma enfermeira experiente de classificação de risco que atende pelo WhatsApp de um plano de saúde no Brasil
(referências: Caderno de Atenção Básica 28 do Ministério da Saúde, AIDPI, Cadernetas da Criança, da Gestante e da Pessoa Idosa, SAMU 192).
Leia a conversa INTEIRA (mensagens e respostas) e escolha UMA saída para a pessoa:
- 192: SÓ se o caso está nesta lista (fora dela, nunca 192):
{LISTA192_TXT}
  (item 11 só para gestante; item 12 só para pessoa idosa)
- PS: ir ao pronto-socorro agora (gestante: maternidade). Risco de vida, de perder um órgão, um membro ou a visão nas próximas horas,
  sem estar na lista do 192. Pense em quadros graves que se escondem atrás de queixas comuns: doença de base descompensada,
  infecção em quem tem defesa baixa (quimioterapia, transplante, corticoide ou imunossupressor, bebê pequeno), reação grave a remédio,
  piora depois de começar ou trocar um remédio, sintoma que piora dia após dia, sinal de sangramento interno, desidratação grave,
  sinais neurológicos, sinais de alerta da gravidez e do recém-nascido.
  Suspeita de problema que pode precisar de cirurgia de urgência (apendicite, torção, gravidez fora do útero, obstrução do intestino) é PS.
- HOJE: precisa ser visto por um profissional hoje (infecção que se espalha ou com febre, possível fratura, ferimento que precisa de cuidado,
  exposição que tem prazo para tratamento, objeto preso sem falta de ar, dor forte sem sinal de gravidade no corpo todo).
- CONVERSA: queixa leve sem sinal de alerta, que se cuida em casa com orientação, ou dúvida simples de saúde.
- EQUIPE: precisa de um profissional da equipe, mas não é urgente; ou falta informação e você não consegue decidir com segurança.
- FORA: não é para triagem por aqui: administrativo, plano, carteirinha, reembolso, resultado de exame, receita, pedido de remédio sem
  queixa aguda, qual especialista para problema antigo, assunto que não é de saúde.
Como julgar:
- Julgue o CONJUNTO: queixa, idade, gravidez, doenças e remédios, há quanto tempo, se está piorando. Uma palavra solta não basta:
  "roxo" de pancada não é falta de ar; "desmaiei de sono" não é desmaio; dor forte num lugar só, sem outro sinal, não é emergência.
- Em quem tem doença de base, defesa baixa, gravidez, bebê pequeno ou idoso frágil, o mesmo sintoma pode ser mais grave.
- Se há sinal de alarme possível e você está entre dois níveis, escolha o mais grave. Se o caso é leve e sem sinal, não exagere.
Calibre (erros comuns de exagero):
- Pergunta de qual especialista, exame, resultado, receita, remédio de uso contínuo, preparo de exame, sem queixa aguda = FORA (não EQUIPE).
- Queixa leve, sem nenhum sinal de alerta, que se cuida em casa = CONVERSA (com orientação e sinais para procurar), não EQUIPE nem HOJE.
- HOJE só com um sinal concreto que peça avaliação no dia. EQUIPE só quando precisa de um profissional e nenhuma outra saída serve.
- Uma palavra que soa grave fora do contexto ("desmaiei de sono", "roxo" de pancada ou de coleta, "infartei de rir") não conta.
- Se o caso parece de 192 mas não está claramente num item da lista, use PS.
- Coerência: se no motivo você suspeita de um quadro que precisa de pronto-socorro ou atendimento imediato, a saída é PS.
- Quando: um sinal que aconteceu em outro dia e já passou pesa menos que o mesmo sinal hoje; olhe como a pessoa está AGORA.
@@BLOCO_SINAL@@Em "explica", escreva para a pessoa, em português simples, UMA frase (até 25 palavras) com o que ela contou que levou à
saída (ex.: "Um desmaio numa pessoa de 70 anos com diabetes precisa ser visto por um médico."). Sem diagnóstico, sem termos técnicos,
sem dizer para onde ir e sem citar 192 nem SAMU.
Responda SÓ com JSON:
{{"saida": "192|PS|HOJE|CONVERSA|EQUIPE|FORA", "item_192": numero ou null, "motivo": "uma frase curta", "explica": "..."@@CAMPO_SINAL@@}}"""

RE_MOTIVO_PS = re.compile(
    r"pronto[- ]?socorro|\bPS\b|emerg[eê]ncia|atendimento imediato|urg[eê]ncia imediata|\bUPA\b|"
    r"apendicite|tor[cç][aã]o (de |do |testic|ovar)|ect[oó]pica|obstru[cç][aã]o intestinal|sepse|meningite|"
    r"infarto|embolia|trombose|\bAVC\b|cetoacidose|hemorragia|anafilax|intoxica[cç][aã]o|mon[oó]xido",
    re.I,
)
RE_NEGA = re.compile(r"\b(n[aã]o|sem|nem|dispensa|evitar)\b", re.I)


def sem_piso(regra: str) -> bool:
    """A tabela não tinha regra para o caso (ou só o nível padrão da queixa, fora bebê): o juiz decide sozinho."""
    return regra.startswith(("sem_bloco.", "intencao.", "sem_regra")) or (
        regra.endswith(".sem_sinal") and not regra.startswith("bebe.")
    )


def sinais_atencao(regra: str, saida: str) -> list[str]:
    """Rodada 6 (ajuste 2): regra de sinal abaixo do 192 = sinal de atenção (o juiz pode dizer que não está presente).
    Alarme duro (lista do 192, suicídio, dúvida persistente) não entra."""
    if saida == "192" or regra.startswith(
        ("lista_192", "duvida_persistente", "saude_mental", "risco_suicidio", "pre_modelo")
    ):
        return []
    sns = regra.rsplit(".", 1)[-1].split("+")
    return sns if all(x in SINAIS and SINAIS[x].get("tipo") != "intensidade" for x in sns) else []


def explicacao(bruto: object) -> str | None:
    """A frase do juiz para a pessoa (teste do dono, 01/11/2026: a saida diz o porque), ou `None`."""
    if not isinstance(bruto, str):
        return None
    frase = " ".join(bruto.split())
    if not 10 <= len(frase) <= 300 or re.search(r"https?:|www\.|\b192\b|samu", frase, re.I):
        return None
    return frase if frase.endswith((".", "!", "?")) else frase + "."


# ------------------------------------------------------------------------------------------------ chamadas ao modelo
async def extrair(chamar: Chamar, msgs: list[str], qa: list[dict[str, Any]]) -> dict[str, Any]:
    for _ in range(2):
        try:
            return normalizar(_json(await chamar(PROMPT_EXTRATOR, transcricao(msgs, qa), 1500)))
        except (ValueError, json.JSONDecodeError):
            continue
    raise RuntimeError("extrator sem JSON valido")


async def ler_resposta(chamar: Chamar, pergunta: str, resposta: str) -> str:
    out = (await chamar(PROMPT_LEITOR, f"Pergunta: {pergunta}\nResposta: {resposta}", 10)).upper()
    if "NAO_SEI" in out or "NÃO_SEI" in out:
        return "NAO_SEI"
    if "NAO" in out or "NÃO" in out:
        return "NAO"
    return "SIM" if "SIM" in out else "NAO_SEI"


async def juiz_pergunta(chamar: Chamar, msgs: list[str], qa: list[dict[str, Any]]) -> str | None:
    try:
        p = _json(await chamar(PROMPT_JUIZ_PERGUNTA, transcricao(msgs, qa), 200)).get("pergunta")
    except (ValueError, json.JSONDecodeError):
        return None
    return p.strip() if isinstance(p, str) and p.strip() else None


async def juiz(
    chamar: Chamar,
    msgs: list[str],
    qa: list[dict[str, Any]],
    sinais_tabela: list[str] | None = None,
    max_tokens: int = 300,
) -> dict[str, Any]:
    if sinais_tabela:
        desc = "; ".join(SINAIS[x]["desc"] for x in sinais_tabela)
        bloco = (
            'A tabela de regras marcou este sinal de alerta: "'
            + desc
            + '". Pela conversa, esse sinal está REALMENTE presente '
            "(a pessoa disse ou confirmou), ou foi um mal-entendido (resposta lida errado, palavra fora de "
            "contexto)?\n"
        )
        campo = ', "sinal_da_tabela_presente": true ou false'
    else:
        bloco, campo = "", ""
    sistema = PROMPT_JUIZ.replace("@@BLOCO_SINAL@@", bloco).replace("@@CAMPO_SINAL@@", campo)
    j: dict[str, Any] | None = None
    for _ in range(2):
        try:
            lido = _json(await chamar(sistema, transcricao(msgs, qa), max_tokens))
        except (ValueError, json.JSONDecodeError):
            continue
        if lido.get("saida") in SEV:
            j = lido
            break
    if j is None:
        raise RuntimeError("juiz sem saida valida")
    # 192 so' com item da lista; sem item valido vira PS
    try:
        bruto = j.get("item_192")
        item = int(str(bruto)) if bruto not in (None, "", "null") else None
    except (TypeError, ValueError):
        item = None
    if j["saida"] == "192" and item not in {x["item"] for x in TAB["lista_192"]}:
        j["saida"], j["rebaixado_192"] = "PS", True
    j["item_192"] = item
    return j


# ------------------------------------------------------------------------------------------------ um passo da conversa
def contexto_novo(msgs: list[str]) -> dict[str, Any]:
    """O estado da triagem (serializavel em JSON: vai na `coleta_contexto` da Helena entre os turnos)."""
    return {
        "msgs": [m for m in msgs if m.strip()],
        "qa": [],
        "estado": {"perguntas": [], "duvidas": {}, "sem_condicao": True, "lidos": {}},
        "f": None,
        "pre": None,
        "juiz_perguntou": False,
        "pendente": None,
    }


def _saida_192(regra: str) -> dict[str, Any]:
    return {"tipo": "saida", "saida": "192", "regra": regra, "cvv": "item8" in regra or "suicid" in regra}


async def passo(chamar: Chamar, ctx: dict[str, Any], resposta: str | None) -> dict[str, Any]:
    """Avanca a triagem UM turno. `resposta` = a fala da pessoa a pergunta pendente (`None` no 1o turno).
    Devolve {"tipo": "pergunta", "texto": ...} (a pendente fica em `ctx`) ou {"tipo": "saida", "saida": ...}."""
    msgs: list[str] = ctx["msgs"]
    qa: list[dict[str, Any]] = ctx["qa"]
    estado: dict[str, Any] = ctx["estado"]
    lidos: dict[str, str] = estado["lidos"]
    if ctx["f"] is None:
        pre = cinco_sinais(msgs)
        ctx["pre"] = pre
        if pre and pre not in REGEX_CONFIRMA:
            return _saida_192(f"pre_modelo.cinco_sinais.{pre}")
        f = await extrair(chamar, msgs, qa)
        estado["duvidas"] = {k: 1 for k, v in f["sinais"].items() if v == "duvida"}
    else:
        d = ctx["pendente"]
        if d is None or resposta is None:
            raise ValueError("passo sem pergunta pendente")
        qa.append({"pergunta": d["texto"], "resposta": resposta, "sinal": d["sinal"], "motivo": d["motivo"]})
        estado["perguntas"].append({"sinal": d["sinal"], "texto": d["texto"]})
        f = await extrair(chamar, msgs, qa)
        sn = d["sinal"]
        if not sn.startswith("_") and SINAIS[sn].get("tipo") != "intensidade":
            leitura = await ler_resposta(chamar, d["texto"], resposta)
            qa[-1]["leitura"] = leitura
            confirma = d["motivo"].startswith(("aviso_5_sinais", "confirma_192"))
            if leitura == "NAO_SEI":
                if confirma:
                    lidos[sn] = "afirmado"  # na duvida sobre item do 192, lado seguro
                else:
                    lidos.pop(sn, None)
            else:
                presente = (leitura == "SIM") != bool(SINAIS[sn].get("pergunta_inverte"))
                lidos[sn] = "afirmado" if presente else "negado"
            if confirma and sn in ("idoso_desmaio_ou_engasgo", "desmaio") and sn in lidos:
                lidos["idoso_desmaio_ou_engasgo"] = lidos["desmaio"] = lidos[sn]
        f["sinais"].update(lidos)
        if sn.startswith("_"):
            pass
        elif SINAIS[sn].get("tipo") == "intensidade":
            if f.get("intensidade_0_10") is None:
                estado["duvidas"][sn] = estado["duvidas"].get(sn, 0) + 1
        elif f["sinais"].get(sn) not in ("afirmado", "negado"):
            estado["duvidas"][sn] = estado["duvidas"].get(sn, 0) + 1
            f["sinais"][sn] = "duvida"
    ctx["f"] = f
    ctx["pendente"] = None
    estado["detalhado"] = bool(qa) or len(" ".join(msgs)) > 80
    pre = ctx["pre"]
    aviso = REGEX_CONFIRMA.get(pre) if pre else None
    d = decidir(f, estado)
    if aviso and aviso[0] not in [x["sinal"] for x in qa] and f["sinais"].get(aviso[0]) != "afirmado":
        d = {"tipo": "pergunta", "sinal": aviso[0], "texto": aviso[1], "motivo": f"aviso_5_sinais.{pre}"}
    if d["tipo"] == "saida" and d["saida"] == "192":
        return _saida_192(str(d["regra"]))
    if (
        len(qa) < MAX_PERGUNTAS
        and precisa_pergunta_suicidio(f, msgs, qa)
        and not (d["tipo"] == "pergunta" and d["motivo"].startswith(("aviso_5_sinais", "confirma_192")))
    ):
        d = {
            "tipo": "pergunta",
            "sinal": "_suicidio",
            "texto": PERGUNTA_SUICIDIO,
            "motivo": "saude_mental.pensamento_de_morte",
        }
    confirmando = d["tipo"] == "pergunta" and d["motivo"].startswith(
        ("aviso_5_sinais", "confirma_192", "saude_mental.pensamento")
    )
    if not confirmando and not ctx["juiz_perguntou"] and len(qa) < MAX_PERGUNTAS:
        ctx["juiz_perguntou"] = True
        pj = await juiz_pergunta(chamar, msgs, qa)
        if pj:
            d = {"tipo": "pergunta", "sinal": "_juiz", "texto": pj, "motivo": "juiz.pergunta_aberta"}
    if d["tipo"] == "pergunta":
        ctx["pendente"] = {"sinal": d["sinal"], "texto": d["texto"], "motivo": d["motivo"]}
        return {"tipo": "pergunta", "texto": d["texto"], "motivo": d["motivo"]}
    return await _final(chamar, ctx, d)


async def _final(chamar: Chamar, ctx: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """Piso da tabela + juiz (so' sobe; sem regra na tabela, o juiz decide sozinho)."""
    msgs: list[str] = ctx["msgs"]
    qa: list[dict[str, Any]] = ctx["qa"]
    estado: dict[str, Any] = ctx["estado"]
    f: dict[str, Any] = ctx["f"]
    piso = dict(d)
    out: dict[str, Any] = {"tipo": "saida", "piso": piso["saida"], "piso_regra": piso["regra"]}
    atn = sinais_atencao(str(piso["regra"]), str(piso["saida"]))
    j = await juiz(chamar, msgs, qa, atn, max_tokens=400)
    mot = str(j.get("motivo") or "")
    mt = RE_MOTIVO_PS.search(mot)
    if (
        mt
        and SEV.get(j["saida"], 0) < SEV["PS"]
        and not RE_NEGA.search(mot[max(0, mt.start() - 25) : mt.start()])
    ):
        j["saida"], j["coerencia_ps"] = "PS", True
    if j["saida"] == "192":
        j["saida"], j["limitado_a_PS"] = "PS", True
    out.update(juiz=j["saida"], juiz_motivo=mot, explica=explicacao(j.get("explica")))
    if atn and j.get("sinal_da_tabela_presente") is False:
        for x in atn:
            f["sinais"][x] = "negado"
        est2 = dict(estado, perguntas=estado["perguntas"] + [{"sinal": "_fim", "texto": ""}] * MAX_PERGUNTAS)
        piso = decidir(f, est2)
        out.update(sinal_retirado=atn, piso=piso["saida"], piso_regra=piso["regra"])
    if piso["saida"] == "192":
        return {**out, **_saida_192(str(piso["regra"]))}
    livre = sem_piso(str(piso["regra"]))
    if livre or SEV[j["saida"]] > SEV[piso["saida"]]:
        saida = j["saida"]
        regra = ("juiz.sem_regra_na_tabela" if livre else "juiz.acima_do_piso") + f" (piso {piso['saida']})"
    else:
        saida, regra = piso["saida"], str(piso["regra"])
    out.update(saida=saida, regra=regra, publico=piso.get("publico"))
    return out

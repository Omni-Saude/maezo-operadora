"""Gera os RASCUNHOS dos assuntos novos da base de saude com o Claude Sonnet 5.5 (fase 5, so' dev).

Roda como TAREFA na AWS (`scripts/tarefa_aws/lancar.py`, README desta pasta), com a role do simulador:

    python scripts/base_saude_rascunhos/gerar.py --saida s3://<bucket>/<prefixo>/<data> \
        --paginas s3://<bucket>/<prefixo>/entrada/paginas.json [--ids con-gota,exa-ferritina] [--limite N]

O QUE VAI AO MODELO: so' o titulo do assunto, a categoria e (quando ha') o TRECHO da pagina oficial publica
(`paginas.json`, coletado por `coletar_paginas.py`). ZERO dado de beneficiario — por isso o perfil GLOBAL
do Bedrock (`global.anthropic.claude-sonnet-5-5`, sem `temperature`, `effort=low`) e' aceitavel aqui.

CADA RASCUNHO PASSA, antes de ser aceito: pelas cercas de saida da Helena (recusa, canal, encaminhamento),
pelos termos proibidos da base, pela regua de tom (frase <= 20 palavras), pela MESMA checagem de conduta da
saude gerativa (`saude_gerativa.verificar`: dose, mandar usar/parar, dose esquecida, combinacao, minimizar,
diagnostico, promessa, genero, link) e por regras desta fase (sem alarme/pronto-socorro, sem frequencia,
sem "dor muito forte", sem idade/esquema de vacina, sem telefone). Reprovou -> UMA reescrita; reprovou de
novo -> `reprovado` (nao entra na base). Depois, o Claude Opus 5 faz uma LEITURA DE CONDUTA independente
(conduta indevida, erro factual provavel, alarme inventado, linguagem) — vai para a planilha do medico.

Saida: `rascunhos.jsonl` (um por assunto) e `manifesto.json` (commit, prompts, modelos, contagens).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import FECHO_SAUDE_MENTAL, AssuntoNovo, carregar_assuntos, titulos_das_paginas  # noqa: E402

MODELO_REDATOR = "global.anthropic.claude-sonnet-5-5"
MODELO_LEITOR = "global.anthropic.claude-opus-5"
REGIAO = "sa-east-1"
MAX_TOKENS = 3000
LIMITE_DO_RASCUNHO = 600
_TRECHO_MAX = 4000

PROMPT_REDATOR = """\
Você escreve RASCUNHOS de textos curtos para a base de conhecimento de saúde de uma operadora de plano de \
saúde. Quem lê é um beneficiário leigo, no WhatsApp. Um médico vai revisar cada texto antes de qualquer uso \
real. O texto INFORMA; nunca orienta conduta.

COMO ESCREVER
- Português do Brasil, linguagem simples e neutra. De 3 a 5 frases. No máximo 550 caracteres no total.
- Cada frase com no máximo 18 palavras. Sem markdown, sem listas, sem emojis, sem títulos.
- Condição/doença: o que é; os sintomas ou características mais comuns, em geral; que o tratamento é \
definido pelo médico e pode envolver (cite tipos gerais, como mudança de hábitos, remédios ou cirurgia, sem \
nome de remédio); qual especialidade costuma cuidar.
- Exame: o que ele mede ou mostra, para que costuma ser pedido e como costuma ser feito; diga que o \
resultado é avaliado pelo médico.
- Procedimento ou cirurgia: o que é, em que situações costuma ser indicado, como costuma ser feito, que a \
indicação é do médico. Sem prometer resultado.
- Vacina: contra o que protege e que o Calendário Nacional de Vacinação define quem pode receber; para \
saber se é o seu caso, procure uma unidade de saúde ou o seu médico.
- Se vier um TRECHO DA FONTE OFICIAL, os fatos têm de estar de acordo com ele; escreva com as suas palavras \
(não copie frases inteiras). Sem trecho, use só conhecimento médico consolidado e nada controverso.

NUNCA ESCREVA
- dose, dosagem, posologia, quantidade (mg, ml, gotas, comprimidos), horário, intervalo, nome de remédio;
- o verbo "tomar" em nenhuma forma (use "usar"); "você deve", "você precisa", "você tem", "você está com";
- "é normal", "não é grave", "não se preocupe", "fique tranquilo", "nada demais";
- diagnóstico ou comparação com a pessoa que lê; promessa ou menção de cobertura do plano;
- frequência de exame, vacina ou consulta ("todo ano", "anualmente", "a cada X anos"), idades e esquemas \
de vacina, valores de referência de exame;
- sinais de alarme, quando ir ao pronto-socorro, 192, SAMU ou "dor muito forte" (isso vem de outra parte \
do sistema); links, telefones, nomes de sites;
- palavras que suponham o gênero de quem lê ("sozinho/sozinha", "avaliado/avaliada"). Dizer que a doença \
atinge mulheres ou homens, como fato, pode.

SINÔNIMOS: de 3 a 8 termos que a pessoa poderia digitar para falar deste assunto: nome popular, sigla, \
grafia sem acento, erro comum de digitação. Em minúsculas. Nada genérico demais ("dor", "exame", "vacina", \
"cirurgia", "doença").

Responda SÓ com JSON: {"texto": "...", "sinonimos": ["...", "..."]}
"""
PROMPT_LEITOR = """\
Você é médico revisor de segurança de uma operadora de plano de saúde. Vai ler o RASCUNHO de um texto \
informativo para beneficiários leigos. A regra da casa: o texto informa, nunca orienta conduta individual, \
nunca dá dose, frequência, diagnóstico nem diz quando ir ao pronto-socorro (isso vem de outra parte do \
sistema). Avalie e responda SÓ com JSON:
{"conduta_indevida": true|false, "erro_factual_provavel": true|false, "alarme_inventado": true|false, \
"linguagem_leiga": 1-5, "observacao": "até 30 palavras: o que o médico humano deve conferir"}
- conduta_indevida: manda fazer, usar, parar ou evitar algo; dose; frequência; quando procurar urgência.
- erro_factual_provavel: algo provavelmente incorreto, exagerado ou desatualizado para o Brasil.
- alarme_inventado: cria um sinal de alarme ou critério de gravidade.
"""
VERSAO = "base-saude-rascunho-v1-" + hashlib.sha256(PROMPT_REDATOR.encode("utf-8")).hexdigest()[:10]


def _normalizar(texto: str) -> str:
    d = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in d if not unicodedata.combining(c))


# --- O trecho da fonte ------------------------------------------------------------------------------------
def trecho_da_pagina(assunto: AssuntoNovo, paginas: dict[str, Any]) -> str:
    if assunto.pagina is None:
        return ""
    texto = str((paginas.get(assunto.pagina) or {}).get("texto") or "")
    if not assunto.termos:
        return texto[:_TRECHO_MAX]
    linhas = texto.splitlines()
    termos = [_normalizar(t) for t in assunto.termos]
    escolhidas: list[int] = []
    for i, linha in enumerate(linhas):
        if any(t in _normalizar(linha) for t in termos):
            escolhidas.extend((i - 1, i, i + 1))
    vistas = sorted({i for i in escolhidas if 0 <= i < len(linhas)})
    return "\n".join(linhas[i] for i in vistas)[:_TRECHO_MAX]


def mensagem(assunto: AssuntoNovo, trecho: str) -> str:
    partes = [f"Assunto: {assunto.titulo}", f"Categoria: {assunto.categoria}"]
    if trecho:
        nome = next((f.nome for f in assunto.fontes if f.url == assunto.pagina), "fonte oficial")
        partes.append(f"[TRECHO DA FONTE OFICIAL: {nome}]\n{trecho}\n[FIM DO TRECHO]")
    else:
        partes.append("Não há página leiga oficial para este assunto na lista de fontes da operadora.")
    if assunto.saude_mental:
        partes.append(
            "É um assunto de saúde mental: não escreva o fecho de apoio (CVV/equipe); o sistema acrescenta."
        )
    return "\n\n".join(partes)


# --- A checagem ---------------------------------------------------------------------------------------------
_ALARME = re.compile(r"\b(?:pronto[ -]?socorro|pronto[ -]?atendimento|192|samu|emergencia)\b")
_DOR_MUITO_FORTE = re.compile(r"\bdor(?:es)? muito (?:forte|intensa)s?\b")
_FREQUENCIA = re.compile(
    r"\b(?:todo ano|todos os anos|anualmente|a cada \d+|uma vez (?:por|ao) ano|de \d+ em \d+|"
    r"a cada (?:dois|tres|cinco|seis) (?:anos|meses))\b"
)
_IDADE_OU_ESQUEMA = re.compile(r"\b\d+ (?:anos|meses|semanas)\b|\b(?:primeira|segunda|terceira) aplicacao\b")
_TELEFONE = re.compile(r"\d{4,}")
_DEVE = re.compile(r"\bvoce (?:deve|precisa|tem que|tem de)\b")
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


def checar(texto: str, assunto: AssuntoNovo) -> list[str]:
    """Tudo o que REPROVA o rascunho (lista vazia = aceito). Usa as cercas e a checagem do proprio repo."""
    from maezo.agents.helena.prompts import (
        menciona_encaminhamento,
        motivo_de_canal_nao_confirmado,
        motivo_de_recusa,
    )
    from maezo.conhecimento import base_saude as bs
    from maezo.conhecimento import saude_gerativa as sg
    from maezo.qa.simulador.checagens import frases_longas

    regras: list[str] = []
    if not texto.strip():
        return ["vazio"]
    if len(texto) > LIMITE_DO_RASCUNHO:
        regras.append(f"longo:{len(texto)}")
    if frases_longas(texto):
        regras.append("frase_longa")
    regras += [f"base:{r}" for r in bs.termos_proibidos_no_texto(texto)]
    if motivo_de_recusa(texto, "inform", start_aconteceu=None) is not None:
        regras.append("cerca:recusa")
    if motivo_de_canal_nao_confirmado(texto) is not None:
        regras.append("cerca:canal")
    if menciona_encaminhamento(texto):
        regras.append("cerca:encaminhamento")
    pedido = sg.Pedido(modo=sg.MODO_INFORMAR, mensagem="", crianca=True)
    regras += [f"conduta:{v.regra}" for v in sg.verificar(texto, pedido, cercas=lambda _t: [])]
    plano = _normalizar(texto)
    for nome, padrao in (
        ("alarme_ou_pronto_socorro", _ALARME),
        ("dor_muito_forte", _DOR_MUITO_FORTE),
        ("frequencia", _FREQUENCIA),
        ("voce_deve", _DEVE),
    ):
        if padrao.search(plano):
            regras.append(nome)
    if assunto.categoria == "vacina" and _IDADE_OU_ESQUEMA.search(plano):
        regras.append("idade_ou_esquema_de_vacina")
    if _TELEFONE.search(texto) or "http" in plano or "www" in plano:
        regras.append("telefone_ou_link")
    if "**" in texto or "#" in texto or _EMOJI.search(texto) or re.search(r"(?m)^\s*[-•*]\s", texto):
        regras.append("formato")
    return list(dict.fromkeys(regras))


def ler_json(texto: str) -> dict[str, Any] | None:
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dados = json.loads(texto[inicio : fim + 1])
    except ValueError:
        return None
    return dados if isinstance(dados, dict) else None


def limpar_sinonimos(brutos: Any) -> list[str]:
    saida: list[str] = []
    for s in brutos if isinstance(brutos, list) else []:
        if isinstance(s, str):
            t = " ".join(s.strip().lower().split())
            if 2 <= len(t) <= 60 and t not in saida:
                saida.append(t)
    return saida[:10]


# --- O Bedrock ------------------------------------------------------------------------------------------
class Bedrock:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        self._c = boto3.client(
            "bedrock-runtime",
            region_name=REGIAO,
            config=Config(
                read_timeout=180, connect_timeout=10, retries={"max_attempts": 4, "mode": "adaptive"}
            ),
        )

    def conversar(
        self, modelo: str, sistema: str, mensagens: list[dict[str, Any]], *, esforco: bool
    ) -> dict[str, Any]:
        extra = {"additionalModelRequestFields": {"output_config": {"effort": "low"}}} if esforco else {}
        inicio = time.monotonic()
        for tentativa in range(4):
            try:
                r = self._c.converse(
                    modelId=modelo,
                    system=[{"text": sistema}],
                    messages=mensagens,
                    inferenceConfig={"maxTokens": MAX_TOKENS},
                    **extra,
                )
                blocos = r.get("output", {}).get("message", {}).get("content", [])
                uso = r.get("usage", {})
                return {
                    "texto": "\n".join(b["text"] for b in blocos if "text" in b).strip(),
                    "entrada": int(uso.get("inputTokens", 0)),
                    "saida": int(uso.get("outputTokens", 0)),
                    "latencia_ms": int((time.monotonic() - inicio) * 1000),
                    "erro": None,
                }
            except Exception as exc:  # erro de servico: repete com espera; o codigo vai para o registro
                codigo = str(getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__))
                if tentativa == 3:
                    return {"texto": "", "erro": codigo, "entrada": 0, "saida": 0, "latencia_ms": 0}
                time.sleep(5.0 * 2**tentativa)
        return {"texto": "", "erro": "sem_resposta", "entrada": 0, "saida": 0, "latencia_ms": 0}


def _usuario(texto: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"text": texto}]}


def redigir(bedrock: Bedrock, assunto: AssuntoNovo, paginas: dict[str, Any]) -> dict[str, Any]:
    trecho = trecho_da_pagina(assunto, paginas)
    msgs = [_usuario(mensagem(assunto, trecho))]
    c1 = bedrock.conversar(MODELO_REDATOR, PROMPT_REDATOR, msgs, esforco=True)
    d1 = ler_json(c1["texto"]) or {}
    t1 = " ".join(str(d1.get("texto") or "").split())
    v1 = checar(t1, assunto) if not c1["erro"] else [f"erro:{c1['erro']}"]
    registro: dict[str, Any] = {
        "id": assunto.id,
        "titulo": assunto.titulo,
        "categoria": assunto.categoria,
        "grupo": assunto.grupo,
        "saude_mental": assunto.saude_mental,
        "fontes": [asdict(f) for f in assunto.fontes],
        "trecho_chars": len(trecho),
        "modelo": MODELO_REDATOR,
        "prompt_versao": VERSAO,
        "tentativa1": t1,
        "violacoes1": v1,
        "tentativa2": None,
        "violacoes2": [],
        "sinonimos": limpar_sinonimos(d1.get("sinonimos")),
        "tokens": [c1["entrada"], c1["saida"]],
    }
    final, violacoes = t1, v1
    if v1 and not c1["erro"]:
        pedido = (
            "A verificação automática REPROVOU o rascunho por: "
            + ", ".join(v1)
            + ". Reescreva seguindo TODAS as regras e responda só com o JSON."
        )
        msgs2 = [*msgs, {"role": "assistant", "content": [{"text": c1["texto"] or "{}"}]}, _usuario(pedido)]
        c2 = bedrock.conversar(MODELO_REDATOR, PROMPT_REDATOR, msgs2, esforco=True)
        d2 = ler_json(c2["texto"]) or {}
        t2 = " ".join(str(d2.get("texto") or "").split())
        v2 = checar(t2, assunto) if not c2["erro"] else [f"erro:{c2['erro']}"]
        registro.update(tentativa2=t2, violacoes2=v2)
        registro["tokens"] = [registro["tokens"][0] + c2["entrada"], registro["tokens"][1] + c2["saida"]]
        if d2.get("sinonimos"):
            registro["sinonimos"] = limpar_sinonimos(d2.get("sinonimos"))
        final, violacoes = t2, v2
    if assunto.saude_mental and final and not violacoes:
        final = f"{final} {FECHO_SAUDE_MENTAL}"
    registro["texto_final"] = final if not violacoes else ""
    registro["desfecho"] = (
        "reprovado" if violacoes else ("primeira" if not registro["violacoes1"] else "reescrita")
    )
    if registro["texto_final"]:
        leitura = bedrock.conversar(
            MODELO_LEITOR,
            PROMPT_LEITOR,
            [_usuario(f"Assunto: {assunto.titulo}\nRASCUNHO:\n{registro['texto_final']}")],
            esforco=False,
        )
        registro["leitura_opus"] = ler_json(leitura["texto"]) or {"erro": leitura["erro"] or "ilegivel"}
    return registro


# --- Saida -------------------------------------------------------------------------------------------------
def _s3() -> Any:
    import boto3

    return boto3.client("s3", region_name=REGIAO)


def ler(origem: str) -> str:
    if origem.startswith("s3://"):
        bucket, _, chave = origem[5:].partition("/")
        return str(_s3().get_object(Bucket=bucket, Key=chave)["Body"].read().decode("utf-8"))
    return Path(origem).read_text(encoding="utf-8")


def gravar(destino: str, nome: str, conteudo: str) -> None:
    if destino.startswith("s3://"):
        bucket, _, prefixo = destino[5:].partition("/")
        extra: dict[str, Any] = {"ServerSideEncryption": "aws:kms"}
        if os.environ.get("MAEZO_SIMULADOR_KMS_KEY"):
            extra["SSEKMSKeyId"] = os.environ["MAEZO_SIMULADOR_KMS_KEY"]
        _s3().put_object(
            Bucket=bucket, Key=f"{prefixo.rstrip('/')}/{nome}", Body=conteudo.encode("utf-8"), **extra
        )
    else:
        Path(destino).mkdir(parents=True, exist_ok=True)
        (Path(destino) / nome).write_text(conteudo, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--saida", required=True)
    p.add_argument("--paginas", required=True, help="paginas.json (caminho ou s3://)")
    p.add_argument("--ids", default="", help="so' estes ids (virgula)")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--concorrencia", type=int, default=4)
    args = p.parse_args(argv)
    paginas = json.loads(ler(args.paginas))
    assuntos = carregar_assuntos(titulos=titulos_das_paginas(paginas))
    if args.ids:
        quero = set(args.ids.split(","))
        assuntos = [a for a in assuntos if a.id in quero]
    if args.limite:
        assuntos = assuntos[: args.limite]
    bedrock = Bedrock()
    inicio = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=args.concorrencia) as pool:
        registros = list(pool.map(lambda a: redigir(bedrock, a, paginas), assuntos))
    gravar(
        args.saida, "rascunhos.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros)
    )
    contagem: dict[str, int] = {}
    for r in registros:
        contagem[r["desfecho"]] = contagem.get(r["desfecho"], 0) + 1
    manifesto = {
        "commit": os.environ.get("TAREFA_GIT_SHA", ""),
        "inicio": inicio.isoformat(),
        "fim": datetime.now(UTC).isoformat(),
        "modelo_redator": MODELO_REDATOR,
        "modelo_leitor": MODELO_LEITOR,
        "parametros": "Claude 5.x sem temperature; redator com output_config.effort=low",
        "prompt_versao": VERSAO,
        "prompt_redator": PROMPT_REDATOR,
        "prompt_leitor": PROMPT_LEITOR,
        "assuntos": len(registros),
        "desfechos": contagem,
        "tokens": [sum(r["tokens"][0] for r in registros), sum(r["tokens"][1] for r in registros)],
        "dado_de_beneficiario": "nenhum (prompts sinteticos: titulo + trecho de pagina publica)",
    }
    gravar(args.saida, "manifesto.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
    print(json.dumps({k: manifesto[k] for k in ("assuntos", "desfechos", "tokens")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A LEITURA da saude (desenho original, AGJ-HELENA-TRIAGE de 05/07/2026; dono, 01/11/2026).

O modelo SO' LE a conversa e anota, num JSON fechado, o que a tabela DMN precisa: o assunto, o publico, o
codigo do sintoma (so' da lista da tabela daquele publico), a intensidade e a idade. Ele nao conversa, nao
pergunta, nao julga gravidade e nao escreve nada para a pessoa: quem decide e' a tabela (ADR-0012).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

from maezo.agents.helena.prompts import SINTOMA_CODIGOS_BY_POPULATION

#: (sistema, texto, max_tokens) -> resposta do modelo.
Chamar = Callable[[str, str, int], Awaitable[str]]

ASSUNTOS: Final[frozenset[str]] = frozenset({"sintoma", "pergunta_clinica", "duvida_saude", "outro"})
POPULACOES: Final[frozenset[str]] = frozenset(SINTOMA_CODIGOS_BY_POPULATION)
INTENSIDADES: Final[frozenset[str]] = frozenset({"leve", "moderada", "grave", "desconhecida"})

#: O que cada codigo da tabela quer dizer, em palavras de WhatsApp (o codigo so' vale com a palavra que o
#: justifica; na duvida, null — o caso vai para a equipe do mesmo jeito).
_DESCRICAO: Final[Mapping[str, Mapping[str, str]]] = {
    "adult": {
        "dor_toracica": "dor, aperto ou pressão no PEITO agora",
        "dispneia": "falta de ar ou dificuldade para respirar agora (tosse ou nariz entupido sozinhos NÃO)",
        "deficit_neurologico": "de repente: boca torta, fala enrolada, fraqueza ou dormência de um lado do "
        "corpo, confusão mental ou perda da visão",
        "cefaleia_subita_intensa": "dor de cabeça que começou DE REPENTE e muito forte, 'a pior da vida' "
        "(dor de cabeça comum NÃO)",
        "sangramento_ativo": "sangramento acontecendo agora (ferida que sangra, vômito ou tosse com sangue)",
        "reacao_alergica": "reação alérgica (coceira, placas, inchaço) depois de remédio, comida ou picada",
        "sincope": "desmaiou ou perdeu os sentidos nesta doença (mesmo que já tenha voltado)",
        "febre": "febre",
        "dor_abdominal": "dor de barriga",
    },
    "pediatric": {
        "febre": "febre",
        "dificuldade_respiratoria": "respirando com esforço, muito rápido, gemendo, afundando as costelas ou "
        "com os lábios roxos",
        "convulsao": "convulsão ou ataque (tremores com perda de consciência)",
        "letargia": "muito molinha, difícil de acordar ou que não reage (só 'caidinho' NÃO)",
        "sinais_desidratacao": "vômito ou diarreia com pouco xixi, boca seca, olho fundo ou choro sem "
        "lágrima",
        "petequias_febre": "febre com manchas vermelhas ou roxas pelo corpo",
        "choro_inconsolavel": "bebê com choro que não para, que nada acalma",
        "recusa_alimentar": "bebê que NÃO QUER mamar, recusa o peito ou a mamadeira (mamar com dificuldade "
        "por nariz entupido NÃO)",
        "hipotonia": "bebê mole, largado, sem força",
        "cianose": "recém-nascido roxo (lábios ou corpo); criança maior com lábios roxos é "
        "dificuldade_respiratoria",
        "vomitos_persistentes": "bebê vomitando várias vezes, tudo o que mama",
    },
    "gestante": {
        "sangramento_vaginal": "sangramento pela vagina",
        "cefaleia_alteracao_visual": "dor de cabeça com visão turva, pontos brilhantes ou embaçada",
        "perda_liquido": "perda de líquido pela vagina",
        "contracoes_regulares": "contrações ou barriga endurecendo em intervalos regulares",
        "movimentos_fetais_reduzidos": "o bebê mexendo menos ou parou de mexer",
        "febre": "febre",
    },
    "mental_health": {
        "ideacao_suicida": "pensa em se matar, em morrer ou em não estar mais aqui",
        "autolesao": "se machucou ou se corta de propósito",
        "agitacao_agressividade": "agitação forte ou agressividade",
        "surto_psicotico": "vendo ou ouvindo coisas que não existem, falando sem sentido, fora da realidade",
        "crise_ansiedade": "crise de ansiedade agora",
        "crise_panico": "crise de pânico agora",
    },
}


def _lista_de_codigos() -> str:
    linhas = []
    for populacao, codigos in SINTOMA_CODIGOS_BY_POPULATION.items():
        linhas.append(f"  {populacao}:")
        linhas.extend(f"    - {c}: {_DESCRICAO[populacao][c]}" for c in codigos)
    return "\n".join(linhas)


PROMPT_LEITURA: Final[str] = f"""Você é um ANOTADOR de conversas de WhatsApp de um plano de saúde no Brasil.
Você NÃO conversa, NÃO orienta, NÃO diagnostica e NÃO decide gravidade: só anota fatos em JSON.

Leia a conversa inteira. Anote o caso da ÚLTIMA mensagem da pessoa:
- se a última mensagem continua o mesmo caso (mesmo paciente), use tudo o que foi dito sobre ele na conversa;
- se ela fala de OUTRO paciente ou de outro problema, anote só o caso novo.

Campos:
- assunto (um):
  "sintoma": relata um problema de saúde atual de alguém (mesmo que pergunte o que pode ser ou o que fazer);
  "pergunta_clinica": pede opinião sobre um caso de alguém, diagnóstico, remédio, dose ou o que fazer,
  sem relatar sintoma novo;
  "duvida_saude": pergunta geral de saúde sem caso de ninguém ("o que é PSA?", "para que serve tal exame?");
  "outro": não é de saúde (plano, boleto, carteirinha, cortesia, troca de assunto sem conteúdo).
  Se a mensagem cita um problema de saúde de alguém (queda, visão falhando, dor, tristeza, não quer ir à
  escola...), mesmo pedindo algo administrativo (andador, médico do plano, atestado, cobertura), o assunto é
  "sintoma".
- populacao: "gestante" se a paciente está grávida; "pediatric" se o paciente tem menos de 12 anos;
  "mental_health" se a queixa é emocional ou psiquiátrica; senão "adult".
- sintoma_codigo: UM código da lista da populacao, só se a pessoa disse algo que o justifica; senão null.
{_lista_de_codigos()}
- intensidade: "grave" (insuportável, a pior, muito forte, piorando muito, não consegue fazer nada),
  "moderada" (forte, incomodando bastante), "leve" (fraca, pouca), "desconhecida" (não disse).
- idade_anos: a idade em anos completos, se foi dita; senão null.
- idade_meses: a idade TOTAL em meses de criança ("20 dias" = 0, "2 semanas" = 0, "recém-nascido" = 0,
  "5 meses" = 5, "1 ano e 2 meses" = 14, "3 anos" = 36); senão null.
- idade_gestacional_semanas: semanas de gravidez se foram ditas; senão null.
- risco_imediato: true só se há plano ou intenção de se matar agora; senão false.

Responda SÓ com o JSON:
{{"assunto": "...", "populacao": "...", "sintoma_codigo": null, "intensidade": "desconhecida",
 "idade_anos": null, "idade_meses": null, "idade_gestacional_semanas": null, "risco_imediato": false}}"""


@dataclass(frozen=True)
class Leitura:
    assunto: str
    populacao: str
    sintoma_codigo: str | None
    intensidade: str
    idade_anos: int | None
    idade_meses: int | None
    idade_gestacional_semanas: int | None
    risco_imediato: bool

    def para_a_tabela(self) -> dict[str, object]:
        """Os campos que `_evaluate_dmn` le (os mesmos nomes da extracao de sempre)."""
        return {
            "population": self.populacao,
            "sintoma_codigo": self.sintoma_codigo,
            "intensidade": self.intensidade,
            "idade_anos": self.idade_anos,
            "idade_meses": self.idade_meses,
            "idade_gestacional_semanas": self.idade_gestacional_semanas,
            "risco_imediato": self.risco_imediato,
        }


def _inteiro(valor: object) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor >= 0 else None
    if isinstance(valor, float) and valor >= 0:
        return int(valor)
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor.strip())
    return None


def ler_json(bruto: str) -> Leitura:
    """O JSON do modelo validado contra o vocabulario fechado; fora dele (inclusive tipo inesperado),
    `ValueError` (quem chama decide)."""
    try:
        return _ler_json(bruto)
    except (TypeError, OverflowError, AttributeError) as exc:
        raise ValueError("leitura com tipo inesperado") from exc


def _ler_json(bruto: str) -> Leitura:
    achado = re.search(r"\{.*\}", bruto, re.S)
    if achado is None:
        raise ValueError("leitura sem JSON")
    dados = json.loads(achado.group(0))
    if not isinstance(dados, dict):
        raise ValueError("leitura nao e' objeto")
    assunto = dados.get("assunto")
    populacao = dados.get("populacao")
    if assunto not in ASSUNTOS or populacao not in POPULACOES:
        raise ValueError("leitura fora do vocabulario")
    codigo = dados.get("sintoma_codigo")
    if codigo not in SINTOMA_CODIGOS_BY_POPULATION[str(populacao)]:
        codigo = None  # codigo de outra populacao ou inventado: sem codigo (a tabela cai no fail-safe)
    intensidade = dados.get("intensidade")
    idade_anos = _inteiro(dados.get("idade_anos"))
    idade_meses = _inteiro(dados.get("idade_meses"))
    if populacao == "pediatric" and idade_meses is None and idade_anos is not None:
        # A tabela pediatrica le' so' `idade_meses` (as regras "menos de 1 mes"/"menos de 3 meses").
        idade_meses = idade_anos * 12
    return Leitura(
        assunto=str(assunto),
        populacao=str(populacao),
        sintoma_codigo=str(codigo) if codigo else None,
        intensidade=str(intensidade) if intensidade in INTENSIDADES else "desconhecida",
        idade_anos=idade_anos,
        idade_meses=idade_meses,
        idade_gestacional_semanas=_inteiro(dados.get("idade_gestacional_semanas")),
        risco_imediato=dados.get("risco_imediato") is True,
    )


async def ler(chamar: Chamar, conversa: str) -> Leitura:
    """Uma leitura (duas tentativas); sem JSON valido nas duas, `ValueError`."""
    for _ in range(2):
        try:
            return ler_json(await chamar(PROMPT_LEITURA, conversa, 300))
        except (ValueError, json.JSONDecodeError):
            continue
    raise ValueError("leitura sem JSON valido")


__all__ = ["PROMPT_LEITURA", "Chamar", "Leitura", "ler", "ler_json"]

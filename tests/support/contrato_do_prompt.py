"""Contrato entre o TEXTO de um prompt e a lista fechada de fatos do sitio (DL-0191).

A lista `permitidos` decide o que chega ao modelo. O texto do prompt decide o que o modelo e'
mandado ESCREVER. Os dois tem de concordar: um template que pede "guia/lote/conta TISS" ao modelo
enquanto a lista tirou os tres do contexto convida o modelo a dizer que faltam ou, pior, a
inventa-los. Este modulo confere a metade que a lista nao cobre.

Como funciona:

- O `VOCABULARIO_DE_CAMPOS` e' FECHADO: nome da chave do caso -> padrao do rotulo com que um
  template a pediria. Sai da lista "nunca entra" do ADR-0079 §2 e das chaves que ficam so' no
  dossie (DL-0190/DL-0191). Nao lista campo que um sitio pode mandar ao modelo.
- So' o TRECHO DO PEDIDO e' lido (de `inicio` ate' `fim`, tipicamente de "Tarefa:" ate' as
  regras duras). O prompt de sistema e as regras duras NOMEIAM dado proibido para proibi-lo
  ("nunca CPF, nome, CNS"); ler esses trechos acusaria a propria proibicao.
- Falha se o trecho pede um campo do vocabulario cuja chave NAO esta em `permitidos`.

Para adotar num agente novo (U1c): uma linha na tabela de
`tests/unit/agents/test_prompt_template_contrato.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

#: Chave do caso -> padrao (regex, sem diferenciar caixa) do rotulo que a pediria num template.
VOCABULARIO_DE_CAMPOS: Final[Mapping[str, str]] = {
    # vinculo do caso
    "numero_guia_tiss": r"\bguias?\b",
    "numero_lote_tiss": r"(?<!tipo de )\blotes?\b",
    "numero_conta": r"\bconta(s)? TISS\b|\bn[uú]mero da conta\b|/conta\b|\bconta/",
    "glosa_id": r"\bglosa_id\b|\bid(entificador)? da glosa\b",
    "protocolo_reembolso": r"\bprotocolos?\b",
    # identificacao direta e indireta da pessoa
    "relacao_com_titular": r"\bpaciente\b|\btitular\b|\bdependente\b",
    "nome": r"\bnome\b",
    "cpf": r"\bCPF\b",
    "cns": r"\bCNS\b|cart[aã]o nacional de sa[uú]de",
    "numero_carteirinha": r"\bcarteirinha\b|\bmatr[ií]cula\b",
    "telefone": r"\btelefone\b",
    "email": r"\be-?mail\b",
    "endereco": r"\bendere[cç]o\b|\bCEP\b|\bbairro\b",
    "data_nascimento": r"\bdata de nascimento\b|\bidade\b|\bsexo\b",
    "beneficiario_pseudo_id": r"pseudo_id|\bpseud[oô]nimos?\b",
    "prestador_id": r"\bprestador_id\b|\bCNPJ\b|\bCRM\b",
    # saude
    "cid10": r"\bCID(-?10)?\b|\bdiagn[oó]stico\b",
    # referencias e anexos
    "documentos_refs": r"\bdocumentos?\b|\banexos?\b",
    "patient_facts": r"\bFHIR\b",
    # texto livre
    "justificativa_clinica": r"\bjustificativa cl[ií]nica\b|\bindica[cç][aã]o cl[ií]nica\b|\blaudos?\b",
}


def trecho_do_pedido(template: str, *, inicio: str, fim: str) -> str:
    """O pedaco do template que diz ao modelo o que escrever: de `inicio` (incluso) ate' `fim`."""
    comeco = template.index(inicio)
    final = template.index(fim, comeco)
    return template[comeco:final]


def campos_pedidos_fora_da_lista(
    template: str,
    *,
    permitidos: Iterable[str],
    inicio: str,
    fim: str,
    vocabulario: Mapping[str, str] = VOCABULARIO_DE_CAMPOS,
) -> list[str]:
    """Chaves do vocabulario que o trecho do pedido nomeia e que a lista do sitio nao manda."""
    lista = frozenset(permitidos)
    # Quebra de linha do template vira espaco: "tipo de\nlote" e' "tipo de lote".
    trecho = " ".join(trecho_do_pedido(template, inicio=inicio, fim=fim).split())
    return sorted(
        chave
        for chave, padrao in vocabulario.items()
        if chave not in lista and re.search(padrao, trecho, flags=re.IGNORECASE)
    )

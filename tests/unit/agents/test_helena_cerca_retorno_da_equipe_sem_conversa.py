"""DL-0146 (decisao do dono, 19/10/2026): nenhum texto ao beneficiario promete CONVERSA com o atendente.

O atendente nao conversa com o beneficiario: ele devolve o caso com UMA mensagem final (a retomada), que o
agente entrega (pendencia 12 do SP-OP-ESCALATION-001). "O atendente responde por aqui, nesta conversa"
prometia um dialogo que nao existe. A promessa aprovada e' a do RETORNO ao fim da analise, sem prazo:
"Assim que a equipe concluir a análise, te retorno por este canal."

Tres cercas:
  1. REGRESSAO: nenhuma string literal de `src/maezo` (fora docstring) volta a prometer conversa;
  2. os textos novos passam pelas cercas de saida REAIS da Helena e do Lucas;
  3. os textos novos nao prometem prazo.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path

import pytest

from maezo.agents.helena import graph as hg
from maezo.agents.helena.prompts import (
    menciona_encaminhamento,
    motivo_de_canal_nao_confirmado,
)
from maezo.agents.helena.prompts import motivo_de_recusa as recusa_helena
from maezo.agents.lucas import graph as lg
from maezo.agents.lucas.prompts import motivo_de_recusa as recusa_lucas

_SRC = Path(hg.__file__).resolve().parents[2]

#: As formas da promessa de conversa (texto sem acento, minusculo).
_PROMESSA_DE_CONVERSA = re.compile(
    r"\brespond\w* por aqui\b"
    r"|\b(?:o|um) atendente (?:vai )?(?:te |lhe )?respond"
    r"|\bquando o atendente respond"
    r"|\b(?:atendente|alguem)\b[^.]{0,30}\bvai falar com voce aqui"
)

#: Os textos reescritos pela DL-0146.
TEXTOS_HELENA = {
    nome: getattr(hg, nome)
    for nome in (
        "RESPOSTA_HANDOFF_JA_ABERTO",
        "RESPOSTA_HANDOFF_JA_ABERTO_COBRANCA",
        "RESPOSTA_HANDOFF_JA_ABERTO_COM_EMERGENCIA",
        "RESPOSTA_CASO_ABERTO_PRAZO",
        "RESPOSTA_CASO_ABERTO_SUPERVISOR",
        "RESPOSTA_CASO_ABERTO_URGENTE",
        "RESPOSTA_HANDOFF_CONTATO_DA_EQUIPE",
        "RESPOSTA_CASO_ABERTO_PEDIDO_DE_PESSOA",
        "RESPOSTA_CASO_ABERTO_RECADO",
        "RESPOSTA_CASO_ABERTO_CURTO_2",
    )
}
TEXTOS_LUCAS = {
    nome: getattr(lg, nome)
    for nome in (
        "ACK_ATENDIMENTO_JA_ABERTO",
        "ACK_ESCALACAO_RECUSADO",
        "ACK_ESCALACAO",
        "_ACK_ESCALACAO_ATRASO",
        "TEXTO_CANCELAMENTO_ENCAMINHADO",
        "TEXTO_CANCELAMENTO_JA_REGISTRADO",
    )
}
TODOS = {
    **{f"helena.{k}": v for k, v in TEXTOS_HELENA.items()},
    **{f"lucas.{k}": v for k, v in TEXTOS_LUCAS.items()},
}


def _plano(texto: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sem.lower().split())


def _docstrings(arvore: ast.AST) -> set[int]:
    ids: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and no.body:
            primeiro = no.body[0]
            if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant):
                ids.add(id(primeiro.value))
    return ids


def _literais(caminho: Path) -> list[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    pular = _docstrings(arvore)
    textos: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.JoinedStr):
            partes = (v.value for v in no.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            textos.append("".join(partes))
        elif isinstance(no, ast.Constant) and isinstance(no.value, str) and id(no) not in pular:
            textos.append(no.value)
    return textos


def test_a_varredura_nao_e_vacua() -> None:
    """A varredura ve os modulos dos agentes: um caminho errado a deixaria verde por nada."""
    arquivos = list(_SRC.rglob("*.py"))
    assert (_SRC / "agents" / "helena" / "graph.py") in arquivos
    assert (_SRC / "agents" / "lucas" / "graph.py") in arquivos
    literais = _literais(_SRC / "agents" / "helena" / "graph.py")
    assert hg.RESPOSTA_HANDOFF_JA_ABERTO in literais


def test_a_regua_pega_as_frases_antigas() -> None:
    """Os textos de antes da DL-0146, literalmente: a regua tem de reprovar cada um."""
    antigas = (
        "Seu pedido já está com a nossa equipe. O atendente responde por aqui, nesta conversa.",
        "Seu pedido segue com a equipe. O atendente responde por aqui.",
        "Seu pedido já está com a nossa equipe. Um atendente vai te responder aqui mesmo, nesta conversa.",
        "Seu pedido já está com a nossa equipe. Quando o atendente responder aqui, você pode pedir para "
        "falar com um supervisor.",
        "Encaminhei seu caso, e um atendente vai falar com você aqui mesmo, nesta conversa.",
        "Vou passar sua dúvida para alguém da nossa equipe, que vai te responder por aqui.",
    )
    for frase in antigas:
        assert _PROMESSA_DE_CONVERSA.search(_plano(frase)), frase


def test_nenhuma_string_de_src_promete_conversa_com_o_atendente() -> None:
    achados = [
        f"{caminho.relative_to(_SRC)}: {texto[:90]!r}"
        for caminho in sorted(_SRC.rglob("*.py"))
        for texto in _literais(caminho)
        if _PROMESSA_DE_CONVERSA.search(_plano(texto))
    ]
    assert not achados, f"promessa de conversa com o atendente voltou (DL-0146): {achados}"


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_texto_novo_promete_so_o_retorno_sem_prazo(nome: str) -> None:
    texto = _plano(TODOS[nome])
    assert "concluir a analise" in texto or "a analise terminar" in texto, nome
    assert "por este canal" in texto, nome
    assert not _PROMESSA_DE_CONVERSA.search(texto), nome
    assert not any(c.isdigit() for c in texto.replace("192", "")), nome
    assert not re.search(r"\b(?:hoje|amanha|em breve|logo|horas?|dias?|minutos?|prazo|ate o)\b", texto), nome


@pytest.mark.parametrize("nome", sorted(TEXTOS_HELENA))
def test_texto_novo_da_helena_passa_pelas_cercas_de_saida(nome: str) -> None:
    texto = TEXTOS_HELENA[nome]
    assert recusa_helena(texto, "escalate", start_aconteceu=True) is None
    assert motivo_de_canal_nao_confirmado(texto) is None
    # O aviso de caso aberto continua dizendo que a equipe tem o caso (os dois sem mencao ja' eram assim).
    sem_mencao = {"RESPOSTA_CASO_ABERTO_RECADO", "RESPOSTA_CASO_ABERTO_CURTO_2"}
    assert menciona_encaminhamento(texto) is (nome not in sem_mencao)


@pytest.mark.parametrize("nome", sorted(TEXTOS_LUCAS))
@pytest.mark.parametrize("rota", ["ack_escalacao", "mensagem"])
def test_texto_novo_do_lucas_passa_pela_cerca_de_saida(nome: str, rota: str) -> None:
    assert recusa_lucas(TEXTOS_LUCAS[nome], rota, None) is None


# --- 4. o que o sistema NAO faz hoje nao e' prometido (decisao do dono, 19/10/2026, item 12) ---------------
#: O atendente nao ve a conversa e a mensagem da pessoa nao e' repassada ao caso; nao existe como a pessoa
#: acionar um supervisor. Estes tres avisos de caso aberto so' prometem o retorno da equipe.
_AVISOS_SEM_CAPACIDADE_EXTRA = (
    "RESPOSTA_CASO_ABERTO_SUPERVISOR",
    "RESPOSTA_CASO_ABERTO_PEDIDO_DE_PESSOA",
    "RESPOSTA_CASO_ABERTO_RECADO",
)
_CAPACIDADE_QUE_NAO_EXISTE = re.compile(
    r"supervis|direcion|repass|encaminh|\brepet\w*|conte esse pedido|falar com"
)


@pytest.mark.parametrize("nome", _AVISOS_SEM_CAPACIDADE_EXTRA)
def test_aviso_de_caso_aberto_nao_promete_supervisor_nem_repasse_do_recado(nome: str) -> None:
    texto = _plano(getattr(hg, nome))
    assert not _CAPACIDADE_QUE_NAO_EXISTE.search(texto), nome
    assert "te retorno por este canal" in texto or "retorno a voce por este canal" in texto, nome


def test_os_tres_avisos_nao_colidem_com_o_aviso_generico_de_caso_aberto() -> None:
    """Texto igual ao `RESPOSTA_HANDOFF_JA_ABERTO` duplicaria o corpus e a escolha de variante."""
    textos = [getattr(hg, n) for n in _AVISOS_SEM_CAPACIDADE_EXTRA]
    assert len(set(textos)) == 3
    assert hg.RESPOSTA_HANDOFF_JA_ABERTO not in textos

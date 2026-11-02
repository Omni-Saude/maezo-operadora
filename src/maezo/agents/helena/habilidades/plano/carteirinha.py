"""A CARTEIRINHA do plano: o pedido, os fatos (lista FECHADA) e o texto fixo, sem modelo (habilidade `plano`).

Limpeza da saude, etapa 1 (dono, 02/11/2026): MOVIDO SEM MUDANCA de `helena_v4.py` (nasceu na v4, DL-0115, mas
e' dado do plano, nao saude). A resposta da carteirinha deixa de depender da chave da v4 (no dev a v4 esta'
ligada: o mesmo texto). `helena_v4` reimporta os nomes ate' sair. Modulo FOLHA (sem import do grafo).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Final

from maezo.agents.helena.consultas_plano import (
    RESPOSTA_SEM_VINCULO_ATIVO,
    marcador_da_carteirinha,
    so_vinculos_ativos,
)
from maezo.ports.tina import ElegibilidadeView

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


def fatos_da_carteirinha(
    view: ElegibilidadeView, *, so_ativos: bool = False, hoje: date | None = None
) -> dict[str, object]:
    """Os fatos que PODEM ir ao texto da carteirinha, lista FECHADA: codigo (so' se JA vier mascarado), plano,
    segmentacao, inicio da vigencia, cancelamento e numero ANS do plano. Nome, data de nascimento, CNS,
    CPF, titular e referencias NAO existem aqui: nunca chegam ao texto.

    `so_ativos=True` (chave `MAEZO_HELENA_CARTEIRINHA_COMPLETA`, decisao do dono de 17/10/2026): so' os
    vinculos ATIVOS em `hoje` (`consultas_plano.vinculo_ativo`), filtrados ANTES do limite, e a data de
    cancelamento (sempre futura entao) vai como `cancelamento_previsto`. `False` (o default) = os fatos de
    antes, byte a byte."""
    if so_ativos:
        view = so_vinculos_ativos(view, hoje or date.today())
    chave_cancelamento = "cancelamento_previsto" if so_ativos else "cancelamento"
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
                chave_cancelamento: _data_br(v.cancelamento),
                "registro_ans": ans if _ANS_REGISTRO.fullmatch(ans) else None,
            }
        )
    return {"vinculos": vinculos}


def texto_da_carteirinha(
    fatos: Mapping[str, object], *, completos: frozenset[str] = frozenset(), so_ativos: bool = False
) -> str:
    """A resposta da carteirinha: plano, segmentacao, vigencia e numero ANS, com o codigo MASCARADO, e o
    cartao completo no aplicativo/portal. Sem modelo. Campo que o cadastro nao traz diz "nao consta no
    cadastro" (nunca se completa um fato ausente).

    Chave `MAEZO_HELENA_CARTEIRINHA_COMPLETA` (decisao do dono de 17/10/2026): `so_ativos=True` sem vinculo =
    `RESPOSTA_SEM_VINCULO_ATIVO`; `completos` = as mascaras cujo numero completo este turno tem — nessas
    linhas sai "Número da carteirinha" com o MARCADOR (o numero so' entra no envio, `CarteirinhasDoTurno`),
    e a frase de seguranca deixa de dizer que o codigo inteiro nao e' mostrado. Os defaults = o texto de
    antes."""
    vinculos = fatos.get("vinculos")
    blocos: list[str] = []
    mostrou_completo = False
    if isinstance(vinculos, list):
        for v in vinculos:
            if not isinstance(v, Mapping):
                continue
            vigencia = v.get("vigencia_inicio")
            mascarada = v.get("codigo_mascarado")
            if isinstance(mascarada, str) and mascarada in completos:
                mostrou_completo = True
                codigo = f"- Número da carteirinha: {marcador_da_carteirinha(mascarada)}"
            else:
                codigo = f"- Código: {mascarada or 'não consigo mostrar com segurança'}"
            linhas = [
                codigo,
                f"- Plano: {v.get('plano') or NAO_CONSTA}",
                f"- Segmentação: {v.get('segmentacao') or NAO_CONSTA}",
                f"- Vigência: {('desde ' + str(vigencia)) if vigencia else NAO_CONSTA}",
                f"- Nº ANS do plano: {v.get('registro_ans') or NAO_CONSTA}",
            ]
            if v.get("cancelamento"):
                linhas.append(f"- Cancelamento: {v['cancelamento']}")
            if v.get("cancelamento_previsto"):
                linhas.append(f"- Cancelamento previsto: {v['cancelamento_previsto']}")
            blocos.append("\n".join(linhas))
    if not blocos and so_ativos:
        return RESPOSTA_SEM_VINCULO_ATIVO
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
        + (
            "Por segurança, não mostro outros dados pessoais por aqui. "
            if mostrou_completo
            else "Por segurança, não mostro o código inteiro nem outros dados pessoais por aqui. "
        )
        + "Se quiser falar com uma pessoa da equipe, é só me pedir."
    )


__all__ = ["NAO_CONSTA", "fatos_da_carteirinha", "pede_carteirinha", "texto_da_carteirinha"]

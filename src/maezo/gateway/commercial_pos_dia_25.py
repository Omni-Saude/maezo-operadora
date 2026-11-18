"""Depois do corte do relogio-sombra: so' recusa e encaminha a pessoa (E-SF-07, EP-REG-09a).

Base normativa (so' referencia, sem copia): `docs/compliance/legal/textos-base/RN-413-2016.md`, Art. 5º, §6º,
e Art. 6º.

Quando o relogio-sombra marca a hipotese `possivel_vigencia_automatica`, nenhuma acao adversa e' tomada por
automacao: bloqueio de uso por falta de pagamento, cobranca retroativa, alegacao de doenca ou lesao
preexistente e rescisao sao recusados, cada um com codigo fixo, e o caso vai a pessoa. Este modulo NAO
decide nada: nao conclui que o contrato esta' vigente, nao rescinde, nao nega, nao fixa valor. Fora da
hipotese, tambem nao autoriza nada: so' devolve que nao ha' recusa deste modulo.

Nada montado: nenhum comando de admissao, worker ou politica de autonomia importa este modulo.
"""

from __future__ import annotations

from typing import Final

from maezo.domain.rn413_contracting_clock import exigir_relogio_canonico

#: Acao adversa -> codigo da recusa enquanto a hipotese estiver marcada. Todas encaminham a pessoa.
RECUSAS_COM_A_HIPOTESE: Final[dict[str, str]] = {
    "bloqueio_de_uso": "human_review_required",
    "cobranca_retroativa": "human_review_required",
    "alegacao_dlp": "dlp_barred_rn413_5_6",
    "rescisao": "rescisao_autonoma_recusada",
}


class PosCorteRecusado(PermissionError):  # noqa: N818
    """Recusa com codigo fixo; o caso segue para pessoa. Nunca ecoa valor recebido."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.encaminhamento = "pessoa"
        super().__init__(code)


def recusar_acao_adversa(estado: object, *, acao: str) -> None:
    """Levanta `PosCorteRecusado` se a acao e' adversa e o caso tem a hipotese marcada; senao, volta.

    Voltar NAO e' autorizacao: a acao continua sujeita a tudo o mais que ja' a governa.
    """
    estado_canonico = exigir_relogio_canonico(estado)
    if type(acao) is not str or acao not in RECUSAS_COM_A_HIPOTESE:
        raise PosCorteRecusado("pos_corte_acao_desconhecida")
    if estado_canonico.possivel_vigencia_automatica:
        raise PosCorteRecusado(RECUSAS_COM_A_HIPOTESE[acao])

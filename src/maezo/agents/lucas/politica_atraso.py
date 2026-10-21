"""Politica de ATRASO do Lucas: quando um atraso abre caso humano (decisao do dono, sessao de 14/10/2026).

Antes (DMN `lucas_billing_admissibility` v0.1): QUALQUER indicio de atraso — um ciclo sem conciliacao, uma
competencia `vencida`, `dias_atraso_max > 0` — escalava a pergunta de cobranca. Na bateria final do simulador
(200 conversas, 14/10/2026) isso abriu caso para 20 conversas que so' perguntavam valor, vencimento ou
"esta' em dia?". A decisao do dono: o Lucas responde os FATOS primeiro, sempre (valor, vencimento e situacao
de cada mes), e so' abre caso humano para contestacao, negociacao/parcelamento, cancelamento, pedido
explicito de pessoa ou ATRASO REAL — e atraso real e' o que este modulo mede, com limiar configuravel:

  atraso_critico  =  pagador PESSOA FISICA (titulo do beneficiario)
                     E ( meses `vencida` >= `meses_minimo`  OU  maior atraso em dias > `dias_minimo` ).

Padrao (decisao do dono): 2 meses e 30 dias. Um mes sem pagamento, ate' 30 dias, e' resposta com fatos e a
oferta "Se quiser, posso te passar para alguem da nossa equipe." — sem caso. Contrato de EMPRESA e adesao
(titulo de terceiro) nunca sao criticos: o atraso e' da empresa/administradora, nao do beneficiario.

O limiar mora aqui (um lugar so'), e o receptor o configura por `MAEZO_LUCAS_ATRASO_MESES_MIN` e
`MAEZO_LUCAS_ATRASO_DIAS_MIN` (`platform/webhooks/whatsapp/settings.py`). Quem calcula e' o grafo, sobre os
fatos da fonte — nunca o modelo — e a DMN so' le' o resultado (`atraso_critico`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MESES_MINIMO_PADRAO: Final[int] = 2
DIAS_MINIMO_PADRAO: Final[int] = 30


@dataclass(frozen=True, slots=True)
class LimiaresDeAtraso:
    """`meses_minimo`: quantas competencias `vencida` bastam; `dias_minimo`: critico acima disto (dias)."""

    meses_minimo: int = MESES_MINIMO_PADRAO
    dias_minimo: int = DIAS_MINIMO_PADRAO

    def __post_init__(self) -> None:
        if (
            type(self.meses_minimo) is not int
            or type(self.dias_minimo) is not int
            or not 1 <= self.meses_minimo <= 24
            or not 1 <= self.dias_minimo <= 365
        ):
            raise ValueError("politica_atraso: limiar fora da faixa (meses 1..24, dias 1..365)")

    def critico(self, *, meses_vencidos: int, dias_atraso_max: int | None) -> bool:
        """O atraso e' critico? (So' para pagador PF: quem chama exclui o titulo de terceiro.)"""
        if meses_vencidos >= self.meses_minimo:
            return True
        return dias_atraso_max is not None and dias_atraso_max > self.dias_minimo


__all__ = ["DIAS_MINIMO_PADRAO", "MESES_MINIMO_PADRAO", "LimiaresDeAtraso"]

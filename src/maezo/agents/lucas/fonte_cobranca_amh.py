"""`FonteCobranca` REAL: os fatos de cobranca do Lucas lidos pelo contrato `billing-status` da AMH.

Substitui a `FonteCobrancaSimulada` com `MAEZO_LUCAS_FONTE_COBRANCA=amh` (decisao do dono de 06/10/2026):
`platform/webhooks/service.py::_build_fonte_cobranca_amh` a compoe sobre os executores do gateway
(`gateway/amh_interop.py`). O default continua `simulada`, e `amh` so' sobe com os contratos publicados e
pinados (ADR-0037, XRG-2/XRG-3) — sem isso o receptor recusa servir. O "consentimento" ligado hoje e' a
base legal fixa de execucao de contrato (`identidade_amh.BaseLegalExecucaoDeContrato`), nunca uma
afirmacao de que houve consentimento.

O CAMINHO, em quatro passos, cada um com a sua saida fechada:
  1. pseudonimo do Maezo -> `portable_subject_ref` (so' a AMH liga telefone a pessoa, XRD-05);
  2. consentimento para o proposito de atendimento ao beneficiario;
  3. leitura da situacao de cobranca pelo `BillingStatusPort`;
  4. traducao para os quatro fatos que a DMN do Lucas consome e, desde DL-0086 (08/10/2026), para os
     FATOS DE VALOR da janela (resumo e competencias), com que o Lucas responde sozinho.

NENHUM passo inventa fato. Se a pessoa nao e' resolvida, se nao ha' consentimento, se a leitura falha ou se
a fonte nao tem `status_conciliado`/`ciclos_sem_conciliacao`, o retorno e' `Indisponivel` com um motivo de
classe fechado, os campos de conciliacao ficam AUSENTES na entrada do Lucas e quem decide e' a DMN (o
catch-all dela escala a um humano). O Lucas nunca afirma pagamento nem atraso sem fato.

O que o Lucas recebe do boleto e' o rotulo MASCARADO (`****1234`) e a referencia de ORIGEM do dado
(`amh-billing:{data da fonte}`), nunca nosso numero, bloqueto, linha digitavel ou link.

FATOS DE VALOR (DL-0086, decisao do dono/DPO de 08/10/2026). Por competencia: competencia, vencimento,
situacao, valor total, coparticipacao, saldo, data de liquidacao e o boleto MASCARADO; do resumo: valor em
aberto e maior atraso em dias. No maximo a janela que o contrato devolveu (`JANELA_MESES`). Cada valor e'
conferido contra a forma do contrato (decimal `0.00`, data `AAAA-MM-DD`, mascara `****NNNN`, situacao do
enum): o que nao tem a forma vira AUSENTE, nunca corrigido nem completado — e' texto que vai ao modelo.
"""

from __future__ import annotations

import re
from typing import Final, Protocol, runtime_checkable

from maezo.agents.lucas.fonte_cobranca import CompetenciaCobranca, FatosCobranca, Indisponivel
from maezo.ports.billing_status import BillingStatusPort, BillingStatusView, CompetenciaBilling
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

#: Janela de competencias pedida a AMH. O contrato aceita 1 a 36; 12 e' o padrao do contrato.
JANELA_MESES: int = 12

#: As formas do contrato (`billing-status.openapi.yaml`). Valor fora delas e' AUSENTE (nunca corrigido).
_DECIMAL: Final[re.Pattern[str]] = re.compile(r"^[0-9]+\.[0-9]{2}$")
_DATA: Final[re.Pattern[str]] = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
_MASCARA: Final[re.Pattern[str]] = re.compile(r"^\*{4}[0-9]{4}$")
_COMPETENCIA: Final[re.Pattern[str]] = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_SITUACOES: Final[frozenset[str]] = frozenset({"paga", "em_aberto", "vencida", "cancelada", "sem_titulo"})


def _na_forma(valor: object, forma: re.Pattern[str]) -> str | None:
    return valor if isinstance(valor, str) and forma.match(valor) else None


def _competencia_como_fato(c: CompetenciaBilling) -> CompetenciaCobranca | None:
    """Uma competencia do contrato -> fato do Lucas, campo a campo na forma do contrato. `None` quando
    nem a competencia nem a situacao tem forma valida: sem elas o resto nao diz de que mes se trata."""
    if not _na_forma(c.competencia, _COMPETENCIA) or c.situacao not in _SITUACOES:
        return None
    return CompetenciaCobranca(
        competencia=c.competencia,
        situacao=c.situacao,
        vencimento=_na_forma(c.vencimento, _DATA),
        valor_total=_na_forma(c.valor_total, _DECIMAL),
        valor_coparticipacao=_na_forma(c.valor_coparticipacao, _DECIMAL),
        valor_saldo=_na_forma(c.valor_saldo, _DECIMAL),
        liquidado_em=_na_forma(c.liquidado_em, _DATA),
        boleto=_na_forma(c.boleto_numero_mascarado, _MASCARA),
    )


def _dias_validos(dias: object) -> int | None:
    return dias if isinstance(dias, int) and not isinstance(dias, bool) and dias >= 0 else None


@runtime_checkable
class ResolvedorDeSujeito(Protocol):
    """Pseudonimo do Maezo -> `portable_subject_ref`, ou `None` quando nao ha' (um) sujeito unico.

    Mais de um candidato (titular e dependente no mesmo aparelho) TAMBEM e' `None` aqui: escolher o primeiro
    seria falar da cobranca de outra pessoa. A pergunta "de quem?" e' da conversa, nao desta fonte.
    """

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None: ...


@runtime_checkable
class FonteDeConsentimento(Protocol):
    """A decisao de consentimento que autoriza ler a cobranca daquele sujeito, ou `None` se nao ha'."""

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None: ...


def _competencia_de_referencia(view: BillingStatusView, competencia: str | None) -> CompetenciaBilling | None:
    """A competencia de que se tira o rotulo do boleto: a pedida; senao a mais recente em atraso/aberta."""
    if competencia is not None:
        return next((c for c in view.competencias if c.competencia == competencia), None)
    pendentes = [c for c in view.competencias if c.situacao in ("vencida", "em_aberto")]
    if pendentes:
        return max(pendentes, key=lambda c: c.competencia)
    return view.competencias[0] if view.competencias else None


class FonteCobrancaAmh:
    """`FonteCobranca` sobre o contrato publicado. Nunca levanta por falha da dependencia."""

    def __init__(
        self,
        *,
        billing: BillingStatusPort,
        resolvedor: ResolvedorDeSujeito,
        consentimento: FonteDeConsentimento,
        purpose_of_use: str,
    ) -> None:
        self._billing = billing
        self._resolvedor = resolvedor
        self._consentimento = consentimento
        self._purpose = purpose_of_use

    async def fatos(
        self, pseudo_id: str, competencia: str | None, *, phone_hash: str | None = None
    ) -> FatosCobranca | Indisponivel:
        if not pseudo_id:
            return Indisponivel("pseudo_id_ausente")
        if competencia is not None and not competencia_valida(competencia):
            return Indisponivel("competencia_invalida")
        try:
            ref = await self._resolvedor.portable_ref(pseudo_id, phone_hash=phone_hash)
            if not ref:
                return Indisponivel("sujeito_nao_resolvido")
            consentimento = await self._consentimento.decisao(ref, self._purpose)
            if not consentimento:
                return Indisponivel("sem_consentimento")
            leitura = await self._billing.get_billing_status(
                ref,
                purpose_of_use=self._purpose,
                consent_decision_ref=consentimento,
                competencia=competencia,
                janela_meses=JANELA_MESES,
            )
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            return Indisponivel("fonte_indisponivel")
        if not leitura.succeeded or leitura.value is None:
            # Toda recusa (consentimento, tempo, contrato violado, fonte fora) e' "nao sei": o motivo fino
            # fica no log do adaptador; aqui o Lucas so' precisa saber que nao ha' fato.
            return Indisponivel("fonte_indisponivel")
        view = leitura.value
        resumo = view.resumo
        if resumo.status_conciliado is None or resumo.ciclos_sem_conciliacao is None:
            return Indisponivel("fato_ausente")
        referencia = _competencia_de_referencia(view, competencia)
        competencias = tuple(
            fato
            for fato in (_competencia_como_fato(c) for c in view.competencias[:JANELA_MESES])
            if fato is not None
        )
        return FatosCobranca(
            status_conciliado=resumo.status_conciliado,
            ciclos_sem_conciliacao=resumo.ciclos_sem_conciliacao,
            numero_boleto=(referencia.boleto_numero_mascarado or "") if referencia else "",
            cnab_ref=f"amh-billing:{view.fonte_atualizada_em[:10]}",
            valor_em_aberto=_na_forma(resumo.valor_em_aberto, _DECIMAL),
            dias_atraso_max=_dias_validos(resumo.dias_atraso_max),
            vencimento_referencia=_na_forma(referencia.vencimento, _DATA) if referencia else None,
            competencias=competencias,
        )

"""`FonteCobranca` REAL: os fatos de cobranca do Lucas lidos pelo contrato `billing-status` da AMH.

Substitui a `FonteCobrancaSimulada` quando o contrato estiver publicado e pinado (ADR-0037). Ate' la' ela
NAO e' ligada em lugar nenhum: `platform/webhooks/service.py` e as settings continuam so' com a simulada.

O CAMINHO, em quatro passos, cada um com a sua saida fechada:
  1. pseudonimo do Maezo -> `portable_subject_ref` (so' a AMH liga telefone a pessoa, XRD-05);
  2. consentimento para o proposito de atendimento ao beneficiario;
  3. leitura da situacao de cobranca pelo `BillingStatusPort`;
  4. traducao para os quatro fatos que a DMN do Lucas consome.

NENHUM passo inventa fato. Se a pessoa nao e' resolvida, se nao ha' consentimento, se a leitura falha ou se
a fonte nao tem `status_conciliado`/`ciclos_sem_conciliacao`, o retorno e' `Indisponivel` com um motivo de
classe fechado, os campos de conciliacao ficam AUSENTES na entrada do Lucas e quem decide e' a DMN (o
catch-all dela escala a um humano). O Lucas nunca afirma pagamento nem atraso sem fato.

O que o Lucas recebe do boleto e' o rotulo MASCARADO (`****1234`) e a referencia de ORIGEM do dado
(`amh-billing:{data da fonte}`), nunca nosso numero, bloqueto, linha digitavel ou link.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from maezo.agents.lucas.fonte_cobranca import FatosCobranca, Indisponivel
from maezo.ports.billing_status import BillingStatusPort, BillingStatusView, CompetenciaBilling
from maezo.runtime.competencia import competencia_valida
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

#: Janela de competencias pedida a AMH. O contrato aceita 1 a 36; 12 e' o padrao do contrato.
JANELA_MESES: int = 12


@runtime_checkable
class ResolvedorDeSujeito(Protocol):
    """Pseudonimo do Maezo -> `portable_subject_ref`, ou `None` quando nao ha' (um) sujeito unico.

    Mais de um candidato (titular e dependente no mesmo aparelho) TAMBEM e' `None` aqui: escolher o primeiro
    seria falar da cobranca de outra pessoa. A pergunta "de quem?" e' da conversa, nao desta fonte.
    """

    async def portable_ref(self, pseudo_id: str) -> str | None: ...


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

    async def fatos(self, pseudo_id: str, competencia: str | None) -> FatosCobranca | Indisponivel:
        if not pseudo_id:
            return Indisponivel("pseudo_id_ausente")
        if competencia is not None and not competencia_valida(competencia):
            return Indisponivel("competencia_invalida")
        try:
            ref = await self._resolvedor.portable_ref(pseudo_id)
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
        return FatosCobranca(
            status_conciliado=resumo.status_conciliado,
            ciclos_sem_conciliacao=resumo.ciclos_sem_conciliacao,
            numero_boleto=(referencia.boleto_numero_mascarado or "") if referencia else "",
            cnab_ref=f"amh-billing:{view.fonte_atualizada_em[:10]}",
        )

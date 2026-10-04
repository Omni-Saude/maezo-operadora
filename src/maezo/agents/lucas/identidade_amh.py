"""As duas pecas de identidade e consentimento que a `FonteCobrancaAmh` precisa, sobre os ports da AMH.

`ResolvedorDeSujeitoAmh`: hash do telefone -> `portable_subject_ref`, SO' quando ha' exatamente UM candidato.
Dois ou mais (titular e dependente no mesmo aparelho) e nenhum viram `None`: escolher o primeiro seria falar
da cobranca de outra pessoa, e a pergunta "de quem?" pertence a conversa, nao a esta fonte.

`FonteDeConsentimentoAmh`: a decisao de consentimento mais recente, so' se `granted`. Sem decisao, revogada,
expirada ou fonte fora: `None` (a cobranca nao e' lida). Fail-closed, nunca "na duvida, le".

Nenhuma das duas levanta por falha da dependencia: recusa do port vira `None`.
"""

from __future__ import annotations

from maezo.ports.consent import ConsentDecisionSource
from maezo.ports.subject_resolution import SubjectResolutionPort


class ResolvedorDeSujeitoAmh:
    def __init__(
        self,
        *,
        port: SubjectResolutionPort,
        amh_tenant: str,
        hash_scheme: str,
        purpose_of_use: str,
    ) -> None:
        self._port = port
        self._tenant = amh_tenant
        self._scheme = hash_scheme
        self._purpose = purpose_of_use

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        del pseudo_id  # a AMH resolve pelo hash do telefone; o pseudonimo derivado nao e' chave dela
        if not phone_hash:
            return None
        resultado = await self._port.resolve_by_phone(
            phone_hash,
            amh_tenant=self._tenant,
            hash_scheme=self._scheme,
            purpose_of_use=self._purpose,
        )
        if not resultado.succeeded or resultado.value is None:
            return None
        resolucao = resultado.value
        if resolucao.resultado != "unico" or len(resolucao.candidatos) != 1:
            return None
        return resolucao.candidatos[0].portable_subject_ref


class FonteDeConsentimentoAmh:
    def __init__(self, source: ConsentDecisionSource) -> None:
        self._source = source

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        resultado = await self._source.latest_decision(portable_ref, purpose_of_use=purpose_of_use)
        if not resultado.succeeded or resultado.value is None:
            return None
        decisao = resultado.value
        # A decisao e' do sujeito e do proposito pedidos: um eco diferente nao autoriza nada.
        if (
            not decisao.granted
            or decisao.portable_subject_ref != portable_ref
            or decisao.purpose_of_use != purpose_of_use
        ):
            return None
        return decisao.consent_decision_ref

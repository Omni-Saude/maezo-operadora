"""As duas pecas de identidade e consentimento que a `FonteCobrancaAmh` precisa, sobre os ports da AMH.

`ResolvedorDeSujeitoAmh`: hash do telefone -> `portable_subject_ref`, SO' quando ha' exatamente UM candidato.
Dois ou mais (titular e dependente no mesmo aparelho) e nenhum viram `None`: escolher o primeiro seria falar
da cobranca de outra pessoa, e a pergunta "de quem?" pertence a conversa, nao a esta fonte.
`portable_ref_com_desfecho` expoe tambem o desfecho fechado (`unico`/`nenhum`/`multiplos`/`indisponivel`),
usado so' pelo aviso de identidade da Helena (DL-0078) para distinguir "nenhum candidato" das outras causas.

`FonteDeConsentimentoAmh`: a decisao de consentimento mais recente, so' se `granted`. Sem decisao, revogada,
expirada ou fonte fora: `None` (a cobranca nao e' lida). Fail-closed, nunca "na duvida, le".

`BaseLegalExecucaoDeContrato`: a peca LIGADA hoje no lugar do consentimento (decisao do dono de
06/10/2026). Devolve a referencia fixa da base legal, nunca uma decisao de consentimento.

Nenhuma levanta por falha da dependencia: recusa do port vira `None`.
"""

from __future__ import annotations

import math

from maezo.ports.consent import ConsentDecisionSource
from maezo.ports.errors import DEFAULT_PORT_TIMEOUT_SECONDS
from maezo.ports.subject_resolution import SubjectResolutionPort


class ResolvedorDeSujeitoAmh:
    def __init__(
        self,
        *,
        port: SubjectResolutionPort,
        amh_tenant: str,
        hash_scheme: str,
        purpose_of_use: str,
        timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    ) -> None:
        # Teto da chamada de resolucao ao port. O Lucas fica no padrao do port (5 s); a Helena passa o
        # prazo total da identidade (DL-0079), que o executor do gateway ainda limita a 30 s.
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("ResolvedorDeSujeitoAmh: timeout_seconds invalido")
        self._port = port
        self._tenant = amh_tenant
        self._scheme = hash_scheme
        self._purpose = purpose_of_use
        self._timeout = float(timeout_seconds)

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        ref, _ = await self.portable_ref_com_desfecho(pseudo_id, phone_hash=phone_hash)
        return ref

    async def portable_ref_com_desfecho(
        self, pseudo_id: str, *, phone_hash: str | None
    ) -> tuple[str | None, str]:
        """A referencia (so' com candidato UNICO) e o desfecho FECHADO da resolucao.

        Desfecho: `unico` (a referencia vem junto), `nenhum` (a AMH respondeu, com sucesso, que NAO ha'
        candidato: `resultado == "nenhum"` E conjunto vazio), `multiplos` (telefone compartilhado) ou
        `indisponivel` (sem hash, recusa do port, ou resposta incoerente — `unico` com outro numero de
        candidatos, `nenhum` com candidato). So' `nenhum` e' resposta DEFINITIVA de ausencia; o resto
        nunca pode ser lido como "nao e' beneficiario" (DL-0078).
        """
        del pseudo_id  # a AMH resolve pelo hash do telefone; o pseudonimo derivado nao e' chave dela
        if not phone_hash:
            return None, "indisponivel"
        resultado = await self._port.resolve_by_phone(
            phone_hash,
            amh_tenant=self._tenant,
            hash_scheme=self._scheme,
            purpose_of_use=self._purpose,
            timeout_seconds=self._timeout,
        )
        if not resultado.succeeded or resultado.value is None:
            return None, "indisponivel"
        resolucao = resultado.value
        if resolucao.resultado == "nenhum" and len(resolucao.candidatos) == 0:
            return None, "nenhum"
        if resolucao.resultado == "multiplos" and len(resolucao.candidatos) > 1:
            return None, "multiplos"
        if resolucao.resultado != "unico" or len(resolucao.candidatos) != 1:
            return None, "indisponivel"
        return resolucao.candidatos[0].portable_subject_ref, "unico"


#: Referencia fixa e auditavel da BASE LEGAL da leitura (LGPD art. 7o, V — execucao de contrato).
BASE_LEGAL_EXECUCAO_DE_CONTRATO: str = "base-legal:execucao-de-contrato"


class BaseLegalExecucaoDeContrato:
    """`FonteDeConsentimento` que NAO e' consentimento: devolve a base legal fixa da leitura.

    Decisao do dono (06/10/2026, `docs/decisions-log.md`): a leitura da situacao de cobranca do
    PROPRIO beneficiario, para atende-lo sobre o contrato dele, se apoia em execucao de contrato, e
    o consentimento ainda nao e' ingerido do lado da AMH. Esta peca NUNCA afirma que houve
    consentimento: o valor devolvido e' o rotulo da base legal (`base-legal:execucao-de-contrato`),
    e e' isso que o executor do gateway registra (hash) no elo de auditoria. O valor nunca vai na
    URL nem na query. `FonteDeConsentimentoAmh` (abaixo) continua sendo a fonte REAL, para quando o
    consentimento existir; trocar uma pela outra e' decisao do dono, nao deste codigo.
    """

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        if not portable_ref or not purpose_of_use:
            return None
        return BASE_LEGAL_EXECUCAO_DE_CONTRATO


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

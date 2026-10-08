"""A ponte entre o ACESSO verificado e os agentes: a referencia JA' resolvida, sem chamar a AMH de novo
(DL-0083).

Com o acesso do beneficiario ligado, quem diz QUEM e' a pessoa e' a verificacao (CPF, e nascimento quando
preciso),
nao o telefone: um telefone compartilhado por titular e dependente nao identifica ninguem, mas uma conversa
`verificado` tem uma referencia. Esta ponte entrega essa referencia aos DOIS caminhos que hoje resolvem pelo
telefone:

  * `ResolvedorVerificado` — no lugar do `ResolvedorDeSujeitoAmh` na fonte de cobranca do Lucas e na
  identidade da
    Helena. Devolve a referencia que o despachante registrou para este `beneficiario_pseudo_id` NO TURNO EM
    CURSO, ou
    `None` (fail-closed): uma conversa nao verificada nunca resolve ninguem, mesmo que algum caminho a chame.
  * `ConsentimentoDoAcesso` — a `FonteDeConsentimento` ligada quando o fluxo esta' ligado: devolve o
  `consent_ref` que
    a AMH deu ao registro do consentimento (so' metadado de auditoria do executor) e, enquanto o registro
    nao existe
    (gravacao pendente), cai na base legal de execucao de contrato de sempre (`BaseLegalExecucaoDeContrato`).
    O consentimento colhido NAO substitui o caminho da execucao de contrato: complementa.

`RefsVerificadas` e' memoria de PROCESSO, escrita pelo despachante so' durante o turno de uma conversa
verificada e
removida no fim (`finally`). Guarda so' referencias pseudonimas; nunca telefone, CPF ou texto.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from maezo.agents.lucas.fonte_cobranca_amh import FonteDeConsentimento


class RefsVerificadas:
    """`beneficiario_pseudo_id` -> (`portable_subject_ref`, `consent_ref`), so' durante o turno em curso."""

    __slots__ = ("_por_pseudo_id", "_por_ref")

    def __init__(self) -> None:
        self._por_pseudo_id: dict[str, str] = {}
        self._por_ref: dict[str, str | None] = {}

    def __repr__(self) -> str:
        return "RefsVerificadas(<redacted>)"

    @contextmanager
    def do_turno(self, pseudo_id: str, ref: str, consent_ref: str | None) -> Iterator[None]:
        """Registra a referencia verificada durante o `with` e a remove no fim, sempre."""
        self._por_pseudo_id[pseudo_id] = ref
        self._por_ref[ref] = consent_ref
        try:
            yield
        finally:
            self._por_pseudo_id.pop(pseudo_id, None)
            self._por_ref.pop(ref, None)

    def ref_de(self, pseudo_id: str) -> str | None:
        return self._por_pseudo_id.get(pseudo_id)

    def consent_de(self, ref: str) -> str | None:
        return self._por_ref.get(ref)


class ResolvedorVerificado:
    """`ResolvedorDeSujeito` (+ `ResolvedorComDesfecho`) que so' conhece a ref. da conversa verificada."""

    def __init__(self, refs: RefsVerificadas) -> None:
        self._refs = refs

    async def portable_ref(self, pseudo_id: str, *, phone_hash: str | None) -> str | None:
        ref, _ = await self.portable_ref_com_desfecho(pseudo_id, phone_hash=phone_hash)
        return ref

    async def portable_ref_com_desfecho(
        self, pseudo_id: str, *, phone_hash: str | None
    ) -> tuple[str | None, str]:
        del phone_hash  # a identidade vem da verificacao; o telefone nao decide nada aqui
        ref = self._refs.ref_de(pseudo_id)
        return (ref, "unico") if ref else (None, "indisponivel")


class ConsentimentoDoAcesso:
    """`FonteDeConsentimento`: o `consent_ref` do acesso, ou a base legal de execucao de contrato."""

    def __init__(self, refs: RefsVerificadas, fallback: FonteDeConsentimento) -> None:
        self._refs = refs
        self._fallback = fallback

    async def decisao(self, portable_ref: str, purpose_of_use: str) -> str | None:
        consent_ref = self._refs.consent_de(portable_ref)
        if consent_ref:
            return consent_ref
        return await self._fallback.decisao(portable_ref, purpose_of_use)


__all__ = ["ConsentimentoDoAcesso", "RefsVerificadas", "ResolvedorVerificado"]

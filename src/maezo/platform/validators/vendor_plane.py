"""Production-validator do plano vendor — verificação do estado DEFAULT-OFF (VW5+, WAVES §2.9-ii).

**O que é.** Um verificador LOCAL, executável por teste ou CLI (`python -m
maezo.platform.validators.vendor_plane`), que recebe a descrição do ALVO (o que o deploy tem
HOJE: perfil de capacidades, estado de migrations, contagem de bootstraps de worker, linhas do
store de autoridade vendor, ledger de evidência da missão) e responde um relatório PASS/FAIL por
item. Ele é o INQUÉRITO legível que o go/no-go humano lê — não é o go/no-go.

**Os cinco itens (WAVES §2.9 a–e):**

- **(a) flags default-off** — o default de `PortalSettings.capabilities` na base de código é
  exatamente `"identity"` (o default que nenhuma onda vendor tocou) E o perfil deployado não
  concede a audiência `vendor`. Perfil deployado desconhecido = FAIL (fail-closed), nunca
  "assume-se que está default".
- **(b) migrations 0019+0020+0021 head linear** — a base de código tem UMA head (`0021`), `0021`
  revisa `0020` (leitura estrutural local, via `ScriptDirectory` do alembic) E o alvo reporta
  essa head com `0019`, `0020` e `0021` aplicados. Heads bifurcadas, revisão faltando ou estado
  desconhecido = FAIL.
- **(c) readiness 19/19** — o alvo reporta `len(ALL_WORKER_BOOTSTRAPS) == 19` (o número é
  CONTRATO, constante nomeada aqui — derivá-lo do mesmo tuple que o alvo importou anularia a
  checagem) E a base de código local ainda tem exatamente 19. Um 20º bootstrap é uma mudança de
  contrato que passa por aqui DE NOVO, consciente — nunca por um `>=` complacente.
  RE-APROVAÇÃO CONSCIENTE 18→19 (VW4 wiring GP11, 2026-10-07): o 19º bootstrap é
  `register_suppression_workers` (OP20, insumos DPO ACEITOS pelo dono — VW0-DECISION-REGISTER
  §"INCORPORAÇÃO VW4-ANSWERS", sha ab262f7b…); a head 0020→0021 é o store de supressão
  `portal_vendor_suppressions` do mesmo pacote.
- **(d) zero memberships vendor publicadas no estado default, com `SOURCE_UNAVAILABLE`
  operante** — o store de autoridade (migration `0019`) tem ZERO linhas no alvo, e a sonda
  comportamental prova que a inércia é HONESTA: o job de publicação VW1-P0 (`VendorMembershipPublicationJob`),
  contra store vazio e contra store ausente, RECUSA com o erro tipado (`ReadRefusalError`) e
  nunca chega ao publisher. Um job que respondesse `VendorJobResult(0, 0)` para store vazio —
  zero FABRICADO — viraria este item VERMELHO. Linhas > 0 no estado default = FAIL (há
  membership publicada sem ato humano verificado); desconhecido = FAIL.
- **(e) ledger de evidência da missão verde** — verificação de EXISTÊNCIA/ARQUIVO (regular,
  não vazio, decodável, com digest SHA-256 registrado como evidência), NUNCA parse de conteúdo:
  o ledger vive em `docs/audits/` (gitignored, editado concorrentemente) e pode carregar
  material sensível de missão. Arquivo ausente, vazio, ilegível ou caminho desconhecido = FAIL.

**Fail-closed é o default em TODOS os itens.** `None` (dado não coletado) e exceção (coleta
falhou) são FAIL com motivo `unknown`/`error` — a ausência de prova não é prova de conformidade.

**O que isto NÃO é.** Não é gate de CI (isso é `scripts/ci/`), não aplica nada no alvo, não liga
flag de produção, não faz deploy hook, não toca DMN/BPMN, não avalia RATIFY (os fences
PW1-*/PW2-A são intocados — este módulo é LEITOR). O go/no-go de deploy permanece ato HUMANO;
este relatório apenas deixa o estado VERIFICÁVEL para quem decide.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

import maezo
from maezo.gateway.human.queue import ReadRefusalError
from maezo.gateway.human.vendor_membership_administration import VendorAdministrationReason
from maezo.gateway.human.vendor_membership_publication_job import (
    VendorMembershipPublicationJob,
    VendorPublicationLedger,
)
from maezo.portal.api.config import PortalSettings
from maezo.tools.workers.bootstrap import ALL_WORKER_BOOTSTRAPS

__all__ = [
    "DEFAULT_CAPABILITIES_PROFILE",
    "EXPECTED_WORKER_BOOTSTRAP_COUNT",
    "VENDOR_AUDIENCE_TOKEN",
    "VendorPlaneCheck",
    "VendorPlaneReport",
    "VendorPlaneTarget",
    "probe_source_unavailable_operative",
    "validate_vendor_plane",
]

#: (c) — o 18/18 é CONTRATO (docstring do composition root e readiness do worker-runtime), não
#: um valor a re-derivar do tuple do alvo: derivar do mesmo tuple que se audita é auto-certificação.
EXPECTED_WORKER_BOOTSTRAP_COUNT: Final = 18

#: (a) — o default de capabilities que nenhuma onda vendor tocou (VW1-P4 adicionou o quarto
#: literal; o DEFAULT permaneceu este).
DEFAULT_CAPABILITIES_PROFILE: Final = "identity"

#: (a) — o token de audiência cuja presença num perfil deployado indica flag vendor ligada.
VENDOR_AUDIENCE_TOKEN: Final = "vendor"

#: (d) — a razão tipada cuja operância a sonda comporta mais abaixo.
SOURCE_UNAVAILABLE_REASON: Final = "SOURCE_UNAVAILABLE"

#: (b) — a head linear esperada e as revisões vendor cuja aplicação o alvo deve reportar
#: (0021 = store de supressão GP11 — VW4 wiring, insumos aceitos sha ab262f7b…).
EXPECTED_HEAD: Final = "0021"
#: O pai IMEDIATO da head (o par de revisão que o item (b) prova estruturalmente).
EXPECTED_HEAD_PARENT: Final = "0020"
VENDOR_MIGRATIONS: Final = ("0019", "0020", "0021")

#: (e) — quantos bytes bastam para probar decodabilidade SEM parse de conteúdo.
_LEDGER_PROBE_BYTES: Final = 4096


# ---------------------------------------------------------------------------
# Alvo e relatório
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VendorPlaneTarget:
    """A descrição do alvo, coletada por quem executa o validator.

    TODO campo é o que o deploy TEM; `None` é "não coletado" e o item correspondente FAILA
    (fail-closed). Nada aqui é inferido — o validator nunca vai ao deploy buscar o que o
    operador não trouxe.
    """

    #: (a) — o valor efetivo de `MAEZO_PORTAL_CAPABILITIES` no alvo (o literal deployado).
    capabilities_deployed: str | None = None
    #: (b) — as heads que o alembic do alvo reporta (`alembic heads`), na ordem reportada.
    migration_heads: tuple[str, ...] | None = None
    #: (b) — as revisões aplicadas no banco do alvo (`alembic current`/`alembic history -r`).
    applied_migrations: tuple[str, ...] | None = None
    #: (c) — `len(ALL_WORKER_BOOTSTRAPS)` do processo/imagem do alvo (readiness 18/18).
    worker_bootstrap_count: int | None = None
    #: (d) — linhas do store de autoridade vendor (`portal_vendor_channels`, migration 0019).
    published_vendor_memberships: int | None = None
    #: (e) — caminho do ledger de evidência da missão (existência/arquivo, sem parse).
    evidence_ledger_path: Path | None = None


class VendorPlaneCheck(BaseModel):
    """Um item do relatório: nome, veredito, motivo legível e fatos observados.

    `observed` carrega só escalares (`str`/`int`/`bool`/`None`) — o relatório inteiro é
    JSON-serializável e não carrega conteúdo de missão (o digest do ledger é evidência, não
    conteúdo).
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )

    name: str
    passed: bool
    detail: str
    observed: Mapping[str, str | int | bool | None] = Field(default_factory=dict)


class VendorPlaneReport(BaseModel):
    """Relatório estruturado dos cinco itens; `passed` só com os cinco PASS."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )

    checks: tuple[VendorPlaneCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def by_name(self) -> Mapping[str, VendorPlaneCheck]:
        return {check.name: check for check in self.checks}

    def as_json(self) -> str:
        import json

        return json.dumps(
            {
                "schema": "maezo-vendor-plane-validation/v1",
                "passed": self.passed,
                "checks": [
                    {
                        "name": check.name,
                        "passed": check.passed,
                        "detail": check.detail,
                        "observed": dict(check.observed),
                    }
                    for check in self.checks
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )


# ---------------------------------------------------------------------------
# Leituras estruturais locais (a base de código SOB validação)
# ---------------------------------------------------------------------------


def portal_capabilities_default() -> str:
    """O default de `PortalSettings.capabilities` NA BASE DE CÓDIGO (não o deployado)."""
    default = PortalSettings.model_fields["capabilities"].default
    if not isinstance(default, str):
        raise TypeError("capabilities default não é str")
    return default


def local_worker_bootstrap_count() -> int:
    """`len(ALL_WORKER_BOOTSTRAPS)` na base de código sob validação."""
    return len(ALL_WORKER_BOOTSTRAPS)


def local_migration_chain() -> tuple[tuple[str, ...], dict[str, str]]:
    """`(heads, edges)` do script alembic local — a head linear é lida do alembic, não de regex.

    Levanta em qualquer estado que o alembic recuse (bifurcação malformada, revisão repetida) —
    e o chamador converte a exceção em FAIL (fail-closed).
    """
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    versions_dir = Path(maezo.__file__).parent / "platform" / "migrations"
    config = AlembicConfig()
    config.set_main_option("script_location", str(versions_dir))
    script = ScriptDirectory.from_config(config)
    heads = tuple(sorted(script.get_heads()))
    edges = {
        revid: str(revision.down_revision)
        for revid, revision in ((r.revision, r) for r in script.walk_revisions())
    }
    return heads, edges


# ---------------------------------------------------------------------------
# Sonda comportamental (d): SOURCE_UNAVAILABLE operante, zero NUNCA fabricado
# ---------------------------------------------------------------------------


class _FixedChannelReader:
    """`VendorChannelReader` que devolve as linhas fixadas (tuple vazio = store VAZIO;
    `None` = store AUSENTE — os dois são UNKNOWN pelo contrato do job, nunca zero)."""

    def __init__(self, rows: tuple[object, ...] | None) -> None:
        self._rows = rows

    async def channels(self, tenant: str) -> tuple[object, ...] | None:
        return self._rows


class _ProbeMustNotBeReached:
    """Seams que, se alcançados, denunciam: um store vazio NÃO pode chegar a membership nem a
    publisher — qualquer chamada aqui é a fabricação que o item (d) existe para acusar."""

    async def membership(self, tenant: str, channel_ref: str) -> object:
        raise AssertionError("store vazio não pode chegar à leitura de membership")

    async def publish(self, payload: bytes) -> None:
        raise AssertionError("store vazio não pode chegar ao publisher de acesso")


async def _probe_source_unavailable_async(*, tenant: str) -> tuple[bool, str]:
    for label, rows in (("store_vazio", ()), ("store_ausente", None)):
        with tempfile.TemporaryDirectory(prefix="vendor-plane-validator-") as tmp:
            ledger = VendorPublicationLedger(path=Path(tmp) / "ledger.json", tenant=tenant)
            job = VendorMembershipPublicationJob(
                channels=_FixedChannelReader(rows),  # type: ignore[arg-type]
                memberships=_ProbeMustNotBeReached(),  # type: ignore[arg-type]
                publisher=_ProbeMustNotBeReached(),
                ledger=ledger,
            )
            try:
                await job.run()
            except ReadRefusalError:
                continue
            except BaseException as exc:  # a sonda relata QUALQUER desfecho
                return False, f"{label}: desfecho inesperado {type(exc).__name__}"
            return False, f"{label}: job respondeu sem recusar — zero fabricado ou publisher alcançado"
    return True, "store vazio e store ausente recusam com ReadRefusalError; publisher nunca alcançado"


def probe_source_unavailable_operative(*, tenant: str = "validator-probe-tenant") -> tuple[bool, str]:
    """Roda o job VW1-P0 REAL contra store vazio e store ausente (wrapper síncrono).

    PASS sse as duas rodadas recusarem com `ReadRefusalError` (a inércia honesta do job) —
    um job que iterasse um conjunto vazio e respondesse `VendorJobResult(0, 0)` (zero fabricado)
    ou que alcançasse o publisher torna a sonda FAIL. Exceção de qualquer natureza propaga e é
    convertida em FAIL pelo item (d) — fail-closed dos dois lados.

    `asyncio.run` por contrato de superfície síncrona: chamado DENTRO de um loop em execução,
    levanta `RuntimeError` — e o item (d) converte isso em FAIL, nunca em PASS por omissão.
    """
    probe: Coroutine[Any, Any, tuple[bool, str]] = _probe_source_unavailable_async(tenant=tenant)
    return asyncio.run(probe)


# ---------------------------------------------------------------------------
# Os cinco itens
# ---------------------------------------------------------------------------


def _check_vendor_flags_default_off(target: VendorPlaneTarget) -> VendorPlaneCheck:
    name = "vendor_flags_default_off"
    try:
        default = portal_capabilities_default()
    except Exception as exc:  # fail-closed: default ilegível é FAIL, não exceção
        return VendorPlaneCheck(
            name=name, passed=False, detail=f"unknown: default de capabilities ilegível ({exc})", observed={}
        )
    default_ok = default == DEFAULT_CAPABILITIES_PROFILE
    observed: dict[str, str | int | bool | None] = {
        "capabilities_default": default,
        "default_untouched": default_ok,
    }
    deployed = target.capabilities_deployed
    if deployed is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: perfil deployado não coletado (fail-closed)",
            observed=observed,
        )
    audiences = [part.strip() for part in deployed.split(",")]
    vendor_on = VENDOR_AUDIENCE_TOKEN in audiences
    shape_ok = all(audiences) and len(set(audiences)) == len(audiences)
    observed.update(
        capabilities_deployed=deployed,
        vendor_granted=vendor_on,
        profile_shape_ok=shape_ok,
    )
    passed = default_ok and not vendor_on and shape_ok
    detail = (
        f"default '{default}' intacto e sem '{VENDOR_AUDIENCE_TOKEN}' no perfil deployado"
        if passed
        else (
            f"default de capabilities na base é '{default}' (esperado '{DEFAULT_CAPABILITIES_PROFILE}')"
            if not default_ok
            else f"perfil deployado '{deployed}' concede '{VENDOR_AUDIENCE_TOKEN}'"
            if vendor_on
            else f"perfil deployado '{deployed}' tem forma inválida"
        )
    )
    return VendorPlaneCheck(name=name, passed=passed, detail=detail, observed=observed)


def _check_migrations_linear_head(target: VendorPlaneTarget) -> VendorPlaneCheck:
    name = "migrations_linear_head"
    observed: dict[str, str | int | bool | None] = {}
    try:
        local_heads, edges = local_migration_chain()
    except Exception as exc:  # fail-closed
        return VendorPlaneCheck(
            name=name, passed=False, detail=f"unknown: cadeia local ilegível ({exc})", observed=observed
        )
    chain_ok = local_heads == (EXPECTED_HEAD,) and edges.get(EXPECTED_HEAD) == EXPECTED_HEAD_PARENT
    observed.update(
        local_heads=",".join(local_heads),
        edge_head_revises_previous=edges.get(EXPECTED_HEAD),
        chain_linear_local=chain_ok,
    )
    if not chain_ok:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail=f"cadeia local divergiu: heads={local_heads!r} (esperado ('{EXPECTED_HEAD}',))",
            observed=observed,
        )
    heads = target.migration_heads
    if heads is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: heads do alvo não coletadas (fail-closed)",
            observed=observed,
        )
    applied = target.applied_migrations
    if applied is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: revisões aplicadas no alvo não coletadas (fail-closed)",
            observed=observed,
        )
    applied_set = set(applied)
    heads_ok = tuple(heads) == (EXPECTED_HEAD,)
    applied_ok = all(revision in applied_set for revision in VENDOR_MIGRATIONS)
    observed.update(
        target_heads=",".join(heads),
        vendor_migrations_applied=applied_ok,
    )
    passed = heads_ok and applied_ok
    detail = (
        f"head linear '{EXPECTED_HEAD}' no alvo com {VENDOR_MIGRATIONS} aplicados"
        if passed
        else f"alvo com heads={tuple(heads)!r} e aplicados={sorted(applied_set)!r}"
    )
    return VendorPlaneCheck(name=name, passed=passed, detail=detail, observed=observed)


def _check_readiness_workers(target: VendorPlaneTarget) -> VendorPlaneCheck:
    name = "readiness_workers_18_18"
    observed: dict[str, str | int | bool | None] = {}
    try:
        local_count = local_worker_bootstrap_count()
    except Exception as exc:  # fail-closed
        return VendorPlaneCheck(
            name=name, passed=False, detail=f"unknown: bootstraps locais ilegíveis ({exc})", observed=observed
        )
    local_ok = local_count == EXPECTED_WORKER_BOOTSTRAP_COUNT
    observed["worker_bootstraps_local"] = local_count
    observed["worker_bootstraps_expected"] = EXPECTED_WORKER_BOOTSTRAP_COUNT
    if not local_ok:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail=(
                f"base local tem {local_count} bootstraps (contrato {EXPECTED_WORKER_BOOTSTRAP_COUNT}); "
                "mudança de contrato exige re-aprovação consciente deste validator"
            ),
            observed=observed,
        )
    count = target.worker_bootstrap_count
    if count is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: contagem de bootstraps do alvo não coletada (fail-closed)",
            observed=observed,
        )
    observed["worker_bootstraps_target"] = count
    passed = count == EXPECTED_WORKER_BOOTSTRAP_COUNT
    detail = (
        f"alvo em {count}/{EXPECTED_WORKER_BOOTSTRAP_COUNT}"
        if passed
        else f"alvo em {count}/{EXPECTED_WORKER_BOOTSTRAP_COUNT} — readiness degradada"
    )
    return VendorPlaneCheck(name=name, passed=passed, detail=detail, observed=observed)


def _check_default_state_unpublished(target: VendorPlaneTarget) -> VendorPlaneCheck:
    name = "vendor_memberships_unpublished_default"
    observed: dict[str, str | int | bool | None] = {}
    reason_exists = SOURCE_UNAVAILABLE_REASON in {reason.value for reason in VendorAdministrationReason}
    observed["source_unavailable_reason_present"] = reason_exists
    rows = target.published_vendor_memberships
    if rows is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: linhas do store de autoridade vendor não coletadas (fail-closed)",
            observed=observed,
        )
    observed["published_vendor_memberships"] = rows
    if rows != 0:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail=f"estado default com {rows} linha(s) de credenciamento vendor — flag-off violado em dado",
            observed=observed,
        )
    try:
        probe_ok, probe_detail = probe_source_unavailable_operative()
    except Exception as exc:  # fail-closed
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail=f"error: sonda SOURCE_UNAVAILABLE levantou {exc!r}",
            observed=observed,
        )
    observed["source_unavailable_operative"] = probe_ok
    passed = probe_ok and reason_exists
    detail = (
        f"store vazio ({rows} linhas) e {SOURCE_UNAVAILABLE_REASON} operante: {probe_detail}"
        if passed
        else f"store vazio, porém inércia NÃO operante: {probe_detail}"
    )
    return VendorPlaneCheck(name=name, passed=passed, detail=detail, observed=observed)


def _check_evidence_ledger(target: VendorPlaneTarget) -> VendorPlaneCheck:
    """Existência/ARQUIVO, nunca parse de conteúdo (ledger vive em dir gitignored e pode ter
    material sensível de missão). Digest SHA-256 é evidência, não exposição."""
    name = "evidence_ledger_green"
    observed: dict[str, str | int | bool | None] = {}
    path = target.evidence_ledger_path
    if path is None:
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail="unknown: caminho do ledger de evidência da missão não informado (fail-closed)",
            observed=observed,
        )
    observed["evidence_ledger_path"] = str(path)
    try:
        if not path.is_file():
            return VendorPlaneCheck(
                name=name, passed=False, detail=f"ledger ausente ou não-arquivo: {path}", observed=observed
            )
        size = path.stat().st_size
        observed["evidence_ledger_bytes"] = size
        if size == 0:
            return VendorPlaneCheck(
                name=name, passed=False, detail="ledger vazio (0 bytes)", observed=observed
            )
        with path.open("rb") as handle:
            probe = handle.read(_LEDGER_PROBE_BYTES)
            probe.decode("utf-8")  # decodabilidade; o CONTEÚDO não é lido por este validator
            digest = hashlib.sha256(probe)
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        observed["evidence_ledger_sha256"] = digest.hexdigest()
    except Exception as exc:  # fail-closed
        return VendorPlaneCheck(
            name=name,
            passed=False,
            detail=f"error: ledger ilegível ({type(exc).__name__})",
            observed=observed,
        )
    return VendorPlaneCheck(
        name=name,
        passed=True,
        detail=f"ledger presente, regular, não vazio e utf-8 decodável ({size} bytes; digest registrado)",
        observed=observed,
    )


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def validate_vendor_plane(target: VendorPlaneTarget) -> VendorPlaneReport:
    """Roda os cinco itens contra o alvo descrito. Síncrono, sem efeito no alvo."""
    return VendorPlaneReport(
        checks=(
            _check_vendor_flags_default_off(target),
            _check_migrations_linear_head(target),
            _check_readiness_workers(target),
            _check_default_state_unpublished(target),
            _check_evidence_ledger(target),
        )
    )


# ---------------------------------------------------------------------------
# CLI local (lado do operador: colete o alvo, rode, leia)
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: cada fato do alvo é uma flag; o omitido é `None` e FAILA (fail-closed).

    Exit 0 sse o relatório PASSA. A flags NUNCA alteram o alvo — este comando é leitor.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m maezo.platform.validators.vendor_plane",
        description="Production-validator do plano vendor (estado default-off) — leitor, nunca ator.",
    )
    parser.add_argument("--capabilities", help="perfil de capacidades deployado (MAEZO_PORTAL_CAPABILITIES)")
    parser.add_argument("--migration-heads", help="heads do alembic do alvo, separadas por vírgula")
    parser.add_argument("--applied-migrations", help="revisões aplicadas no alvo, separadas por vírgula")
    parser.add_argument("--worker-count", type=int, help="len(ALL_WORKER_BOOTSTRAPS) no alvo (readiness)")
    parser.add_argument("--published-memberships", type=int, help="linhas de portal_vendor_channels no alvo")
    parser.add_argument("--evidence-ledger", type=Path, help="caminho do ledger de evidência da missão")
    parser.add_argument("--json", action="store_true", help="imprime o relatório em JSON")
    args = parser.parse_args(argv)

    def _csv(value: str | None) -> tuple[str, ...] | None:
        return None if value is None else tuple(part.strip() for part in value.split(","))

    report = validate_vendor_plane(
        VendorPlaneTarget(
            capabilities_deployed=args.capabilities,
            migration_heads=_csv(args.migration_heads),
            applied_migrations=_csv(args.applied_migrations),
            worker_bootstrap_count=args.worker_count,
            published_vendor_memberships=args.published_memberships,
            evidence_ledger_path=args.evidence_ledger,
        )
    )
    if args.json:
        print(report.as_json())
    else:
        for check in report.checks:
            mark = "PASS" if check.passed else "FAIL"
            print(f"[{mark}] {check.name}: {check.detail}")
        print(f"VEREDITO: {'PASS' if report.passed else 'FAIL'} — o go/no-go de deploy permanece HUMANO.")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover - exercitado pela CLI, não pela suíte
    raise SystemExit(main())

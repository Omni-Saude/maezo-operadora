"""Proveniencia das tabelas K1 do rco na trilha de auditoria (ADR-0075 secao (f), MZO-5a).

O QUE ISTO FAZ. Para uma decisao DMN que vem do pacote K1 do rco (arquivo em
`spec/processes/dmn/rco/`, id de decisao prefixado `rco_`), a entrada de `AuditRecord.dmn_versions`
ganha, alem das chaves do motor (`version`, `id`, `deploymentId`), ate cinco campos:

- `shaDmn`: o digest `sha256:<hex>` do arquivo `.dmn`, lido do pin
  `config/integrations/rco/rules.lock.json` (secao `artifacts` ou `exemplos_sinteticos`). Para tabela
  do pacote e o mesmo valor de `decisoes[].sha256` do manifesto K1 (contrato-k1.md §1). NUNCA do
  motor: o ADR manda atestar o hash que o pin fixou no deploy. Este leitor ainda re-hasheia o arquivo
  de bordo e recusa divergencia, para que o sha gravado seja o dos bytes que o deploy publica
  (`platform/deploy/engine_deploy.py`, nome de recurso `rco/<arquivo>.dmn`).
- `versaoRegra`: do manifesto K1 que o pin fixa (`manifest_pin`): `regras[].versaoRegra` da regra
  cujo `idRegra` a linha casada emitiu, naquela decisao. Sem manifesto (`sem_pacote`, ou exemplo
  sintetico do Maezo) ou sem `idRegra` na linha, o campo fica AUSENTE (nunca `null`, nunca
  inventado). `idRegra` que o manifesto nao lista para a decisao levanta `RcoRulesPinError`.
  `regras[].sha256_yaml` fica exposto em `RcoPinnedTable.regras` (sem chave nova em `dmn_versions`:
  o ADR-0075 (f) define so os tres campos).
- `ativacaoPacote` e `exigeAditivo` (ADR-0077 §4, MZO-5a): `regras[].ativacao` e `regras[].exige_aditivo`
  da mesma regra, so quando o manifesto pinado e MAJOR 2; `exigeAditivo` e o texto `"true"`/`"false"`.
  Ausentes no MAJOR 1, sem manifesto e sem `idRegra` (nunca `null`, nunca inventados), como `versaoRegra`.
- `idRegra`: da SAIDA `idRegra` da linha casada (o ADR diz "da saida da tabela"). So entra quando a
  avaliacao devolveu exatamente uma linha com essa saida nao nula e nao vazia (`""` e o vazio canonico
  do K1, contrato §6: linha nao adversa sem regra identificada); sem ela fica AUSENTE. Valor fora
  de `^[A-Za-z0-9._-]{1,64}$` (ex.: eco de variavel de entrada, PHI) levanta `RcoRulesPinError` e
  nunca e gravado. O exemplo sintetico de hoje nao tem essa saida.

Para tabela Maezo (id sem o prefixo `rco_`) nada muda: nenhuma chave nova, e o pin nem e lido.

FAIL-CLOSED. Uma decisao `rco_` sem entrada no pin (ou com pin ilegivel, sha divergente, arquivo
ausente, estado desconhecido) levanta `RcoRulesPinError` — antes de avaliar, em
`dmn_transport.evaluate_sync`, e de novo ao montar a entrada de auditoria. Nunca se grava um
`shaDmn` fabricado nem se omite o campo para uma tabela do rco. O erro carrega `.code`/`.message`
(convencao da casa), entao `FunctionWorker.execute()` (`base.py::reclassify_coded_exception`) o
converte em `ValueError` -> `failure(retries=0)`: incidente visivel, sem retry (repetir nao muda o
pin).

ESTADO `fixado` (MZO-2a, contrato-k1.md §11 itens 1-6). O manifesto vendorizado em
`config/integrations/rco/k1/manifesto.json` (ao lado do pin, no checkout e na copia do wheel) e
re-hasheado contra `manifest_pin.sha256` e lido por `maezo.platform.rco_manifesto` — o MESMO leitor do
gate de CI: formato MAJOR 1 ou 2 LIDO, mas o pin exige 2.1.0+ com `provas[]` nao vazia
(`rco_manifesto.provas_ausentes_no_pin`, decisao da ROOT de 16/11/2026; as provas rodam no CIB Seven
no shard de integracao, `rco_provas.executar_provas_do_pin`), sem `origem.sujo`, campos exatos,
prefixo `rco_`, anexos pelo hash, colunas de saida dentro do `saidas[]`, `idRegra` emitidos =
`regras[]`, linha adversa preenchida e, no 2.2.0 (DL-0173), `composicoes[]` bem formada (exposta em
`RcoRulesPin.composicoes`; a escolha do resultado do grupo e de `maezo.platform.rco_composicao`) e, no
2.3.0 (DL-0183), `ordem_restricao`, `sinalAuditoria` e `avisos` do K6 conferidos e expostos
(`RcoRulesPin.ordem_restricao`, `.sinais_auditoria`, `.avisos`); o `grupo_principal` de cada fila do K6
(DL-0186) em `.grupos_principais`.
`artifacts` tem de ser exatamente `decisoes[]` do manifesto (`caminho_consumidor` + `sha256`) e
`provenance` a `origem` dele. Manifesto MAJOR 2 com `ativacao` em `plena`, `conferida` ou `em_correcao`,
com ou sem `exige_aditivo`, e aceito desde a unidade 5e-0 (DL-0181), tudo em sombra: o efeito e de
`rco_ativacao.filtrar_saida`, com `efeito_k1_habilitado` desligado; fora do conjunto fechado e
recusado (`rco_manifesto.ativacao_nao_suportada`, defesa em profundidade sobre o leitor). As pastas
`rco/` e `k1/` passam pela MESMA varredura do gate (`rco_manifesto.verificar_pastas`), em qualquer
estado, e o pin e lido com o mesmo JSON estrito (sem chave repetida, sem NaN/Infinity). Qualquer
desvio recusa o pin inteiro (so as decisoes `rco_` param; as tabelas Maezo nao dependem disto).

CARGA E CACHE. So biblioteca padrao, sem rede. O pin e lido na primeira decisao `rco_` do processo
e guardado (`functools.lru_cache`); trocar o pin exige novo deploy, como trocar a tabela. Carga
preguicosa de proposito: um worker que so consulta tabelas Maezo nao pode deixar de subir por causa
do pin do rco.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final

from maezo.agents import resolve_spec_dir
from maezo.platform import rco_manifesto
from maezo.platform.rco_ativacao import efeito_k1_habilitado, estados_em_cache
from maezo.platform.rco_registro import AvaliacaoRegra, avaliar_regra

#: Prefixo obrigatorio do id de decisao do K1 (ADR-0075 secao (b); `platform/validation/dmn.py`).
RCO_DECISION_PREFIX: Final[str] = "rco_"
#: Pin relativo a raiz do repo — o mesmo valor de `scripts/ci/verify_rco_rules_pin.py::DEFAULT_LOCK_PATH`.
RCO_RULES_PIN_RELATIVE_PATH: Final[str] = "config/integrations/rco/rules.lock.json"
#: Pasta do pacote relativa a raiz do repo — o `lock_format.rules_dir` exigido pelo gate.
RCO_RULES_DIR: Final[str] = "spec/processes/dmn/rco"
LOCK_NAME: Final[str] = "rco-rules-lock"
#: Versao do formato do lock (= `verify_rco_rules_pin.LOCK_VERSION`): 2.0.0 = digest `sha256:<hex>`.
LOCK_VERSION: Final[str] = "2.0.0"
ESTADO_SEM_PACOTE: Final[str] = "sem_pacote"
ESTADO_FIXADO: Final[str] = "fixado"

#: Nome da saida da tabela K1 que identifica a regra casada (ADR-0075 (f): "idRegra: da saida").
SAIDA_ID_REGRA: Final[str] = "idRegra"

ERR_DMN_RCO_PIN: Final[str] = "ERR_DMN_RCO_PIN"

_SHA256: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Forma aceita de `idRegra`: identificador curto, nunca texto livre. Uma saida FEEL pode ecoar uma
#: variavel de entrada (dado de caso/PHI); fora deste padrao o valor NUNCA e gravado (fail-closed).
_ID_REGRA: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RcoRulesPinError(Exception):
    """A proveniencia de uma tabela do rco nao pode ser atestada pelo pin — PERMANENTE.

    Deliberadamente `Exception` com `.code`/`.message` (nao `RuntimeError`): repetir a tarefa nao
    conserta o pin, entao `reclassify_coded_exception` leva a `failure(retries=0)`. A mensagem cita
    so caminhos e ids de decisao do repositorio — nenhum dado de caso.
    """

    def __init__(self, message: str) -> None:
        self.code = ERR_DMN_RCO_PIN
        self.message = message
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True, slots=True)
class RcoPinnedTable:
    """Uma decisao do K1 e o que o pin atesta sobre o arquivo dela."""

    decision_key: str
    path: str  # relativo a raiz do repo, como escrito no pin
    sha_dmn: str  # `sha256:<hex>`
    #: `regras[]` da decisao no manifesto K1, por `idRegra` (versaoRegra + sha256_yaml). `None` quando
    #: nao ha manifesto: estado `sem_pacote` ou exemplo sintetico do Maezo.
    regras: Mapping[str, rco_manifesto.RegraK1] | None = None
    #: MZO-TOL (PROG-COORD-22): achados de `rco_manifesto.tolerancia_prazo_conta_no_dmn` no arquivo da
    #: tabela (linha que libera faixa positiva limitada de `diasAlemDoPrazoContratual`). Nao vazio = o
    #: DMN ja tolera: o gate recusa o pin e o montador zera a tolerancia do Maezo (nunca dobra).
    tolerancia_no_dmn: tuple[str, ...] = ()
    #: MZO-3: a decisao do manifesto K1 pinado (`entradas` e `dominio`, que o montador de entradas usa).
    #: `None` sem manifesto (`sem_pacote` ou exemplo sintetico do Maezo).
    decisao: rco_manifesto.DecisaoK1 | None = None

    def audit_fields(self, id_regra: str | None = None) -> dict[str, str]:
        """Campos do pin para a entrada de `dmn_versions`.

        `versaoRegra` so quando o manifesto a da; `ativacaoPacote` e `exigeAditivo` (ADR-0077 §4) so no
        manifesto MAJOR 2 (no MAJOR 1 a regra nao os tem: ausentes, nunca `null`). `exigeAditivo` e o
        texto `"true"`/`"false"`: o valor de `dict[str, str]` e a coluna jsonb so guardam texto aqui.
        """
        fields = {"shaDmn": self.sha_dmn}
        if id_regra is None or self.regras is None:
            return fields
        regra = self.regras.get(id_regra)
        if regra is None:
            raise RcoRulesPinError(
                f"decisao {self.decision_key!r}: idRegra {id_regra!r} nao esta em regras[] do manifesto K1 "
                "pinado; sem versaoRegra atestada a linha nao e auditada"
            )
        fields["versaoRegra"] = regra.versao_regra
        if regra.ativacao is not None and regra.exige_aditivo is not None:
            fields["ativacaoPacote"] = regra.ativacao
            fields["exigeAditivo"] = "true" if regra.exige_aditivo else "false"
        return fields


@dataclass(frozen=True, slots=True)
class RcoRulesPin:
    """O pin carregado: estado e as decisoes `rco_` que ele cobre."""

    estado: str
    tables: Mapping[str, RcoPinnedTable]
    #: `vocabulario.json` `ativacao.parametros` do pacote pinado (manifesto MAJOR 2, contrato-k1.md
    #: §7). So exposto: a politica de execucao (D1/D2, amostra, aditivo) e de unidade posterior.
    #: `None` sem pacote ou com manifesto MAJOR 1.
    parametros_ativacao: rco_manifesto.ParametrosAtivacao | None = None
    #: MZO-3: o manifesto K1 pinado e a pasta `k1/` ao lado do pin, para o montador ler `entradas.json`
    #: (`rco_entradas.carregar_dicionario_entradas`) sem reler o pin. `None` sem pacote.
    manifesto: rco_manifesto.ManifestoK1 | None = None
    k1_dir: Path | None = None
    #: Formato 2.3.0 (DL-0183): `vocabulario.json` `ordem_restricao` (composicao `mais_restritiva`) e
    #: `sinalAuditoria` (vocabulario fechado do sinal), e `avisos` do K6 por sinal. Sem pacote ou abaixo de
    #: 2.3.0: ordem e avisos vazios e o sinal so com o vazio (`SINAIS_SO_VAZIO`). Quem compoe passa os dois
    #: primeiros a `rco_composicao.compor`; a notificacao as
    #: filas de `avisos` e de unidade posterior.
    ordem_restricao: tuple[str, ...] = ()
    sinais_auditoria: tuple[str, ...] = rco_manifesto.SINAIS_SO_VAZIO
    avisos: Mapping[str, rco_manifesto.AvisoK6] = field(default_factory=lambda: MappingProxyType({}))
    #: DL-0186: `filas.<f>.grupo_principal` do K6 (2.3.0), so as filas que o declaram. O consumidor dos avisos
    #: (`notifications_inbox`) endereca a linha a esse grupo; fila fora do mapa nao tem grupo e o aviso dela
    #: termina como falha com codigo fechado. Vazio sem pacote ou abaixo de 2.3.0.
    grupos_principais: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    #: `combinacao.adversas` e `combinacao.adversa_exige_preenchidos` de `vocabulario.json` (contrato §6):
    #: quem compoe passa os dois a `rco_composicao.compor` (vencedora adversa incompleta -> revisao humana).
    #: Vazios sem pacote: a composicao usa entao o piso dela.
    adversas: frozenset[str] = frozenset()
    adversa_exige_preenchidos: tuple[str, ...] = ()

    @property
    def composicoes(self) -> tuple[rco_manifesto.ComposicaoK1, ...]:
        """`composicoes[]` do manifesto pinado (formato 2.2.0, DL-0173); vazio sem pacote ou abaixo de 2.2.

        Nenhuma decisao de um grupo (padrao inclusive) e avaliada sozinha: quem avalia usa
        `maezo.platform.rco_composicao.exigir_avaliacao_isolada` antes e `compor` para o grupo.
        """
        return self.manifesto.composicoes if self.manifesto is not None else ()


def is_rco_decision(decision_key: str) -> bool:
    return decision_key.startswith(RCO_DECISION_PREFIX)


def _entries(lock: Mapping[str, Any], section: str) -> list[tuple[PurePosixPath, str]]:
    raw = lock.get(section)
    if not isinstance(raw, list):
        raise RcoRulesPinError(f"pin: secao {section!r} ausente ou nao e lista")
    out: list[tuple[PurePosixPath, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise RcoRulesPinError(f"pin: entrada de {section!r} nao e objeto")
        path, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256.match(digest):
            raise RcoRulesPinError(f"pin: entrada de {section!r} sem 'path' ou sem 'sha256' sha256:<64 hex>")
        rel = PurePosixPath(path)
        if rel.parent != PurePosixPath(RCO_RULES_DIR) or rel.suffix != ".dmn":
            raise RcoRulesPinError(f"pin: {path!r} nao e um .dmn direto em {RCO_RULES_DIR}")
        out.append((rel, digest))
    return out


def _decision_ids(raw: bytes, rel: PurePosixPath) -> list[str]:
    try:
        return rco_manifesto.ids_de_decisao(raw, f"pin: {rel}")
    except rco_manifesto.ManifestoK1InvalidoError as exc:
        raise RcoRulesPinError(str(exc)) from exc


def _k1_dir(lock_path: Path) -> Path:
    """O pin mora em config/integrations/rco/; o manifesto e os anexos, em .../rco/k1/ (checkout e wheel)."""
    return lock_path.parent / PurePosixPath(rco_manifesto.K1_DIR).name


def checar_lock_format(lock: Mapping[str, Any]) -> None:
    """`lock_format` do pin (nome, pasta e versao). Levanta `RcoRulesPinError`. Usado tambem por
    `rco_provas.manifesto_do_pin`, para que a execucao das provas leia o pin com a MESMA checagem."""
    fmt = lock.get("lock_format")
    if (
        not isinstance(fmt, dict)
        or fmt.get("name") != LOCK_NAME
        or fmt.get("rules_dir") != RCO_RULES_DIR
        or fmt.get("version") != LOCK_VERSION
    ):
        raise RcoRulesPinError(
            f"pin: lock_format exige name={LOCK_NAME!r}, rules_dir={RCO_RULES_DIR!r} "
            f"e version={LOCK_VERSION!r}"
        )


def checar_manifest_pin(lock: Mapping[str, Any]) -> str:
    """`manifest_pin` do estado `fixado`: exatamente `path` (= `K1_MANIFESTO_PATH`) e `sha256`
    `sha256:<64 hex>`. Devolve o digest; levanta `RcoRulesPinError`. Mesmo uso de `checar_lock_format`."""
    pin = lock.get("manifest_pin")
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise RcoRulesPinError("pin: estado 'fixado' exige manifest_pin com exatamente path e sha256")
    digest = pin.get("sha256")
    if (
        pin.get("path") != rco_manifesto.K1_MANIFESTO_PATH
        or not isinstance(digest, str)
        or not (rco_manifesto.is_digest(digest))
    ):
        raise RcoRulesPinError(
            f"pin: manifest_pin exige path={rco_manifesto.K1_MANIFESTO_PATH!r} e sha256 sha256:<64 hex>"
        )
    return digest


def _manifesto_fixado(
    lock: Mapping[str, Any], lock_path: Path, rules_dir: Path
) -> tuple[
    rco_manifesto.ManifestoK1,
    rco_manifesto.VocabularioPacoteK1,
    Mapping[str, rco_manifesto.AvisoK6],
    Mapping[str, str],
    set[str],
]:
    """Estado `fixado`: le e confere o manifesto K1 pinado.

    Devolve o manifesto, o `vocabulario.json` tipado (parametros de ativacao; `ordem_restricao` e
    `sinalAuditoria` no 2.3.0), os `avisos` e os `grupo_principal` do K6 (2.3.0) e os nomes aceitos em
    `k1/` (manifesto, anexos e arquivos de provas, como o gate).
    """
    digest = checar_manifest_pin(lock)
    k1_dir = _k1_dir(lock_path)
    try:
        raw = (k1_dir / rco_manifesto.K1_MANIFESTO_NOME).read_bytes()
    except OSError as exc:
        raise RcoRulesPinError(f"pin: manifesto K1 ausente ou ilegivel em {k1_dir}") from exc
    if rco_manifesto.sha256_prefixado(raw) != digest:
        raise RcoRulesPinError("pin: sha256 do manifesto K1 diverge de manifest_pin")
    try:
        manifesto = rco_manifesto.ler_manifesto(raw)
    except rco_manifesto.ManifestoK1InvalidoError as exc:
        raise RcoRulesPinError(
            f"pin: manifesto K1 recusado ({len(exc.problemas)} problema(s)): {exc}"
        ) from exc
    sem_provas = rco_manifesto.provas_ausentes_no_pin(manifesto)
    if sem_provas:
        # Mesma recusa do gate (`pin-sem-provas`): pin real so com provas do motor (decisao da ROOT).
        raise RcoRulesPinError(f"pin: pacote K1 sem provas do motor: {'; '.join(sem_provas)}")
    provenance = lock.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("rco_repo") != manifesto.repo
        or provenance.get("rco_commit_sha") != manifesto.commit_sha
    ):
        raise RcoRulesPinError("pin: provenance difere da origem do manifesto K1")
    problemas = rco_manifesto.verificar_pacote(manifesto, k1_dir=k1_dir, rules_dir=rules_dir)
    if problemas:
        raise RcoRulesPinError(f"pin: pacote K1 nao confere com o manifesto: {'; '.join(problemas)}")
    nao_ligadas = rco_manifesto.ativacao_nao_suportada(manifesto)
    if nao_ligadas:
        raise RcoRulesPinError(
            f"pin: pacote K1 com {len(nao_ligadas)} regra(s) de ativacao fora do contrato K1 §7 "
            "(ativacao fora do conjunto fechado ou exige_aditivo nao booleano): " + "; ".join(nao_ligadas[:5])
        )
    try:
        vocab = rco_manifesto.carregar_vocabulario(manifesto, k1_dir=k1_dir)
        avisos = rco_manifesto.carregar_avisos_k6(manifesto, vocab, k1_dir=k1_dir)
        grupos = rco_manifesto.carregar_grupos_k6(manifesto, k1_dir=k1_dir)
    except rco_manifesto.ManifestoK1InvalidoError as exc:
        raise RcoRulesPinError(f"pin: vocabulario.json ou k6-filas.json do K1 recusado: {exc}") from exc
    # A MESMA fonte do gate (`verify_rco_rules_pin`): manifesto, anexos e arquivos de `provas[]`.
    nomes_k1 = rco_manifesto.nomes_k1_declarados(raw)
    return manifesto, vocab, avisos, grupos, nomes_k1


def load_rco_rules_pin(lock_path: Path, *, rules_dir: Path) -> RcoRulesPin:
    """Le o pin e mapeia cada decisao `rco_` ao sha256 do seu arquivo. Fail-closed em tudo.

    `rules_dir` e a pasta de bordo de `spec/processes/dmn/rco/` (checkout ou wheel). Cada arquivo
    listado e re-hasheado: o sha gravado na auditoria tem de ser o dos bytes que o deploy publica.
    Em `fixado` o manifesto K1 e lido e cada tabela do pacote leva as `regras[]` dele
    (`versaoRegra`/`sha256_yaml` por `idRegra`).
    """
    try:
        # Mesmo JSON estrito do gate: chave repetida e NaN/Infinity recusados.
        lock = rco_manifesto.json_estrito(lock_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RcoRulesPinError(f"pin ilegivel em {lock_path} ({type(exc).__name__})") from exc
    if not isinstance(lock, dict):
        raise RcoRulesPinError("pin: raiz nao e objeto")
    checar_lock_format(lock)

    estado = lock.get("estado")
    artifacts = _entries(lock, "artifacts")
    por_caminho: dict[str, rco_manifesto.DecisaoK1] = {}
    parametros: rco_manifesto.ParametrosAtivacao | None = None
    vocab: rco_manifesto.VocabularioPacoteK1 | None = None
    avisos: Mapping[str, rco_manifesto.AvisoK6] = MappingProxyType({})
    grupos: Mapping[str, str] = MappingProxyType({})
    nomes_k1: set[str] = set()
    manifesto: rco_manifesto.ManifestoK1 | None = None
    if estado == ESTADO_FIXADO:
        manifesto, vocab, avisos, grupos, nomes_k1 = _manifesto_fixado(lock, lock_path, rules_dir)
        parametros = vocab.parametros_ativacao
        por_caminho = manifesto.decisao_por_caminho()
        pinados = {str(rel): digest for rel, digest in artifacts}
        if len(pinados) != len(artifacts) or pinados != {c: d.sha256 for c, d in por_caminho.items()}:
            raise RcoRulesPinError("pin: artifacts tem de ser exatamente decisoes[] do manifesto K1")
    elif estado == ESTADO_SEM_PACOTE:
        if lock.get("manifest_pin") is not None or artifacts:
            raise RcoRulesPinError("pin: estado 'sem_pacote' exige manifest_pin = null e artifacts = []")
    else:
        raise RcoRulesPinError(f"pin: estado desconhecido {estado!r}")

    listados = [*artifacts, *_entries(lock, "exemplos_sinteticos")]
    # Mesma varredura do gate (`rco_manifesto.verificar_pastas`): nada fora do pin/manifesto nas pastas.
    soltos = rco_manifesto.verificar_pastas(
        rules_dir=rules_dir,
        k1_dir=_k1_dir(lock_path),
        listados_rco={rel.name for rel, _ in listados},
        nomes_k1=nomes_k1,
    )
    if soltos:
        raise RcoRulesPinError("pin: pastas do pacote fora do pin: " + "; ".join(d for _, d in soltos))
    tables: dict[str, RcoPinnedTable] = {}
    for rel, digest in listados:
        try:
            raw = (rules_dir / rel.name).read_bytes()
        except OSError as exc:
            raise RcoRulesPinError(f"pin: arquivo listado ausente ou ilegivel: {rel}") from exc
        if rco_manifesto.sha256_prefixado(raw) != digest:
            raise RcoRulesPinError(f"pin: sha256 de {rel} diverge do pin")
        decisao = por_caminho.get(str(rel))
        # MZO-TOL: a MESMA leitura do gate (`tolerancia-prazo-conta-no-dmn`), sobre os bytes ja lidos.
        tolerancia_no_dmn = tuple(rco_manifesto.tolerancia_prazo_conta_no_dmn(raw, str(rel)))
        for decision_key in _decision_ids(raw, rel):
            if decision_key in tables:
                raise RcoRulesPinError(f"pin: decisao {decision_key!r} declarada em mais de um arquivo")
            if decisao is not None and decision_key != decisao.id:
                raise RcoRulesPinError(f"pin: {rel} declara {decision_key!r}, o manifesto diz {decisao.id!r}")
            regras = decisao.regras if decisao is not None else None
            tables[decision_key] = RcoPinnedTable(
                decision_key,
                str(rel),
                digest,
                regras=regras,
                tolerancia_no_dmn=tolerancia_no_dmn,
                decisao=decisao,
            )
    return RcoRulesPin(
        estado=estado,
        tables=MappingProxyType(tables),
        parametros_ativacao=parametros,
        manifesto=manifesto,
        k1_dir=_k1_dir(lock_path) if manifesto is not None else None,
        ordem_restricao=vocab.ordem_restricao if vocab is not None else (),
        sinais_auditoria=vocab.sinais_auditoria if vocab is not None else rco_manifesto.SINAIS_SO_VAZIO,
        avisos=avisos,
        grupos_principais=grupos,
        adversas=vocab.adversas if vocab is not None else frozenset(),
        adversa_exige_preenchidos=vocab.adversa_exige_preenchidos if vocab is not None else (),
    )


def _default_lock_path() -> Path:
    """Checkout do repo, depois a copia ao lado do pacote (force-include do pyproject)."""
    parents = Path(__file__).resolve().parents
    candidates = [parents[4] / RCO_RULES_PIN_RELATIVE_PATH, parents[2] / RCO_RULES_PIN_RELATIVE_PATH]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RcoRulesPinError(f"pin nao encontrado; tentados: {[str(c) for c in candidates]}")


@lru_cache(maxsize=1)
def rco_rules_pin() -> RcoRulesPin:
    """O pin de bordo, carregado uma vez por processo (falha nao e cacheada: tenta de novo)."""
    try:
        rules_dir = resolve_spec_dir() / "processes" / "dmn" / "rco"
    except Exception as exc:  # FileNotFoundError / SpecDirOverrideRefusedError
        raise RcoRulesPinError(f"pasta do pacote rco nao resolvida ({type(exc).__name__})") from exc
    return load_rco_rules_pin(_default_lock_path(), rules_dir=rules_dir)


def rco_pinned_table(decision_key: str) -> RcoPinnedTable | None:
    """`None` para tabela Maezo (o pin nem e lido); a entrada do pin para `rco_`, ou levanta."""
    if not is_rco_decision(decision_key):
        return None
    pinned = rco_rules_pin().tables.get(decision_key)
    if pinned is None:
        raise RcoRulesPinError(
            f"decisao {decision_key!r} tem prefixo rco_ mas nao esta no pin {RCO_RULES_PIN_RELATIVE_PATH}; "
            "sem shaDmn atestado ela nao e avaliada nem auditada"
        )
    return pinned


def rco_avaliacao(
    decision_key: str, row: Mapping[str, Any] | None = None, *, sombra: bool = False
) -> tuple[dict[str, str], AvaliacaoRegra | None]:
    """Campos extras da entrada de `dmn_versions` e, quando ha regra K1 casada num manifesto MAJOR 2, a
    avaliacao que alimenta o registro de decisoes (ADR-0077 §4, 5c-2).

    `{}`/`None` para Maezo. Para rco: `shaDmn` (+ `idRegra`, `versaoRegra`, `ativacaoPacote`,
    `exigeAditivo`); e, so com `ativacaoPacote` (mesma regra de presenca), `estadoManual`,
    `ativacaoEfetiva` e `modo`, calculados UMA vez aqui para que a auditoria e o registro digam o mesmo.

    `sombra=True` (MZO-3, avaliacao da sombra do montador): o indicador `efeito_k1_habilitado` NAO e lido
    (vale desligado), entao `modo` e sempre `sombra`, e a avaliacao sai marcada para nao consumir ordinal.
    """
    pinned = rco_pinned_table(decision_key)
    if pinned is None:
        return {}, None
    id_regra = row.get(SAIDA_ID_REGRA) if row is not None else None
    if id_regra is None or id_regra == "":
        # `""` e o vazio canonico do K1 (contrato §6): linha nao adversa sem regra identificada. So
        # `shaDmn` e gravado; o gate exige `idRegra` preenchido em linha adversa.
        return pinned.audit_fields(), None
    if not isinstance(id_regra, str) or _ID_REGRA.fullmatch(id_regra) is None:
        # O valor nao entra nem na mensagem: pode ser dado de caso ecoado pela tabela.
        raise RcoRulesPinError(
            f"decisao {decision_key!r}: saida {SAIDA_ID_REGRA!r} fora do padrao {_ID_REGRA.pattern!r}; "
            "valor nao gravado na auditoria"
        )
    fields = pinned.audit_fields(id_regra)
    fields["idRegra"] = id_regra
    avaliacao: AvaliacaoRegra | None = None
    parametros = rco_rules_pin().parametros_ativacao
    if "ativacaoPacote" in fields and pinned.regras is not None and parametros is not None:
        avaliacao = avaliar_regra(
            decision_key=decision_key,
            sha_dmn=pinned.sha_dmn,
            regra=pinned.regras[id_regra],
            parametros=parametros,
            row=row,
            estados=estados_em_cache(),
            habilitado=False if sombra else efeito_k1_habilitado(),
            sombra=sombra,
        )
    if avaliacao is not None:
        fields.update(avaliacao.campos_de_auditoria())
    return fields, avaliacao


def rco_audit_fields(decision_key: str, row: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Campos extras da entrada de `dmn_versions`: `{}` para Maezo; `shaDmn` (+ opcionais) para rco."""
    return rco_avaliacao(decision_key, row)[0]


__all__ = [
    "ERR_DMN_RCO_PIN",
    "RCO_DECISION_PREFIX",
    "RCO_RULES_DIR",
    "RCO_RULES_PIN_RELATIVE_PATH",
    "RcoPinnedTable",
    "RcoRulesPin",
    "RcoRulesPinError",
    "checar_lock_format",
    "checar_manifest_pin",
    "is_rco_decision",
    "load_rco_rules_pin",
    "rco_audit_fields",
    "rco_avaliacao",
    "rco_pinned_table",
    "rco_rules_pin",
]

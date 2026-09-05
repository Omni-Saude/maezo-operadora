"""`SP-OP-ADEQUACAO-001` nao tem binding de agente, POR DECISAO — gap `PERSP-B5-ADEQ-BINDING`.

Decisao do dono R-049 (2026-09-04, opcao B): *nenhum agente inicia `SP-OP-ADEQUACAO-001`*. A
ausencia de binding era, ate aqui, uma OMISSAO (`grep -rni adequacao spec/agents/` -> 0); passa a
ser uma posicao declarada, registrada no contrato do processo
(`docs/processes/contracts/SP-OP-ADEQUACAO-001.md`, §Bindings de agente) e no manifesto do Andre.
Nenhuma `process_key` nova foi concedida a agente nenhum.

Por que a decisao e a correta (fatos, nao prosa): Andre e convocado por DELEGACAO A2A sobre uma
instancia que JA esta rodando (`operadora.adequacao.prepare_remediation_dossier` ->
`analytics.population`; o no `start_process` do grafo dele e no-op fora do fluxo `pagto_dossier`),
e a ponte que INICIARIA o processo pelo gatilho `mudanca_rede` (`network_change_bridge`,
PERSP-NETBRIDGE/AF-01) nao existe em `src/`.

Este arquivo e a cerca: ele varre TODO `spec/agents/*/agent.yaml` — nao so o do Andre — porque a
decisao e sobre o PROCESSO, nao sobre um agente. Conceder a chave a qualquer agente e ato explicito
do dono e tem de ficar VERMELHO aqui primeiro.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maezo.agents import resolve_spec_agents_dir
from maezo.gateway.effect_pep import KNOWN_PROCESS_KEYS, AgentCapabilities

_ADEQUACAO_KEY = "SP-OP-ADEQUACAO-001"


def _agent_manifests() -> list[Path]:
    """Todo `agent.yaml` shipado, incluindo o `_template` (um binding la se propagaria)."""
    paths = sorted(resolve_spec_agents_dir().glob("*/agent.yaml"))
    assert paths, "spec/agents/*/agent.yaml nao encontrou manifesto algum — cerca vacua"
    return paths


_MANIFESTS = _agent_manifests()


def test_a_chave_de_adequacao_esta_no_universo_adr0016() -> None:
    """Nao-vacuidade: a cerca abaixo so significa algo se a chave for realmente concedivel."""
    assert _ADEQUACAO_KEY in KNOWN_PROCESS_KEYS


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_nenhum_manifesto_declara_adequacao_como_processo_iniciavel(manifest: Path) -> None:
    """R-049: `process_keys:` e a UNICA lista que concede autoridade de start ao effect-PEP."""
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    assert _ADEQUACAO_KEY not in (data.get("process_keys") or [])


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_nenhuma_visao_de_capacidade_permite_iniciar_adequacao(manifest: Path) -> None:
    """A mesma prova pelo lado do PEP: a construcao de producao (`AgentCapabilities.of`) recusa.

    Ler o YAML prova o que esta escrito; construir a visao de capacidade prova o que o PEP
    concede — as duas pernas, porque uma grafia futura de `process_keys` que o loader aceitasse e
    o PEP interpretasse diferente passaria por apenas uma delas.
    """
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    caps = AgentCapabilities.of(
        principal=str(data.get("id", manifest.parent.name)),
        tools=data.get("tools") or [],
        process_keys=data.get("process_keys") or [],
    )
    assert caps.allows_process_key(_ADEQUACAO_KEY) is False


def test_nenhum_agente_no_conjunto_inteiro_inicia_adequacao() -> None:
    """A afirmacao agregada que o contrato do processo publica, provada de uma vez so."""
    concedem = [
        manifest.parent.name
        for manifest in _MANIFESTS
        if _ADEQUACAO_KEY
        in ((yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("process_keys") or [])
    ]
    assert concedem == [], (
        "R-049 declara que NENHUM agente inicia SP-OP-ADEQUACAO-001; conceder a chave e ato "
        f"explicito do dono e exige atualizar o contrato do processo. Concedem hoje: {concedem}"
    )

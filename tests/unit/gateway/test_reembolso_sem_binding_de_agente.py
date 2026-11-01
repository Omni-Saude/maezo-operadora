"""`SP-OP-REEMBOLSO-001`: so o LUCAS tem binding de agente — gap `PERSP-REEMBOLSO-BINDING` (revisto por
DL-0159/DL-0160).

DL-0160 (28/10/2026): o Lucas passa a ABRIR o pedido de reembolso (flag `MAEZO_REEMBOLSO_V1`), entao a
chave entra em
`process_keys` DELE — e so dele. A arbitragem original segue valendo para a Marina e para todos os
demais agentes.

Arbitragem original (nao decisao do dono, ao contrario de `PERSP-B5-ADEQ-BINDING`/R-049): o achado original
(`phase1/perspective-part-A.md` Sec.5 item 5) foi REFUTADO pelo verificador
(`phase1/perspective-part-A-VERIFICATION.md` C-4) como **MENOR PRIVILEGIO CORRETO, nao defeito**.
Marina serve `SP-OP-REEMBOLSO-001` via delegacao A2A (`operadora.reembolso.analyze_request`) de
DENTRO de uma instancia JA rodando (`src/maezo/agents/marina/graph.py:28-34`); o no `start_process`
do grafo dela e explicitamente NO-OP para esse fluxo. Nenhum manifesto declara a `process_key` —
por desenho, nao por omissao — e este arquivo e a cerca que prova isso, no mesmo formato de
`test_adequacao_sem_binding_de_agente.py`.

Este arquivo varre TODO `spec/agents/*/agent.yaml` — nao so o da Marina — porque a arbitragem e
sobre o PROCESSO: conceder a chave a QUALQUER agente e ato explicito do dono e tem de ficar
VERMELHO aqui primeiro.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from maezo.agents import resolve_spec_agents_dir
from maezo.gateway.effect_pep import KNOWN_PROCESS_KEYS, AgentCapabilities

_REEMBOLSO_KEY = "SP-OP-REEMBOLSO-001"

#: O UNICO agente com autoridade de start sobre o reembolso (DL-0160). Conceder a chave a QUALQUER outro e ato
#: explicito do dono e tem de ficar VERMELHO aqui primeiro.
_AGENTES_COM_A_CHAVE = frozenset({"lucas"})


def _agent_manifests() -> list[Path]:
    """Todo `agent.yaml` shipado, incluindo o `_template` (um binding la se propagaria)."""
    paths = sorted(resolve_spec_agents_dir().glob("*/agent.yaml"))
    assert paths, "spec/agents/*/agent.yaml nao encontrou manifesto algum — cerca vacua"
    return paths


_MANIFESTS = _agent_manifests()


def test_a_chave_de_reembolso_esta_no_universo_adr0016() -> None:
    """Nao-vacuidade: a cerca abaixo so significa algo se a chave for realmente concedivel."""
    assert _REEMBOLSO_KEY in KNOWN_PROCESS_KEYS


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_so_o_lucas_declara_reembolso_como_processo_iniciavel(manifest: Path) -> None:
    """`process_keys:` e a UNICA lista que concede autoridade de start ao effect-PEP — so o Lucas a
    declara para
    REEMBOLSO (a Marina, que SERVE este processo via A2A, NAO)."""
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    declara = _REEMBOLSO_KEY in (data.get("process_keys") or [])
    assert declara == (manifest.parent.name in _AGENTES_COM_A_CHAVE)


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_a_visao_de_capacidade_so_permite_iniciar_reembolso_ao_lucas(manifest: Path) -> None:
    """A mesma prova pelo lado do PEP: a construcao de producao (`AgentCapabilities.of`) concede so ao Lucas.

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
    assert caps.allows_process_key(_REEMBOLSO_KEY) is (manifest.parent.name in _AGENTES_COM_A_CHAVE)


def test_so_o_lucas_no_conjunto_inteiro_inicia_reembolso() -> None:
    """A afirmacao agregada que o contrato do processo publica, provada de uma vez so."""
    concedem = [
        manifest.parent.name
        for manifest in _MANIFESTS
        if _REEMBOLSO_KEY
        in ((yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("process_keys") or [])
    ]
    assert set(concedem) == _AGENTES_COM_A_CHAVE, (
        "DL-0160: so o Lucas inicia SP-OP-REEMBOLSO-001 (menor privilegio — Marina so serve via A2A, "
        "start_process e NO-OP para reembolso); conceder a chave a outro agente e ato explicito do dono. "
        f"Concedem hoje: {concedem}"
    )


def test_marina_declara_a_arbitragem_no_proprio_manifesto() -> None:
    """A Marina especificamente carrega o COMENTARIO de arbitragem (nao so a ausencia da chave) —
    a mesma disciplina do comentario `PERSP-B5-ADEQ-BINDING` no manifesto do Andre."""
    marina_yaml = next(m for m in _MANIFESTS if m.parent.name == "marina")
    text = marina_yaml.read_text(encoding="utf-8")
    assert "PERSP-REEMBOLSO-BINDING" in text
    assert _REEMBOLSO_KEY not in yaml.safe_load(text).get("process_keys", [])


def test_reembolso_contract_documents_the_binding_arbitration() -> None:
    """`docs/processes/contracts/SP-OP-REEMBOLSO-001.md` must carry the same declared-absence
    the agent manifests prove mechanically, so a future audit reads the reasoning where a human
    actually looks (the contract), not just infers it from an absent YAML key."""
    repo_root = Path(__file__).resolve().parents[3]
    contract = repo_root / "docs" / "processes" / "contracts" / "SP-OP-REEMBOLSO-001.md"
    text = contract.read_text(encoding="utf-8")
    assert "PERSP-REEMBOLSO-BINDING" in text
    assert "so dele" in text and "Marina NAO declara a chave" in text


def test_reembolso_contract_reproduction_is_structural_not_a_substring_grep() -> None:
    """The contract's stated reproduction for the binding arbitration must be the SAME fact this
    fence checks mechanically above (a YAML parse of `process_keys:`) — never a bare
    `grep -rn REEMBOLSO spec/agents/*/agent.yaml` substring count.

    A substring grep is structurally unable to stay true: the arbitration comment this very
    contract asks Marina's manifest to carry (`test_marina_declara_a_arbitragem_no_proprio_manifesto`
    above) legitimately contains the word "REEMBOLSO" without declaring the process key, and so
    does the contract's own explanatory prose about that comment — either one alone makes
    `grep -rn REEMBOLSO spec/agents/*/agent.yaml` return more than 0 lines while the real,
    structural fact (no `process_keys:` entry names `SP-OP-REEMBOLSO-001`) stays true. Mutation:
    restoring the old "retorna **0 linhas**"/"-> 0 hits" substring-grep claim must turn this
    test RED (VERIFY-DOCS-SWEEP F1)."""
    repo_root = Path(__file__).resolve().parents[3]
    contract = repo_root / "docs" / "processes" / "contracts" / "SP-OP-REEMBOLSO-001.md"
    text = contract.read_text(encoding="utf-8")
    assert "0 linhas" not in text, "reproducao de substring-grep falsificavel voltou ao contrato"
    assert "0 hits" not in text, "reproducao de substring-grep falsificavel voltou ao contrato"
    assert "process_keys:" in text
    assert "parse de YAML" in text, "contrato deve apontar para a reproducao estrutural real"

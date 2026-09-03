"""A cerca de perspectiva (PR-1) deve acusar ZERO na cadeia SP-OP-RECURSO-001.

PORQUE ESTE TESTE EXISTE (achado M1 do gatekeeper R1 do PR-3). A reconstrucao da cadeia RECURSO
na perspectiva do pagador foi entregue com a alegacao de «zero achados» e o desenho fixa a meta em
«0 (Tier A e Tier B)» — mas a cerca do PR-1, rodada de fora, acusava **5** hits no BPMN novo,
entre eles a frase `resposta da operadora` (regra R2-CORE C1) e uma referencia ao id **deletado**
`Flow_GWDec_Recorrer`. Uma meta que ninguem executa nao e uma meta; este modulo executa.

O MODULO NAO E COPIADO PARA CA. Ele nasce no PR-1 (`feat/perspectiva-fence-vocabulario`) e o PR-4
e quem o cabeia em `make validate-artifacts`. Enquanto ele nao estiver na arvore, este teste
SKIPPA — com a razao nomeando o PR-1, para que o skip seja legivel como «a dependencia ainda nao
mergeou», nunca como «a cerca passou». No dia em que o PR-1 mergear, o skip vira execucao real
sem que ninguem precise lembrar de ligar nada.

ESCOPO: apenas os artefatos que o PR-3 reconstruiu. A metade CONTAS (`SP-OP-CONTAS-001`,
`glosa_triage.dmn`, o manifesto shadow e as 2 superficies `operadora.contas.start_recurso` de
`action-approvals.yaml`) continua acusando e e do PR-4 — por isso a varredura aqui e por ARQUIVO,
nao a raiz inteira: um pino global ficaria vermelho por trabalho alheio e seria desligado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

perspective = pytest.importorskip(
    "maezo.platform.validation.perspective",
    reason=(
        "o modulo da cerca de perspectiva chega no PR-1 "
        "(feat/perspectiva-fence-vocabulario, ADR-0040 D7) — ainda nao esta nesta arvore"
    ),
)
from maezo.platform.validation.result import Report  # noqa: E402 — depende do importorskip acima

_REPO = Path(__file__).resolve().parents[3]

#: Os 4 artefatos de processo da cadeia + a definicao da agente que os opera. Caminhos relativos
#: a raiz do repo; a existencia de cada um e assertada (uma varredura sobre um caminho inexistente
#: acusaria zero por vacuidade, que e o modo de falha que este teste nao pode ter).
_ARTEFATOS_RECURSO: tuple[str, ...] = (
    "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn",
    "spec/processes/dmn/recurso_admissibility.dmn",
    "spec/processes/dmn/recurso_eligibility.dmn",
    "spec/processes/dmn/recurso_sla.dmn",
    "spec/agents/marina/agent.yaml",
)


def _hits(caminho: Path) -> list[str]:
    """Roda a cerca sobre UM arquivo e devolve as mensagens dos achados."""
    report = Report()
    if caminho.suffix == ".bpmn":
        perspective.check_processes([caminho], [], [], report)
    elif caminho.suffix == ".dmn":
        perspective.check_processes([], [caminho], [], report)
    else:
        perspective.check_yaml_defs([caminho], report)
    return [str(f.message) for f in report.findings]


@pytest.mark.parametrize("relativo", _ARTEFATOS_RECURSO)
def test_artefato_recurso_sem_vocabulario_de_recorrente(relativo: str) -> None:
    caminho = _REPO / relativo
    assert caminho.is_file(), f"{relativo} nao existe — a varredura seria vacua"
    achados = _hits(caminho)
    assert achados == [], (
        f"{relativo} carrega vocabulario de perspectiva-prestador ({len(achados)} achado(s)): "
        + " | ".join(achados)
    )


def test_a_cerca_realmente_acusa_quando_ha_inversao(tmp_path: Path) -> None:
    """NAO-VACUIDADE: o mesmo caminho de chamada acusa uma inversao plantada.

    Sem isto, um `_hits` que devolvesse `[]` por engano (API trocada, arquivo ilegivel tratado
    como vazio) faria os 5 testes acima passarem sem provar nada.
    """
    plantado = tmp_path / "plantado.bpmn"
    plantado.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">\n'
        '  <bpmn:process id="P">\n'
        "    <bpmn:documentation>Aguardar a resposta da operadora ao recurso "
        "interposto.</bpmn:documentation>\n"
        "  </bpmn:process>\n"
        "</bpmn:definitions>\n",
        encoding="utf-8",
    )
    assert _hits(plantado), "a cerca tem de acusar a inversao plantada"

"""Porta `FonteCobranca`: de onde o Lucas tira os fatos de conciliacao de uma cobranca.

POR QUE EXISTE (plano `docs/plans/lucas-numero-unico.md` §2.5 e §6(a)). O grafo do Lucas CONSOME
`status_conciliado`/`ciclos_sem_conciliacao` pre-resolvidos e nunca os calcula (ADR-0012, invariante
L0 de `graph.py`). Hoje nao existe quem os resolva: nao ha leitura de CNAB nem ponte telefone ->
matricula (§7, lista do time). Esta porta e' o lugar onde essa fonte vai encaixar, e ate' ela
existir quem responde e' a `FonteCobrancaSimulada`.

O QUE A PORTA DEVOLVE. Dois resultados, sem terceiro:
  - `FatosCobranca`: fatos de conciliacao de UMA competencia de UM beneficiario pseudonimizado;
  - `Indisponivel`: a fonte nao conseguiu responder. O motivo e' um token de CLASSE fechado, nunca
    texto de excecao nem valor de entrada ecoado. Quem recebe `Indisponivel` nao inventa fato
    nenhum: os campos de conciliacao ficam AUSENTES na entrada do Lucas, e quem decide o que fazer
    com a ausencia e' a DMN (o catch-all dela escala).

O QUE A PORTA NAO FAZ. Nao decide rota, nao classifica inadimplencia e nao conhece regra de
negocio: limiar de ciclos e mapeamento para RESPONDER/LEMBRETE/ESCALAR_HUMANO moram em
`spec/processes/dmn/lucas_billing_admissibility.dmn`. Nao recebe texto do beneficiario (§3): a
entrada e' o pseudonimo e a competencia `YYYY-MM`, mais nada.

A FONTE SIMULADA. Deterministica por hash do pseudonimo: o mesmo `pseudo_id` sempre cai no mesmo
perfil, sem relogio, sem aleatoriedade e sem estado. E' o que deixa o programa de teste
(`tools/scripts/programa_lucas.py`, corpus `tests/evals/lucas/casos.json`) reproduzivel caso a caso.
Os perfis sao FATOS SINTETICOS, nao regra: eles so' cobrem as combinacoes que a DMN DRAFT
distingue (conciliado; em aberto sem atraso; 1 e 2 ciclos sem conciliacao; fonte fora). O numero do
boleto e a referencia CNAB sao rotulos sinteticos com prefixo `SIM-`/`cnab-sim-`, nunca um numero
que se confunda com boleto real. A fonte real substitui esta classe; ela nao deve ser usada fora de
dev (a cerca que impoe isso e' a da onda (c), §4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, runtime_checkable

from maezo.runtime.competencia import competencia_valida

#: Motivos de `Indisponivel`. Fechado: a fonte real tem de mapear as falhas dela para um destes.
MotivoIndisponivel = Literal["pseudo_id_ausente", "competencia_invalida", "fonte_indisponivel"]


@dataclass(frozen=True, slots=True)
class FatosCobranca:
    """Fatos de conciliacao de uma competencia. Nenhum campo e' veredito: sao entradas da DMN."""

    status_conciliado: bool
    ciclos_sem_conciliacao: int
    numero_boleto: str
    cnab_ref: str

    def como_entrada_lucas(self) -> dict[str, Any]:
        """As chaves de `_CALLER_INPUT_FIELDS` que estes fatos preenchem — nenhuma outra.

        `competencia` NAO entra: ela vem de quem pediu (o handoff), e a fonte so' responde por ela.
        """
        return {
            "status_conciliado": self.status_conciliado,
            "ciclos_sem_conciliacao": self.ciclos_sem_conciliacao,
            "numero_boleto": self.numero_boleto,
            "cnab_ref": self.cnab_ref,
        }


@dataclass(frozen=True, slots=True)
class Indisponivel:
    """A fonte nao respondeu. `motivo` e' token de classe, nunca texto livre."""

    motivo: MotivoIndisponivel


@runtime_checkable
class FonteCobranca(Protocol):
    """Porta dos fatos de cobranca. Uma implementacao NUNCA levanta por falha da dependencia:
    falha vira `Indisponivel("fonte_indisponivel")`, para o chamador nao ter dois caminhos de erro.
    """

    async def fatos(self, pseudo_id: str, competencia: str | None) -> FatosCobranca | Indisponivel: ...


#: Perfis da fonte simulada, na ordem do bucket do hash. Nomes estaveis: o corpus os cita.
PerfilSimulado = Literal["conciliado", "em_aberto", "atraso_1_ciclo", "atraso_2_ciclos", "indisponivel"]
PERFIS_SIMULADOS: Final[tuple[PerfilSimulado, ...]] = (
    "conciliado",
    "em_aberto",
    "atraso_1_ciclo",
    "atraso_2_ciclos",
    "indisponivel",
)

#: Prefixo do material do hash. Versionado: mudar o mapeamento pseudonimo -> perfil e' mudar o
#: resultado do programa inteiro, entao exige subir a versao (e o corpus acompanha).
_SEMENTE: Final[str] = "lucas-fonte-simulada/v1"


def perfil_simulado(pseudo_id: str) -> PerfilSimulado:
    """O perfil em que `pseudo_id` cai na fonte simulada. Puro e deterministico."""
    digest = hashlib.sha256(f"{_SEMENTE}:{pseudo_id}".encode()).digest()
    return PERFIS_SIMULADOS[digest[0] % len(PERFIS_SIMULADOS)]


_FATOS_DO_PERFIL: Final[dict[PerfilSimulado, tuple[bool, int]]] = {
    "conciliado": (True, 0),
    "em_aberto": (False, 0),
    "atraso_1_ciclo": (False, 1),
    "atraso_2_ciclos": (False, 2),
}


class FonteCobrancaSimulada:
    """`FonteCobranca` deterministica por hash do pseudonimo (ver docstring do modulo)."""

    async def fatos(self, pseudo_id: str, competencia: str | None) -> FatosCobranca | Indisponivel:
        if not pseudo_id:
            return Indisponivel("pseudo_id_ausente")
        # `YYYY-MM`: a regra unica de `runtime/competencia.py` (a da coluna `lucas_competencia`).
        if competencia is not None and not competencia_valida(competencia):
            return Indisponivel("competencia_invalida")
        perfil = perfil_simulado(pseudo_id)
        if perfil == "indisponivel":
            return Indisponivel("fonte_indisponivel")
        conciliado, ciclos = _FATOS_DO_PERFIL[perfil]
        rotulo = hashlib.sha256(f"{_SEMENTE}:{pseudo_id}:{competencia or ''}".encode()).hexdigest()
        return FatosCobranca(
            status_conciliado=conciliado,
            ciclos_sem_conciliacao=ciclos,
            numero_boleto=f"SIM-{rotulo[:12].upper()}",
            cnab_ref=f"cnab-sim-{rotulo[12:24]}",
        )

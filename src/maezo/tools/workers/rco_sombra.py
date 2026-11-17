"""Avaliacao em SOMBRA de uma decisao K1 do rco com as entradas do montador (MZO-3).

O QUE ISTO FAZ. Liga o montador de entradas (`rco_montador.montar_entradas`) ao caminho que avalia as
tabelas K1: le a decisao e o dicionario de entradas do pacote PINADO (`rco_pin.rco_rules_pin`, manifesto
e `entradas.json` em `config/integrations/rco/k1/`), monta as entradas com os fatos que o worker
entrega, avalia a tabela pelo funil de sempre (`dmn_transport.evaluate_sync`) e leva a auditoria do
montador para a entrada `rco_` de `dmn_versions` (`_audit_ctx.record_dmn_version`, chaves
`derivacoes` e `entradasNulas`, ao lado de `shaDmn`, `estadoManual` etc.). O registro de decisoes K1
(`harness._registrar_decisoes_rco`, migracao 0035) recebe a avaliacao da regra casada pelo mesmo funil,
sem coluna nova.

POR QUE NADA PRODUZ EFEITO (tres razoes independentes, como `adequacao._record_gap_shadow`):
1. `avaliar_em_sombra` devolve `None`: quem chama nunca liga o resultado a nada; as linhas da tabela
   sao descartadas aqui e nenhuma variavel de processo nasce disto;
2. toda excecao e engolida e vira log estruturado (so o TIPO e o codigo do desfecho, nunca a mensagem,
   que pode trazer dado de caso): pin ilegivel, tabela fora do pin, dicionario recusado, dominio sem
   mapeamento, motor fora — o worker segue identico;
3. o indicador de efeito do K1 (`rco_ativacao.efeito_k1_habilitado`, `MAEZO_RCO_EFEITO_K1`) nao e lido
   nem mudado aqui; toda regra segue no estado manual do arquivo de estados (`sombra`).

INERTE HOJE: o pin esta em `sem_pacote` (nenhuma tabela `rco_` pinada): a funcao sai antes de montar.
Com o pacote pinado, os termos de `diasAteApresentacao` ainda nao tem produtor no Maezo (ver
`contas.fatos_prazo_conta_rco`): as entradas entram nulas, com o motivo, e nulo nunca nega nem glosa.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import structlog

from maezo.platform.rco_entradas import DicionarioEntradasK1, carregar_dicionario_entradas
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import rco_montador, rco_pin
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync

logger = structlog.get_logger(__name__)

#: Desfechos da avaliacao em sombra (codigos fechados do log; nenhum dado de caso).
SEM_TRANSPORTE: Final[str] = "sem_transporte"
PIN_INDISPONIVEL: Final[str] = "pin_indisponivel"
SEM_PACOTE: Final[str] = "sem_pacote"
PIN_AMH_INDISPONIVEL: Final[str] = "pin_amh_indisponivel"
DICIONARIO_RECUSADO: Final[str] = "dicionario_recusado"
DOMINIO_NAO_SUPORTADO: Final[str] = "dominio_nao_suportado"
MONTADOR_RECUSOU: Final[str] = "montador_recusou"
AVALIACAO_FALHOU: Final[str] = "avaliacao_falhou"
AVALIADA: Final[str] = "avaliada"

#: Quem monta os fatos do caso recebe a versao do K3 pinada (regra 8 do montador) e devolve `FatosDoCaso`.
MontarFatos = Callable[[tuple[int, int]], rco_montador.FatosDoCaso]

#: Cache do dicionario por (pasta k1, sha256 do dicionario no manifesto): so a leitura bem-sucedida.
_dicionarios: dict[tuple[str, str], DicionarioEntradasK1] = {}


def auditoria_das_entradas(montadas: rco_montador.EntradasMontadas) -> dict[str, Any]:
    """O que vai para a entrada `rco_` de `dmn_versions`: so inteiros, nulos e codigos.

    - `derivacoes`: `{nomeDMN: {diasBrutos, toleranciaAplicada, valorEntregue, motivoTolerancia}}`
      (MZO-TOL; hoje so `diasAlemDoPrazoContratual`);
    - `entradasNulas`: `{nomeDMN: MotivoNulo}` das entradas que foram nulas a tabela;
    - `versaoDicionarioEntradas`: a versao do `entradas.json` usado.
    """
    return {
        "derivacoes": {nome: dict(registro) for nome, registro in montadas.derivacoes.items()},
        "entradasNulas": dict(montadas.nulos),
        "versaoDicionarioEntradas": montadas.versao_dicionario,
    }


def _dicionario(pin: rco_pin.RcoRulesPin) -> DicionarioEntradasK1 | None:
    if pin.manifesto is None or pin.k1_dir is None:
        return None
    chave = (str(pin.k1_dir), pin.manifesto.dicionario_entradas_sha256)
    if chave not in _dicionarios:
        _dicionarios[chave] = carregar_dicionario_entradas(pin.manifesto, k1_dir=pin.k1_dir)
    return _dicionarios[chave]


def _versao_k3_pinada() -> tuple[int, int]:
    """Regra 8 do montador: SO `AmhContractPin.versao_manifesto_pinada` do pin verificado."""
    from maezo.adapters.amh.contract import load_contract_pin  # preguicoso: so com tabela pinada

    return load_contract_pin().versao_manifesto_pinada


def avaliar(
    dmn: DmnTransport | None,
    decision_key: str,
    montar_fatos: MontarFatos,
    *,
    tenant: str | None = None,
) -> str:
    """Monta e avalia; devolve SO o codigo do desfecho (nunca linhas, nunca entradas). Levanta o que
    `montar_fatos` levantar; `avaliar_em_sombra` e a porta dos workers e engole tudo.

    Os fatos so sao montados depois de confirmar que a tabela esta pinada (hoje nunca: `sem_pacote`)."""
    if dmn is None:
        return SEM_TRANSPORTE
    try:
        pin = rco_pin.rco_rules_pin()
    except rco_pin.RcoRulesPinError:
        return PIN_INDISPONIVEL
    tabela = pin.tables.get(decision_key)
    if tabela is None or tabela.decisao is None:
        return SEM_PACOTE
    try:
        dicionario = _dicionario(pin)
    except ManifestoK1InvalidoError:
        return DICIONARIO_RECUSADO
    if dicionario is None:
        return SEM_PACOTE
    try:
        versao_k3 = _versao_k3_pinada()
    except Exception:
        return PIN_AMH_INDISPONIVEL
    fatos = montar_fatos(versao_k3)
    try:
        montadas = rco_montador.montar_entradas(tabela.decisao, dicionario, fatos)
    except rco_montador.DominioNaoSuportadoError:
        return DOMINIO_NAO_SUPORTADO
    except rco_montador.MontadorEntradasError:
        return MONTADOR_RECUSOU
    try:
        evaluate_sync(
            dmn,
            decision_key,
            dict(montadas.variaveis),
            tenant=tenant,
            entradas_rco=auditoria_das_entradas(montadas),
        )
    except Exception:
        return AVALIACAO_FALHOU
    return AVALIADA


def avaliar_em_sombra(
    dmn: DmnTransport | None,
    decision_key: str,
    montar_fatos: MontarFatos,
    *,
    tenant: str | None = None,
    topico: str | None = None,
) -> None:
    """Porta dos workers: avalia em sombra e devolve `None`. Nunca levanta `Exception`.

    `topico`: so rotulo de log (o topico do worker que chamou); nada dele vai ao motor."""
    try:
        desfecho = avaliar(dmn, decision_key, montar_fatos, tenant=tenant)
    except Exception as exc:  # razao 2 do modulo: a sombra nunca perturba o worker
        logger.warning("rco_sombra_falhou", decision_key=decision_key, erro=type(exc).__name__, topico=topico)
        return
    logger.info("rco_sombra", decision_key=decision_key, desfecho=desfecho, topico=topico)


__all__ = [
    "AVALIACAO_FALHOU",
    "AVALIADA",
    "DICIONARIO_RECUSADO",
    "DOMINIO_NAO_SUPORTADO",
    "MONTADOR_RECUSOU",
    "PIN_AMH_INDISPONIVEL",
    "PIN_INDISPONIVEL",
    "SEM_PACOTE",
    "SEM_TRANSPORTE",
    "MontarFatos",
    "auditoria_das_entradas",
    "avaliar",
    "avaliar_em_sombra",
]

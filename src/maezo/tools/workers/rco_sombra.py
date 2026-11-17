"""Avaliacao em SOMBRA de uma decisao K1 do rco com as entradas do montador (MZO-3).

O QUE ISTO FAZ. Liga o montador de entradas (`rco_montador.montar_entradas`) ao caminho que avalia as
tabelas K1: le a decisao e o dicionario de entradas do pacote PINADO (`rco_pin.rco_rules_pin`, manifesto
e `entradas.json` em `config/integrations/rco/k1/`), monta as entradas com os fatos que o worker
entrega, avalia a tabela pelo funil de sempre (`dmn_transport.evaluate_sync`, com `sombra=True`) e leva
a auditoria do montador para a entrada `rco_` de `dmn_versions` (`_audit_ctx.record_dmn_version`, chaves
`derivacoes`, `entradasNulas`, `versaoDicionarioEntradas` e `origemAvaliacao`, ao lado de `shaDmn`,
`estadoManual` etc.). O registro de decisoes K1 (`harness._registrar_decisoes_rco`, migracao 0035)
recebe a avaliacao da regra casada pelo mesmo funil, sem coluna nova.

POR QUE NADA PRODUZ EFEITO (razoes independentes, como `adequacao._record_gap_shadow`):
1. `avaliar_em_sombra` devolve `None`: quem chama nunca liga o resultado a nada; as linhas da tabela
   sao descartadas aqui e nenhuma variavel de processo nasce disto;
2. a avaliacao e SEMPRE `modo=sombra`: o indicador de efeito do K1 (`rco_ativacao.efeito_k1_habilitado`,
   `MAEZO_RCO_EFEITO_K1`) nem e lido no caminho da sombra (`evaluate_sync(sombra=True)` ->
   `rco_pin.rco_avaliacao(sombra=True)`), qualquer que seja o estado manual da regra; e a avaliacao da
   regra sai marcada (`AvaliacaoRegra.sombra`), sem ordinal e sem amostragem: nao consome a janela de
   revisao inicial (5d/5e) do caminho que decide;
3. as falhas ESPERADAS da sombra (pin ou manifesto invalido, dicionario recusado, pin AMH ilegivel,
   recusa do montador, erro transitorio ou permanente do motor, prazo esgotado) viram um codigo de
   desfecho; QUALQUER outra excecao e erro de programacao: nao e engolida em silencio — vira log ERROR
   (so o nome do tipo) e a metrica `maezo_rco_sombra_total{desfecho="erro_de_programacao"}`. Ela nao
   sobe porque subir faria o worker falhar (incidente) e mudaria o desfecho dele por causa da sombra;
4. um prazo total (`PRAZO_SOMBRA_S`) segura a chamada ao motor (`asyncio.wait_for`, como o teto de
   `harness._registrar_decisoes_rco`). Os demais passos sao locais (pin e dicionario em cache, montador
   puro).

MINIMIZACAO (a mesma da migracao 0035): log, auditoria e registro levam so codigos, inteiros, a versao
do dicionario e hashes; nunca chave de negocio, numero de guia ou lote, beneficiario ou prestador.

INERTE HOJE: o pin esta em `sem_pacote` (nenhuma tabela `rco_` pinada): a funcao sai antes de montar.
Com o pacote pinado, os termos de `diasAteApresentacao` ainda nao tem produtor no Maezo (ver
`contas.fatos_prazo_conta_rco`): as entradas entram nulas, com o motivo, e nulo nunca nega nem glosa.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import structlog

from maezo.platform.observability import record_rco_sombra
from maezo.platform.rco_entradas import DicionarioEntradasK1, carregar_dicionario_entradas
from maezo.platform.rco_manifesto import ManifestoK1InvalidoError
from maezo.tools.workers import rco_montador, rco_pin
from maezo.tools.workers.dmn_transport import (
    DmnEvaluationError,
    DmnNoResultError,
    DmnTransport,
    DmnVariableDecodeError,
    evaluate_sync,
)

logger = structlog.get_logger(__name__)

#: Desfechos da avaliacao em sombra (codigos fechados do log e da metrica; nenhum dado de caso).
SEM_TRANSPORTE: Final[str] = "sem_transporte"
PIN_INDISPONIVEL: Final[str] = "pin_indisponivel"
SEM_PACOTE: Final[str] = "sem_pacote"
PIN_AMH_INDISPONIVEL: Final[str] = "pin_amh_indisponivel"
DICIONARIO_RECUSADO: Final[str] = "dicionario_recusado"
DOMINIO_NAO_SUPORTADO: Final[str] = "dominio_nao_suportado"
MONTADOR_RECUSOU: Final[str] = "montador_recusou"
AVALIACAO_FALHOU: Final[str] = "avaliacao_falhou"
PRAZO_ESGOTADO: Final[str] = "prazo_esgotado"
AVALIADA: Final[str] = "avaliada"
ERRO_DE_PROGRAMACAO: Final[str] = "erro_de_programacao"
DESFECHOS: Final[frozenset[str]] = frozenset(
    {
        SEM_TRANSPORTE,
        PIN_INDISPONIVEL,
        SEM_PACOTE,
        PIN_AMH_INDISPONIVEL,
        DICIONARIO_RECUSADO,
        DOMINIO_NAO_SUPORTADO,
        MONTADOR_RECUSOU,
        AVALIACAO_FALHOU,
        PRAZO_ESGOTADO,
        AVALIADA,
        ERRO_DE_PROGRAMACAO,
    }
)

#: Prazo total da chamada ao motor na sombra, em segundos (minor 3 da revisao de MZO-3).
PRAZO_SOMBRA_S: Final[float] = 5.0
#: Marca da entrada `rco_` de `dmn_versions` escrita pela sombra (a revisao 5d/5e a ignora).
ORIGEM_SOMBRA: Final[str] = "sombra_montador"

#: Falhas esperadas do motor na sombra: transitoria (`DmnEvaluationError`), permanente
#: (`DmnNoResultError` e `DmnDeterministicEvaluationError`, sua subclasse), decodificacao de saida e o
#: pin recusado ao avaliar/auditar (`RcoRulesPinError`).
_FALHAS_DO_MOTOR: Final[tuple[type[Exception], ...]] = (
    DmnEvaluationError,
    DmnNoResultError,
    DmnVariableDecodeError,
    rco_pin.RcoRulesPinError,
)

#: Quem monta os fatos do caso recebe a versao do K3 pinada (regra 8 do montador) e devolve `FatosDoCaso`.
MontarFatos = Callable[[tuple[int, int]], rco_montador.FatosDoCaso]

#: Cache do dicionario por (pasta k1, sha256 do dicionario no manifesto): so a leitura bem-sucedida.
_dicionarios: dict[tuple[str, str], DicionarioEntradasK1] = {}


def auditoria_das_entradas(montadas: rco_montador.EntradasMontadas) -> dict[str, Any]:
    """O que vai para a entrada `rco_` de `dmn_versions`: so inteiros, nulos e codigos.

    - `derivacoes`: `{nomeDMN: {diasBrutos, toleranciaAplicada, valorEntregue, motivoTolerancia}}`
      (MZO-TOL; hoje so `diasAlemDoPrazoContratual`);
    - `entradasNulas`: `{nomeDMN: MotivoNulo}` das entradas que foram nulas a tabela;
    - `versaoDicionarioEntradas`: a versao do `entradas.json` usado;
    - `origemAvaliacao`: `sombra_montador` (a entrada nao veio do caminho que decide).
    """
    return {
        "derivacoes": {nome: dict(registro) for nome, registro in montadas.derivacoes.items()},
        "entradasNulas": dict(montadas.nulos),
        "versaoDicionarioEntradas": montadas.versao_dicionario,
        "origemAvaliacao": ORIGEM_SOMBRA,
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
    """Monta e avalia; devolve SO o codigo do desfecho (nunca linhas, nunca entradas).

    So as falhas ESPERADAS viram desfecho; qualquer outra excecao sobe (e `avaliar_em_sombra` a conta
    como erro de programacao). Os fatos so sao montados depois de confirmar que a tabela esta pinada."""
    from maezo.adapters.amh.contract import AmhContractPinError  # preguicoso, como `_versao_k3_pinada`

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
    except AmhContractPinError:
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
            sombra=True,
            prazo_s=PRAZO_SOMBRA_S,
        )
    except TimeoutError:
        return PRAZO_ESGOTADO
    except _FALHAS_DO_MOTOR:
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
    """Porta dos workers: avalia em sombra e devolve `None`; nunca muda o desfecho de quem chama.

    Erro de programacao (excecao fora das esperadas) nao e silencioso: log ERROR so com o NOME do tipo
    (a mensagem pode trazer dado de caso) e a metrica `erro_de_programacao`; nao sobe, porque subir
    faria o worker falhar por causa da sombra. `topico`: so rotulo de log."""
    try:
        desfecho = avaliar(dmn, decision_key, montar_fatos, tenant=tenant)
    except Exception as exc:  # razao 3 do modulo: erro de programacao, contado e logado, nunca efeito
        record_rco_sombra(ERRO_DE_PROGRAMACAO)
        logger.error(
            "rco_sombra_erro_de_programacao",
            decision_key=decision_key,
            erro=type(exc).__name__,
            topico=topico,
        )
        return
    record_rco_sombra(desfecho)
    logger.info("rco_sombra", decision_key=decision_key, desfecho=desfecho, topico=topico)


__all__ = [
    "AVALIACAO_FALHOU",
    "AVALIADA",
    "DESFECHOS",
    "DICIONARIO_RECUSADO",
    "DOMINIO_NAO_SUPORTADO",
    "ERRO_DE_PROGRAMACAO",
    "MONTADOR_RECUSOU",
    "ORIGEM_SOMBRA",
    "PIN_AMH_INDISPONIVEL",
    "PIN_INDISPONIVEL",
    "PRAZO_ESGOTADO",
    "PRAZO_SOMBRA_S",
    "SEM_PACOTE",
    "SEM_TRANSPORTE",
    "MontarFatos",
    "auditoria_das_entradas",
    "avaliar",
    "avaliar_em_sombra",
]

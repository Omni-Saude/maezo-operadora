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

GUARDA DA COMPOSICAO (DL-0173): `avaliar` chama `rco_composicao.exigir_avaliacao_isolada` antes de montar;
uma decisao que e membro (ou padrao) de um grupo de `composicoes[]` nunca e avaliada sozinha (desfecho
`membro_de_composicao`, nada avaliado). O grupo inteiro passa por `avaliar_composicao_em_sombra`: monta as
entradas de cada membro, faz a UNIAO (o primeiro valor fica; mesmo nome com valor OU tipo diferente, nulo
contra valor e `True` contra `1` inclusive: `entradas_em_conflito`, nada avaliado), avalia cada membro pelo
funil (`evaluate_sync(sombra=True)`, com a auditoria do montador DAQUELE membro) e aplica a regra do grupo
(`rco_composicao.compor`, `modo=sombra`) com o vocabulario do pin. As mesmas razoes 1-4 valem: devolve
`None`, so codigos fechados no log e na metrica, nenhuma `Exception` sobe. O prazo e UM so para
o grupo (`PRAZO_SOMBRA_S`), repartido em partes iguais entre os membros: este modulo nao le relogio
(`test_montador_e_sombra_nao_leem_relogio`), entao a parte de um membro rapido nao passa ao seguinte, mas a
soma nunca passa do total. O prazo cobre SO as chamadas ao motor (nao a leitura do pin, `montar_fatos` nem o
montador, que sao locais). `BaseException` (cancelamento, saida) propaga, como em `avaliar_em_sombra`.
Alem de `maezo_rco_sombra_total`, a composicao conta `maezo_rco_composicao_revisao_humana_total{motivo,
modo="sombra"}` quando a regra do grupo da revisao humana (o log de `rco_composicao` leva `modo=sombra`):
nada e encaminhado a humano.

REGISTRO DA COMPOSICAO (DL-0187, emenda ao DL-0185): logo depois de `compor`, a sombra chama
`_audit_ctx.registrar_composicao(resultado, modo="sombra")`. O modo vem DAQUI (a avaliacao e sombra por
construcao), nunca do indicador de efeito. Efeito: a entrada de `dmn_versions` de cada membro avaliado leva
`composicaoK1` (nome, `modo=sombra`, desfecho, vencedora, motivo, pelo padrao) e, com sinal,
`sinaisAuditoria`; o harness NAO enfileira aviso de composicao em `modo=sombra` (conta
`maezo_rco_aviso_total{desfecho="suprimido_sombra"}`), e a reconciliacao nao espera linha no outbox para ela.
`registrar_composicao` nunca levanta. Ligada ao AUTH por `tools.workers.auth_rco` (`composicao_carencia_cpt`).

COBERTURA DAS ENTRADAS (DL-0187): toda avaliacao cujo montador entregou as entradas conta, por entrada
DISTINTA, `maezo_rco_entrada_cobertura_total{unidade, entrada, origem, estado, motivo}`: a unidade avaliada
(`UNIDADES_COBERTURA`), a origem K1 do dicionario (ou `start_sem_procedencia` para o valor lido das variaveis
de start, cuja procedencia o contrato nao declara), `preenchida`|`nula` e o `MotivoNulo`. So rotulos fechados;
nunca o valor. E a primeira medida da sombra no AUTH: com o start de hoje quase toda entrada sai nula, entao
o que se mede primeiro e a cobertura, nao a concordancia.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

import structlog

from maezo.platform import rco_composicao
from maezo.platform.observability import record_rco_entrada_cobertura, record_rco_sombra
from maezo.platform.rco_entradas import DicionarioEntradasK1, carregar_dicionario_entradas
from maezo.platform.rco_manifesto import DecisaoK1, ManifestoK1InvalidoError
from maezo.tools.workers import _audit_ctx, rco_montador, rco_pin
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
#: DL-0173: a decisao pedida sozinha e membro (ou padrao) de um grupo de `composicoes[]`: nada avaliado.
MEMBRO_DE_COMPOSICAO: Final[str] = "membro_de_composicao"
#: Composicao: o grupo pedido nao esta em `composicoes[]` do pin; nada avaliado.
COMPOSICAO_AUSENTE: Final[str] = "composicao_ausente"
#: Composicao: dois membros montaram a mesma entrada com valores diferentes; nada avaliado.
ENTRADAS_EM_CONFLITO: Final[str] = "entradas_em_conflito"
#: Composicao avaliada: a regra do grupo escolheu uma decisao (`DESFECHO_ESCOLHIDO`).
COMPOSICAO_ESCOLHIDA: Final[str] = "composicao_escolhida"
#: Composicao avaliada: o grupo caiu em `revisao_humana` (o motivo fica na metrica da composicao).
COMPOSICAO_REVISAO_HUMANA: Final[str] = "composicao_revisao_humana"
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
        MEMBRO_DE_COMPOSICAO,
        COMPOSICAO_AUSENTE,
        ENTRADAS_EM_CONFLITO,
        COMPOSICAO_ESCOLHIDA,
        COMPOSICAO_REVISAO_HUMANA,
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

#: DL-0187: as unidades que a sombra avalia hoje (rotulo `unidade` de `maezo_rco_entrada_cobertura_total`,
#: = `observability.RCO_COBERTURA_UNIDADES`, paridade em teste): a composicao ligada ao AUTH e as decisoes
#: avaliadas sozinhas (AUTH e CONTAS). Unidade nova ligada a um worker entra aqui e la.
UNIDADES_COBERTURA: Final[frozenset[str]] = frozenset(
    {"composicao_carencia_cpt", "rco_geral_elegibilidade", "rco_geral_prazos_conta"}
)
#: DL-0187: origem de cobertura do valor que o montador le das variaveis de START (`Fonte.GUIA`): o contrato
#: de start nao declara de onde veio (guia TISS copiada ou digitada), entao nao se conta como `tiss`.
ORIGEM_START: Final[str] = "start_sem_procedencia"
#: = `observability.RCO_COBERTURA_ORIGENS` (paridade em teste): as origens K1 do dicionario e a do start.
ORIGENS_COBERTURA: Final[frozenset[str]] = frozenset(
    {"tiss", "cadastro", "derivado", "fora_tiss", "parametro", ORIGEM_START}
)
ESTADO_PREENCHIDA: Final[str] = "preenchida"
ESTADO_NULA: Final[str] = "nula"
MOTIVO_NENHUM: Final[str] = "nenhum"

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


def cobertura_das_entradas(
    montadas: Iterable[rco_montador.EntradasMontadas], dicionario: DicionarioEntradasK1
) -> list[tuple[str, str, str, str]]:
    """`(entrada, origem, estado, motivo)` por entrada DISTINTA das montagens (a primeira vence; o montador e
    puro, a mesma entrada sai igual em todo membro). So nomes do dicionario e codigos: nunca o valor."""
    vistas: dict[str, tuple[str, str, str]] = {}
    for m in montadas:
        for nome in m.variaveis:
            if nome in vistas:
                continue
            fonte = m.fontes.get(nome)
            entrada = dicionario.entradas.get(nome)
            if fonte == rco_montador.Fonte.GUIA.value:
                origem = ORIGEM_START
            else:
                origem = entrada.origem if entrada is not None else ""
            motivo = m.nulos.get(nome)
            vistas[nome] = (
                origem,
                ESTADO_NULA if motivo is not None else ESTADO_PREENCHIDA,
                motivo if motivo is not None else MOTIVO_NENHUM,
            )
    return [(nome, *v) for nome, v in vistas.items()]


def _registrar_cobertura(
    unidade: str, montadas: Iterable[rco_montador.EntradasMontadas], dicionario: DicionarioEntradasK1
) -> None:
    """Conta a cobertura (DL-0187). Nunca levanta: a medida nao muda o desfecho da sombra."""
    try:
        for entrada, origem, estado, motivo in cobertura_das_entradas(montadas, dicionario):
            record_rco_entrada_cobertura(
                unidade=unidade, entrada=entrada, origem=origem, estado=estado, motivo=motivo
            )
    except Exception as exc:  # telemetria: so o tipo, nunca o caso
        logger.error("rco_sombra_cobertura_falhou", unidade=unidade, erro=type(exc).__name__)


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
    try:
        rco_composicao.exigir_avaliacao_isolada(pin, decision_key)
    except rco_composicao.DecisaoSoEmComposicaoError:
        return MEMBRO_DE_COMPOSICAO  # DL-0173: membro de grupo so com `avaliar_composicao_em_sombra`
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
    _registrar_cobertura(decision_key, (montadas,), dicionario)
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


def avaliar_composicao(
    dmn: DmnTransport | None,
    nome_composicao: str,
    montar_fatos: MontarFatos,
    *,
    tenant: str | None = None,
) -> str:
    """Monta, avalia e compoe o grupo `nome_composicao`; devolve SO o codigo do desfecho.

    Mesma forma de `avaliar`: so as falhas ESPERADAS viram desfecho; qualquer outra excecao sobe (e
    `avaliar_composicao_em_sombra` a conta como erro de programacao), inclusive a que `compor` absorveu
    como `avaliador_falhou` (ela e relancada aqui). Os fatos sao montados UMA vez e servem a todos os
    membros (o montador e puro)."""
    from maezo.adapters.amh.contract import AmhContractPinError  # preguicoso, como `avaliar`

    if dmn is None:
        return SEM_TRANSPORTE
    try:
        pin = rco_pin.rco_rules_pin()
    except rco_pin.RcoRulesPinError:
        return PIN_INDISPONIVEL
    if pin.manifesto is None:
        return SEM_PACOTE
    grupo = next((c for c in pin.composicoes if c.nome == nome_composicao), None)
    if grupo is None:
        return COMPOSICAO_AUSENTE
    decisoes: dict[str, DecisaoK1] = {}
    for membro in grupo.decisoes:
        tabela = pin.tables.get(membro)
        if tabela is None or tabela.decisao is None:
            return SEM_PACOTE
        decisoes[membro] = tabela.decisao
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
    montadas: dict[str, rco_montador.EntradasMontadas] = {}
    try:
        for membro, decisao in decisoes.items():
            montadas[membro] = rco_montador.montar_entradas(decisao, dicionario, fatos)
    except rco_montador.DominioNaoSuportadoError:
        return DOMINIO_NAO_SUPORTADO
    except rco_montador.MontadorEntradasError:
        return MONTADOR_RECUSOU
    _registrar_cobertura(nome_composicao, montadas.values(), dicionario)
    uniao: dict[str, Any] = {}
    for m in montadas.values():
        for nome, valor in m.variaveis.items():
            if nome not in uniao:
                uniao[nome] = valor  # o primeiro valor fica; nunca sobrescrito
                continue
            anterior = uniao[nome]
            # mesmo valor E mesmo tipo: `True == 1` e `None` contra valor sao conflito, nunca uniao
            if type(anterior) is not type(valor) or anterior != valor:
                return ENTRADAS_EM_CONFLITO
    auditorias = {membro: auditoria_das_entradas(m) for membro, m in montadas.items()}
    prazo_membro = PRAZO_SOMBRA_S / len(grupo.decisoes)
    falhas: list[Exception] = []

    def avaliador(decisao: str, entradas: Mapping[str, Any]) -> rco_composicao.Retorno:
        try:
            return evaluate_sync(
                dmn,
                decisao,
                dict(entradas),
                tenant=tenant,
                entradas_rco=auditorias[decisao],
                sombra=True,
                prazo_s=prazo_membro,
            )
        except Exception as exc:  # guardada para o desfecho; `compor` a trata como `avaliador_falhou`
            falhas.append(exc)
            raise

    resultado = rco_composicao.compor(
        grupo,
        avaliador,
        uniao,
        modo=rco_composicao.MODO_SOMBRA,
        ordem_restricao=pin.ordem_restricao,
        sinais_auditoria=pin.sinais_auditoria,
        adversas=pin.adversas or rco_composicao.ADVERSAS_PISO,
        adversa_exige_preenchidos=pin.adversa_exige_preenchidos or rco_composicao.ADVERSA_EXIGE_PISO,
    )
    # DL-0187: o modo e o desta avaliacao (sombra por construcao); o harness nao enfileira aviso de sombra e
    # a auditoria leva `composicaoK1`/`sinaisAuditoria`. Nunca levanta.
    _audit_ctx.registrar_composicao(resultado, modo=rco_composicao.MODO_SOMBRA)
    if falhas:
        falha = falhas[0]
        if isinstance(falha, TimeoutError):
            return PRAZO_ESGOTADO
        if isinstance(falha, _FALHAS_DO_MOTOR):
            return AVALIACAO_FALHOU
        raise falha  # erro de programacao no avaliador: `compor` nao o esconde da sombra
    return COMPOSICAO_REVISAO_HUMANA if resultado.revisao_humana else COMPOSICAO_ESCOLHIDA


def avaliar_composicao_em_sombra(
    dmn: DmnTransport | None,
    nome_composicao: str,
    montar_fatos: MontarFatos,
    *,
    tenant: str | None = None,
    topico: str | None = None,
) -> None:
    """Porta dos workers para um GRUPO de `composicoes[]`: avalia em sombra e devolve `None`.

    Mesmo contrato de `avaliar_em_sombra`: nunca muda o desfecho de quem chama, nao escreve variavel de
    processo e nao le o indicador de efeito do K1. Registra a composicao com `modo=sombra`
    (`_audit_ctx.registrar_composicao`, DL-0187): a auditoria leva `composicaoK1`/`sinaisAuditoria` e nenhum
    aviso e enfileirado (o harness so enfileira `modo=efeito`). Erro de programacao (`Exception` fora das
    esperadas): log ERROR so com o NOME do tipo e a metrica `erro_de_programacao`, sem subir;
    `BaseException` propaga. O prazo (`PRAZO_SOMBRA_S`) limita so as chamadas ao motor. `topico`: so
    rotulo de log."""
    try:
        desfecho = avaliar_composicao(dmn, nome_composicao, montar_fatos, tenant=tenant)
    except Exception as exc:  # razao 3 do modulo: erro de programacao, contado e logado, nunca efeito
        record_rco_sombra(ERRO_DE_PROGRAMACAO)
        logger.error(
            "rco_sombra_composicao_erro_de_programacao",
            composicao=nome_composicao,
            erro=type(exc).__name__,
            topico=topico,
        )
        return
    record_rco_sombra(desfecho)
    logger.info("rco_sombra_composicao", composicao=nome_composicao, desfecho=desfecho, topico=topico)


__all__ = [
    "AVALIACAO_FALHOU",
    "AVALIADA",
    "COMPOSICAO_AUSENTE",
    "COMPOSICAO_ESCOLHIDA",
    "COMPOSICAO_REVISAO_HUMANA",
    "DESFECHOS",
    "DICIONARIO_RECUSADO",
    "DOMINIO_NAO_SUPORTADO",
    "ENTRADAS_EM_CONFLITO",
    "ERRO_DE_PROGRAMACAO",
    "ESTADO_NULA",
    "ESTADO_PREENCHIDA",
    "MEMBRO_DE_COMPOSICAO",
    "MONTADOR_RECUSOU",
    "MOTIVO_NENHUM",
    "ORIGEM_START",
    "ORIGENS_COBERTURA",
    "ORIGEM_SOMBRA",
    "PIN_AMH_INDISPONIVEL",
    "PIN_INDISPONIVEL",
    "PRAZO_ESGOTADO",
    "PRAZO_SOMBRA_S",
    "SEM_PACOTE",
    "SEM_TRANSPORTE",
    "UNIDADES_COBERTURA",
    "MontarFatos",
    "auditoria_das_entradas",
    "avaliar",
    "avaliar_composicao",
    "avaliar_composicao_em_sombra",
    "avaliar_em_sombra",
    "cobertura_das_entradas",
]

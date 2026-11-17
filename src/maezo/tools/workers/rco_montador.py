"""Montador de entradas das decisoes K1 do rco (MZO-2b; plano do programa, linha MZO-2).

O QUE ISTO FAZ. Para uma decisao do pacote K1 (`DecisaoK1`, com `dominio` e `entradas` do manifesto),
monta o mapa `{nomeDMN: valor | None}` que a tabela recebe, a partir de quatro fontes de fatos e do
dicionario de entradas do K1 (`entradas.json`, versao 1.1.0, lido por
`maezo.platform.rco_entradas`). O dicionario e a autoridade: cada entrada so vem da ORIGEM que ele
declara (`tiss` -> guia; `cadastro` -> cadastro ou K3; `fora_tiss` -> solicitacao fora da TISS;
`derivado` -> a formula `formula.formula` dele). Nenhuma regra de negocio mora aqui (AGENTS.md regra 5).

REGRAS DURAS (coordenacao do programa, 16/11/2026):
1. Dado ausente entra NULO (`None`). Nunca inventado, presumido, com valor padrao ou inferido. Valor
   fora do tipo ou do dominio declarado no dicionario tambem entra nulo (com o motivo registrado): o
   dicionario e o contrato do que pode entrar na tabela.
2. Entrada derivada SEM formula estruturada no dicionario (as anteriores ao gate PROG-RCO-15 que ainda
   estao em `derivadas_sem_formula_legado`) entra nula: o montador nao adivinha formula a partir da
   descricao. Derivada com formula e calculada EXATAMENTE pela formula publicada; se o texto da formula
   ou os termos mudarem no dicionario, o montador recusa (`MontadorEntradasError`) ate ser revisto.
   Formula publicada que o Maezo ainda nao sabe calcular sem inventar (lacuna L-prog-rco-04 do rco, ou
   termo sem produtor no Maezo) fica em `FORMULAS_PENDENTES` e entra nula
   (`formula_publicada_nao_implementada`). Formula implementada num dicionario anterior a ela
   (`_FormulaImplementada.desde`; a 1.1.0 nao tinha a de `diasAteApresentacao`) entra nula por desenho.
3. Carencia com grupo e saldo, situacao cadastral e CPT na data vem do K3 v1.16 da AMH
   (`schemas/contracts/maezo/v1.16/`, UNPUBLISHED). Enquanto o contrato AMH pinado
   (`config/integrations/amh/contracts.lock.json`, `AmhContractPin.versao_manifesto_pinada`) for
   anterior a 1.16, essas entradas sao NULAS mesmo que alguem passe fatos K3. A tabela
   `rco_geral_carencia` com essas entradas nulas cai em `revisao_humana` (fila `cadastro`), nunca em
   `negar`: conferido no motor (camunda-engine-dmn 7.24, `VerificadorNulos` do rco) e travado em
   `tests/unit/tools/workers/test_rco_montador.py` pela leitura da tabela.
4. Fato K3 so vale para a DATA DO ATENDIMENTO: a derivacao do lago (k1-entradas-origem.yaml,
   `saldoCarenciaDias`/`cptVigente`) manda calcular na data do atendimento, nunca na corrente. Fatos K3
   avaliados em outra data (ou sem a data do atendimento na guia) entram nulos.
5. Cadastro e fora da TISS NUNCA vem das variaveis de processo (sementes do start, nao verificadas):
   so dos mapas que o worker que os resolveu entrega. Da guia, `data_atendimento` e
   `codigo_procedimento_tuss` vem das variaveis do contrato de start, pelo nome que o dicionario
   declara (o valor ainda passa pelo tipo e pelo dominio do dicionario).
6. O CARATER DO ATENDIMENTO NUNCA vem das variaveis de start: ate o DL-0170 o caminho de start o
   preenchia com `"eletivo"` quando faltava (`agents/rafael/delegation.py`, `agents/rafael/graph.py`; hoje o
   Rafael recusa caso sem carater), a variavel de start segue sendo semente nao verificada, e `eletivo` -> `1`
   e o unico valor que deixa casar as linhas `negar` de `rco_geral_carencia`/`rco_geral_elegibilidade`.
   Ele so entra por `FatosDoCaso.carater_atendimento_guia` (valor da guia TISS, sem padrao), senao nulo.
   LIGAR O MONTADOR (MZO-3) EXIGE que o start carregue o carater da guia ou nulo: os padroes `"eletivo"`
   do Rafael ja sairam (DL-0170).
7. Carencia do K3 (grupo, prazo e saldo) exige, alem do pin >= 1.16, a lacuna
   `saldo_isencoes_portabilidade` FECHADA na configuracao `config/integrations/rco/k3-lacunas.json`: o K3
   declara que nao se sabe se `saldo_dias` ja considera isencoes e portabilidade (k1-entradas-origem.yaml
   237-240). Configuracao explicita, desligada por padrao, nunca efeito do pin; lida fail-closed (ausente
   ou malformada = aberta; `fechada: true` sem `decidido_por` e `decidido_em` = aberta). Aberta, essas
   tres entradas entram nulas, mesmo com o K3 pinado.
8. `FatosDoCaso.versao_k3_pinada` vem de quem chama: LIGAR O MONTADOR EXIGE preenche-la so com
   `AmhContractPin.versao_manifesto_pinada` do pin verificado (`load_contract_pin`), nunca a mao.
9. `diasAlemDoPrazoContratual` (MZO-TOL, decisao do dono PROG-COORD-22): depois da formula, os dias
   positivos perdem a tolerancia interna de `config/integrations/rco/tolerancia-apresentacao-conta.json`
   (`max(0, dias - tolerancia)`); `<= 0` e nulo seguem intactos. Configuracao ausente ou invalida, ou a
   tabela pinada `rco_geral_prazos_conta` ja liberando faixa positiva limitada (achado do pin), zeram a
   tolerancia: ela nunca dobra nem se amplia por erro. O registro vai em `EntradasMontadas.derivacoes`.
   `diasAteApresentacao` (MZO-3, dicionario 1.2.0) e calculada das datas da guia e do protocolo do lote;
   sem os termos (hoje nenhum worker do Maezo os produz: `FatosDoCaso.termos_sem_produtor`) segue nula.
10. Termos de formula com origem `tiss` vem de `FatosDoCaso.tiss`, os `fora_tiss` de `FatosDoCaso.fora_tiss`
   e os `cadastro` de `FatosDoCaso.cadastro`, pela chave de `CAMPOS_TERMO_*` — nunca das variaveis de
   start. Data so como `AAAA-MM-DD` (ou `datetime.date`): instante com hora ou fuso entra nulo
   (`termo_data_invalida`), porque o dia de um instante depende do fuso, que o rco nao publica. Nenhuma
   data tem padrao: em particular, nunca a data de hoje que `contas._normalize_data_recebimento_lote`
   usa quando o lote nao traz o recebimento.

ESCOPO DESTA ENTREGA: framework + os dominios `regras-gerais` (transversal: toda guia passa por
elegibilidade, carencia e CPT) e `diarias-taxas` (maior volume de autorizacao no agregado real do
backtest 2026-10 do rco: 38,9% das autorizacoes solicitadas; `dados-agregados/sinistro-glosa-tuss.csv`
x `historico/diagnostico/priorizacao/codigos-dominio.csv`). Decisao de outro dominio levanta
`DominioNaoSuportadoError`: quem chama encaminha o caso para pessoa (plano, risco "montador maior que o
previsto": "os demais ficam em fila humana").
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Final

from maezo.platform.rco_entradas import DicionarioEntradasK1, EntradaK1
from maezo.platform.rco_manifesto import DecisaoK1, json_estrito
from maezo.tools.workers import rco_pin

#: Primeira versao do contrato AMH (manifest aditivo) que traz o K3 das entradas de decisao.
VERSAO_K3_ENTRADAS: Final[tuple[int, int]] = (1, 16)
#: Configuracao das lacunas do K3 (regra 7 do modulo). Muda so por PR, com a decisao do negocio citada.
K3_LACUNAS_RELATIVO: Final[str] = "config/integrations/rco/k3-lacunas.json"
LACUNA_SALDO: Final[str] = "saldo_isencoes_portabilidade"
#: Atributos de `FatosK3V116` que a lacuna acima segura (as entradas da tabela de carencia).
_K3_CARENCIA: Final[frozenset[str]] = frozenset({"grupo", "dias", "saldo_dias"})


class MontadorEntradasError(ValueError):
    """O montador nao sabe montar a decisao com seguranca (dicionario, manifesto ou mapeamento divergem)."""


class DominioNaoSuportadoError(MontadorEntradasError):
    """Dominio do rco ainda sem mapeamento no montador: o caso vai para pessoa."""


class Fonte(StrEnum):
    GUIA = "guia"  # variavel do contrato de start, lida da guia TISS
    GUIA_EXPLICITA = "guia_explicita"  # campo de `FatosDoCaso` com o valor da guia, nunca o start
    CADASTRO = "cadastro"  # fato de cadastro entregue pelo worker que o resolveu
    FORA_TISS = "fora_tiss"  # fato da solicitacao fora da TISS
    K3_V116 = "k3_v1_16"  # rota TINA do K3 (manifest AMH v1.16)
    FORMULA = "formula"  # `formula.formula` publicada no dicionario
    FORMULA_PENDENTE = "formula_pendente"  # formula publicada que o Maezo ainda nao calcula: nula
    NULO_POR_DESENHO = "nulo_por_desenho"  # derivada sem formula estruturada


class MotivoNulo(StrEnum):
    AUSENTE = "ausente_na_fonte"
    TIPO_INVALIDO = "tipo_invalido"
    FORA_DO_DOMINIO = "fora_do_dominio"
    DERIVADA_SEM_FORMULA = "derivada_sem_formula"
    TERMO_AUSENTE = "termo_da_formula_ausente"
    TERMO_SEM_PRODUTOR = "termo_sem_produtor_no_maezo"
    TERMO_DATA_INVALIDA = "termo_data_invalida"
    REGIME_DA_GUIA_AUSENTE = "regime_da_guia_ausente"
    FORMULA_PENDENTE = "formula_publicada_nao_implementada"
    PRAZO_CONTRATUAL_NEGATIVO = "prazo_contratual_negativo"
    K3_NAO_PINADO = "k3_v1_16_nao_pinado"
    K3_FORA_DA_DATA = "k3_fora_da_data_do_atendimento"
    K3_LACUNA_SALDO_ABERTA = "k3_lacuna_saldo_isencoes_aberta"


@dataclass(frozen=True, slots=True)
class FatosK3V116:
    """Fatos das rotas TINA do manifest AMH v1.16 (UNPUBLISHED), para o procedimento da guia.

    Espelha os campos do schema v1.16 (lido, nunca copiado):
    - `tina-carencia-grupo.openapi.yaml` 0.1.0, item de `itens[]` da carencia do procedimento:
      `grupo` (`parto_a_termo|demais|nao_classificado`), `dias`, `saldo_dias`; `data_atendimento` do corpo;
    - `tina-elegibilidade-situacao.openapi.yaml` 0.2.0: `situacao_cadastral` (enum do K1 ou null);
    - `tina-cpt-data.openapi.yaml` 0.2.0: `tem_cpt_vigente` avaliado com `data_atendimento`.

    QUAL item de `itens[]` e o do procedimento: LACUNA declarada no K1 (derivacao do lago de
    `grupoCarencia`: "o relatorio nao diz como a rota liga o procedimento ao grupo"). Nenhum adaptador
    produz este objeto hoje; ele existe para o montador ja nascer no formato do K3.
    """

    data_atendimento: str | None
    grupo: str | None = None
    dias: int | None = None
    saldo_dias: int | None = None
    situacao_cadastral: str | None = None
    tem_cpt_vigente: bool | None = None


@dataclass(frozen=True, slots=True)
class FatosDoCaso:
    """As fontes de fatos de um caso. Campo ausente = fato ausente."""

    guia: Mapping[str, object]  # variaveis do contrato de start (nomes Maezo)
    #: SO `AmhContractPin.versao_manifesto_pinada` do pin verificado (regra 8 do modulo).
    versao_k3_pinada: tuple[int, int]
    cadastro: Mapping[str, object] = field(default_factory=dict)
    fora_tiss: Mapping[str, object] = field(default_factory=dict)
    k3: FatosK3V116 | None = None
    #: Valor do elemento `caraterAtendimento` da guia TISS, como a guia o traz, SEM PADRAO: nunca a
    #: variavel de start `carater_atendimento` (semente nao verificada; ate o DL-0170 era preenchida com
    #: "eletivo" quando faltava). None = nulo.
    carater_atendimento_guia: str | None = None
    #: Elementos da guia TISS que sao TERMOS de formula (chaves de `CAMPOS_TERMO_TISS`), entregues pelo
    #: worker que leu a guia, sem padrao. Nunca as variaveis de start.
    tiss: Mapping[str, object] = field(default_factory=dict)
    #: Tipo da guia de onde vieram as datas, para a formula de `diasAteApresentacao`: `REGIME_INTERNACAO`
    #: (guia de resumo de internacao: `dataFinalFaturamento`, a alta) ou `REGIME_AMBULATORIAL` (guia
    #: SP/SADT: `dataExecucao`). Qualquer outro valor ou None = nulo; o montador nunca o deduz das datas.
    regime_guia: str | None = None
    #: Termos de formula que nenhum worker do Maezo produz no caminho que chama (nome do termo no
    #: dicionario): entram nulos com `termo_sem_produtor_no_maezo`, mesmo que algum mapa traga a chave.
    termos_sem_produtor: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EntradasMontadas:
    decisao: str
    versao_dicionario: str
    variaveis: Mapping[str, object]  # {nomeDMN: valor | None}, toda entrada da decisao presente
    fontes: Mapping[str, str]  # {nomeDMN: Fonte}
    nulos: Mapping[str, str]  # {nomeDMN: MotivoNulo} das entradas nulas
    #: {nomeDMN: registro} das derivadas com ajuste de configuracao (hoje so `diasAlemDoPrazoContratual`,
    #: MZO-TOL): o bruto, o ajuste aplicado, o valor entregue a tabela e o motivo, para reconstruir o
    #: desfecho na auditoria. So inteiros e codigos: nenhum dado de caso.
    derivacoes: Mapping[str, Mapping[str, object]] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class MapeamentoEntrada:
    """De onde o montador tira uma entrada. `origem` repete a do dicionario e e conferida."""

    origem: str
    fonte: Fonte
    #: so K3: atributo de `FatosK3V116`. Guia, cadastro e fora da TISS usam o nome Maezo que o PROPRIO
    #: dicionario declara (`nome_maezo`, senao `nome_snake`): o montador nao inventa nome de variavel.
    campo: str | None = None


# --- conversao declarada pelo rco ----------------------------------------------------------------
#: `caraterAtendimento`: o de-para dos valores do Maezo para a TUSS tabela 23 e o que o rco DECLARA
#: (`valores_maezo` do seu dicionario de dados; o K1 ainda nao o publica em `entradas.json`). Versionado,
#: com a origem (repo, commit, linhas), em `config/integrations/rco/carater-tuss23.json`. Casamento exato.
CARATER_TUSS23_RELATIVO: Final[str] = "config/integrations/rco/carater-tuss23.json"


def ler_lacuna_saldo_fechada(caminho: Path) -> bool:
    """`True` SO com `saldo_isencoes_portabilidade.fechada is True` E `decidido_por`/`decidido_em` texto nao
    vazio. Qualquer outra coisa (arquivo ausente, JSON invalido, campo faltando, sem procedencia) = aberta."""
    try:
        lacuna = json_estrito(caminho.read_bytes())[LACUNA_SALDO]
        fechada = lacuna["fechada"] is True
        procedencia = all(
            isinstance(lacuna[c], str) and lacuna[c].strip() for c in ("decidido_por", "decidido_em")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return fechada and procedencia


def lacuna_saldo_fechada() -> bool:
    """A configuracao de bordo (checkout, depois a copia ao lado do pacote); sem arquivo = aberta."""
    parents = Path(__file__).resolve().parents
    for candidato in (parents[4] / K3_LACUNAS_RELATIVO, parents[2] / K3_LACUNAS_RELATIVO):
        if candidato.is_file():
            return ler_lacuna_saldo_fechada(candidato)
    return False


@lru_cache(maxsize=1)
def carater_tuss23() -> Mapping[str, str]:
    """`valores_maezo` + `aceitos_sem_conversao` do arquivo versionado. Levanta `MontadorEntradasError`."""
    parents = Path(__file__).resolve().parents
    for candidato in (parents[4] / CARATER_TUSS23_RELATIVO, parents[2] / CARATER_TUSS23_RELATIVO):
        if candidato.is_file():
            try:
                dados = json_estrito(candidato.read_bytes())
                tabela = {str(k): str(v) for k, v in dict(dados["valores_maezo"]).items()}
                tabela.update({str(c): str(c) for c in list(dados["aceitos_sem_conversao"])})
            except (ValueError, KeyError, TypeError) as exc:
                raise MontadorEntradasError(
                    f"{CARATER_TUSS23_RELATIVO} ilegivel ({type(exc).__name__})"
                ) from None
            if not set(tabela.values()) <= {"1", "2"}:
                raise MontadorEntradasError(f"{CARATER_TUSS23_RELATIVO}: valor fora da TUSS 23")
            return MappingProxyType(tabela)
    raise MontadorEntradasError(f"{CARATER_TUSS23_RELATIVO} nao encontrado")


# --- formulas publicadas (derivacao.formula) -------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _FormulaImplementada:
    texto: str  # o texto EXATO de `formula.formula` no dicionario
    termos: tuple[tuple[str, str], ...]  # (nome, origem) de cada termo, na ordem do dicionario
    calcular: Callable[[Mapping[str, object]], tuple[int | None, MotivoNulo | None]]
    #: Primeira versao do dicionario que publica esta formula. Dicionario anterior sem a formula: a
    #: entrada e nula por desenho (nunca calculada por uma formula que aquele pacote nao publicou).
    desde: tuple[int, int, int] = (1, 1, 0)
    #: Fatos que o TEXTO da formula nomeia fora dos termos (hoje so o regime da guia de
    #: `diasAteApresentacao`: "internacao, alta" x "ambulatorial"). Lidos de campos explicitos de
    #: `FatosDoCaso` (`_condicao`), nunca deduzidos.
    condicoes: tuple[str, ...] = ()
    #: Ajuste de CONFIGURACAO do Maezo sobre o valor ja validado (nunca regra de negocio nova): recebe o
    #: calculo bruto da formula e o valor validado contra o dicionario e devolve (valor entregue, registro
    #: de auditoria). So `diasAlemDoPrazoContratual` tem um.
    ajuste: Callable[[object, object], tuple[object, Mapping[str, object]]] | None = None


# --- tolerancia do prazo de apresentacao da conta (MZO-TOL, decisao do dono PROG-COORD-22) ----------
# A regra do rco e o contrato do prestador dizem o prazo do contrato (30 dias no contrato OMNI); o Maezo
# aceita, por CONFIGURACAO INTERNA, uma folga sobre esse prazo antes de entregar os dias a tabela
# `rco_geral_prazos_conta`, que segue glosando com `diasAlemDoPrazoContratual > 0`. Valor UNICO para
# todos os prestadores: um arquivo versionado, sem sobreposicao por prestador, caso ou tenant, e sem
# variavel de ambiente. Nunca publicado: nem a chave nem o numero aparecem para prestador ou beneficiario
# (`tests/unit/tools/workers/test_rco_montador_tolerancia.py` varre os caminhos que falam com eles).
# Nao se aplica a `prazoComprovacaoUsoVencido` (OPME de urgencia), que segue o prazo do contrato.
TOLERANCIA_CONTA_RELATIVO: Final[str] = "config/integrations/rco/tolerancia-apresentacao-conta.json"
CHAVE_TOLERANCIA_CONTA: Final[str] = "tolerancia_apresentacao_conta_dias"
#: Teto = a decisao do dono PROG-COORD-22 (30 dias alem dos 30 do contrato: 60 no total). Acima dele o
#: arquivo e invalido e a tolerancia cai a 0: ampliar exige nova decisao e mudanca deste teto por PR.
TOLERANCIA_CONTA_MAXIMA_DIAS: Final[int] = 30
_CAMPOS_TOLERANCIA: Final[frozenset[str]] = frozenset(
    {"_comentario", CHAVE_TOLERANCIA_CONTA, "decidido_por", "decidido_em"}
)
#: A tabela que recebe `diasAlemDoPrazoContratual`; a guarda le o DMN DELA no pin.
DECISAO_PRAZOS_CONTA: Final[str] = "rco_geral_prazos_conta"


class MotivoTolerancia(StrEnum):
    APLICADA = "aplicada"
    DENTRO_DO_PRAZO = "dentro_do_prazo_contratual"
    TERMO_NULO = "termo_nulo"
    FORA_DO_DOMINIO = "fora_do_dominio"  # calculo fora do tipo ou do dominio do dicionario: entra nulo
    CONFIGURACAO_INVALIDA = "configuracao_invalida"  # ausente ou malformada: tolerancia 0
    DMN_JA_TOLERA = "dmn_ja_tolera"  # a tabela pinada libera faixa positiva limitada: tolerancia 0
    DMN_NAO_VERIFICADO = "dmn_nao_verificado"  # pin ilegivel ou tabela fora do pin: tolerancia 0


def ler_tolerancia_conta(caminho: Path) -> int | None:
    """A tolerancia do arquivo, SO com: objeto JSON estrito com exatamente as chaves conhecidas, valor
    inteiro (nao booleano) em 0..`TOLERANCIA_CONTA_MAXIMA_DIAS` e `decidido_por`/`decidido_em` texto
    nao vazio. Qualquer outra coisa (arquivo ausente, JSON invalido, chave a mais como um valor por
    prestador, tipo ou faixa errados, sem procedencia) = `None`, que o montador le como tolerancia 0
    (o prazo do contrato)."""
    try:
        dados = json_estrito(caminho.read_bytes())
        if not isinstance(dados, dict) or set(dados) != _CAMPOS_TOLERANCIA:
            return None
        valor = dados[CHAVE_TOLERANCIA_CONTA]
        procedencia = all(
            isinstance(dados[c], str) and dados[c].strip() for c in ("decidido_por", "decidido_em")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not procedencia or not isinstance(valor, int) or isinstance(valor, bool):
        return None
    return valor if 0 <= valor <= TOLERANCIA_CONTA_MAXIMA_DIAS else None


_tolerancia_lida: list[int] = []  # cache so do SUCESSO (como `rco_pin.rco_rules_pin`)


def tolerancia_conta_configurada() -> int | None:
    """A configuracao de bordo (checkout, depois a copia ao lado do pacote); sem arquivo = `None`.

    So a leitura valida fica em cache: falha (ausente ou invalida) e lida de novo na proxima chamada."""
    if _tolerancia_lida:
        return _tolerancia_lida[0]
    parents = Path(__file__).resolve().parents
    for candidato in (parents[4] / TOLERANCIA_CONTA_RELATIVO, parents[2] / TOLERANCIA_CONTA_RELATIVO):
        if candidato.is_file():
            valor = ler_tolerancia_conta(candidato)
            if valor is not None:
                _tolerancia_lida.append(valor)
            return valor
    return None


def dmn_prazos_conta_bloqueia_tolerancia() -> MotivoTolerancia | None:
    """Motivo para zerar a tolerancia do Maezo pelo DMN pinado, ou `None` se ela pode valer.

    Le o achado que o carregador do pin ja calculou sobre os bytes da tabela (a mesma leitura do gate,
    codigo `tolerancia-prazo-conta-no-dmn`). Fail-closed: pin ilegivel ou tabela fora do pin = nao
    verificado (tolerancia 0; sem a tabela pinada a decisao nem e avaliada).
    """
    try:
        tabela = rco_pin.rco_rules_pin().tables.get(DECISAO_PRAZOS_CONTA)
    except rco_pin.RcoRulesPinError:
        return MotivoTolerancia.DMN_NAO_VERIFICADO
    if tabela is None:
        return MotivoTolerancia.DMN_NAO_VERIFICADO
    return MotivoTolerancia.DMN_JA_TOLERA if tabela.tolerancia_no_dmn else None


def aplicar_tolerancia(dias_alem: int | None, tolerancia: int) -> int | None:
    """`max(0, dias_alem - tolerancia)` so quando `dias_alem > 0`; `<= 0` e nulo passam intactos."""
    if dias_alem is None or dias_alem <= 0:
        return dias_alem
    return max(0, dias_alem - tolerancia)


def _registro_tolerancia(
    bruto: int | None, tolerancia: int | None, entregue: int | None, motivo: MotivoTolerancia
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "diasBrutos": bruto,
            "toleranciaAplicada": tolerancia,
            "valorEntregue": entregue,
            "motivoTolerancia": motivo.value,
        }
    )


def _ajustar_dias_alem_do_prazo(bruto: object, valor: object) -> tuple[object, Mapping[str, object]]:
    """O ajuste de `diasAlemDoPrazoContratual`. Nulo segue nulo (nunca zero, nunca glosa); `<= 0` segue
    intacto; positivo perde a tolerancia configurada, que vale 0 com configuracao invalida ou com o DMN
    pinado ja tolerando (ou nao verificavel). So le configuracao e pin quando ha dias positivos.
    Calculo fora do dominio do dicionario entra nulo, mas a auditoria guarda o bruto."""
    if not isinstance(valor, int) or isinstance(valor, bool):
        if isinstance(bruto, int) and not isinstance(bruto, bool):
            return None, _registro_tolerancia(bruto, None, None, MotivoTolerancia.FORA_DO_DOMINIO)
        return None, _registro_tolerancia(None, None, None, MotivoTolerancia.TERMO_NULO)
    if valor <= 0:
        return valor, _registro_tolerancia(valor, 0, valor, MotivoTolerancia.DENTRO_DO_PRAZO)
    configurada = tolerancia_conta_configurada()
    if configurada is None:
        return valor, _registro_tolerancia(valor, 0, valor, MotivoTolerancia.CONFIGURACAO_INVALIDA)
    bloqueio = dmn_prazos_conta_bloqueia_tolerancia()
    if bloqueio is not None:
        return valor, _registro_tolerancia(valor, 0, valor, bloqueio)
    entregue = aplicar_tolerancia(valor, configurada)
    return entregue, _registro_tolerancia(valor, configurada, entregue, MotivoTolerancia.APLICADA)


class _SemProdutor:
    """Marca do termo que nenhum worker do Maezo produz (`FatosDoCaso.termos_sem_produtor`)."""

    def __repr__(self) -> str:
        return "SEM_PRODUTOR"


SEM_PRODUTOR: Final[_SemProdutor] = _SemProdutor()


def _motivo_do_termo(valor: object) -> MotivoNulo:
    return MotivoNulo.TERMO_SEM_PRODUTOR if valor is SEM_PRODUTOR else MotivoNulo.TERMO_AUSENTE


def _dias_alem_do_prazo_contratual(termos: Mapping[str, object]) -> tuple[int | None, MotivoNulo | None]:
    """`diasAteApresentacao - prazoApresentacaoContratualDias` (nulo se faltar um dos termos ou se o
    prazo do contrato for negativo) — dicionario K1 1.1.0, entrada `diasAlemDoPrazoContratual`."""
    dias = termos.get("diasAteApresentacao")
    prazo = termos.get("prazoApresentacaoContratualDias")
    if prazo is SEM_PRODUTOR:
        return None, MotivoNulo.TERMO_SEM_PRODUTOR
    if (
        not isinstance(dias, int)
        or isinstance(dias, bool)
        or not isinstance(prazo, int)
        or isinstance(prazo, bool)
    ):
        return None, MotivoNulo.TERMO_AUSENTE
    if prazo < 0:
        return None, MotivoNulo.PRAZO_CONTRATUAL_NEGATIVO
    return dias - prazo, None


_DATA_ISO: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def data_do_termo(valor: object) -> date | None:
    """A data de um termo: `datetime.date` (nao `datetime`) ou texto exatamente `AAAA-MM-DD` valido.

    Instante (`datetime`, ou texto com hora e/ou fuso) e qualquer outra forma = `None`: o dia de um
    instante depende do fuso, e o rco nao publica qual. Nunca converte fuso, nunca trunca, nunca usa
    a data corrente."""
    if isinstance(valor, datetime):
        return None
    if isinstance(valor, date):
        return valor
    if not isinstance(valor, str) or _DATA_ISO.fullmatch(valor) is None:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _data(termos: Mapping[str, object], nome: str) -> tuple[date | None, MotivoNulo | None]:
    valor = termos.get(nome)
    if valor is None or valor is SEM_PRODUTOR or (isinstance(valor, str) and not valor.strip()):
        return None, _motivo_do_termo(valor)
    data = data_do_termo(valor)
    return (data, None) if data is not None else (None, MotivoNulo.TERMO_DATA_INVALIDA)


def _dias_entre(termos: Mapping[str, object], inicio: str, fim: str) -> tuple[int | None, MotivoNulo | None]:
    """`fim - inicio` em dias corridos (negativo se o fim vier antes: a formula nao corta); nulo se faltar
    ou for invalido um dos termos. O primeiro termo com problema da o motivo."""
    data_inicio, motivo = _data(termos, inicio)
    if motivo is not None:
        return None, motivo
    data_fim, motivo = _data(termos, fim)
    if motivo is not None or data_inicio is None or data_fim is None:
        return None, motivo or MotivoNulo.TERMO_AUSENTE
    return (data_fim - data_inicio).days, None


#: Valores de `FatosDoCaso.regime_guia` e o termo de inicio que cada um escolhe na formula publicada.
REGIME_INTERNACAO: Final[str] = "internacao"
REGIME_AMBULATORIAL: Final[str] = "ambulatorial"
_INICIO_POR_REGIME: Final[Mapping[str, str]] = MappingProxyType(
    {REGIME_INTERNACAO: "dataFinalFaturamento", REGIME_AMBULATORIAL: "dataExecucao"}
)


def _dias_ate_apresentacao(termos: Mapping[str, object]) -> tuple[int | None, MotivoNulo | None]:
    """`dataRecebimentoLote - dataFinalFaturamento` (internacao, alta) ou `dataRecebimentoLote -
    dataExecucao` (ambulatorial), em dias corridos, nulo se faltar um dos termos — dicionario K1 1.2.0,
    entrada `diasAteApresentacao` (rco `docs/dicionario/regras-gerais.yaml`, f77f96ac). O regime vem
    do tipo da guia (`regimeGuia`); sem ele, nulo: nunca se escolhe a data pela que estiver presente."""
    inicio = _INICIO_POR_REGIME.get(str(termos.get("regimeGuia")))
    if inicio is None:
        return None, MotivoNulo.REGIME_DA_GUIA_AUSENTE
    return _dias_entre(termos, inicio, "dataRecebimentoLote")


def _dias_ate_recurso(termos: Mapping[str, object]) -> tuple[int | None, MotivoNulo | None]:
    """`dataRecebimentoLoteRecurso - dataDemonstrativoAnalise`, em dias corridos, nulo se faltar um dos
    termos — dicionario K1 1.2.0, entrada `diasAteRecurso`."""
    return _dias_entre(termos, "dataDemonstrativoAnalise", "dataRecebimentoLoteRecurso")


FORMULAS: Final[Mapping[str, _FormulaImplementada]] = MappingProxyType(
    {
        "diasAlemDoPrazoContratual": _FormulaImplementada(
            texto=(
                "diasAteApresentacao - prazoApresentacaoContratualDias "
                "(nulo se faltar um dos termos ou se o prazo do contrato for negativo)"
            ),
            termos=(("diasAteApresentacao", "derivado"), ("prazoApresentacaoContratualDias", "cadastro")),
            calcular=_dias_alem_do_prazo_contratual,
            ajuste=_ajustar_dias_alem_do_prazo,
        ),
        "diasAteApresentacao": _FormulaImplementada(
            texto=(
                "dataRecebimentoLote - dataFinalFaturamento (internação, alta) ou "
                "dataRecebimentoLote - dataExecucao (ambulatorial), em dias corridos "
                "(nulo se faltar um dos termos)"
            ),
            termos=(
                ("dataFinalFaturamento", "tiss"),
                ("dataExecucao", "tiss"),
                ("dataRecebimentoLote", "fora_tiss"),
            ),
            calcular=_dias_ate_apresentacao,
            desde=(1, 2, 0),
            condicoes=("regimeGuia",),
        ),
        "diasAteRecurso": _FormulaImplementada(
            texto=(
                "dataRecebimentoLoteRecurso - dataDemonstrativoAnalise, em dias corridos "
                "(nulo se faltar um dos termos)"
            ),
            termos=(("dataDemonstrativoAnalise", "fora_tiss"), ("dataRecebimentoLoteRecurso", "fora_tiss")),
            calcular=_dias_ate_recurso,
            desde=(1, 2, 0),
        ),
    }
)
#: Formulas que o dicionario 1.2.0 publica e o Maezo ainda NAO calcula: entram nulas. O valor diz por que
#: (lacuna L-prog-rco-04 do rco, `governanca/decisoes/parciais/programa/lacunas-rco.md` em f77f96ac, ou
#: termo sem produtor no Maezo). Implementar uma exige a lacuna fechada no rco e o produtor do termo aqui.
FORMULAS_PENDENTES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "cidNaDlpDeclarada": "L-prog-rco-04 (5); sem produtor das doencas declaradas com CPT no cadastro",
        "diasAposCirurgia": "L-prog-rco-04 (1): qual cirurgia vale com mais de uma do mesmo profissional",
        "familiaDiariaTaxa": "L-prog-rco-04 (10); parametro regras/diarias-taxas/dominio.yaml fora do K1",
        "internacaoObstetricaComAcompanhante": "L-prog-rco-04 (6): tipo 3 sem acompanhante no anexo",
        "mesmoProfissionalCirurgia": "L-prog-rco-04 (2): grupo 3 da TUSS 22 sem coluna na tabela",
        "procedimentoAlcancadoCpt": "L-prog-rco-04 (2) e (3): grupo 3 da TUSS 22 e leito de alta tecnologia",
        "registroAnvisaItem": "L-prog-rco-04 (7): elemento ausente (false) x nao extraido (nulo)",
        "reinternacaoMesmaCausa": "L-prog-rco-04 (8); sem produtor do historico de internacoes no Maezo",
        "visitaNaDataDaAlta": "L-prog-rco-04 (4): elemento do XSD que marca a guia encerrada por alta",
    }
)
#: Chave, no mapa de cadastro, de termo de formula que nao e entrada do dicionario.
CAMPOS_TERMO_CADASTRO: Final[Mapping[str, str]] = MappingProxyType(
    {"prazoApresentacaoContratualDias": "prazo_apresentacao_contratual_dias"}
)
#: Chave, em `FatosDoCaso.tiss`, dos termos de origem `tiss` (elementos da guia, nao entradas do dicionario).
CAMPOS_TERMO_TISS: Final[Mapping[str, str]] = MappingProxyType(
    {"dataFinalFaturamento": "data_final_faturamento", "dataExecucao": "data_execucao"}
)
#: Chave, em `FatosDoCaso.fora_tiss`, dos termos de origem `fora_tiss` (protocolos e demonstrativos).
CAMPOS_TERMO_FORA_TISS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "dataRecebimentoLote": "data_recebimento_lote",
        "dataDemonstrativoAnalise": "data_demonstrativo_analise",
        "dataRecebimentoLoteRecurso": "data_recebimento_lote_recurso",
    }
)


def _condicao(nome: str, fatos: FatosDoCaso) -> object:
    """Os fatos que o texto de uma formula nomeia fora dos termos (`_FormulaImplementada.condicoes`)."""
    if nome == "regimeGuia":
        return fatos.regime_guia
    raise MontadorEntradasError(f"condicao de formula {nome!r} sem campo em FatosDoCaso")


def _termo(nome: str, origem: str, fatos: FatosDoCaso) -> object:
    """O valor bruto de um termo nao derivado, do mapa da SUA origem; sem chave = MontadorEntradasError."""
    if nome in fatos.termos_sem_produtor:
        return SEM_PRODUTOR
    fontes: dict[str, tuple[Mapping[str, str], Mapping[str, object]]] = {
        "cadastro": (CAMPOS_TERMO_CADASTRO, fatos.cadastro),
        "tiss": (CAMPOS_TERMO_TISS, fatos.tiss),
        "fora_tiss": (CAMPOS_TERMO_FORA_TISS, fatos.fora_tiss),
    }
    campos, mapa = fontes.get(origem, ({}, {}))
    chave = campos.get(nome)
    if chave is None:
        raise MontadorEntradasError(f"termo {nome!r} de origem {origem!r} sem chave no montador")
    return mapa.get(chave)


# --- mapeamento por dominio ------------------------------------------------------------------------
def _guia() -> MapeamentoEntrada:
    return MapeamentoEntrada("tiss", Fonte.GUIA)


_CADASTRO: Final[MapeamentoEntrada] = MapeamentoEntrada("cadastro", Fonte.CADASTRO)
_FORA_TISS: Final[MapeamentoEntrada] = MapeamentoEntrada("fora_tiss", Fonte.FORA_TISS)


def _k3(atributo: str) -> MapeamentoEntrada:
    return MapeamentoEntrada("cadastro", Fonte.K3_V116, atributo)


_SEM_FORMULA: Final[MapeamentoEntrada] = MapeamentoEntrada("derivado", Fonte.NULO_POR_DESENHO)
_COM_FORMULA: Final[MapeamentoEntrada] = MapeamentoEntrada("derivado", Fonte.FORMULA)
_FORMULA_PENDENTE: Final[MapeamentoEntrada] = MapeamentoEntrada("derivado", Fonte.FORMULA_PENDENTE)

#: Entradas comuns aos dominios (mesma entrada do dicionario em toda tabela).
_COMUNS: Final[dict[str, MapeamentoEntrada]] = {
    # TISS dataSolicitacao (pedido) / dataExecucao (conta) da guia, AAAA-MM-DD: contrato de start
    # `data_atendimento` (SP-OP-AUTH-001, MZO-2b). NAO e `data_solicitacao_iso` (instante do Maezo).
    "dataAtendimento": _guia(),
    # So do campo explicito da guia (regra 6 do modulo), nunca da variavel de start.
    "caraterAtendimento": MapeamentoEntrada("tiss", Fonte.GUIA_EXPLICITA, "carater_atendimento_guia"),
}

MAPEAMENTOS: Final[Mapping[str, Mapping[str, MapeamentoEntrada]]] = MappingProxyType(
    {
        "regras-gerais": MappingProxyType(
            {
                **_COMUNS,
                "codigoProcedimento": _guia(),
                "tipoTransacao": _guia(),
                # K3 v1.16 (nulo enquanto o pin AMH for anterior a 1.16)
                "grupoCarencia": _k3("grupo"),
                "carenciaContratualDias": _k3("dias"),
                "saldoCarenciaDias": _k3("saldo_dias"),
                "situacaoCadastral": _k3("situacao_cadastral"),
                "cptVigente": _k3("tem_cpt_vigente"),
                # cadastro sem rota TINA hoje
                "autorizacaoExtemporanea": _CADASTRO,
                "classeMaterial": _CADASTRO,
                "diasDesdeFimImpedimento": _CADASTRO,
                "evidenciaOperadora": _CADASTRO,
                "motivoExtemporaneidade": _CADASTRO,
                "pedidosExtemporaneos12mPrestador": _CADASTRO,
                # derivadas
                "diasAlemDoPrazoContratual": _COM_FORMULA,
                "diasAteApresentacao": _COM_FORMULA,
                "diasAteRecurso": _COM_FORMULA,
                "cidNaDlpDeclarada": _FORMULA_PENDENTE,
                "diasAposCirurgia": _FORMULA_PENDENTE,
                "mesmoProfissionalCirurgia": _FORMULA_PENDENTE,
                "procedimentoAlcancadoCpt": _FORMULA_PENDENTE,
                "visitaNaDataDaAlta": _FORMULA_PENDENTE,
            }
        ),
        "diarias-taxas": MappingProxyType(
            {
                **_COMUNS,
                "deficienciaCadastro": _CADASTRO,
                "idadeBeneficiario": _CADASTRO,
                "avisoItensNaoIncluidos": _FORA_TISS,
                "criterioRevisaoAtendido": _FORA_TISS,
                "internacaoProgramadaOuComplicacao": _FORA_TISS,
                "registroAnvisaVigente": _FORA_TISS,
                "familiaDiariaTaxa": _FORMULA_PENDENTE,
                "internacaoObstetricaComAcompanhante": _FORMULA_PENDENTE,
                "registroAnvisaItem": _FORMULA_PENDENTE,
                "reinternacaoMesmaCausa": _FORMULA_PENDENTE,
            }
        ),
    }
)


# --- validacao de valor contra o dicionario --------------------------------------------------------
def _inteiro(valor: object) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar(entrada: EntradaK1, valor: object) -> tuple[object, MotivoNulo | None]:
    """O valor, se tem o tipo e esta no dominio do dicionario; senao (None, motivo). Nunca converte."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None, MotivoNulo.AUSENTE
    tipo = entrada.tipo
    if tipo == "string" and not isinstance(valor, str):
        return None, MotivoNulo.TIPO_INVALIDO
    if tipo == "boolean" and not isinstance(valor, bool):
        return None, MotivoNulo.TIPO_INVALIDO
    if tipo == "integer" and not _inteiro(valor):
        return None, MotivoNulo.TIPO_INVALIDO
    if tipo == "double" and (
        isinstance(valor, bool) or not isinstance(valor, int | float) or not math.isfinite(valor)
    ):
        return None, MotivoNulo.TIPO_INVALIDO
    dominio = entrada.dominio
    if not dominio.verificavel:
        return valor, None
    if dominio.tipo == "lista":
        texto = str(valor).lower() if isinstance(valor, bool) else str(valor)
        return (valor, None) if texto in dominio.valores else (None, MotivoNulo.FORA_DO_DOMINIO)
    if dominio.tipo == "faixa":
        if isinstance(valor, bool) or not isinstance(valor, int | float):
            return None, MotivoNulo.FORA_DO_DOMINIO
        if (dominio.minimo is not None and valor < dominio.minimo) or (
            dominio.maximo is not None and valor > dominio.maximo
        ):
            return None, MotivoNulo.FORA_DO_DOMINIO
        return valor, None
    if dominio.tipo == "padrao" and dominio.padrao is not None:
        ok = isinstance(valor, str) and dominio.padrao.fullmatch(valor) is not None
        return (valor, None) if ok else (None, MotivoNulo.FORA_DO_DOMINIO)
    return valor, None


# --- conferencia mapeamento x dicionario x manifesto -----------------------------------------------
def conferir_mapeamento(
    dicionario: DicionarioEntradasK1, dominio: str, entradas: tuple[str, ...]
) -> list[str]:
    """Divergencias entre o mapeamento do dominio, o dicionario e as entradas que o manifesto declara."""
    mapa = MAPEAMENTOS.get(dominio)
    if mapa is None:
        return [f"dominio {dominio!r} sem mapeamento no montador"]
    problemas: list[str] = []
    for nome in entradas:
        entrada = dicionario.entradas.get(nome)
        mapeamento = mapa.get(nome)
        if entrada is None:
            problemas.append(f"{nome}: entrada da decisao ausente do dicionario {dicionario.versao}")
            continue
        if mapeamento is None:
            problemas.append(f"{nome}: entrada sem mapeamento no dominio {dominio!r}")
            continue
        if mapeamento.origem != entrada.origem:
            problemas.append(
                f"{nome}: origem {entrada.origem!r} no dicionario, {mapeamento.origem!r} no montador"
            )
        if mapeamento.fonte is Fonte.FORMULA:
            impl = FORMULAS.get(nome)
            publicada = entrada.formula
            if impl is None:
                problemas.append(f"{nome}: mapeada com formula, mas sem formula implementada")
            elif publicada is None:
                if versao_dicionario(dicionario) >= impl.desde:
                    problemas.append(
                        f"{nome}: formula implementada nao publicada no dicionario {dicionario.versao}"
                    )
            elif (
                publicada.formula != impl.texto
                or tuple((t.nome, t.origem) for t in publicada.termos) != impl.termos
            ):
                problemas.append(f"{nome}: formula do dicionario diverge da implementada; reveja o montador")
        elif mapeamento.fonte is Fonte.FORMULA_PENDENTE:
            if nome not in FORMULAS_PENDENTES or nome in FORMULAS:
                problemas.append(f"{nome}: formula pendente tem de estar so em FORMULAS_PENDENTES")
        elif mapeamento.fonte is Fonte.NULO_POR_DESENHO and entrada.formula is not None:
            problemas.append(f"{nome}: o dicionario publicou formula; implemente-a em vez de mandar nulo")
    return problemas


def versao_dicionario(dicionario: DicionarioEntradasK1) -> tuple[int, int, int]:
    """A versao SemVer do dicionario como tupla (o leitor ja garantiu o formato)."""
    maior, menor, patch = (int(p) for p in dicionario.versao.split("."))
    return maior, menor, patch


def _fonte_efetiva(mapeamento: MapeamentoEntrada, entrada: EntradaK1) -> Fonte:
    """A fonte registrada: derivada sem formula no dicionario (anterior a ela) e nula por desenho."""
    if mapeamento.fonte in (Fonte.FORMULA, Fonte.FORMULA_PENDENTE) and entrada.formula is None:
        return Fonte.NULO_POR_DESENHO
    return mapeamento.fonte


# --- montagem ---------------------------------------------------------------------------------------
def _k3_valor(
    entrada: EntradaK1, atributo: str, fatos: FatosDoCaso, data: object
) -> tuple[object, MotivoNulo | None]:
    if fatos.versao_k3_pinada < VERSAO_K3_ENTRADAS:
        return None, MotivoNulo.K3_NAO_PINADO
    k3 = fatos.k3
    if k3 is None:
        return None, MotivoNulo.AUSENTE
    if data is None or k3.data_atendimento != data:
        return None, MotivoNulo.K3_FORA_DA_DATA
    if atributo in _K3_CARENCIA and not lacuna_saldo_fechada():
        return None, MotivoNulo.K3_LACUNA_SALDO_ABERTA
    return _validar(entrada, getattr(k3, atributo))


def _montar_uma(
    nome: str,
    dicionario: DicionarioEntradasK1,
    mapa: Mapping[str, MapeamentoEntrada],
    fatos: FatosDoCaso,
    data: object,
    derivacoes: dict[str, Mapping[str, object]] | None = None,
) -> tuple[object, MotivoNulo | None]:
    entrada = dicionario.entradas[nome]
    mapeamento = mapa[nome]
    fonte = mapeamento.fonte
    if _fonte_efetiva(mapeamento, entrada) is Fonte.NULO_POR_DESENHO:
        return None, MotivoNulo.DERIVADA_SEM_FORMULA
    if fonte is Fonte.FORMULA_PENDENTE:
        return None, MotivoNulo.FORMULA_PENDENTE
    if fonte is Fonte.FORMULA:
        impl = FORMULAS[nome]
        termos: dict[str, object] = {}
        for termo, origem in impl.termos:
            if origem == "derivado":
                montavel = termo in mapa and termo in dicionario.entradas
                termos[termo] = _montar_uma(termo, dicionario, mapa, fatos, data)[0] if montavel else None
            else:
                termos[termo] = _termo(termo, origem, fatos)
        for condicao in impl.condicoes:
            termos[condicao] = _condicao(condicao, fatos)
        calculado, motivo = impl.calcular(termos)
        valor, motivo = (None, motivo) if motivo is not None else _validar(entrada, calculado)
        if impl.ajuste is None:
            return valor, motivo
        # MZO-TOL: o valor AJUSTADO e o que sai daqui. Uma formula futura que tenha
        # `diasAlemDoPrazoContratual` como termo derivado (recursao acima) receberia o valor tolerado, nao
        # o bruto: reveja entao se a tolerancia lhe cabe e o teste que guarda `prazoComprovacaoUsoVencido`
        # (`test_ajuste_so_em_dias_alem_do_prazo_contratual`), que segue o prazo do contrato.
        entregue, registro = impl.ajuste(calculado, valor)
        if derivacoes is not None:
            derivacoes[nome] = registro
        if entregue is None:
            return None, motivo  # nulo segue nulo, com o motivo de origem
        return _validar(entrada, entregue)
    if fonte is Fonte.K3_V116:
        if mapeamento.campo is None:  # todo mapeamento K3 nomeia o atributo; sem ele, nao ha fonte
            raise MontadorEntradasError(f"{nome}: mapeamento K3 sem atributo")
        return _k3_valor(entrada, mapeamento.campo, fatos, data)
    if fonte is Fonte.GUIA_EXPLICITA:
        if nome != "caraterAtendimento" or mapeamento.campo != "carater_atendimento_guia":
            raise MontadorEntradasError(f"{nome}: campo explicito da guia so existe para o carater")
        bruto: object = fatos.carater_atendimento_guia
        if isinstance(bruto, str):
            bruto = carater_tuss23().get(bruto, bruto)  # exato, como o adaptador do rco
        return _validar(entrada, bruto)
    origem_fatos = {Fonte.GUIA: fatos.guia, Fonte.CADASTRO: fatos.cadastro, Fonte.FORA_TISS: fatos.fora_tiss}
    return _validar(entrada, origem_fatos[fonte].get(entrada.variavel_maezo))


def montar_entradas(
    decisao: DecisaoK1, dicionario: DicionarioEntradasK1, fatos: FatosDoCaso
) -> EntradasMontadas:
    """Monta as entradas de `decisao`. Levanta `DominioNaoSuportadoError` (caso para pessoa) ou
    `MontadorEntradasError` (mapeamento divergente do dicionario/manifesto: incidente, sem retry)."""
    if not decisao.entradas:
        raise MontadorEntradasError(f"{decisao.id}: decisao sem entradas declaradas no manifesto")
    if decisao.dominio not in MAPEAMENTOS:
        raise DominioNaoSuportadoError(
            f"{decisao.id}: dominio {decisao.dominio!r} sem mapeamento no montador"
        )
    problemas = conferir_mapeamento(dicionario, decisao.dominio, decisao.entradas)
    if problemas:
        raise MontadorEntradasError(f"{decisao.id}: " + "; ".join(problemas))
    mapa = MAPEAMENTOS[decisao.dominio]
    entrada_data = dicionario.entradas.get("dataAtendimento")
    data = _validar(entrada_data, fatos.guia.get(entrada_data.variavel_maezo))[0] if entrada_data else None
    variaveis: dict[str, object] = {}
    fontes: dict[str, str] = {}
    nulos: dict[str, str] = {}
    derivacoes: dict[str, Mapping[str, object]] = {}
    for nome in decisao.entradas:
        valor, motivo = _montar_uma(nome, dicionario, mapa, fatos, data, derivacoes)
        variaveis[nome] = valor
        fontes[nome] = _fonte_efetiva(mapa[nome], dicionario.entradas[nome]).value
        if valor is None:
            nulos[nome] = (motivo or MotivoNulo.AUSENTE).value
    return EntradasMontadas(
        decisao=decisao.id,
        versao_dicionario=dicionario.versao,
        variaveis=MappingProxyType(variaveis),
        fontes=MappingProxyType(fontes),
        nulos=MappingProxyType(nulos),
        derivacoes=MappingProxyType(derivacoes),
    )

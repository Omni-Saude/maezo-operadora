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
2. Entrada derivada SEM formula estruturada no dicionario (as 171 anteriores ao gate PROG-RCO-15) entra
   nula: o montador nao adivinha formula a partir da descricao. Derivada com formula e calculada
   EXATAMENTE pela formula publicada; se o texto da formula ou os termos mudarem no dicionario, o
   montador recusa (`MontadorEntradasError`) ate ser revisto.
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
   so dos mapas que o worker que os resolveu entrega. A guia vem das variaveis do contrato de start.

ESCOPO DESTA ENTREGA: framework + os dominios `regras-gerais` (transversal: toda guia passa por
elegibilidade, carencia e CPT) e `diarias-taxas` (maior volume de autorizacao no agregado real do
backtest 2026-10 do rco: 38,9% das autorizacoes solicitadas; `dados-agregados/sinistro-glosa-tuss.csv`
x `historico/diagnostico/priorizacao/codigos-dominio.csv`). Decisao de outro dominio levanta
`DominioNaoSuportadoError`: quem chama encaminha o caso para pessoa (plano, risco "montador maior que o
previsto": "os demais ficam em fila humana").
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from maezo.platform.rco_entradas import DicionarioEntradasK1, EntradaK1
from maezo.platform.rco_manifesto import DecisaoK1

#: Primeira versao do contrato AMH (manifest aditivo) que traz o K3 das entradas de decisao.
VERSAO_K3_ENTRADAS: Final[tuple[int, int]] = (1, 16)


class MontadorEntradasError(ValueError):
    """O montador nao sabe montar a decisao com seguranca (dicionario, manifesto ou mapeamento divergem)."""


class DominioNaoSuportadoError(MontadorEntradasError):
    """Dominio do rco ainda sem mapeamento no montador: o caso vai para pessoa."""


class Fonte(StrEnum):
    GUIA = "guia"  # variavel do contrato de start, lida da guia TISS
    CADASTRO = "cadastro"  # fato de cadastro entregue pelo worker que o resolveu
    FORA_TISS = "fora_tiss"  # fato da solicitacao fora da TISS
    K3_V116 = "k3_v1_16"  # rota TINA do K3 (manifest AMH v1.16)
    FORMULA = "formula"  # `formula.formula` publicada no dicionario
    NULO_POR_DESENHO = "nulo_por_desenho"  # derivada sem formula estruturada


class MotivoNulo(StrEnum):
    AUSENTE = "ausente_na_fonte"
    TIPO_INVALIDO = "tipo_invalido"
    FORA_DO_DOMINIO = "fora_do_dominio"
    DERIVADA_SEM_FORMULA = "derivada_sem_formula"
    TERMO_AUSENTE = "termo_da_formula_ausente"
    PRAZO_CONTRATUAL_NEGATIVO = "prazo_contratual_negativo"
    K3_NAO_PINADO = "k3_v1_16_nao_pinado"
    K3_FORA_DA_DATA = "k3_fora_da_data_do_atendimento"


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
    versao_k3_pinada: tuple[int, int]  # `AmhContractPin.versao_manifesto_pinada`
    cadastro: Mapping[str, object] = field(default_factory=dict)
    fora_tiss: Mapping[str, object] = field(default_factory=dict)
    k3: FatosK3V116 | None = None


@dataclass(frozen=True, slots=True)
class EntradasMontadas:
    decisao: str
    versao_dicionario: str
    variaveis: Mapping[str, object]  # {nomeDMN: valor | None}, toda entrada da decisao presente
    fontes: Mapping[str, str]  # {nomeDMN: Fonte}
    nulos: Mapping[str, str]  # {nomeDMN: MotivoNulo} das entradas nulas


@dataclass(frozen=True, slots=True)
class MapeamentoEntrada:
    """De onde o montador tira uma entrada. `origem` repete a do dicionario e e conferida."""

    origem: str
    fonte: Fonte
    #: so K3: atributo de `FatosK3V116`. Guia, cadastro e fora da TISS usam o nome Maezo que o PROPRIO
    #: dicionario declara (`nome_maezo`, senao `nome_snake`): o montador nao inventa nome de variavel.
    campo: str | None = None
    conversao: Mapping[str, str] | None = None  # de-para declarado pelo rco (so texto -> texto)


# --- conversao declarada pelo rco ----------------------------------------------------------------
#: `caraterAtendimento`: o dicionario do rco declara `nome_maezo = carater_atendimento` e o de-para dos
#: valores do Maezo para a TUSS tabela 23 (`docs/dicionario-dados.yaml`, campo `caraterAtendimento`,
#: `valores_maezo`: eletivo=1, urgencia=2, emergencia=2; "os codigos 1 e 2 tambem sao aceitos sem
#: conversao"). LACUNA: o `entradas.json` publicado no K1 nao carrega `valores_maezo`; pedido ao rco.
CARATER_TUSS23: Final[Mapping[str, str]] = MappingProxyType(
    {"eletivo": "1", "urgencia": "2", "emergencia": "2", "1": "1", "2": "2"}
)


# --- formulas publicadas (derivacao.formula) -------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _FormulaImplementada:
    texto: str  # o texto EXATO de `formula.formula` no dicionario
    termos: tuple[tuple[str, str], ...]  # (nome, origem) de cada termo, na ordem do dicionario
    calcular: Callable[[Mapping[str, object]], tuple[int | None, MotivoNulo | None]]


def _dias_alem_do_prazo_contratual(termos: Mapping[str, object]) -> tuple[int | None, MotivoNulo | None]:
    """`diasAteApresentacao - prazoApresentacaoContratualDias` (nulo se faltar um dos termos ou se o
    prazo do contrato for negativo) — dicionario K1 1.1.0, entrada `diasAlemDoPrazoContratual`."""
    dias = termos.get("diasAteApresentacao")
    prazo = termos.get("prazoApresentacaoContratualDias")
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


FORMULAS: Final[Mapping[str, _FormulaImplementada]] = MappingProxyType(
    {
        "diasAlemDoPrazoContratual": _FormulaImplementada(
            texto=(
                "diasAteApresentacao - prazoApresentacaoContratualDias "
                "(nulo se faltar um dos termos ou se o prazo do contrato for negativo)"
            ),
            termos=(("diasAteApresentacao", "derivado"), ("prazoApresentacaoContratualDias", "cadastro")),
            calcular=_dias_alem_do_prazo_contratual,
        ),
    }
)
#: Chave, no mapa de cadastro, de termo de formula que nao e entrada do dicionario.
CAMPOS_TERMO_CADASTRO: Final[Mapping[str, str]] = MappingProxyType(
    {"prazoApresentacaoContratualDias": "prazo_apresentacao_contratual_dias"}
)


# --- mapeamento por dominio ------------------------------------------------------------------------
def _guia(conversao: Mapping[str, str] | None = None) -> MapeamentoEntrada:
    return MapeamentoEntrada("tiss", Fonte.GUIA, conversao=conversao)


_CADASTRO: Final[MapeamentoEntrada] = MapeamentoEntrada("cadastro", Fonte.CADASTRO)
_FORA_TISS: Final[MapeamentoEntrada] = MapeamentoEntrada("fora_tiss", Fonte.FORA_TISS)


def _k3(atributo: str) -> MapeamentoEntrada:
    return MapeamentoEntrada("cadastro", Fonte.K3_V116, atributo)


_SEM_FORMULA: Final[MapeamentoEntrada] = MapeamentoEntrada("derivado", Fonte.NULO_POR_DESENHO)
_COM_FORMULA: Final[MapeamentoEntrada] = MapeamentoEntrada("derivado", Fonte.FORMULA)

#: Entradas comuns aos dominios (mesma entrada do dicionario em toda tabela).
_COMUNS: Final[dict[str, MapeamentoEntrada]] = {
    # TISS dataSolicitacao (pedido) / dataExecucao (conta) da guia, AAAA-MM-DD: contrato de start
    # `data_atendimento` (SP-OP-AUTH-001, MZO-2b). NAO e `data_solicitacao_iso` (instante do Maezo).
    "dataAtendimento": _guia(),
    "caraterAtendimento": _guia(CARATER_TUSS23),
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
                "cidNaDlpDeclarada": _SEM_FORMULA,
                "diasAposCirurgia": _SEM_FORMULA,
                "diasAteApresentacao": _SEM_FORMULA,
                "diasAteRecurso": _SEM_FORMULA,
                "mesmoProfissionalCirurgia": _SEM_FORMULA,
                "procedimentoAlcancadoCpt": _SEM_FORMULA,
                "visitaNaDataDaAlta": _SEM_FORMULA,
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
                "familiaDiariaTaxa": _SEM_FORMULA,
                "internacaoObstetricaComAcompanhante": _SEM_FORMULA,
                "registroAnvisaItem": _SEM_FORMULA,
                "reinternacaoMesmaCausa": _SEM_FORMULA,
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
            if impl is None or publicada is None:
                problemas.append(f"{nome}: formula publicada e implementada tem de existir as duas")
            elif (
                publicada.formula != impl.texto
                or tuple((t.nome, t.origem) for t in publicada.termos) != impl.termos
            ):
                problemas.append(f"{nome}: formula do dicionario diverge da implementada; reveja o montador")
        elif mapeamento.fonte is Fonte.NULO_POR_DESENHO and entrada.formula is not None:
            problemas.append(f"{nome}: o dicionario publicou formula; implemente-a em vez de mandar nulo")
    return problemas


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
    return _validar(entrada, getattr(k3, atributo))


def _montar_uma(
    nome: str,
    dicionario: DicionarioEntradasK1,
    mapa: Mapping[str, MapeamentoEntrada],
    fatos: FatosDoCaso,
    data: object,
) -> tuple[object, MotivoNulo | None]:
    entrada = dicionario.entradas[nome]
    mapeamento = mapa[nome]
    fonte = mapeamento.fonte
    if fonte is Fonte.NULO_POR_DESENHO:
        return None, MotivoNulo.DERIVADA_SEM_FORMULA
    if fonte is Fonte.FORMULA:
        impl = FORMULAS[nome]
        termos: dict[str, object] = {}
        for termo, origem in impl.termos:
            if origem == "derivado":
                montavel = termo in mapa and termo in dicionario.entradas
                termos[termo] = _montar_uma(termo, dicionario, mapa, fatos, data)[0] if montavel else None
            else:
                termos[termo] = fatos.cadastro.get(CAMPOS_TERMO_CADASTRO[termo])
        calculado, motivo = impl.calcular(termos)
        if motivo is not None:
            return None, motivo
        return _validar(entrada, calculado)
    if fonte is Fonte.K3_V116:
        if mapeamento.campo is None:  # todo mapeamento K3 nomeia o atributo; sem ele, nao ha fonte
            raise MontadorEntradasError(f"{nome}: mapeamento K3 sem atributo")
        return _k3_valor(entrada, mapeamento.campo, fatos, data)
    origem_fatos = {Fonte.GUIA: fatos.guia, Fonte.CADASTRO: fatos.cadastro, Fonte.FORA_TISS: fatos.fora_tiss}
    bruto = origem_fatos[fonte].get(entrada.variavel_maezo)
    if mapeamento.conversao is not None and isinstance(bruto, str):
        bruto = mapeamento.conversao.get(bruto.strip().lower(), bruto)
    return _validar(entrada, bruto)


def montar_entradas(
    decisao: DecisaoK1, dicionario: DicionarioEntradasK1, fatos: FatosDoCaso
) -> EntradasMontadas:
    """Monta as entradas de `decisao`. Levanta `DominioNaoSuportadoError` (caso para pessoa) ou
    `MontadorEntradasError` (mapeamento divergente do dicionario/manifesto: incidente, sem retry)."""
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
    for nome in decisao.entradas:
        valor, motivo = _montar_uma(nome, dicionario, mapa, fatos, data)
        variaveis[nome] = valor
        fontes[nome] = mapa[nome].fonte.value
        if valor is None:
            nulos[nome] = (motivo or MotivoNulo.AUSENTE).value
    return EntradasMontadas(
        decisao=decisao.id,
        versao_dicionario=dicionario.versao,
        variaveis=MappingProxyType(variaveis),
        fontes=MappingProxyType(fontes),
        nulos=MappingProxyType(nulos),
    )

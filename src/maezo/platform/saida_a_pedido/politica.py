"""Leitores das politicas de saida a pedido (E-SF-09): janela de arrependimento e pedido de cancelamento.

Fail-closed: qualquer violacao levanta `PoliticaInvalida(code=...)`. O prazo e' DADO (YAML), nunca
Python; trocar a variante da janela e' ato de dado e exige bloco de ratificacao juridica completo.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Final, Literal, cast

import yaml

from maezo.agents import resolve_spec_dir

NOME_JANELA: Final[str] = "janela-arrependimento.yaml"
NOME_CANCELAMENTO: Final[str] = "pedido-cancelamento.yaml"

_PENDENTE: Final[str] = "PENDENTE"
_DURACAO: Final[re.Pattern[str]] = re.compile(r"^P([1-9][0-9]*)D$")

_PROMESSA_ANTES_DA_Q3: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b\d+\s*dias?\b|\bsete\b|\b1\s*clique\b|\bum\s+clique\b"
)
_PRAZO_ANTES_DA_REG27: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bdias?\s+úteis\b|\bd\.u\.|\bP\d+D\b|\bdez\s+dias\b"
)

VarianteJanela = Literal["A", "B", "UNRATIFIED_UNION", "D"]
Combinacao = Literal["unica", "assinatura_ate_ultimo_termo_final", "independentes"]
NomeAncora = Literal["signed_at", "vigencia_start"]
FonteExigida = Literal["acceptance_receipt", "vigencia_receipt"]

_VARIANTES: Final[tuple[str, ...]] = ("A", "B", "UNRATIFIED_UNION", "D")
_COMBINACOES: Final[frozenset[str]] = frozenset(
    {"unica", "assinatura_ate_ultimo_termo_final", "independentes"}
)
_ANCORAS: Final[tuple[str, ...]] = ("signed_at", "vigencia_start")
_FONTES: Final[frozenset[str]] = frozenset({"acceptance_receipt", "vigencia_receipt"})
_STATUS: Final[frozenset[str]] = frozenset({"NAO_RATIFICADO", "RATIFICADO"})
_JURIDICO_CHAVES: Final[frozenset[str]] = frozenset({"aprovado", "aprovador", "data", "evidencia_ref"})
_INCISOS: Final[tuple[str, ...]] = ("I", "II", "III", "IV", "V", "VI")
_TOQUES: Final[int] = 2


class PoliticaInvalida(ValueError):  # noqa: N818 - nome fixado pelo desenho
    """Politica ausente, malformada ou que abre um portao sem ratificacao."""

    code: str

    def __init__(self, code: str, detalhe: str = "") -> None:
        super().__init__(f"{code}: {detalhe}" if detalhe else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Ancoragem:
    nome: NomeAncora
    fonte_exigida: FonteExigida
    dispositivo_ref: str
    duracao: timedelta  # derivada de duracao_iso; o numero vem do YAML


@dataclass(frozen=True, slots=True)
class PoliticaJanela:
    variante_ativa: VarianteJanela
    ratificada: bool
    display_numeric_promise: bool
    ancoras_da_variante: tuple[Ancoragem, ...]
    combinacao: Combinacao
    declaracao_texto_ratificado: bool
    declaracao_texto_digest: str | None
    textos: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PoliticaPedidoCancelamento:
    versao: int
    toques_ate_protocolo: int
    incisos_exigidos: tuple[str, ...]
    destaque_obrigatorio: tuple[str, ...]
    texto_ratificado: bool
    prazo_exibir: bool
    linha_alternativa_exibir: bool
    reg27_ratificado: bool
    rtj3a_ratificado: bool
    textos: Mapping[str, str]


def caminho_padrao(nome_arquivo: str) -> Path:
    """`<spec>/policies/saida-a-pedido/<nome>` pelo unico mecanismo T0.3 (`resolve_spec_dir`)."""
    return resolve_spec_dir() / "policies" / "saida-a-pedido" / nome_arquivo


def lint_copy(
    texto: str, *, promessa_ratificada: bool, prazo_cancelamento_ratificado: bool
) -> tuple[str, ...]:
    """Codigos de violacao de copy: promessa numerica antes da Q3, prazo regulatorio antes da REG-27."""
    codigos: list[str] = []
    if not promessa_ratificada and _PROMESSA_ANTES_DA_Q3.search(texto):
        codigos.append("WITHDRAWAL_PROMISE_BEFORE_Q3")
    if not prazo_cancelamento_ratificado and _PRAZO_ANTES_DA_REG27.search(texto):
        codigos.append("REGULATORY_DEADLINE_BEFORE_REG27")
    return tuple(codigos)


# ---------------------------------------------------------------------------------------------------
# Helpers de leitura
# ---------------------------------------------------------------------------------------------------


def _ler_yaml(path: Path | None, nome: str) -> dict[str, Any]:
    if path is None:
        try:
            path = caminho_padrao(nome)
        except Exception as exc:  # SpecDirOverrideRefusedError e qualquer falha de resolucao: fail-closed
            raise PoliticaInvalida("POLICY_MISSING", type(exc).__name__) from exc
    try:
        bruto = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PoliticaInvalida("POLICY_MISSING", str(path)) from exc
    try:
        dados = yaml.safe_load(bruto)
    except yaml.YAMLError as exc:
        raise PoliticaInvalida("POLICY_MALFORMED", "yaml invalido") from exc
    if not isinstance(dados, dict):
        raise PoliticaInvalida("POLICY_MALFORMED", "raiz nao e' mapping")
    return cast("dict[str, Any]", dados)


def _mapa(valor: object, onde: str, permitidas: frozenset[str]) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise PoliticaInvalida("POLICY_MALFORMED", f"{onde} nao e' mapping")
    extras = sorted(str(k) for k in valor if k not in permitidas)
    if extras:
        raise PoliticaInvalida("UNKNOWN_KEY", f"{onde}: {', '.join(extras)}")
    return cast("dict[str, Any]", valor)


def _exige(mapa: Mapping[str, Any], chave: str, onde: str) -> Any:
    if chave not in mapa:
        raise PoliticaInvalida("POLICY_MALFORMED", f"{onde}.{chave} ausente")
    return mapa[chave]


def _booleano(valor: object, onde: str) -> bool:
    if not isinstance(valor, bool):
        raise PoliticaInvalida("POLICY_MALFORMED", f"{onde} deve ser booleano")
    return valor


def _texto(valor: object, onde: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise PoliticaInvalida("POLICY_MALFORMED", f"{onde} deve ser texto nao vazio")
    return valor


def _juridico_completo(valor: object) -> bool:
    """Bloco de ratificacao juridica completo: `aprovado is True` e os tres campos preenchidos."""
    if not isinstance(valor, dict) or set(valor) - _JURIDICO_CHAVES:
        return False
    if valor.get("aprovado") is not True:
        return False
    for chave in ("aprovador", "data", "evidencia_ref"):
        campo = valor.get(chave)
        if campo is None or not str(campo).strip() or str(campo).strip() == _PENDENTE:
            return False
    return True


def _confere_bloco_juridico(valor: object, onde: str) -> None:
    _mapa(valor, onde, _JURIDICO_CHAVES)


def _textos(bruto: object, permitidas: frozenset[str]) -> dict[str, str]:
    mapa = _mapa(bruto, "textos", permitidas)
    return {str(k): _texto(v, f"textos.{k}") for k, v in mapa.items()}


# ---------------------------------------------------------------------------------------------------
# Janela de arrependimento (EP-REG-03a)
# ---------------------------------------------------------------------------------------------------


def _duracao(valor: object, onde: str) -> timedelta:
    casou = _DURACAO.match(valor) if isinstance(valor, str) else None
    if casou is None:
        raise PoliticaInvalida("DURATION_MALFORMED", onde)
    return timedelta(days=int(casou.group(1)))


def carregar_politica_janela(path: Path | None = None) -> PoliticaJanela:
    dados = _mapa(
        _ler_yaml(path, NOME_JANELA),
        "raiz",
        frozenset(
            {
                "versao",
                "politica",
                "status",
                "variante_ativa",
                "display_numeric_promise",
                "fora_da_janela",
                "ratificacao",
                "ancoras",
                "variantes",
                "declaracao_art8_par1",
                "textos",
            }
        ),
    )
    ratificacao = _mapa(_exige(dados, "ratificacao", "raiz"), "ratificacao", frozenset({"juridico"}))
    _confere_bloco_juridico(_exige(ratificacao, "juridico", "ratificacao"), "ratificacao.juridico")
    ancoras_yaml = _mapa(_exige(dados, "ancoras", "raiz"), "ancoras", frozenset(_ANCORAS))
    for nome_ancora, bruta in ancoras_yaml.items():
        _mapa(bruta, f"ancoras.{nome_ancora}", frozenset({"fonte_exigida", "dispositivo_ref", "duracao_iso"}))
    variantes_yaml = _mapa(_exige(dados, "variantes", "raiz"), "variantes", frozenset(_VARIANTES))
    for nome_variante, bruta in variantes_yaml.items():
        _mapa(bruta, f"variantes.{nome_variante}", frozenset({"ancoras", "combinacao"}))
    declaracao = _mapa(
        _exige(dados, "declaracao_art8_par1", "raiz"),
        "declaracao_art8_par1",
        frozenset({"texto_ratificado", "texto_digest"}),
    )
    textos = _textos(
        _exige(dados, "textos", "raiz"), frozenset({"existencia_do_direito", "aviso_no_cancelamento"})
    )

    status = dados.get("status")
    if status not in _STATUS:
        raise PoliticaInvalida("POLICY_MALFORMED", "status invalido")
    if dados.get("fora_da_janela") != "route_to_human":
        raise PoliticaInvalida("AUTO_REFUSAL_FORBIDDEN_PENDING_Q3", "fora_da_janela deve ser route_to_human")
    ratificada = status == "RATIFICADO" and _juridico_completo(ratificacao["juridico"])

    ativa = dados.get("variante_ativa")
    if ativa not in _VARIANTES:
        raise PoliticaInvalida("POLICY_MALFORMED", "variante_ativa desconhecida")
    if ativa != "UNRATIFIED_UNION" and not ratificada:
        raise PoliticaInvalida("VARIANT_REQUIRES_RATIFICATION", str(ativa))
    promessa = _booleano(_exige(dados, "display_numeric_promise", "raiz"), "display_numeric_promise")
    if promessa and not ratificada:
        raise PoliticaInvalida("NUMERIC_PROMISE_REQUIRES_RATIFICATION", "display_numeric_promise")

    texto_ratificado = _booleano(
        _exige(declaracao, "texto_ratificado", "declaracao_art8_par1"),
        "declaracao_art8_par1.texto_ratificado",
    )
    if texto_ratificado and not ratificada:
        raise PoliticaInvalida("DECLARATION_TEXT_REQUIRES_RATIFICATION", "declaracao_art8_par1")
    digest = declaracao.get("texto_digest")
    if digest is not None and not isinstance(digest, str):
        raise PoliticaInvalida("POLICY_MALFORMED", "texto_digest deve ser texto ou null")

    for chave, texto in textos.items():
        if lint_copy(texto, promessa_ratificada=ratificada and promessa, prazo_cancelamento_ratificado=False):
            raise PoliticaInvalida("NUMERIC_PROMISE_REQUIRES_RATIFICATION", f"textos.{chave}")

    ancoragens: dict[str, Ancoragem] = {}
    for nome_ancora, bruta in ancoras_yaml.items():
        fonte = bruta.get("fonte_exigida")
        if fonte not in _FONTES:
            raise PoliticaInvalida("POLICY_MALFORMED", f"ancoras.{nome_ancora}.fonte_exigida")
        ancoragens[nome_ancora] = Ancoragem(
            nome=cast("NomeAncora", nome_ancora),
            fonte_exigida=cast("FonteExigida", fonte),
            dispositivo_ref=_texto(bruta.get("dispositivo_ref"), f"ancoras.{nome_ancora}.dispositivo_ref"),
            duracao=_duracao(bruta.get("duracao_iso"), f"ancoras.{nome_ancora}.duracao_iso"),
        )

    escolhida = variantes_yaml.get(str(ativa))
    if escolhida is None:
        raise PoliticaInvalida("POLICY_MALFORMED", f"variante {ativa} ausente de variantes")
    citadas = escolhida.get("ancoras")
    if not isinstance(citadas, list) or not citadas:
        raise PoliticaInvalida("POLICY_MALFORMED", "variantes.<ativa>.ancoras")
    selecionadas: list[Ancoragem] = []
    for nome_ancora in citadas:
        if nome_ancora not in ancoragens:
            raise PoliticaInvalida("POLICY_MALFORMED", f"ancora {nome_ancora} ausente de ancoras")
        selecionadas.append(ancoragens[nome_ancora])
    combinacao = escolhida.get("combinacao")
    if combinacao not in _COMBINACOES:
        raise PoliticaInvalida("POLICY_MALFORMED", "variantes.<ativa>.combinacao")

    return PoliticaJanela(
        variante_ativa=cast("VarianteJanela", ativa),
        ratificada=ratificada,
        display_numeric_promise=promessa,
        ancoras_da_variante=tuple(selecionadas),
        combinacao=cast("Combinacao", combinacao),
        declaracao_texto_ratificado=texto_ratificado,
        declaracao_texto_digest=digest,
        textos=textos,
    )


# ---------------------------------------------------------------------------------------------------
# Pedido de cancelamento pela pagina (EP-REG-27b + RT-05)
# ---------------------------------------------------------------------------------------------------


def carregar_politica_cancelamento(path: Path | None = None) -> PoliticaPedidoCancelamento:
    dados = _mapa(
        _ler_yaml(path, NOME_CANCELAMENTO),
        "raiz",
        frozenset(
            {
                "versao",
                "politica",
                "status",
                "toques_ate_protocolo",
                "ratificacao",
                "bloco_art15",
                "prazo_comprovante_efetivo",
                "linha_alternativa_os08",
                "textos",
            }
        ),
    )
    ratificacao = _mapa(
        _exige(dados, "ratificacao", "raiz"), "ratificacao", frozenset({"reg27_juridico", "rtj3a_juridico"})
    )
    _confere_bloco_juridico(
        _exige(ratificacao, "reg27_juridico", "ratificacao"), "ratificacao.reg27_juridico"
    )
    _confere_bloco_juridico(
        _exige(ratificacao, "rtj3a_juridico", "ratificacao"), "ratificacao.rtj3a_juridico"
    )
    bloco = _mapa(
        _exige(dados, "bloco_art15", "raiz"),
        "bloco_art15",
        frozenset({"incisos_exigidos", "destaque_obrigatorio", "texto_ratificado", "texto_ref"}),
    )
    prazo = _mapa(
        _exige(dados, "prazo_comprovante_efetivo", "raiz"),
        "prazo_comprovante_efetivo",
        frozenset({"exibir", "dispositivo_ref"}),
    )
    linha = _mapa(
        _exige(dados, "linha_alternativa_os08", "raiz"),
        "linha_alternativa_os08",
        frozenset({"existencia", "redacao_ratificada", "exibir"}),
    )
    textos = _textos(
        _exige(dados, "textos", "raiz"),
        frozenset(
            {
                "entrada_titular_pf",
                "entrada_dependente_coletivo",
                "botao_toque_1",
                "botao_toque_2",
                "estado_aguardando",
                "pergunta_motivo",
                "botao_pular_motivo",
                "motivo_valor_alto",
                "motivo_mudanca_de_cidade",
                "motivo_outro_vinculo",
                "motivo_rede_nao_atende",
                "motivo_atendimento_servico",
                "motivo_prefiro_nao_dizer",
            }
        ),
    )

    versao = dados.get("versao")
    if not isinstance(versao, int) or isinstance(versao, bool):
        raise PoliticaInvalida("POLICY_MALFORMED", "versao")
    if dados.get("status") not in _STATUS:
        raise PoliticaInvalida("POLICY_MALFORMED", "status invalido")
    toques = dados.get("toques_ate_protocolo")
    if toques != _TOQUES or isinstance(toques, bool):
        raise PoliticaInvalida("TOUCH_COUNT_NOT_TWO", str(toques))

    incisos = bloco.get("incisos_exigidos")
    destaque = bloco.get("destaque_obrigatorio")
    if incisos != list(_INCISOS) or not isinstance(destaque, list) or "II" not in destaque:
        raise PoliticaInvalida("ART15_BLOCK_INCOMPLETE", "incisos I-VI e destaque do inciso II")

    reg27 = _juridico_completo(ratificacao["reg27_juridico"])
    rtj3a = _juridico_completo(ratificacao["rtj3a_juridico"])
    texto_ratificado = _booleano(
        _exige(bloco, "texto_ratificado", "bloco_art15"), "bloco_art15.texto_ratificado"
    )
    if (texto_ratificado or bloco.get("texto_ref") is not None) and not reg27:
        raise PoliticaInvalida("ART15_TEXT_PENDING_REG27", "bloco_art15")
    prazo_exibir = _booleano(
        _exige(prazo, "exibir", "prazo_comprovante_efetivo"), "prazo_comprovante_efetivo.exibir"
    )
    if prazo_exibir and not reg27:
        raise PoliticaInvalida("DEADLINE_DISPLAY_PENDING_REG27", "prazo_comprovante_efetivo")
    linha_exibir = _booleano(
        _exige(linha, "exibir", "linha_alternativa_os08"), "linha_alternativa_os08.exibir"
    )
    redacao = _booleano(
        _exige(linha, "redacao_ratificada", "linha_alternativa_os08"),
        "linha_alternativa_os08.redacao_ratificada",
    )
    if linha_exibir and not (redacao and rtj3a):
        raise PoliticaInvalida("OS08_LINE_PENDING_RT_J3A", "linha_alternativa_os08")

    for chave, texto in textos.items():
        codigos = lint_copy(texto, promessa_ratificada=False, prazo_cancelamento_ratificado=reg27)
        if codigos:
            codigo = (
                "REGULATORY_DEADLINE_BEFORE_REG27"
                if "REGULATORY_DEADLINE_BEFORE_REG27" in codigos
                else codigos[0]
            )
            raise PoliticaInvalida(codigo, chave)

    return PoliticaPedidoCancelamento(
        versao=versao,
        toques_ate_protocolo=toques,
        incisos_exigidos=tuple(str(i) for i in incisos),
        destaque_obrigatorio=tuple(str(i) for i in destaque),
        texto_ratificado=texto_ratificado,
        prazo_exibir=prazo_exibir,
        linha_alternativa_exibir=linha_exibir,
        reg27_ratificado=reg27,
        rtj3a_ratificado=rtj3a,
        textos=textos,
    )

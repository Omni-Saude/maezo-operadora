"""Ativacao das regras do pacote K1 do rco por classe de risco (ADR-0077 §1-§4; unidade 5c-1).

SO LOGICA PURA, DADO E INDICADOR DE AMBIENTE. Nada aqui e ligado a worker, processo, banco ou User Task
(isso e 5c-2/5d). Cinco pecas:

1. `efeito_k1_habilitado`: o indicador por ambiente (§1). Falso por padrao; so vale verdadeiro quando a
   chave `MAEZO_RCO_EFEITO_K1` e verdadeira E o `MAEZO_ENV` VIVO esta em `AMBIENTES_QUE_PODEM_LIGAR`
   (so `dev`). Producao, vazio e desconhecido: sempre falso; chave ligada fora da lista gera aviso.
2. `carregar_estados`: leitor fail-closed de `spec/processes/dmn/rco-regras-estado.yaml` (§2).
3. `ativacao_efetiva`: a tabela do §2 com a ordem em_correcao > conferencia_integral > conferida > plena.
4. `filtrar_saida`: o filtro do §3 (o que o worker faria com a saida de uma linha de decisao), e
   `conferencias_confirmadas`/`janela_de_conferencia_aberta`: a janela da `conferida` conta so
   conferencias confirmadas por humano (DL-0182).
5. `amostrar` / `amostra_inicial`: a amostragem HMAC do §4.

Fora do arquivo de estado e do `structlog`, so biblioteca padrao nas partes puras.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import threading
import time
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import structlog
import yaml
from yaml.constructor import ConstructorError as _YamlConstructorError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# 1. Indicador por ambiente
# ---------------------------------------------------------------------------

#: Chave de configuracao/ambiente do indicador. Nenhum arquivo de deploy a define.
CHAVE_EFEITO_K1: Final[str] = "MAEZO_RCO_EFEITO_K1"
#: Mesma variavel e mesmo vocabulario de `agents/helena/habilidades/cobertura/manual.py`.
VARIAVEL_DO_AMBIENTE: Final[str] = "MAEZO_ENV"
#: Ambientes em que o indicador PODE valer verdadeiro. Homologacao entra aqui quando existir; abrir
#: producao e codigo (unidade da Onda 3), nunca configuracao.
AMBIENTES_QUE_PODEM_LIGAR: Final[frozenset[str]] = frozenset({"dev"})
_VERDADEIROS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on", "sim"})


def _verdadeira(valor: object) -> bool:
    if valor is True:
        return True
    return isinstance(valor, str) and valor.strip().lower() in _VERDADEIROS


def efeito_k1_habilitado(*, configurado: object | None = None) -> bool:
    """Indicador `efeito_k1_habilitado` (ADR-0077 §1). Le `MAEZO_ENV` VIVO a cada chamada.

    `configurado` (a configuracao do grafo) tem precedencia sobre a variavel `MAEZO_RCO_EFEITO_K1`
    quando nao e `None`. Verdadeiro so com chave verdadeira E ambiente em `AMBIENTES_QUE_PODEM_LIGAR`.
    Chave verdadeira fora da lista: falso e aviso estruturado (nunca excecao).
    """
    valor = os.environ.get(CHAVE_EFEITO_K1) if configurado is None else configurado
    if not _verdadeira(valor):
        return False
    ambiente = os.environ.get(VARIAVEL_DO_AMBIENTE, "").strip().lower()
    if ambiente in AMBIENTES_QUE_PODEM_LIGAR:
        return True
    logger.warning(
        "rco_efeito_k1_recusado_ambiente",
        chave=CHAVE_EFEITO_K1,
        ambiente=ambiente or "(vazio)",
        permitidos=sorted(AMBIENTES_QUE_PODEM_LIGAR),
    )
    return False


# ---------------------------------------------------------------------------
# 2. Estado manual por (idRegra, versaoRegra)
# ---------------------------------------------------------------------------

ESTADOS_MANUAIS: Final[tuple[str, ...]] = ("sombra", "ativa_l2", "desligada")
ESTADO_PADRAO: Final[str] = "sombra"
ARQUIVO_ESTADO_NOME: Final[str] = "rco-regras-estado.yaml"
_ID_REGRA: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SEMVER: Final[re.Pattern[str]] = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_CAMPOS_RAIZ: Final[frozenset[str]] = frozenset({"version", "regras"})
_CAMPOS_ENTRADA: Final[frozenset[str]] = frozenset({"idRegra", "versaoRegra", "estado"})

ChaveRegra = tuple[str, str]


@dataclass(frozen=True, slots=True)
class EstadosManuais:
    """O arquivo de estado ja lido. `ilegivel` => toda regra e `desligada` e o alerta fica ligado."""

    estados: Mapping[ChaveRegra, str]
    ilegivel: bool = False
    motivo: str | None = None

    def estado_de(self, id_regra: str, versao_regra: str) -> str:
        if self.ilegivel:
            return "desligada"
        return self.estados.get((id_regra, versao_regra), ESTADO_PADRAO)


class _CarregadorSemChaveRepetida(yaml.SafeLoader):
    """`SafeLoader` que recusa chave repetida (o padrao do PyYAML e "a ultima vence", sem aviso)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        vistas: set[Any] = set()
        for no_chave, _ in node.value:
            chave = self.construct_object(no_chave, deep=deep)
            try:
                hash(chave)
            except TypeError:
                raise _YamlConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "chave nao hasheavel",
                    no_chave.start_mark,
                ) from None
            if chave in vistas:
                raise _YamlConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"chave repetida: {chave!r}",
                    no_chave.start_mark,
                )
            vistas.add(chave)
        return super().construct_mapping(node, deep=deep)


def caminho_padrao_do_estado() -> Path:
    """`<spec>/processes/dmn/rco-regras-estado.yaml` pelo mecanismo unico (`resolve_spec_dir`)."""
    from maezo.agents import resolve_spec_dir

    return resolve_spec_dir() / "processes" / "dmn" / ARQUIVO_ESTADO_NOME


def _recusar(motivo: str, caminho: Path | None) -> EstadosManuais:
    logger.error("rco_regras_estado_ilegivel", motivo=motivo, caminho=str(caminho) if caminho else None)
    return EstadosManuais(MappingProxyType({}), ilegivel=True, motivo=motivo)


def ler_estados(texto: str) -> EstadosManuais:
    """Interpreta o conteudo do arquivo. Qualquer problema => `ilegivel` (nunca levanta)."""
    return _interpretar(texto, None)


def _interpretar(texto: str, caminho: Path | None) -> EstadosManuais:
    try:
        dados = yaml.load(
            texto, Loader=_CarregadorSemChaveRepetida
        )  # Loader = subclasse de yaml.SafeLoader (so tipos YAML basicos, nunca objeto Python arbitrario)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        # RecursionError: aninhamento profundo; TypeError/ValueError: construtores do PyYAML.
        return _recusar(f"yaml_invalido: {type(exc).__name__}", caminho)
    if not isinstance(dados, dict) or set(dados) != _CAMPOS_RAIZ:
        return _recusar("raiz_invalida", caminho)
    if type(dados["version"]) is not int or dados["version"] != 1:
        return _recusar("versao_desconhecida", caminho)
    lista = dados["regras"]
    if not isinstance(lista, list):
        return _recusar("regras_nao_e_lista", caminho)
    estados: dict[ChaveRegra, str] = {}
    for i, item in enumerate(lista):
        if not isinstance(item, dict) or set(item) != _CAMPOS_ENTRADA:
            return _recusar(f"entrada_{i}_invalida", caminho)
        id_regra, versao, estado = item["idRegra"], item["versaoRegra"], item["estado"]
        if not (isinstance(id_regra, str) and _ID_REGRA.fullmatch(id_regra)):
            return _recusar(f"entrada_{i}_idRegra_invalido", caminho)
        if not (isinstance(versao, str) and _SEMVER.fullmatch(versao)):
            return _recusar(f"entrada_{i}_versaoRegra_invalida", caminho)
        if not (isinstance(estado, str) and estado in ESTADOS_MANUAIS):
            return _recusar(f"entrada_{i}_estado_invalido", caminho)
        if (id_regra, versao) in estados:
            return _recusar(f"entrada_{i}_repetida", caminho)
        estados[(id_regra, versao)] = estado
    return EstadosManuais(MappingProxyType(estados))


def carregar_estados(caminho: Path | None = None) -> EstadosManuais:
    """Le o arquivo de estado. Fail-closed: ausente, ilegivel ou invalido => toda regra `desligada`.

    O caminho vem do argumento (testes) ou do padrao via `resolve_spec_dir`, que ja tem a guarda de
    override do diretorio de spec; nao ha variavel de ambiente propria que desvie o arquivo, porque
    ele decide se uma regra do rco age. Nunca levanta.
    """
    try:
        if caminho is None:
            caminho = caminho_padrao_do_estado()
        texto = caminho.read_text(encoding="utf-8")
        return _interpretar(texto, caminho)
    except Exception as exc:  # qualquer falha fecha (inclui SpecDirOverrideRefusedError)
        return _recusar(f"leitura_falhou: {type(exc).__name__}", caminho)


#: Validade do cache do estado manual por processo (segundos). O arquivo e do deploy (spec/ da imagem):
#: a janela so evita reler o disco e repetir o erro de arquivo ilegivel a cada decisao, e e curta para
#: que um arquivo corrigido valha logo. A fonte da auditoria nao e o cache: cada linha grava o estado
#: que de fato usou (`estadoManual`).
TTL_CACHE_ESTADOS_S: Final[float] = 30.0
_cache_lock = threading.Lock()
_cache_estados: tuple[float, EstadosManuais] | None = None


def estados_em_cache(
    *, ttl_s: float = TTL_CACHE_ESTADOS_S, relogio: Callable[[], float] = time.monotonic
) -> EstadosManuais:
    """`carregar_estados()` com cache por processo e validade curta (`TTL_CACHE_ESTADOS_S`).

    Seguro entre threads (o worker despacha cada tarefa numa thread). `carregar_estados` nunca levanta,
    entao o cache nunca guarda excecao; um arquivo ilegivel e guardado como ilegivel (fail-closed) pela
    mesma janela.
    """
    global _cache_estados
    agora = relogio()
    with _cache_lock:
        if _cache_estados is not None and 0 <= agora - _cache_estados[0] < ttl_s:
            return _cache_estados[1]
        estados = carregar_estados()
        _cache_estados = (agora, estados)
        return estados


def limpar_cache_estados() -> None:
    """Esquece o cache (testes e troca de arquivo)."""
    global _cache_estados
    with _cache_lock:
        _cache_estados = None


# ---------------------------------------------------------------------------
# 3. Ativacao efetiva (ADR-0077 §2)
# ---------------------------------------------------------------------------

ATIVACOES_EFETIVAS: Final[tuple[str, ...]] = ("plena", "conferida", "conferencia_integral", "em_correcao")
#: Do menos ao mais restritivo.
_RESTRICAO: Final[Mapping[str, int]] = MappingProxyType({a: i for i, a in enumerate(ATIVACOES_EFETIVAS)})
OVERRIDE_UNICO: Final[str] = "conferencia_integral"

ALERTA_ARQUIVO_ILEGIVEL: Final[str] = "arquivo_estado_ilegivel"
ALERTA_ESTADO_IGNORADO: Final[str] = "estado_manual_ignorado_efeito_k1_desabilitado"
ALERTA_ATIVACAO_DESCONHECIDA: Final[str] = "ativacao_pacote_desconhecida"
ALERTA_OVERRIDE_DESCONHECIDO: Final[str] = "override_desconhecido"


@dataclass(frozen=True, slots=True)
class EstadoEfetivo:
    """Resultado de `ativacao_efetiva`. `modo`: sombra (so registro), desligada (tudo adverso vai ao
    humano) ou ativa (aplica `ativacao_efetiva`)."""

    modo: str
    ativacao_efetiva: str
    alertas: tuple[str, ...] = ()


def ativacao_efetiva(
    *,
    estado_manual: str,
    ativacao_pacote: str | None,
    override: str | None,
    efeito_k1_habilitado: bool,
    arquivo_ilegivel: bool = False,
) -> EstadoEfetivo:
    """Funcao pura do §2. `ativacao_efetiva` e sempre a mais restritiva entre o pacote e o override
    (`em_correcao` > `conferencia_integral` > `conferida` > `plena`); `modo` vem do estado manual e do
    indicador. `ativacao_pacote=None` (pacote MAJOR 1) conta como `plena`; valor desconhecido de
    pacote, override ou estado manual fecha para o lado seguro, com alerta."""
    alertas: list[str] = []
    if arquivo_ilegivel:
        alertas.append(ALERTA_ARQUIVO_ILEGIVEL)

    pacote = "plena" if ativacao_pacote is None else ativacao_pacote
    if pacote not in _RESTRICAO or pacote == OVERRIDE_UNICO:
        alertas.append(ALERTA_ATIVACAO_DESCONHECIDA)
        pacote = "em_correcao"
    efetiva = pacote
    if override is not None:
        if override != OVERRIDE_UNICO:
            alertas.append(ALERTA_OVERRIDE_DESCONHECIDO)
        if _RESTRICAO[OVERRIDE_UNICO] > _RESTRICAO[efetiva]:
            efetiva = OVERRIDE_UNICO

    estado = "desligada" if arquivo_ilegivel else estado_manual
    if estado not in ESTADOS_MANUAIS:
        estado = "desligada"
        alertas.append("estado_manual_desconhecido")
    if not efeito_k1_habilitado:
        if estado != "sombra":
            alertas.append(f"{ALERTA_ESTADO_IGNORADO}:{estado}")
        modo = "sombra"
    else:
        modo = {"sombra": "sombra", "desligada": "desligada", "ativa_l2": "ativa"}[estado]
    return EstadoEfetivo(modo, efetiva, tuple(alertas))


# ---------------------------------------------------------------------------
# 4. Filtro de saida (ADR-0077 §3)
# ---------------------------------------------------------------------------

DECISOES_ADVERSAS: Final[frozenset[str]] = frozenset({"negar", "glosar", "recomendar_negativa"})
#: Lista de PERMISSAO das decisoes nao adversas do K1 (`config/integrations/rco/vocabulario-saida.yaml`,
#: secao `k1.decisao`, menos as adversas do contrato K1 §6). Decisao nula ou fora das duas listas vai
#: ao humano: dado ausente ou desconhecido nunca chega a efeito.
DECISOES_NAO_ADVERSAS: Final[frozenset[str]] = frozenset(
    {
        "liberar",
        "exigir_autorizacao",
        "exigir_documento",
        "revisao_humana",
        "pagar_pacote",
        "pagar_referencia",
        "limitar",
        "informar",
        "prazo",
    }
)
#: Classe de adverso que pode levar cada decisao adversa automatica a efeito (contrato K1 §6):
#: `contas` so com `glosar`; `administrativo` com `negar` ou `glosar`. Qualquer outra combinacao
#: (inclusive `clinico` com `negar`) vai ao humano.
CLASSES_COM_EFEITO: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {"glosar": frozenset({"contas", "administrativo"}), "negar": frozenset({"administrativo"})}
)
#: Decisoes adversas com amostragem e contagem de ordinal por regra (§3): `recomendar_negativa` e do medico.
DECISOES_COM_AMOSTRAGEM: Final[frozenset[str]] = frozenset({"negar", "glosar"})
_COM_AMOSTRAGEM: Final[frozenset[str]] = DECISOES_COM_AMOSTRAGEM
ACOES: Final[frozenset[str]] = frozenset(
    {"registrar_apenas", "revisao_humana", "conferencia", "efeito_com_amostragem", "efeito"}
)
MARCA_EM_CORRECAO: Final[str] = "regra_em_correcao"
MARCA_DESLIGADA: Final[str] = "regra_desligada"
MARCA_SEM_ADITIVO: Final[str] = "sem_aditivo"
MARCA_RECOMENDACAO: Final[str] = "recomendacao_clinica"
MARCA_DECISAO_DESCONHECIDA: Final[str] = "decisao_desconhecida"
MARCA_CLASSE_INCOERENTE: Final[str] = "classe_incoerente"


@dataclass(frozen=True, slots=True)
class AcaoDeSaida:
    """O que o worker faz com a saida. `fila` e a da linha da decisao. `amostrada` so e verdadeiro em
    `efeito_com_amostragem` quando a amostragem (§4) escolheu o caso para revisao depois do efeito."""

    acao: str
    fila: str | None = None
    marca: str | None = None
    amostrada: bool = False


def filtrar_saida(
    estado: EstadoEfetivo,
    *,
    decisao: str | None,
    classe_adverso: str | None,
    fila: str | None,
    exige_aditivo: bool,
    aditivo_cobre: bool | None,
    conferencia_inicial: bool = False,
    amostrada: bool = False,
) -> AcaoDeSaida:
    """Funcao pura do §3. `aditivo_cobre=None` conta como sem aditivo. `conferencia_inicial` diz que a
    janela de conferencia da regra ainda esta aberta (so importa em `conferida`): menos de
    `conferencia_negativa_admin_decisoes` conferencias CONFIRMADAS por humano desde o ultimo `nao`
    (`janela_de_conferencia_aberta`, DL-0182); `amostrada` vem de `amostrar`/`amostra_inicial`."""
    if estado.modo == "sombra":
        return AcaoDeSaida("registrar_apenas")
    if decisao in DECISOES_NAO_ADVERSAS:
        return AcaoDeSaida("efeito")
    if decisao not in DECISOES_ADVERSAS:
        return AcaoDeSaida("revisao_humana", fila, MARCA_DECISAO_DESCONHECIDA)
    if estado.modo == "desligada":
        return AcaoDeSaida("revisao_humana", fila, MARCA_DESLIGADA)
    ativacao = estado.ativacao_efetiva
    if ativacao == "em_correcao":
        return AcaoDeSaida("revisao_humana", fila, MARCA_EM_CORRECAO)
    if exige_aditivo and aditivo_cobre is not True:
        return AcaoDeSaida("revisao_humana", fila, MARCA_SEM_ADITIVO)
    if decisao == "recomendar_negativa":
        return AcaoDeSaida("revisao_humana", fila, MARCA_RECOMENDACAO)
    if classe_adverso not in CLASSES_COM_EFEITO.get(decisao or "", frozenset()):
        return AcaoDeSaida("revisao_humana", fila, MARCA_CLASSE_INCOERENTE)
    if ativacao == "conferencia_integral":
        return AcaoDeSaida("conferencia", fila)
    if ativacao == "conferida" and conferencia_inicial:
        return AcaoDeSaida("conferencia", fila)
    if ativacao == "conferida" and decisao == "negar":
        # DL-0181: enquanto o dono nao resolver D2 x authorization_denial (item congelado), `negar` de
        # regra `conferida` NUNCA vira automatico, com qualquer contagem de conferencias confirmadas
        # (DL-0182): fechada a janela, continua em conferencia humana.
        return AcaoDeSaida("conferencia", fila)
    if decisao in _COM_AMOSTRAGEM:
        return AcaoDeSaida("efeito_com_amostragem", fila, amostrada=amostrada)
    return AcaoDeSaida("efeito")  # pragma: no cover - toda adversa e negar/glosar/recomendar_negativa


# ---------------------------------------------------------------------------
# 4b. Janela de conferencia da `conferida` (ADR-0077 §3, emenda de 17/11/2026, DL-0182)
# ---------------------------------------------------------------------------

#: Vocabulario de `rco_decisao_regra_desfecho.concordou` (migracao 0035).
CONCORDOU_SIM: Final[str] = "sim"
CONCORDOU_NAO: Final[str] = "nao"
SEM_DESFECHO: Final[str] = "sem_desfecho"


def conferencias_confirmadas(desfechos: Iterable[tuple[int, int, str]]) -> int:
    """Quantas conferencias da regra contam para a janela: a definicao que o SQL do registro espelha.

    `desfechos`: `(id_do_desfecho, id_da_decisao, concordou)` dos desfechos de decisoes com
    `acao = conferencia` de UM (tenant, idRegra, versaoRegra), em qualquer ordem. Conta as DECISOES
    distintas com desfecho `sim` de id MAIOR que o do `nao` mais recente (um `nao` zera a janela);
    `sem_desfecho` nem conta nem zera. Decisao em sombra ou que nao foi conferencia nunca entra aqui:
    quem chama so passa desfechos de `acao = conferencia`.
    """
    lista = list(desfechos)
    ultimo_nao = max((i for i, _, c in lista if c == CONCORDOU_NAO), default=0)
    return len({d for i, d, c in lista if c == CONCORDOU_SIM and i > ultimo_nao})


def janela_de_conferencia_aberta(confirmadas: int | None, janela: int) -> bool:
    """`True` enquanto a regra `conferida` ainda confere antes do efeito: menos de `janela`
    (`conferencia_negativa_admin_decisoes`) conferencias confirmadas. Contagem desconhecida (`None`) ou
    invalida mantem a janela aberta: na duvida, confere."""
    if confirmadas is None or isinstance(confirmadas, bool) or not isinstance(confirmadas, int):
        return True
    return confirmadas < janela


# ---------------------------------------------------------------------------
# 5. Amostragem (ADR-0077 §4)
# ---------------------------------------------------------------------------

MOTIVO_INICIAL: Final[str] = "inicial"
MOTIVO_ALEATORIA: Final[str] = "aleatoria"
MOTIVO_CHAVE_AUSENTE: Final[str] = "chave_ausente"
_ESCALA: Final[int] = 1_000_000  # 100% = 1.000.000 ; granularidade 0,0001 ponto percentual
_FATOR_PCT: Final[int] = 10_000


def _campo(valor: str) -> bytes:
    bruto = valor.encode("utf-8")
    return len(bruto).to_bytes(4, "big") + bruto


def limiar_amostra(pct: float) -> int:
    """`pct` (0..100, ate 4 casas decimais) -> limiar inteiro em [0, 1.000.000].

    Exatidao: com no maximo 4 casas, `pct * 10_000` e inteiro em aritmetica exata; `Decimal(str(pct))`
    le o decimal mais curto que identifica o float (o que o JSON do pacote escreveu), sem o erro
    binario de `pct * 10_000` em ponto flutuante, e `to_integral_value` so arredonda ruido abaixo de
    0,5. Valor fora de [0, 100], nao finito ou bool levanta `ValueError` (o leitor do pacote ja recusa).
    """
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
        raise ValueError("pct invalido")
    if not 0 <= pct <= 100:
        raise ValueError("pct fora de [0, 100]")
    return int((Decimal(str(pct)) * _FATOR_PCT).to_integral_value())


def amostrar(
    chave: bytes | None,
    id_chave: str | None,
    chave_negocio: str,
    id_regra: str,
    versao_regra: str,
    pct: float,
) -> tuple[bool, str]:
    """Amostragem deterministica e nao previsivel pelo prestador (§4).

    `HMAC-SHA256(chave, campo(chave_negocio) || campo(idRegra) || campo(versaoRegra))`, cada campo em
    UTF-8 com prefixo de 4 bytes big-endian (a concatenacao nao e ambigua); os 8 primeiros bytes como
    inteiro sem sinal; amostrada se `valor % 1_000_000 < limiar_amostra(pct)`. O vies do modulo e menor
    que 1e6 / 2**64 (~5e-14). Sem chave (ou sem `id_chave`, que impediria a auditoria reproduzir a
    amostra): `(True, "chave_ausente")` — falhar para mais revisao e o lado seguro. Nao amostrada:
    `(False, "")`.
    """
    if not isinstance(chave, bytes) or not chave or not id_chave:
        return True, MOTIVO_CHAVE_AUSENTE
    if not all(isinstance(c, str) and c for c in (chave_negocio, id_regra, versao_regra)):
        # Chave de negocio vazia viraria um bloco unico amostrado (ou nao) em conjunto: revisa.
        return True, MOTIVO_CHAVE_AUSENTE
    try:
        limiar = limiar_amostra(pct)
    except ValueError:
        return True, MOTIVO_CHAVE_AUSENTE
    msg = _campo(chave_negocio) + _campo(id_regra) + _campo(versao_regra)
    digest = hmac.new(chave, msg, hashlib.sha256).digest()
    valor = int.from_bytes(digest[:8], "big")
    if valor % _ESCALA < limiar:
        return True, MOTIVO_ALEATORIA
    return False, ""


def amostra_inicial(ordinal: int, revisao_inicial_decisoes: int) -> tuple[bool, str]:
    """As primeiras `revisao_inicial_decisoes` decisoes da regra (ordinal 1-based) sao 100% revistas."""
    if 1 <= ordinal <= revisao_inicial_decisoes:
        return True, MOTIVO_INICIAL
    return False, ""


__all__ = [
    "ACOES",
    "AMBIENTES_QUE_PODEM_LIGAR",
    "ATIVACOES_EFETIVAS",
    "AcaoDeSaida",
    "CHAVE_EFEITO_K1",
    "CONCORDOU_NAO",
    "CONCORDOU_SIM",
    "SEM_DESFECHO",
    "DECISOES_ADVERSAS",
    "CLASSES_COM_EFEITO",
    "DECISOES_COM_AMOSTRAGEM",
    "ESTADOS_MANUAIS",
    "EstadoEfetivo",
    "EstadosManuais",
    "VARIAVEL_DO_AMBIENTE",
    "amostra_inicial",
    "amostrar",
    "conferencias_confirmadas",
    "janela_de_conferencia_aberta",
    "ativacao_efetiva",
    "carregar_estados",
    "efeito_k1_habilitado",
    "estados_em_cache",
    "filtrar_saida",
    "ler_estados",
    "limiar_amostra",
    "limpar_cache_estados",
]

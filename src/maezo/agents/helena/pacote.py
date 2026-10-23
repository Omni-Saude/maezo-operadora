"""O pacote de comportamento da Helena (ADR-0067 fase 3): validacao e aplicacao sobre o turno.

O pacote vem de `maezo.runtime.pacote_do_agente` (SDK): `spec/agents/helena/pacote/` publicado fora da
imagem, com ponteiro por ambiente. Aqui a Helena (1) recusa o que nao entende — chave desconhecida, tipo
errado, valor fora do dominio recusam o pacote INTEIRO, e o receptor fica com o ultimo bom — e (2) aplica
o que o pacote declara sobre a configuracao que a plataforma passou para o turno: o pacote VENCE.

Sem `MAEZO_PACOTES_URI` na task nao ha' pacote, e o turno usa exatamente o que a plataforma passou (o
comportamento de antes, e o de todo teste que nao liga o pacote).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

import yaml

from maezo.runtime.pacote_do_agente import (
    CarregadorDePacote,
    PacoteDoAgente,
    PacoteInvalidoError,
    carregador_do_ambiente,
)

ARQUIVO_CONFIGURACAO: Final[str] = "configuracao.yaml"
FORMATO_CONFIGURACAO: Final[str] = "helena-configuracao-v1"

#: chave do pacote -> chave(s) da configuracao do grafo (`graph.build`) e dos nos fora do grafo.
_BOOLEANAS: Final[dict[str, tuple[str, ...]]] = {
    "memoria_clinica": ("memoria_clinica_enabled",),
    "historico": ("historico_enabled", "coleta_enabled"),
    "triagem_v2": ("triagem_v2_enabled",),
    "v4": ("helena_v4_enabled",),
    "carteirinha_completa": ("helena_carteirinha_completa_enabled",),
    "dependentes_nome": ("helena_dependentes_nome_enabled",),
    "base_saude": ("helena_base_saude_enabled",),
}
_MODOS_COBERTURA: Final[frozenset[str]] = frozenset({"simples", "consulta"})
_MODOS_ROTEADOR: Final[frozenset[str]] = frozenset({"desligado", "sombra", "ativo"})
_CHAVES: Final[frozenset[str]] = frozenset(
    {"formato", *_BOOLEANAS, "cobertura_modo", "identidade_nome_operadora", "roteador"}
)
_CHAVES_ROTEADOR: Final[frozenset[str]] = frozenset(
    {"modo", "modelo", "prazo_s", "max_tokens", "concorrencia", "conversas"}
)


def configuracao(pacote: PacoteDoAgente) -> dict[str, Any]:
    """A configuracao do pacote, VALIDADA. Levanta `PacoteInvalidoError` com o motivo (nunca o conteudo)."""
    bruto = pacote.arquivos.get(ARQUIVO_CONFIGURACAO)
    if bruto is None:
        raise PacoteInvalidoError(f"pacote da Helena sem {ARQUIVO_CONFIGURACAO}")
    try:
        dados = yaml.safe_load(bruto)
    except yaml.YAMLError as exc:
        raise PacoteInvalidoError(f"{ARQUIVO_CONFIGURACAO}: YAML invalido ({type(exc).__name__})") from None
    if not isinstance(dados, dict):
        raise PacoteInvalidoError(f"{ARQUIVO_CONFIGURACAO}: tem de ser um mapeamento")
    erros: list[str] = []
    desconhecidas = sorted(set(dados) - _CHAVES)
    if desconhecidas:
        erros.append(f"chaves desconhecidas {desconhecidas}")
    if dados.get("formato") != FORMATO_CONFIGURACAO:
        erros.append(f"formato tem de ser {FORMATO_CONFIGURACAO!r}")
    for chave in _BOOLEANAS:
        if chave in dados and not isinstance(dados[chave], bool):
            erros.append(f"{chave} tem de ser booleano")
    if "cobertura_modo" in dados and dados["cobertura_modo"] not in _MODOS_COBERTURA:
        erros.append(f"cobertura_modo fora de {sorted(_MODOS_COBERTURA)}")
    nome = dados.get("identidade_nome_operadora")
    if nome is not None and (not isinstance(nome, str) or not nome.strip() or len(nome) > 80):
        erros.append("identidade_nome_operadora tem de ser texto curto")
    roteador = dados.get("roteador")
    if roteador is not None:
        erros += _erros_do_roteador(roteador)
    if erros:
        raise PacoteInvalidoError(f"{ARQUIVO_CONFIGURACAO}: " + "; ".join(erros))
    return dados


def _erros_do_roteador(roteador: object) -> list[str]:
    if not isinstance(roteador, dict):
        return ["roteador tem de ser um mapeamento"]
    erros: list[str] = []
    desconhecidas = sorted(set(roteador) - _CHAVES_ROTEADOR)
    if desconhecidas:
        erros.append(f"roteador: chaves desconhecidas {desconhecidas}")
    if roteador.get("modo") not in _MODOS_ROTEADOR:
        erros.append(f"roteador.modo fora de {sorted(_MODOS_ROTEADOR)}")
    if not isinstance(roteador.get("modelo", ""), str):
        erros.append("roteador.modelo tem de ser texto")
    prazo = roteador.get("prazo_s", 3.0)
    if not isinstance(prazo, int | float) or isinstance(prazo, bool) or not 0.5 <= prazo <= 30:
        erros.append("roteador.prazo_s entre 0.5 e 30")
    for chave, teto in (("max_tokens", 4096), ("concorrencia", 64)):
        valor = roteador.get(chave, 1)
        if not isinstance(valor, int) or isinstance(valor, bool) or not 1 <= valor <= teto:
            erros.append(f"roteador.{chave} entre 1 e {teto}")
    conversas = roteador.get("conversas", [])
    if not isinstance(conversas, list) or not all(
        isinstance(c, str) and c.startswith("wa:") and ":hk1_" in c for c in conversas
    ):
        erros.append("roteador.conversas: lista de ids keyed (wa:<tenant>:hk1_...)")
    return erros


def validar(pacote: PacoteDoAgente) -> None:
    """O validador que o carregador chama antes de aceitar um pacote novo."""
    configuracao(pacote)


class PacoteDaHelena:
    """Le' o pacote ativo (via SDK) e o aplica sobre a configuracao do turno."""

    def __init__(self, carregador: CarregadorDePacote | None) -> None:
        self._carregador = carregador
        self._provedores: dict[tuple[str, float, int, int], Any] = {}

    def configuracao_ativa(self) -> dict[str, Any] | None:
        if self._carregador is None:
            return None
        pacote = self._carregador.atual()
        return configuracao(pacote) if pacote is not None else None

    def aplicar_no_grafo(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """A configuracao do `graph.build` com o pacote por cima. Sem pacote: a mesma, sem a fabrica."""
        saida = dict(config)
        fabrica: Callable[..., Any] | None = saida.pop("fabrica_de_inferencia", None)
        dados = self.configuracao_ativa()
        if dados is None:
            return saida
        self._aplicar_booleanas(saida, dados)
        if "cobertura_modo" in dados:
            saida["cobertura_modo"] = dados["cobertura_modo"]
        if "identidade_nome_operadora" in dados:
            saida["identidade_nome_operadora"] = dados["identidade_nome_operadora"]
        roteador = dados.get("roteador")
        if isinstance(roteador, dict):
            modo = roteador["modo"]
            saida["helena_roteador"] = modo
            saida["roteador_concorrencia"] = roteador.get("concorrencia", 4)
            saida["helena_roteador_conversas"] = frozenset(roteador.get("conversas", []))
            if modo == "desligado":
                saida["roteador_inference"] = None
            elif fabrica is not None:
                saida["roteador_inference"] = self._provedor(fabrica, roteador)
        return saida

    def aplicar_nos_nos(self, dependencias: Mapping[str, Any]) -> dict[str, Any]:
        """As dependencias dos nos fora do grafo (escalonamentos) com as chaves do pacote por cima."""
        saida = dict(dependencias)
        dados = self.configuracao_ativa()
        if dados is not None:
            self._aplicar_booleanas(saida, dados, so_existentes=True)
        return saida

    @staticmethod
    def _aplicar_booleanas(
        saida: dict[str, Any], dados: Mapping[str, Any], *, so_existentes: bool = False
    ) -> None:
        for chave, destinos in _BOOLEANAS.items():
            if chave in dados:
                for destino in destinos:
                    if not so_existentes or destino in saida:
                        saida[destino] = dados[chave]

    def _provedor(self, fabrica: Callable[..., Any], roteador: Mapping[str, Any]) -> Any:
        """O provedor do roteador com o modelo e o orcamento do pacote, um por combinacao (pool proprio)."""
        chave = (
            str(roteador.get("modelo", "")).strip(),
            float(roteador.get("prazo_s", 3.0)),
            int(roteador.get("max_tokens", 256)),
            int(roteador.get("concorrencia", 4)),
        )
        if chave not in self._provedores:
            modelo, prazo_s, max_tokens, concorrencia = chave
            self._provedores[chave] = fabrica(
                modelo=modelo, prazo_s=prazo_s, max_tokens=max_tokens, concorrencia=concorrencia
            )
        return self._provedores[chave]


def do_ambiente() -> PacoteDaHelena:
    """O pacote da Helena configurado pela task (ou nenhum, sem `MAEZO_PACOTES_URI`)."""
    return PacoteDaHelena(carregador_do_ambiente("helena", validar=validar))

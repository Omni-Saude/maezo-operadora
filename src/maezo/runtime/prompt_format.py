"""Cache-aware prompt formatting (Onda 2, W8) — stable prefix first, variability last.

WHAT PROBLEM THIS SOLVES. Provider-side prompt caching keys on a BYTE-IDENTICAL PREFIX: a
vendor can reuse the cached attention state for request N+1 only up to the first byte where
it differs from request N. Every byte of per-request variability that lands EARLY in a prompt
truncates the cacheable region for everything after it. So the layout rule is structural, not
stylistic — static/system content first, in a deterministic order, and per-request values
strictly last.

WHY A FORMATTING LAYER AND NOT A CONVENTION. The 15 in-repo assembly sites
(`agents/*/graph.py`, historically all of the shape
``f"{dossier_prompt()}\\n\\nroute={route}\\nfatos={facts}"`` — since CC-11 the facts payload of
11 of them goes through :func:`render_fatos_para_prompt` below, while the static-then-variable
layout is unchanged) each happen to satisfy that rule today. They satisfy it INDEPENDENTLY, by
each author having got the order right — nothing checks it, and the failure mode is silent: a
prompt that moves one variable token above the static block still WORKS, it just quietly stops
being cacheable.
This module makes the boundary an explicit, testable value instead of an emergent property of
an f-string, and hands the two sides to a transport that can declare a cache breakpoint
between them (see :class:`maezo.runtime.inference.BrRegionalRequest`, whose
``stable_prefix``/``variable_suffix`` fields exist for exactly this).

WHAT THIS MODULE DOES NOT DO. It cannot verify that a segment a caller DECLARED stable really
is stable across requests — that is a property of the caller's data, not of any string handed
to a function here. What it does is make the declaration explicit and its consequence
observable: :attr:`FormattedPrompt.stable_prefix` is exactly the region a caller is claiming
is reusable, so a mis-declared value shows up as a prefix that differs between two requests
instead of hiding inside one opaque blob.
:func:`stable_prefix_is_byte_stable` is that check, offered as a first-class function so a
test (or a future canary) asserts the property rather than eyeballing it.

PHI DISCIPLINE. Pure string assembly: no logging, no telemetry, no I/O, no hashing of content
into anything that leaves this frame. Prompt bytes travel only in the returned value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: Separator BETWEEN consecutive static segments, and between the static block and the
#: variable block. Two newlines, matching the shape every in-repo assembly site already emits
#: (`f"{dossier_prompt()}\n\n..."`), so adopting this formatter is byte-neutral at those sites
#: rather than a silent prompt change — prompt bytes feed `PROMPT_VERSIONS` audit provenance
#: (ADR-0007) and eval baselines, so changing them without a version bump would be a defect.
STABLE_SEPARATOR: Final[str] = "\n\n"

#: Separator between variable LINES. One newline — again matching the existing sites.
VARIABLE_LINE_SEPARATOR: Final[str] = "\n"

#: Separator between the ``key=value`` pairs that share one variable line.
VARIABLE_FIELD_SEPARATOR: Final[str] = " "

#: One line of per-request variability: an ordered sequence of ``(key, value)`` pairs rendered
#: as ``key=value`` and joined by a space. Ordered, never sorted — prompt semantics can depend
#: on field order (`agents/helena/prompts.py` docstring: "the donor prompt is the SME-reviewable
#: baseline and LLM prompt ordering can matter"), so this module preserves what the caller wrote
#: and never reorders it on its own authority.
VariableLine = Sequence[tuple[str, object]]


class PromptFormatError(ValueError):
    """Raised when a prompt layout cannot have a meaningful cacheable prefix.

    Fail-closed rather than silently producing an uncacheable prompt: a caller that asks for
    cache-aware formatting and supplies nothing cacheable has a bug at the call site, and a
    formatter that quietly returned an empty prefix would hide it behind a working prompt.
    """


@dataclass(frozen=True, slots=True)
class FormattedPrompt:
    """A prompt split at its cache boundary.

    ``stable_prefix + variable_suffix == text``, exactly — the split is a VIEW of one string,
    never a transformation of it. That identity is what lets a transport send a cache
    breakpoint at ``len(stable_prefix)`` and still transmit the same prompt the model would
    have seen unsplit.

    Frozen + slots, mirroring `ProviderCapabilities`
    (`runtime/inference/capabilities.py::ProviderCapabilities`): a layout that
    has been computed is a fact about one request, not a mutable buffer.
    """

    #: Everything a caller declared static, plus the trailing :data:`STABLE_SEPARATOR`. The
    #: separator belongs to the PREFIX on purpose: it is itself byte-stable, so including it
    #: extends the cacheable region by those bytes instead of donating them to the suffix.
    stable_prefix: str

    #: Everything a caller declared per-request. May be empty (a prompt with no variability at
    #: all is entirely cacheable, which is legitimate).
    variable_suffix: str

    @property
    def text(self) -> str:
        """The full prompt as a provider would receive it."""
        return self.stable_prefix + self.variable_suffix

    @property
    def stable_prefix_chars(self) -> int:
        """Length of the cacheable region, in characters.

        CHARACTERS, NOT TOKENS, and deliberately not an estimate of tokens: this module owns no
        tokenizer and inventing a chars-to-tokens ratio would be a fabricated number. A real
        cached-token count comes back from the provider's own usage response
        (``BrRegionalResponse.cached_prefix_tokens``), never from arithmetic here.
        """
        return len(self.stable_prefix)


def format_cached_prompt(
    stable_segments: Sequence[str],
    variable_lines: Sequence[VariableLine] = (),
) -> FormattedPrompt:
    """Assemble a prompt with the maximal byte-stable prefix the caller's declaration allows.

    Args:
        stable_segments: Static/system content, in the order it should appear. Joined by
            :data:`STABLE_SEPARATOR`. Order is preserved verbatim — see :data:`VariableLine`.
        variable_lines: Per-request content, one entry per output line, each a sequence of
            ``(key, value)`` pairs rendered ``key=value``. Values are rendered with ``str()``
            (``f"{value}"`` semantics), matching what the existing f-string sites emit for the
            dicts and scalars they pass.

    Returns:
        A :class:`FormattedPrompt` whose ``stable_prefix`` depends ONLY on ``stable_segments``.
        That is the whole contract: two calls sharing ``stable_segments`` produce byte-identical
        prefixes no matter how their ``variable_lines`` differ.

    Raises:
        PromptFormatError: no non-empty static segment was supplied, so there is no cacheable
            prefix to speak of.
    """
    kept = [segment for segment in stable_segments if segment]
    if not kept:
        raise PromptFormatError(
            "cache-aware formatting requires at least one non-empty static segment — a prompt "
            "with no stable region has no cacheable prefix, and returning one silently would "
            "hide the defect at the call site. Pass the system/instruction text as a stable "
            "segment and per-request values as variable lines."
        )

    stable_prefix = STABLE_SEPARATOR.join(kept) + STABLE_SEPARATOR
    variable_suffix = VARIABLE_LINE_SEPARATOR.join(
        VARIABLE_FIELD_SEPARATOR.join(f"{key}={value}" for key, value in line) for line in variable_lines
    )
    return FormattedPrompt(stable_prefix=stable_prefix, variable_suffix=variable_suffix)


def stable_prefix_is_byte_stable(prompts: Iterable[FormattedPrompt]) -> bool:
    """True if every prompt in ``prompts`` shares one byte-identical ``stable_prefix``.

    The property this module exists to provide, expressed as a predicate so it can be ASSERTED
    over a set of real requests rather than assumed from the layout. An empty or single-element
    input is vacuously stable, which is the honest answer: one request proves nothing about
    reuse.
    """
    prefixes = {prompt.stable_prefix for prompt in prompts}
    return len(prefixes) <= 1


# --- Renderizacao dos FATOS de um caso -----------------------------------------------------
#
# POR QUE ESTA FUNCAO MORA AQUI, E NAO NUM GRAFO (CC-11 / RAF-12)
#
# Ela nasceu em `agents/rafael/graph.py` como conserto pontual do incidente de 24/08/2026: num
# caso com prestador FORA da rede, o dossie escreveu "nao ha registro de verificacao de teto L2
# ou rede credenciada". A rede TINHA sido verificada e dado FALSO. A causa nao era descarte na
# montagem — `_build_dossier` sempre passou `False` e `None` adiante, os dois. A distincao morria
# na PASSAGEM PARA O MODELO: os fatos iam ao prompt como repr de dicionario Python
# (`'rede_credenciada': False, 'dentro_teto_l2': None`), e num repr os dois parecem a mesma coisa,
# um valor "vazio".
#
# O conserto ficou num agente so'. A auditoria da frota (04/09/2026, CC-11) encontrou o mesmo
# veiculo do defeito vivo em 8 dos 9 agentes que passam fatos a um LLM. Promover a funcao para
# este modulo — o ponto mais baixo que ja' e' dono da FORMA de um prompt — e' o que impede o
# proximo conserto de nascer de novo em um lugar so'.
#
# LIMITE DELIBERADO: esta funcao NAO DECIDE NADA. Ela nao filtra, nao pondera, nao classifica e
# nao muda o dicionario de fatos que o dossie devolve — a proveniencia de auditoria (ADR-0007)
# continua sendo o dicionario intacto. Ela so' escolhe como o modelo LE cada valor.

#: Cabecalho do bloco de booleanos nomeados.
FATOS_CABECALHO: Final[str] = "fatos apurados:"

#: Rotulo do bloco que carrega o resto do caso (tudo que nao e' booleano declarado).
FATOS_CONTEXTO_ROTULO: Final[str] = "demais dados do caso: "

#: Largura da coluna do marcador. `SEM DADO` (8) mais dois espacos — as tres formas ficam
#: alinhadas e visualmente distintas, que e' o ponto todo do conserto.
_LARGURA_MARCADOR: Final[int] = 10

_MARCADOR_SIM: Final[str] = "SIM"
_MARCADOR_NAO: Final[str] = "NAO"

#: Marcador default de "nao apurado". Parametrizado porque um fluxo pode precisar de outra
#: palavra, NUNCA para transformar ausencia em afirmacao.
SEM_DADO_PADRAO: Final[str] = "SEM DADO"


def _valor_do_fato(facts: Mapping[str, Any], caminho: str) -> Any:
    """Le `caminho` em `facts`, aceitando um caminho pontilhado para um fato ANINHADO.

    Aninhado existe porque o andre agrupa os fatos de adequacao de rede num sub-dicionario
    (`facts["adequacao"]["cobertura_geo_suficiente"]`) — e um booleano aninhado sofre
    exatamente o mesmo colapso de repr que um booleano de topo. Segmento ausente, ou um
    intermediario que nao e' `Mapping`, devolve `None`: a leitura segura e' SEM DADO.
    """
    atual: Any = facts
    for segmento in caminho.split("."):
        if not isinstance(atual, Mapping):
            return None
        atual = atual.get(segmento)
    return atual


def _contexto_sem_os_booleanos(facts: Mapping[str, Any], caminhos: Iterable[str]) -> dict[str, Any]:
    """`facts` sem as chaves ja' nomeadas no bloco de booleanos, aninhadas inclusive.

    Deixar a chave nos dois lugares seria manter o defeito ao lado do conserto: o modelo leria
    `NAO  prestador na rede credenciada` e, logo abaixo, `'rede_credenciada': False`.

    COPIA CADA NIVEL ANTES DE ESCREVER NELE — `facts` e os sub-dicionarios do chamador NUNCA sao
    mutados. Isso e' requisito, nao zelo: os sitios de montagem passam ADIANTE o mesmo objeto
    `facts` que vai ao registro de auditoria (ADR-0007), entao podar um sub-dicionario no lugar
    apagaria do registro o fato que acabou de ser apurado — o defeito de 24/08/2026 (`False`
    indistinguivel de ausencia) reaparecendo do outro lado, agora como ausencia de verdade.

    A versao anterior copiava raso SO' O TOPO e escrevia a poda no sub-dicionario do chamador a
    partir do terceiro nivel. Ficou latente porque os mapas de hoje chegam a dois niveis; o
    primeiro mapa com tres o acordaria em producao. A descida abaixo refaz a copia a cada
    caminho de proposito: o custo e' um punhado de dicionarios rasos por prompt, e a alternativa
    (copiar uma vez e reutilizar) e' justamente o que erra quando dois booleanos irmaos moram no
    mesmo pai.
    """
    topo = {caminho for caminho in caminhos if "." not in caminho}
    contexto: dict[str, Any] = {k: v for k, v in facts.items() if k not in topo}

    for caminho in caminhos:
        if "." not in caminho:
            continue
        _podar_copiando_cada_nivel(contexto, caminho.split("."))
    return contexto


def _podar_copiando_cada_nivel(contexto: dict[str, Any], segmentos: list[str]) -> None:
    """Tira `segmentos[-1]` de `contexto`, trocando cada nivel intermediario por uma copia rasa.

    A troca acontece ANTES de qualquer escrita, entao o unico dicionario que esta funcao muta e'
    um que ela mesma acabou de criar (ou o `contexto` de topo, que ja' e' copia). Um segmento
    intermediario ausente ou que nao e' `Mapping` aborta o caminho sem escrever nada: nao ha' o
    que podar, e inventar um dicionario ali mudaria o contexto que o modelo le.
    """
    recipiente = contexto
    for segmento in segmentos[:-1]:
        filho = recipiente.get(segmento)
        if not isinstance(filho, Mapping):
            return
        copia = dict(filho)
        recipiente[segmento] = copia
        recipiente = copia
    recipiente.pop(segmentos[-1], None)


def render_fatos_para_prompt(
    facts: Mapping[str, Any],
    *,
    booleanos: Mapping[str, str],
    sem_dado: str = SEM_DADO_PADRAO,
    linhas_extra: Sequence[str] = (),
) -> str:
    """Serializa os fatos NOMEANDO o estado de cada booleano, em vez de despejar o dicionario.

    Tres estados, tres formas visualmente distintas — e nenhuma delas carrega instrucao, porque
    a primeira versao do conserto (25/08/2026) marcava o fato desfavoravel com
    `[FATO DESFAVORAVEL: cite nomeando]` e o modelo COPIOU a frase em caixa alta para a
    narrativa em 1 de 4 casos medidos. Marcacao que parece frase pronta convida a ser
    reproduzida; token curto nao. A regra de como tratar cada estado mora no prompt.

    O `is True` / `is False` e' deliberado: `1`, `"nao"` e `[]` NAO sao fatos apurados, e um
    `bool()` os converteria em afirmacao sobre uma pessoa. Qualquer coisa que nao seja booleano
    cai em ``sem_dado``, que e' a leitura segura.

    Args:
        facts: O dicionario de fatos do caso, LIDO e nunca mutado.
        booleanos: Mapa `chave do fato -> rotulo em pt-BR`, especifico do fluxo do chamador. A
            ORDEM deste mapa e' a ordem das linhas — o chamador e' dono dela. Uma chave pode ser
            um caminho pontilhado (`"adequacao.cobertura_geo_suficiente"`) para um fato aninhado.
        sem_dado: Marcador de "nao apurado".
        linhas_extra: Linhas ja' formatadas, anexadas depois dos booleanos. Existe para o fato
            que NAO e' do agente: o rafael declara o teto de aprovacao como `APURADO PELO MOTOR`
            porque `ceilings.py` o computa DEPOIS do dossie (C-02, 27/08/2026) — dizer "sem
            dado" sobre ele seria afirmar o que o agente nao sabe.

    Returns:
        O bloco de fatos apurados seguido do restante do caso.

        O restante sai como REPR DE DICIONARIO, e essa e' uma escolha conservadora e nao um
        descuido: e' a forma que os 10 sitios ja' emitem hoje, e trocar por JSON ordenado ou
        por linhas `chave: valor` mudaria os bytes do prompt do rafael — que estao congelados em
        `tests/unit/agents/test_prompt_facts_rendering.py` justamente porque bytes de prompt
        alimentam proveniencia de auditoria (ADR-0007) e baselines de eval (mesma disciplina que
        :data:`STABLE_SEPARATOR` documenta). O repr preserva a ordem de insercao do chamador e
        nunca reordena por conta propria — os montadores de fatos sao literais de dicionario com
        ordem fixa, entao a saida e' estavel entre requisicoes. O que o repr colapsa — `False`
        contra `None` — deixa de importar aqui, porque os booleanos declarados sairam dele.
    """
    linhas: list[str] = []
    for caminho, rotulo in booleanos.items():
        valor = _valor_do_fato(facts, caminho)
        if valor is True:
            marcador = _MARCADOR_SIM
        elif valor is False:
            marcador = _MARCADOR_NAO
        else:
            marcador = sem_dado
        linhas.append(f"  {marcador:<{_LARGURA_MARCADOR}}{rotulo}")
    linhas.extend(linhas_extra)

    contexto = _contexto_sem_os_booleanos(facts, booleanos)
    corpo = "\n".join(linhas)
    return f"{FATOS_CABECALHO}\n{corpo}\n\n{FATOS_CONTEXTO_ROTULO}{contexto}"


__all__ = [
    "FATOS_CABECALHO",
    "FATOS_CONTEXTO_ROTULO",
    "SEM_DADO_PADRAO",
    "STABLE_SEPARATOR",
    "VARIABLE_FIELD_SEPARATOR",
    "VARIABLE_LINE_SEPARATOR",
    "FormattedPrompt",
    "PromptFormatError",
    "VariableLine",
    "format_cached_prompt",
    "render_fatos_para_prompt",
    "stable_prefix_is_byte_stable",
]

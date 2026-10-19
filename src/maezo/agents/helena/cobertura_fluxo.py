"""O FLUXO da cobertura assistida (DL-0128): orquestra extracao, traducao para o nome oficial, opcoes, para
quem, tentativas e a consulta da AMH. As dependencias de rede vem INJETADAS (o grafo as fornece), entao o
fluxo e' testavel sem AWS. Textos e leituras puras em `cobertura_assistida.py`.

Contrato com o grafo: `responder(...)` devolve o `update` do turno (texto, pendencia, contexto) — sempre um
texto, nunca `None`, para a pergunta de cobertura na allowlist (os textos antigos "por aqui eu nao consigo
confirmar cobertura de um procedimento especifico" saem deste caminho).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import structlog

from maezo.agents.helena import cobertura_assistida as ca
from maezo.agents.helena.cobertura_rol import AVISO_DO_DEPENDENTE, literais_da_cobertura, termo_da_continuacao
from maezo.conhecimento.catalogo_procedimentos import BuscaDeProcedimentos, Resultado
from maezo.runtime.dependency_failures import EXTERNAL_DEPENDENCY_FAILURES, PROGRAMMING_ERRORS

logger = structlog.get_logger(__name__)

PEND_RESPONDIDO = "COB2:RESPONDIDO"
_TITULAR = re.compile(
    r"^(?:1|um|opcao 1|para mim|pra mim|eu|e para mim|e pra mim|mim|titular|o titular|a titular|para o "
    r"titular"
    r"|pra eu|para eu|e pra eu|sou eu|e eu|e pra mim mesmo|pra mim mesmo|para mim mesmo|para mim mesma"
    r"|pra mim mesma)$"
)
_GRAUS_POR_PALAVRA: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:filh[oa]s?|menino|menina|bebe)\b"), "filho"),
    (re.compile(r"\b(?:esposa|marido|conjuge|mulher|companheir[oa])\b"), "conjuge"),
    (re.compile(r"\b(?:entead[oa])\b"), "enteado"),
    (re.compile(r"\b(?:net[oa])\b"), "neto"),
)
_TERCEIRO = re.compile(
    r"\b(?:minha mae|meu pai|minha avo|meu avo|meu irmao|minha irma|minha tia|meu tio|minha sogra|meu sogro"
    r"|um amigo|uma amiga|meu amigo|minha amiga|minha prima|meu primo|para (?:a |o )?(?:mae|pai))\b"
)


def quem_pelo_texto(texto: str, graus: Sequence[str], primeiros_nomes: Mapping[int, str]) -> str | None:
    """`titular`, `dependente:N` (1..), `terceiro` ou `None` (ainda nao da' para saber)."""
    plano = ca.normalizar(texto)
    plano = re.sub(r"^(?:e |eh |e para |e pra |para |pra |pro |pra a |pro o )", "", plano).strip()
    if _TITULAR.match(plano) or _TITULAR.match(ca.normalizar(texto)):
        return "titular"
    m = re.fullmatch(r"(?:opcao )?([2-9])", plano)
    if m:
        n = int(m.group(1)) - 1
        return f"dependente:{n}" if 1 <= n <= len(graus) else None
    palavras = set(plano.split())
    por_nome = [n for n, primeiro in primeiros_nomes.items() if primeiro and primeiro in palavras]
    if len(por_nome) == 1:
        return f"dependente:{por_nome[0]}"
    if _TERCEIRO.search(plano):
        return "terceiro"
    for padrao, grau in _GRAUS_POR_PALAVRA:
        if padrao.search(plano):
            iguais = [i for i, g in enumerate(graus, start=1) if grau in ca.normalizar(g)]
            if len(iguais) == 1:
                return f"dependente:{iguais[0]}"
            if not iguais:
                return "fora_do_plano"
            return None
    return None


@dataclass
class Dependencias:
    """O que o fluxo precisa do mundo (o grafo fornece)."""

    extrair: Callable[[str], Awaitable[ca.Pedido | None]]
    cobertura: Callable[[str], Awaitable[Any]]
    dependentes: Callable[[], Awaitable[tuple[list[str], bool, Mapping[int, str]]]]
    elegibilidade: Callable[[], Awaitable[Mapping[str, Any] | None]]
    busca: BuscaDeProcedimentos | None
    pergunta_para_quem: Callable[[list[str], bool], str]
    texto_geral: Callable[[], Awaitable[str]]


def _update(texto: str, pendente: str | None, ctx: ca.Contexto | None, **extra: Any) -> dict[str, Any]:
    saida: dict[str, Any] = {
        "response_text": texto,
        "response_kind": "inform",
        "coleta_pendente": pendente,
        "coleta_contexto": ctx.codificar() if (ctx is not None and pendente) else "",
        "coleta_rodadas": 0,
    }
    saida.update(extra)
    return saida


def _situacao_bloqueada(fatos: Mapping[str, Any] | None) -> str | None:
    """C2: o atendimento NAO esta' liberado no cadastro (suspenso, inadimplente, inativo)? O texto, ou
    `None`."""
    if not fatos:
        return None
    vinculos = [v for v in fatos.get("vinculos") or [] if isinstance(v, Mapping)]
    if not vinculos or any(v.get("atendimento_liberado") is not False for v in vinculos):
        return None
    situacao = next(
        (str(v.get("situacao_beneficiario")) for v in vinculos if v.get("situacao_beneficiario")), ""
    )
    return ca.TEXTO_PLANO_SEM_ATENDIMENTO.format(situacao=f" (situação: {situacao})" if situacao else "")


class FluxoDeCobertura:
    def __init__(self, deps: Dependencias) -> None:
        self.d = deps

    async def responder(self, texto: str, pendente: str, contexto: object) -> dict[str, Any]:
        ctx = ca.Contexto.decodificar(contexto) if pendente.startswith(ca.PREFIXO) else ca.Contexto()
        respondidos = list(ctx.respondidos) if pendente == PEND_RESPONDIDO else []
        if pendente == ca.PEND_EQUIPE and ca.e_nao(texto):
            return _update(ca.TEXTO_SEM_EQUIPE, None, None)
        if pendente == ca.PEND_FALHA and ca.tentar_de_novo(texto):
            return await self._resolver(ctx, prefixo="")
        if pendente == ca.PEND_QUEM:
            graus, com_nome, primeiros = await self.d.dependentes()
            quem = quem_pelo_texto(texto, graus, primeiros)
            if quem is not None:
                return await self._com_quem(ctx, quem, graus)
        if pendente == ca.PEND_OPCOES and ctx.opcoes:
            escolha = ca.escolha_da_opcao(texto, ctx.opcoes)
            if escolha is not None and escolha >= 0:
                codigo, nome = ctx.opcoes[escolha]
                return await self._consultar_um(ctx, codigo or nome, nome, prefixo="")
            if escolha == -1:
                return self._falhou(ctx, ctx.termos[0] if ctx.termos else "esse procedimento")
            novo = termo_da_continuacao(texto)
            if novo:
                ctx = replace(ctx, termos=[novo], opcoes=[])
                return await self._resolver(ctx, prefixo="")
        if pendente == ca.PEND_FORMA:
            novo = termo_da_continuacao(texto)
            if novo:
                ctx = replace(ctx, termos=[novo], opcoes=[])
                return await self._resolver(ctx, prefixo="")
        return await self._nova_pergunta(
            texto, ctx if pendente == ca.PEND_FORMA else ca.Contexto(), respondidos
        )

    # --- pergunta nova --------------------------------------------------------------------------------------
    async def _nova_pergunta(self, texto: str, ctx: ca.Contexto, respondidos: list[str]) -> dict[str, Any]:
        from maezo.agents.helena.cobertura_rol import termo_da_mensagem

        reserva_termos = [t for t in (termo_da_mensagem(texto), termo_da_continuacao(texto)) if t]
        reserva = ca.pedido_deterministico(texto, reserva_termos)
        try:
            modelo = await self.d.extrair(texto)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            modelo = None
        pedido = ca.combinar(modelo, reserva)
        logger.info(
            "helena_cobertura_extraida",
            pelo_modelo=pedido.pelo_modelo,
            procedimentos=len(pedido.procedimentos),
            tipo=pedido.tipo,
            assunto=pedido.assunto,
            urgencia=pedido.urgencia,
            para_quem=pedido.para_quem,
            exclusao=pedido.exclusao_legal,
            discorda=pedido.discorda,
        )
        prefixo = (ca.PREFIXO_URGENCIA if pedido.urgencia else "") + (
            ca.PREFIXO_DISCORDA if pedido.discorda else ""
        )
        ctx = ca.Contexto(
            termos=list(pedido.procedimentos),
            assunto=pedido.assunto,
            urgencia=pedido.urgencia,
            respondidos=respondidos,
        )
        if pedido.assunto == "autorizacao":
            return {"desviar_para": "autorizacao"}
        if pedido.assunto == "rede":
            return {"desviar_para": "rede"}
        if pedido.assunto == "coparticipacao":
            return _update(prefixo + ca.TEXTO_COPARTICIPACAO, None, None)
        if pedido.assunto == "reembolso":
            return _update(prefixo + ca.TEXTO_REEMBOLSO, ca.PEND_EQUIPE, ctx)
        if pedido.exclusao_legal in ca.TEXTO_EXCLUSAO:
            return _update(prefixo + ca.TEXTO_EXCLUSAO[pedido.exclusao_legal], ca.PEND_EQUIPE, ctx)
        if pedido.tipo == "remedio":
            return _update(prefixo + ca.TEXTO_REMEDIO, ca.PEND_EQUIPE, ctx)
        if pedido.tipo == "material" and not pedido.procedimentos:
            return _update(prefixo + ca.TEXTO_MATERIAL, ca.PEND_EQUIPE, ctx)
        if pedido.recem_nascido:
            return _update(prefixo + ca.TEXTO_RECEM_NASCIDO, ca.PEND_EQUIPE, ctx)
        if pedido.para_quem == "terceiro":
            return _update(prefixo + ca.TEXTO_TERCEIRO, ca.PEND_EQUIPE, ctx)
        if not pedido.procedimentos:
            geral = await self.d.texto_geral()
            return _update(prefixo + geral, ca.PEND_FORMA, ctx)
        # Para quem: com dependentes, so' pergunta quando a mensagem nao diz.
        graus, com_nome, primeiros = await self.d.dependentes()
        quem: str | None = "titular" if pedido.para_quem == "titular" else None
        if pedido.quem_texto:
            quem = quem_pelo_texto(pedido.quem_texto, graus, primeiros) or quem
        if quem is None and pedido.para_quem == "dependente" and len(graus) == 1:
            quem = "dependente:1"
        if quem is None and not graus:
            quem = "titular"
        if quem is None:
            ctx = replace(ctx, quem=None)
            return _update(prefixo + self.d.pergunta_para_quem(graus, com_nome), ca.PEND_QUEM, ctx)
        return await self._com_quem(ctx, quem, graus, prefixo=prefixo)

    async def _com_quem(
        self, ctx: ca.Contexto, quem: str, graus: Sequence[str], *, prefixo: str = ""
    ) -> dict[str, Any]:
        if quem == "terceiro":
            return _update(prefixo + ca.TEXTO_TERCEIRO, ca.PEND_EQUIPE, ctx)
        if quem == "fora_do_plano":
            return _update(prefixo + ca.TEXTO_DEPENDENTE_NAO_ATIVO, ca.PEND_EQUIPE, ctx)
        ctx = replace(ctx, quem=quem)
        bloqueio = _situacao_bloqueada(await self.d.elegibilidade())
        if bloqueio:
            prefixo = f"{prefixo}{bloqueio} "
        return await self._resolver(ctx, prefixo=prefixo)

    # --- resolver os procedimentos
    # -----------------------------------------------------------------------------
    async def _candidatos(self, termo: str) -> Resultado | None:
        busca = self.d.busca
        if busca is None or not busca.pronto:
            return None
        return await busca.buscar(termo)

    async def _resolver(self, ctx: ca.Contexto, *, prefixo: str) -> dict[str, Any]:
        if not ctx.termos:
            return _update(prefixo + ca.TEXTO_PEDE_PROCEDIMENTO, ca.PEND_FORMA, ctx)
        if len(ctx.termos) > 1:
            return await self._varios(ctx, prefixo=prefixo)
        termo = ctx.termos[0]
        resultado = await self._candidatos(termo)
        if resultado is None:
            # Sem catalogo: a busca por TEXTO da propria rota de cobertura (as opcoes saem das descricoes).
            return await self._consultar_um(ctx, termo, None, prefixo=prefixo, por_texto=True)
        if resultado.vazio:
            return await self._consultar_um(
                ctx, termo, None, prefixo=prefixo, por_texto=True, ultimo_recurso=True
            )
        if resultado.certo:
            c = resultado.candidatos[0]
            return await self._consultar_um(ctx, c.procedimento.codigo, c.procedimento.nome, prefixo=prefixo)
        opcoes = [(c.procedimento.codigo, c.procedimento.nome) for c in resultado.candidatos]
        if len(opcoes) == 1:
            return await self._consultar_um(ctx, opcoes[0][0], opcoes[0][1], prefixo=prefixo)
        return self._opcoes(ctx, termo, opcoes, prefixo=prefixo)

    def _opcoes(
        self, ctx: ca.Contexto, termo: str, opcoes: list[tuple[str, str]], *, prefixo: str
    ) -> dict[str, Any]:
        ctx = replace(ctx, opcoes=opcoes[:3], tentativas=ctx.tentativas + 1, tentados=[*ctx.tentados, termo])
        logger.info("helena_cobertura_opcoes", opcoes=len(ctx.opcoes), tentativa=ctx.tentativas)
        return _update(
            prefixo + ca.pergunta_das_opcoes(termo, [n for _, n in ctx.opcoes]), ca.PEND_OPCOES, ctx
        )

    def _falhou(self, ctx: ca.Contexto, termo: str, *, prefixo: str = "") -> dict[str, Any]:
        ctx = replace(ctx, tentativas=ctx.tentativas + 1, tentados=[*ctx.tentados, termo], opcoes=[])
        logger.info(
            "helena_cobertura_termo_nao_localizado",
            termo=ca.normalizar(termo)[:60],
            tentativa=ctx.tentativas,
        )
        if ctx.tentativas >= ca.MAX_TENTATIVAS:
            return _update(prefixo + ca.TEXTO_OFERTA_EQUIPE, ca.PEND_EQUIPE, ctx)
        return _update(prefixo + ca.pergunta_da_forma(termo), ca.PEND_FORMA, ctx)

    async def _ler(self, procedimento: str) -> tuple[Any, bool]:
        """(view, falhou). Falha/prazo da AMH = `falhou` (nunca "nao encontrei")."""
        try:
            view = await self.d.cobertura(procedimento)
        except PROGRAMMING_ERRORS:
            raise
        except EXTERNAL_DEPENDENCY_FAILURES:
            view = None
        return view, view is None

    async def _consultar_um(
        self,
        ctx: ca.Contexto,
        procedimento: str,
        nome: str | None,
        *,
        prefixo: str,
        por_texto: bool = False,
        ultimo_recurso: bool = False,
    ) -> dict[str, Any]:
        termo = ctx.termos[0] if ctx.termos else (nome or procedimento)
        consulta = procedimento if not por_texto else (ca.normalizar(procedimento)[:80] or procedimento)
        if por_texto and not 3 <= len(consulta) <= 80:
            return self._falhou(ctx, termo, prefixo=prefixo)
        view, falhou = await self._ler(consulta)
        if falhou:
            ctx = replace(ctx, opcoes=[])
            return _update(prefixo + ca.TEXTO_FALHA_DA_CONSULTA, ca.PEND_FALHA, ctx)
        itens = tuple(getattr(view, "itens", ()) or ())
        if not itens:
            return self._falhou(ctx, termo, prefixo=prefixo)
        item = ca.item_escolhido(view, nome)
        if item is None:
            descricoes = list(
                dict.fromkeys(" ".join(str(getattr(i, "descricao", "") or "").split()) for i in itens)
            )
            descricoes = [d for d in descricoes if d][:3]
            if len(descricoes) == 1:
                item = itens[0]
            elif len(descricoes) > 1 and ctx.tentativas < ca.MAX_TENTATIVAS:
                return self._opcoes(ctx, termo, [("", d) for d in descricoes], prefixo=prefixo)
            else:
                return self._falhou(ctx, termo, prefixo=prefixo)
        nome_final = nome or getattr(item, "descricao", None)
        if nome_final and ca.normalizar(str(nome_final)) in {ca.normalizar(t) for t in ctx.respondidos if t}:
            # E3: a MESMA pergunta de novo: nao repete o texto; reconhece e oferece o proximo passo.
            logger.info("helena_cobertura_repetida")
            return _update(prefixo + ca.TEXTO_REPETIDA.format(nome=nome_final), ca.PEND_EQUIPE, ctx)
        frase, oferece = ca.texto_de_um_resultado(view, item, nome_final)
        texto = ca.montar_resposta([frase], view=view, oferece_equipe=oferece, prefixo=prefixo)
        if (ctx.quem or "").startswith("dependente"):
            texto = f"{texto} {AVISO_DO_DEPENDENTE}"
        logger.info(
            "helena_cobertura_respondida",
            situacao=str(getattr(item, "situacao", "")),
            por_codigo=not por_texto,
            dependente=(ctx.quem or "").startswith("dependente"),
        )
        pendente = ca.PEND_EQUIPE if oferece else PEND_RESPONDIDO
        respondido = replace(
            ctx, opcoes=[], tentativas=0, respondidos=[*ctx.respondidos, str(nome_final or termo)][-4:]
        )
        return _update(
            texto,
            pendente,
            respondido,
            consulta_literais=literais_da_cobertura(view),
            resposta_com_dados_do_plano=True,
        )

    async def _varios(self, ctx: ca.Contexto, *, prefixo: str) -> dict[str, Any]:
        """B1: varios procedimentos na mesma mensagem -> uma linha para cada; os que nao deu para localizar
        pedem o nome do pedido medico (uma pergunta so', para o primeiro)."""
        frases: list[str] = []
        pendentes: list[str] = []
        ultima_view: Any = None
        oferece_algum = False
        literais: list[str] = []

        async def _um(termo: str) -> tuple[str, str | None, Any, bool]:
            resultado = await self._candidatos(termo)
            if resultado is not None and resultado.certo:
                c = resultado.candidatos[0]
                view, falhou = await self._ler(c.procedimento.codigo)
                nome: str | None = c.procedimento.nome
            else:
                view, falhou = await self._ler(ca.normalizar(termo)[:80])
                nome = None
            if falhou:
                return termo, None, None, True
            item = ca.item_escolhido(view, nome) if view is not None else None
            if item is None:
                return termo, None, view, False
            frase, oferece = ca.texto_de_um_resultado(view, item, nome or getattr(item, "descricao", None))
            return termo, frase + ("\x00" if oferece else ""), view, False

        resultados = await asyncio.gather(*(_um(t) for t in ctx.termos[: ca.MAX_PROCEDIMENTOS]))
        falhou_algum = False
        for termo, frase, view, falhou in resultados:
            if falhou:
                falhou_algum = True
                frases.append(f"{termo}: não consegui consultar agora.")
                continue
            if frase is None:
                pendentes.append(termo)
                frases.append(f"{termo}: não localizei pelo nome que você escreveu.")
                continue
            oferece_algum = oferece_algum or frase.endswith("\x00")
            frases.append(frase.rstrip("\x00"))
            ultima_view = view
            literais.extend(literais_da_cobertura(view))
        texto = ca.montar_resposta(frases, view=ultima_view, oferece_equipe=oferece_algum, prefixo=prefixo)
        if pendentes:
            texto += f' Para "{pendentes[0]}", me diga como está escrito no pedido médico.'
            novo = replace(ctx, termos=pendentes[:1], tentativas=1, tentados=[*ctx.tentados, *pendentes])
            return _update(texto, ca.PEND_FORMA, novo, consulta_literais=tuple(literais))
        if falhou_algum:
            return _update(
                texto + " Quer que eu tente de novo?", ca.PEND_FALHA, ctx, consulta_literais=tuple(literais)
            )
        return _update(
            texto,
            ca.PEND_EQUIPE if oferece_algum else PEND_RESPONDIDO,
            replace(ctx, respondidos=[*ctx.respondidos, *ctx.termos][-4:]),
            consulta_literais=tuple(literais),
            resposta_com_dados_do_plano=True,
        )


__all__ = ["PEND_RESPONDIDO", "Dependencias", "FluxoDeCobertura", "quem_pelo_texto"]

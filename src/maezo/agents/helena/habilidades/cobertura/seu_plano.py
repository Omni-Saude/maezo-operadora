"""Camada 2 "SEU PLANO" da cobertura: DESENHO e INTERFACE (DL-0167, HEL-3b). Nao ha' implementacao viva.

A camada 1 (`regra_geral`) responde a regra que vale para todos. A camada 2 responde o que o CADASTRO do
plano da pessoa verificada diz, so' por consulta deterministica as rotas JA' PINADAS do lago
(`config/integrations/amh/contracts.lock.json`). Cada fato entra na resposta como FATO DO CADASTRO, com a
rota registrada como `Fonte(tipo="lago", id=<rota>, versao=<contrato pinado>)`, e o texto passa no MESMO
verificador da camada 1 (`verificador.verificar`), tendo como trecho o texto deterministico dos fatos.

As rotas (rota do lago; contrato pinado; porta hoje em `ports/tina.py`; o que responde, so' fato do cadastro):

- `/elegibilidade`; `tina`; `TinaPort.get_elegibilidade`; plano ativo, segmentacao, tipo, acomodacao.
- `/carencias`; `tina`; `TinaPort.get_carencias`; carencias do proprio titular e a data de fim.
- `/requisicoes`; `tina`; `TinaPort.get_requisicoes`; situacao do pedido de autorizacao.
- `/cobertura`; `tina-cobertura` v1.9; `TinaCoberturaPort.get_cobertura`; situacao do procedimento no
  cadastro (Rol, extra, fora da segmentacao), diretriz de utilizacao, carencia do item.
- `/cpt`; `tina-cpt` v1.15; `TinaCptPort.get_cpt_vigente`; CPT vigente (sim/nao, quantas, maior data fim).
- `/procedimentos`; `tina-procedimentos` v1.14; `TinaProcedimentosPort`; catalogo (resolucao, HEL-2).
- `/rede-credenciada`; `tina-rede-credenciada` v1.8; NENHUMA porta (pendente, ver o item (b)); prestadores.

Como entra na resposta: primeiro a regra geral (camada 1, quando ha'), depois "No cadastro do seu plano:" e
os fatos, e a formula fixa no fim. Nunca: "esta coberto", "vai ser autorizado" (o cadastro PREVE, nao
decide); `coberto=None`/`indeterminado` = "o cadastro nao informa" e a oferta de uma pessoa; `itens` vazio
NAO e' "nao cobre"; `truncada` = pergunta de esclarecimento (HEL-2), nunca a escolha de um item.

Pedidos a T-MZO-A (dona de `ports/tina.py` e da composicao), situacao:
(a) ATENDIDO: a fonte injetada no receptor (`platform/webhooks/whatsapp/helena_consultas.ConsultasPlanoAmh`, a
mesma `consultas_plano` que o despachante passa ao grafo) implementa de novo `FonteDeCobertura` e `FonteDeCpt`
(so' leitura pela rota pinada; nada da cobertura assistida do DL-0154 volta). (c) ATENDIDO: a mesma fonte
implementa `FonteDoCatalogoDeProcedimentos` (`maezo.contratos.fatos_do_plano`), que entrega o catalogo
inteiro de uma foto como `ProcedimentoCatalogo` para `procedimento.resolver`. (b) PENDENTE: o contrato
`tina-rede-credenciada` esta' pinado (bloco `manifest_v1_8`), mas o tipo de valor da porta traria `nome`,
`endereco` e `telefone` do estabelecimento, e a cerca de PHI dos ports (`tests/unit/ports/test_ports_purity`)
so' aceita slot assim com excecao nominal decidida pelo dono e DPO; sem essa decisao nao ha' porta.
Falta tambem a camada 2 propriamente dita (a implementacao de `ConsultaDoSeuPlano` sobre essas fontes) e a
composicao dela no `graph.build`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

#: As rotas que a camada 2 pode consultar (e nenhuma outra).
Rota = Literal["/elegibilidade", "/carencias", "/requisicoes", "/cobertura", "/cpt", "/rede-credenciada"]
ROTAS: Final[tuple[Rota, ...]] = (
    "/elegibilidade",
    "/carencias",
    "/requisicoes",
    "/cobertura",
    "/cpt",
    "/rede-credenciada",
)
#: A pergunta -> as rotas que a respondem (a camada 2 nunca consulta rota que a pergunta nao pede).
ROTAS_POR_ASSUNTO: Final[dict[str, tuple[Rota, ...]]] = {
    "carencia": ("/carencias", "/cobertura"),
    "cobertura_contratual": ("/cobertura", "/elegibilidade"),
    "cpt": ("/cpt",),
    "rede": ("/rede-credenciada",),
    "situacao_do_pedido": ("/requisicoes",),
}


@dataclass(frozen=True, slots=True)
class FatoDoCadastro:
    """Um fato de UMA rota, ja' em texto deterministico (o trecho que o verificador confere)."""

    rota: Rota
    versao_do_contrato: str
    texto: str


@runtime_checkable
class ConsultaDoSeuPlano(Protocol):
    """A interface da camada 2: os fatos do cadastro do PROPRIO sujeito verificado para um assunto.

    Nunca levanta; falha ou fonte ausente = `()` (a camada nao fala e a habilidade segue em falha fechada).
    `procedimento` e' o codigo resolvido pela HEL-2 (nunca o termo digitado pela pessoa)."""

    async def fatos(
        self, portable_subject_ref: str, assunto: str, *, procedimento: str | None
    ) -> tuple[FatoDoCadastro, ...]: ...


__all__ = ["ROTAS", "ROTAS_POR_ASSUNTO", "ConsultaDoSeuPlano", "FatoDoCadastro", "Rota"]

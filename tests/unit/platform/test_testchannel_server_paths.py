"""O nome de pagina da rota `/p/` do Canal de Teste e' validado ANTES de virar caminho.

O controle antigo era `os.path.basename(pedido)`: barrava travessia por EFEITO COLATERAL do
formato do pedido — a barra que faz `../` sumir e' a mesma que faz `pagina.html/extra` virar
`extra`. O CodeQL nao reconhece esse padrao como saneador e as regras `py/path-injection`
(alertas #20/#3/#2) ficaram abertas sobre a linha do `resolve` em `_servir_pagina`. A regra
agora e' explicita e fechada (`_validar_nome_pagina`, lista de caracteres aceitos), e a cerca
continua DUPLA: quem passa daqui ainda encontra `resolve` + comparacao de pai, que e' o que
fecha link simbolico. Dois controles porque um so' e' um controle — e o primeiro agora e'
reconhecido como controle, nao como acaso do formato do input.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

PAGINA_EXISTENTE = "escalonamento.html"


@pytest.fixture(scope="module")
def canal() -> Any:
    """O modulo do canal, sem mexer em ambiente: nada aqui depende de variavel nenhuma."""
    return importlib.import_module("maezo.platform.testchannel.server")


# ------------------------------------------------------- a funcao de validacao


@pytest.mark.parametrize(
    "pedido",
    [
        "../../etc/passwd",  # a travessia classica
        "a/b",  # um separador basta: nome e' UM componente
        "..",  # so' o pai
        ".",  # so' o diretorio
        "pagina.html/extra",  # o caso que o `basename` antigo ACEITAVA por efeito colateral
        "%2e%2e",  # travessia codificada: o `%` nao esta' na lista
        "escalonamento.html?x",  # a query e' cortada em `_servir_pagina`; se um dia nao for, aqui fecha
        "escalonamento.html/extra/",  # barra no fim tambem e' separador
        "a\\b.html",  # barra invertida: separador em Windows
        "",  # vazio
        " ",  # espaco
    ],
)
def test_pedido_que_nao_e_um_componente_e_recusado(canal: Any, pedido: str) -> None:
    assert canal._validar_nome_pagina(pedido) is None


@pytest.mark.parametrize(
    "pedido",
    [
        PAGINA_EXISTENTE,
        "resultados.html",
        "autorizacao.html",
        "estilos.css",  # as extensoes da lista fechada continuam nomes validos
        "app.js",
        "exemplo.html",
        "a" * 200,  # nome longo e' recusado DEPOIS, por nao existir — nao pela regra
    ],
)
def test_nome_de_um_componente_so_passa_pela_regra(canal: Any, pedido: str) -> None:
    """A regra recusa FORMA, nao existencia: um nome de componente unico passa e quem decide se
    a pagina existe e' o disco (`alvo.is_file()`), como ja' era.
    """
    assert canal._validar_nome_pagina(pedido) == pedido


# -------------------------------------------- a rota inteira, sem subir socket


class _PaginaServida:
    """Exercita `_servir_pagina` sem subir socket: captura status e bytes (mesma tecnica de
    `test_canal_portal_origin.py`, que prova a injecao de origem do lado de la' da cerca).
    """

    def __init__(self, servidor: Any, arquivo: str) -> None:
        self.path = f"/p/{arquivo}"
        self.status: int | None = None
        self.corpo = b""
        self.wfile = self

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, nome: str, valor: str) -> None:
        return None

    def end_headers(self) -> None:
        return None

    def write(self, dados: bytes) -> None:
        self.corpo += dados

    def _responder_json(self, status: int, corpo: dict[str, object]) -> None:
        self.status = status
        self.corpo = json.dumps(corpo).encode()

    def executar(self, servidor: Any) -> None:
        servidor.Handler._servir_pagina(self)


@pytest.mark.parametrize(
    "pedido",
    ["../../etc/passwd", "a/b", "%2e%2e", "", "pagina.html/extra"],
)
def test_rota_responde_404_para_pedido_que_nao_vira_caminho(canal: Any, pedido: str) -> None:
    """O pedido hostil e' 404 SEM que `PAGINAS / pedido` seja avaliado uma unica vez — e o corpo
    aponta as paginas que existem, que e' o que o 404 desta rota sempre fez.
    """
    servida = _PaginaServida(canal, pedido)
    servida.executar(canal)

    assert servida.status == 404
    resposta = json.loads(servida.corpo)
    assert resposta["disponiveis"] == canal._paginas_disponiveis()


def test_rota_continua_servindo_a_pagina_que_existe(canal: Any) -> None:
    """A cerca so' RECUSA mais: o que era servido continua servido, com o marcador do portal
    substituido (a pagina nao pode sair com `__PORTAL_PUBLIC_ORIGIN__` cru).
    """
    servida = _PaginaServida(canal, PAGINA_EXISTENTE)
    servida.executar(canal)

    assert servida.status == 200
    corpo = servida.corpo.decode("utf-8")
    assert canal.MARCA_PORTAL not in corpo

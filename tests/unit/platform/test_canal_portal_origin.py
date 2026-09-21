"""A origem do Portal Maezo entregue pelo Canal de Teste: a cerca de dev e a injecao na pagina.

O teste que importa mais e' o segundo: `PORTAL_PUBLIC_ORIGIN` posta em QUALQUER ambiente que nao
seja `dev` tem de ser RECUSADA pelo servidor, com motivo dito no log de boot. A pagina do canal e'
demonstracao declarada, sem autenticacao propria (ver `src/maezo/platform/testchannel/README.md`);
liga-la ao portal em staging, producao ou DR a transformaria em porta de entrada do portal, e essa
decisao nao pode depender de alguem lembrar. E' a cerca 3.3 do mandato de 21/09/2026.

Os demais cobrem o que a cerca sozinha nao cobre:

  * a origem, mesmo em dev, tem de ser `https://<host>` puro — a MESMA regra que
    `maezo.portal.api.config.PortalSettings` aplica ao proprio `public_origin`. Duas regras
    diferentes para a mesma coisa e' o jeito conhecido de elas discordarem em silencio;
  * a substituicao na pagina servida acontece de verdade, e o `Content-Length` e' recalculado
    DEPOIS dela (um cabecalho calculado antes serve resposta truncada, e o sintoma no navegador e'
    um erro de sintaxe no fim do script);
  * `escalonamento.html` continua declarando o marcador. Sem esta asserção, um refactor que
    apagasse o marcador deixaria a pagina permanentemente em "portal nao configurado" — verde no
    CI, quebrada na tela.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

ORIGEM_DEV = "https://portal-maezo-dev.austa.com.br"
MARCADOR = "__PORTAL_PUBLIC_ORIGIN__"
PAGINA = "escalonamento.html"


def _canal(monkeypatch: pytest.MonkeyPatch, **ambiente: str) -> Any:
    """Recarrega o modulo do canal com o ambiente pedido (as constantes sao do import)."""
    for nome in ("MAEZO_ENV", "PORTAL_PUBLIC_ORIGIN"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    return importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))


@pytest.fixture(autouse=True)
def _devolver_modulo_ao_estado_limpo():
    """Mesma limpeza de `test_canal_simular.py`: sem ela um teste futuro herdaria a origem do
    portal LIGADA por efeito colateral deste arquivo — acoplamento que so' aparece meses depois.
    """
    yield
    for nome in ("MAEZO_ENV", "PORTAL_PUBLIC_ORIGIN"):
        os.environ.pop(nome, None)
    importlib.reload(importlib.import_module("maezo.platform.testchannel.server"))


class _PaginaServida:
    """Exercita `_servir_pagina` sem subir socket: captura status, cabecalhos e bytes."""

    def __init__(self, servidor: Any, arquivo: str) -> None:
        self._servidor = servidor
        self.path = f"/p/{arquivo}"
        self.status: int | None = None
        self.cabecalhos: dict[str, str] = {}
        self.corpo = b""
        self.wfile = self

    # --- superficie que `_servir_pagina` usa do BaseHTTPRequestHandler ---
    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, nome: str, valor: str) -> None:
        self.cabecalhos[nome] = valor

    def end_headers(self) -> None:
        return None

    def write(self, dados: bytes) -> None:
        self.corpo += dados

    def _responder_json(self, status: int, corpo: dict[str, object]) -> None:
        self.status = status
        self.corpo = json.dumps(corpo).encode()

    def executar(self) -> None:
        self._servidor.Handler._servir_pagina(self)


# ---------------------------------------------------------------- a cerca de dev


def test_em_dev_a_origem_vale(monkeypatch: pytest.MonkeyPatch) -> None:
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    assert canal.PORTAL_ORIGIN == ORIGEM_DEV
    assert canal.PORTAL_MOTIVO == "ligada"


@pytest.mark.parametrize(
    "ambiente",
    ["staging", "hml", "prod", "producao", "dr", "dev-sa-east-1", "development", "prod-dev"],
)
def test_fora_de_dev_a_origem_e_recusada(monkeypatch: pytest.MonkeyPatch, ambiente: str) -> None:
    """A cerca 3.3. `dev-sa-east-1` tambem e' recusado de proposito: o valor que a task
    definition entrega em `MAEZO_ENV` e' `local.env` (`dev`), e aceitar prefixos abriria
    `dev-us-east-1` amanha sem ninguem decidir isso — o mesmo raciocinio de
    `check_canal_simular.py::AMBIENTE_PERMITIDO`, que e' um caminho e nao um padrao.
    """
    canal = _canal(monkeypatch, MAEZO_ENV=ambiente, PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    assert canal.PORTAL_ORIGIN == ""
    assert canal.PORTAL_MOTIVO.startswith("RECUSADA")
    assert ambiente in canal.PORTAL_MOTIVO


def test_sem_ambiente_declarado_a_origem_e_recusada(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ausencia de `MAEZO_ENV` e' ambiente DESCONHECIDO, e o desconhecido cai do lado que falha
    seguro. E' o caso da maquina de quem roda `python -m maezo.platform.testchannel` com a
    variavel do portal herdada do shell.
    """
    canal = _canal(monkeypatch, PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    assert canal.PORTAL_ORIGIN == ""
    assert "ausente" in canal.PORTAL_MOTIVO


def test_sem_variavel_o_painel_fica_nao_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    canal = _canal(monkeypatch, MAEZO_ENV="dev")
    assert canal.PORTAL_ORIGIN == ""
    assert canal.PORTAL_MOTIVO == "nao configurada"


# --------------------------------------------------- a forma exigida da origem


@pytest.mark.parametrize(
    "origem",
    [
        "http://portal-maezo-dev.austa.com.br",  # sem TLS
        "https://portal-maezo-dev.austa.com.br/api",  # com caminho
        "https://portal-maezo-dev.austa.com.br/",  # barra final ja' e' caminho
        "https://portal-maezo-dev.austa.com.br:8443",  # porta diferente de 443
        "https://user:senha@portal-maezo-dev.austa.com.br",  # credencial embutida
        "https://portal-maezo-dev.austa.com.br?x=1",  # query
        "https://portal-maezo-dev.austa.com.br#f",  # fragmento
        "https://portal-maezo-dev.austa.com.br\\@mau.example",  # barra invertida
        "https://portal maezo.austa.com.br",  # espaco no meio
        "https://portal-maezo-dev.austa.com.br:porta",  # porta nao numerica
        "https://",  # sem host
        "portal-maezo-dev.austa.com.br",  # sem esquema
    ],
)
def test_origem_malformada_e_recusada_mesmo_em_dev(monkeypatch: pytest.MonkeyPatch, origem: str) -> None:
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=origem)
    assert canal.PORTAL_ORIGIN == ""
    assert canal.PORTAL_MOTIVO.startswith("RECUSADA")


def test_porta_443_explicita_e_aceita(monkeypatch: pytest.MonkeyPatch) -> None:
    """443 explicita e' a MESMA origem; o portal a aceita, e recusa-la aqui criaria a segunda
    regra que este arquivo existe para evitar."""
    origem = "https://portal-maezo-dev.austa.com.br:443"
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=origem)
    assert origem == canal.PORTAL_ORIGIN


# ------------------------------------------------------- a injecao na pagina


def test_pagina_servida_em_dev_recebe_a_origem(monkeypatch: pytest.MonkeyPatch) -> None:
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    servida = _PaginaServida(canal, PAGINA)
    servida.executar()

    assert servida.status == 200
    corpo = servida.corpo.decode("utf-8")
    assert MARCADOR not in corpo
    assert f'window.PORTAL_PUBLIC_ORIGIN = "{ORIGEM_DEV}"' in corpo
    # O cabecalho e' calculado DEPOIS da troca: divergir aqui serve resposta truncada.
    assert servida.cabecalhos["Content-Length"] == str(len(servida.corpo))


def test_pagina_servida_fora_de_dev_recebe_string_vazia(monkeypatch: pytest.MonkeyPatch) -> None:
    canal = _canal(monkeypatch, MAEZO_ENV="prod", PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    servida = _PaginaServida(canal, PAGINA)
    servida.executar()

    corpo = servida.corpo.decode("utf-8")
    assert MARCADOR not in corpo
    assert 'window.PORTAL_PUBLIC_ORIGIN = ""' in corpo
    assert ORIGEM_DEV not in corpo
    assert servida.cabecalhos["Content-Length"] == str(len(servida.corpo))


def test_pagina_declara_o_marcador_uma_vez_e_nao_crava_a_origem() -> None:
    """Cerca de regressao da propria injecao: sem o marcador, o painel do portal fica
    permanentemente em "nao configurado" e nada no CI reclama."""
    caminho = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "maezo"
        / "platform"
        / "testchannel"
        / "paginas"
        / PAGINA
    )
    texto = caminho.read_text(encoding="utf-8")
    assert texto.count(f'window.PORTAL_PUBLIC_ORIGIN = "{MARCADOR}"') == 1
    assert "portal-maezo-dev.austa.com.br" not in texto

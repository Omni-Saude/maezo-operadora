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


@pytest.mark.parametrize(
    "origem",
    [
        'https://a"b.austa.com.br',  # aspa: FECHA o literal JavaScript da pagina
        "https://a'b.austa.com.br",  # apostrofo, pelo mesmo motivo
        "https://a<b.austa.com.br",  # `<` abre tag e poderia fechar o `<script>`
        "https://-portal.austa.com.br",  # hifen no inicio do rotulo
        "https://portal-.austa.com.br",  # hifen no fim do rotulo
        "https://portal..austa.com.br",  # rotulo vazio
        "https://PORTAL.austa.com.br",  # netloc que nao e' o hostname canonico
        "https://[::1]",  # IPv6 literal
        "https://portal_maezo.austa.com.br",  # `_` nao e' caractere de rotulo DNS
        "https://" + "a" * 64 + ".austa.com.br",  # rotulo com mais de 63 caracteres
        "https://" + ".".join(["abcdefghij"] * 26),  # mais de 253 caracteres no total
    ],
)
def test_hostname_que_nao_e_rotulo_dns_e_recusado(monkeypatch: pytest.MonkeyPatch, origem: str) -> None:
    """A cerca acrescentada em 21/09/2026, e o motivo dela em um caso: a regra do portal, copiada
    linha por linha, ACEITA `https://a"b.austa.com.br` — `urlsplit` poe isso tudo no netloc, sem
    caminho, query, fragmento nem porta. No portal isso e' uma origem que nunca resolve; aqui o
    valor era concatenado dentro de um literal JavaScript, e a aspa fechava o literal no meio do
    script. A cerca ficou nos dois lugares: rotulo DNS aqui, `json.dumps` na injecao.

    A diferenca em relacao ao portal so' RECUSA mais, nunca aceita mais — e' essa direcao que
    mantem "uma regra so'" verdadeira na pratica.
    """
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=origem)
    assert canal.PORTAL_ORIGIN == ""
    assert canal.PORTAL_MOTIVO.startswith("RECUSADA")


def test_hostname_de_rotulo_unico_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    """O rotulo DNS unico continua valendo: o portal aceita, e recusar aqui criaria a segunda
    regra que este arquivo existe para evitar."""
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN="https://portal")
    assert canal.PORTAL_ORIGIN == "https://portal"


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


def test_a_origem_entra_na_pagina_serializada_e_nao_concatenada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SEGUNDA cerca, testada sozinha. `PORTAL_ORIGIN` e' forcada aqui para um valor que a
    validacao recusaria — de proposito: o ponto deste teste e' que a INJECAO nao depende da
    validacao. Se amanha alguem alargar `_origem_https_pura`, a pagina continua sintaticamente
    intacta, porque o que entra nela e' `json.dumps` de uma string, nao concatenacao.
    """
    hostil = 'https://a"b\\c</script>.example'
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    monkeypatch.setattr(canal, "PORTAL_ORIGIN", hostil)

    servida = _PaginaServida(canal, PAGINA)
    servida.executar()
    corpo = servida.corpo.decode("utf-8")

    assert MARCADOR not in corpo
    # A aspa e a barra invertida chegam ESCAPADAS; o literal nao fecha no meio do script.
    assert f"window.PORTAL_PUBLIC_ORIGIN = {json.dumps(hostil)}" in corpo
    assert 'https://a"b' not in corpo  # a forma CRUA nao aparece em lugar nenhum
    # E o literal e' JSON valido, que e' a prova de que ele fecha onde deve.
    literal = corpo.split("window.PORTAL_PUBLIC_ORIGIN = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(literal) == hostil
    assert servida.cabecalhos["Content-Length"] == str(len(servida.corpo))


def test_marcador_fora_de_literal_cai_para_nao_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falha segura da injecao: uma pagina futura que declarasse o marcador FORA de aspas nao tem
    como ser serializada, e o que sobrava antes era o marcador CRU — string truthy, que faria a
    tela sair chamando `https://__PORTAL_PUBLIC_ORIGIN__/api/v1/portal/session`. Agora ele e'
    apagado, e a pagina cai em "portal nao configurado", que e' um estado que ela trata.
    """
    canal = _canal(monkeypatch, MAEZO_ENV="dev", PORTAL_PUBLIC_ORIGIN=ORIGEM_DEV)
    solta = Path(canal.PAGINAS) / "_marcador_solto_teste.html"
    solta.write_text(f"<p>{MARCADOR}</p>", encoding="utf-8")
    try:
        servida = _PaginaServida(canal, solta.name)
        servida.executar()
        assert servida.corpo.decode("utf-8") == "<p></p>"
    finally:
        solta.unlink()


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

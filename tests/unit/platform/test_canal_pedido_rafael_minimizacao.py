"""DL-0190: o formulario livre "Pedir ao Rafael" do Canal de Teste nao envia CID-10 e so' aceita
guia com o marcador fechado de caso ficticio (`FICT-`); a pagina de autorizacao nao promete
"zero egresso", nao declara fonte de producao e so' traz casos sinteticos de padrao fechado.

Testes de texto sobre o HTML servido: o canal e' uma pagina estatica, e o que se trava aqui e'
o que o navegador recebe. Os casos da pagina sao lidos so' para conferir o padrao sintetico.
"""

from __future__ import annotations

import importlib
import json
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.testchannel.server import HTML, PAGINAS


def _funcao_js(nome: str) -> str:
    inicio = HTML.index(f"function {nome}(")
    fim = HTML.index("\n}", inicio)
    return HTML[inicio:fim]


def test_o_formulario_nao_pede_nem_envia_cid() -> None:
    assert "ag_cid" not in HTML
    assert not re.search(r"\bcid10\s*:", _funcao_js("pedirAoRafael"))
    assert "cid" not in _funcao_js("exemploFicticio").lower()


def test_o_marcador_de_caso_ficticio_e_fechado() -> None:
    assert 'const GUIA_FICTICIA="FICT-";' in HTML


def test_guia_sem_marcador_e_recusada_antes_do_envio() -> None:
    corpo = _funcao_js("pedirAoRafael")
    guarda = corpo.index("startsWith(GUIA_FICTICIA)")
    assert guarda < corpo.index("fetch(")
    assert "return;" in corpo[guarda : corpo.index("fetch(")]
    assert "numero_guia_tiss:guia" in corpo


def test_o_exemplo_ficticio_carrega_o_marcador() -> None:
    assert "GUIA_FICTICIA+String(n)" in _funcao_js("exemploFicticio")


def test_a_pagina_de_autorizacao_nao_promete_zero_egresso() -> None:
    texto = (PAGINAS / "autorizacao.html").read_text(encoding="utf-8")
    assert "(zero egresso)" not in texto
    assert "sintetico, zero egresso" not in texto
    assert "Nao ha garantia de zero egresso" in texto
    assert isinstance(PAGINAS, Path)


# --- autorizacao.html: so' casos sinteticos (decisao do dono, DL-0190) -----------------------

#: Padrao FECHADO dos identificadores sinteticos da pagina. Qualquer outro formato reprova.
_PADRAO_SINTETICO: dict[str, re.Pattern[str]] = {
    "guia": re.compile(r"FICT-AUT-\d{4}"),
    "prestador": re.compile(r"FICT-PRE-\d{2}"),
    "pseudo": re.compile(r"FICT-BNF-\d{4}"),
}

#: Declaracoes de fonte de producao que nao podem voltar a pagina (comparacao sem caixa).
_FONTE_DE_PRODUCAO: tuple[str, ...] = (
    "lago de dados de producao",
    "lago de dados",
    "do lago",
    "amh_omni",
    "athena",
    "sao reais",
    "caso real",
    "casos reais",
    "dado real de beneficiario",
)


def _texto_autorizacao() -> str:
    return (PAGINAS / "autorizacao.html").read_text(encoding="utf-8")


def _json_da_variavel(nome: str) -> Any:
    m = re.search(rf"^\s*var {nome}\s*=\s*(.*?);\s*$", _texto_autorizacao(), re.MULTILINE)
    assert m, f"var {nome} sumiu da pagina"
    return json.loads(m.group(1))


def test_a_pagina_nao_declara_fonte_de_producao() -> None:
    texto = _texto_autorizacao().lower()
    achados = [t for t in _FONTE_DE_PRODUCAO if t in texto]
    assert not achados, f"a pagina voltou a declarar fonte de producao: {achados}"


def test_os_casos_so_tem_identificadores_do_padrao_sintetico() -> None:
    casos = _json_da_variavel("CASOS")
    assert casos, "a pagina precisa de pelo menos um caso sintetico"
    for caso in casos:
        for campo, padrao in _PADRAO_SINTETICO.items():
            assert padrao.fullmatch(str(caso[campo])), f"{campo} fora do padrao sintetico"


def test_nenhum_identificador_fora_do_padrao_em_toda_a_pagina() -> None:
    """Um `FICT-` so' aparece no formato fechado; e nenhum campo de caso fica fora da lista."""
    casos = _json_da_variavel("CASOS")
    for caso in casos:
        assert set(caso) >= set(_PADRAO_SINTETICO)
    texto = _texto_autorizacao()
    for ident in re.findall(r"FICT-[A-Z]+-\d+", texto):
        assert any(p.fullmatch(ident) for p in _PADRAO_SINTETICO.values()), (
            "identificador FICT- fora do padrao"
        )


def test_a_fonte_declarada_e_sintetica() -> None:
    meta = _json_da_variavel("META")
    assert meta["fonte"] == "sintetico (gerado para o Canal de Teste)"


# --- servidor: o proxy `/agente` aplica a mesma regra (DL-0190) ------------------------------


class _AgenteFalso(BaseHTTPRequestHandler):
    corpos: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        self.corpos.append(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)))
        dados = b'{"resultado": {}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def _subir(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


@pytest.fixture
def canal_com_agente(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    modulo = importlib.import_module("maezo.platform.testchannel.server")
    _AgenteFalso.corpos = []
    agente = _subir(_AgenteFalso)
    monkeypatch.setattr(modulo, "AGENTE", f"http://127.0.0.1:{agente.server_address[1]}")
    servidor = _subir(modulo.Handler)
    try:
        yield f"http://127.0.0.1:{servidor.server_address[1]}"
    finally:
        servidor.shutdown()
        agente.shutdown()


def _post(url: str, corpo: bytes) -> tuple[int, bytes]:
    pedido = urllib.request.Request(
        url, data=corpo, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


_GUIA_SEM_MARCADOR = "SINTETICA-SEM-MARCADOR-0001"
_CID_SINTETICO = "Q99.9-SINTETICO"


@pytest.mark.parametrize(
    ("caminho", "corpo"),
    [
        ("/agente/v1/autorizacoes", {"numero_guia_tiss": _GUIA_SEM_MARCADOR}),
        ("/agente/v1/autorizacoes", {"tenant_id": "t"}),
        ("/agente/v1/autorizacoes", {"numero_guia_tiss": "FICT-AUT-0001", "cid10": _CID_SINTETICO}),
        ("/agente/v1/autorizacoes", {"numero_guia_tiss": 12345}),
        ("/agente/v1//autorizacoes", {"numero_guia_tiss": _GUIA_SEM_MARCADOR}),
        ("/agente/v1/%61utorizacoes", {"numero_guia_tiss": _GUIA_SEM_MARCADOR}),
        ("/agente/v1/outra-rota", {"numero_guia_tiss": _GUIA_SEM_MARCADOR}),
        ("/agente/v1/outra-rota", {"cid10": _CID_SINTETICO}),
    ],
    ids=[
        "sem-marcador",
        "sem-guia",
        "com-cid",
        "guia-nao-texto",
        "barra-dupla",
        "percent",
        "outra-rota",
        "cid-outra-rota",
    ],
)
def test_o_proxy_recusa_sem_ecoar_e_sem_repassar(
    canal_com_agente: str, caminho: str, corpo: dict[str, Any]
) -> None:
    status, resposta = _post(canal_com_agente + caminho, json.dumps(corpo).encode())

    assert status == 422
    assert _AgenteFalso.corpos == [], "pedido recusado nao pode chegar ao agente"
    texto = resposta.decode("utf-8")
    assert _GUIA_SEM_MARCADOR not in texto and _CID_SINTETICO not in texto and "12345" not in texto


def test_o_proxy_recusa_corpo_que_nao_e_objeto_na_rota(canal_com_agente: str) -> None:
    status, _ = _post(canal_com_agente + "/agente/v1/autorizacoes", b"nao-e-json")
    assert status == 422 and _AgenteFalso.corpos == []


def test_o_proxy_repassa_caso_ficticio_sem_cid(canal_com_agente: str) -> None:
    corpo = json.dumps({"tenant_id": "t", "numero_guia_tiss": "FICT-AUT-0001"}).encode()
    status, _ = _post(canal_com_agente + "/agente/v1/autorizacoes", corpo)

    assert status == 200
    assert _AgenteFalso.corpos == [corpo], "o corpo repassado e' o recebido, byte a byte"


def test_a_pagina_de_exemplo_manda_caso_ficticio_sem_cid() -> None:
    texto = (PAGINAS / "exemplo.html").read_text(encoding="utf-8")
    assert "cid10" not in texto
    assert 'numero_guia_tiss:"FICT-"+' in texto

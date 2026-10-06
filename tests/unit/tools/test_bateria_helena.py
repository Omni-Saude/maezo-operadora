"""`tools/scripts/bateria_helena.py`: a bateria do dev com as licoes de 30/09 e 01/10/2026 como CODIGO.

O que este arquivo prova, contra um canal e um motor SIMULADOS (nenhuma rede):
  - o cancelamento so' acontece pela chave exata e so' quando a consulta devolve UMA instancia — a
    licao do filtro `processInstanceBusinessKey` (ignorado nas instancias vivas: devolveu as 77);
  - nenhuma consulta usa o filtro errado;
  - so' a faixa sintetica 5511900000xxx passa, e o ritmo respeita o limite por conversa;
  - o roteamento serializado pelo motor (`roteamento`, HashMap Java em base64) e' decodificado;
  - o corpus de casos esta' completo e cada caso declara a rota esperada.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_SCRIPT = _RAIZ / "tools" / "scripts" / "bateria_helena.py"


def _modulo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bateria_helena", _SCRIPT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


B = _modulo()


def _hashmap_java(pares: dict[str, str]) -> str:
    """Um `HashMap<String,String>` serializado como o motor o grava, em base64 (estrutura real:
    cabecalho de classe, `loadFactor`/`threshold`, depois os pares como TC_STRING)."""
    cabecalho = (
        b"\xac\xed\x00\x05sr\x00\x11java.util.HashMap\x05\x07\xda\xc1\xc3\x16`\xd1\x03\x00\x02F\x00\nloadFactorI\x00"
        b"\tthresholdxp?@\x00\x00\x00\x00\x00\x0cw\x08\x00\x00\x00\x10\x00\x00\x00\x03"
    )
    corpo = b""
    for chave, valor in pares.items():
        for texto in (chave, valor):
            dados = texto.encode("utf-8")
            corpo += b"t" + len(dados).to_bytes(2, "big") + dados
    return base64.b64encode(cabecalho + corpo + b"x").decode()


ROTEAMENTO_P1 = _hashmap_java(
    {"prioridade": "P1", "sla_resolucao": "PT30M", "sla_ack": "PT5M", "grupo_atendimento": "plantao-clinico"}
)


def test_o_roteamento_serializado_pelo_motor_e_decodificado() -> None:
    assert B.decodifica_roteamento(ROTEAMENTO_P1) == {
        "prioridade": "P1",
        "sla_resolucao": "PT30M",
        "sla_ack": "PT5M",
        "grupo_atendimento": "plantao-clinico",
    }


@pytest.mark.parametrize("lixo", ["", "nao-e-base64!!", base64.b64encode(b"qualquer coisa").decode()])
def test_roteamento_ilegivel_devolve_vazio_e_nao_levanta(lixo: str) -> None:
    assert B.decodifica_roteamento(lixo) == {}


class Mundo:
    """Canal + motor simulados. Registra toda chamada HTTP e guarda as instancias vivas."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str]] = []
        self.vivas: list[dict[str, Any]] = []
        self.abrir_ao_enviar: dict[str, Any] | None = None
        #: Simula o filtro ERRADO (`processInstanceBusinessKey`), que devolve tudo.
        self.ignora_filtro = False
        self.abrir_clinico_ao_enviar = False
        self.respostas: dict[str, str] = {}
        self.relogio = 0.0
        self.dormidos: list[float] = []

    def agora(self) -> float:
        return self.relogio

    def dormir(self, segundos: float) -> None:
        self.dormidos.append(segundos)
        self.relogio += segundos

    def http(self, metodo: str, url: str, corpo: Any, timeout: float) -> tuple[int, Any]:
        self.chamadas.append((metodo, url))
        if url.endswith("/receptor/simular"):
            conv = f"wa:amh:hk1_{corpo['telefone'][-3:]}"
            if self.abrir_ao_enviar is not None:
                self.vivas = [{"id": "inst-1", "businessKey": f"ESC-amh-{conv}"}]
            if self.abrir_clinico_ao_enviar:
                self.vivas.append({"id": "inst-clin", "businessKey": f"ESC-amh-{conv}-clin"})
            return 200, {"resposta": self.respostas.get(corpo["texto"], "ok"), "conversation_id": conv}
        if "/process-instance?" in url and metodo == "GET":
            if self.ignora_filtro:
                return 200, list(self.vivas)
            chave = urllib.parse.unquote(url.split("businessKey=", 1)[1])
            return 200, [v for v in self.vivas if v["businessKey"] == chave]
        if "/variable-instance?" in url:
            return 200, [
                {"name": "roteamento", "value": ROTEAMENTO_P1},
                {"name": "motivo_categoria", "value": "red_flag_clinico"},
                {"name": "severidade", "value": "grave"},
                {"name": "dmn_decision_ref", "value": "triage_redflag_adult#triage_redflag_adult:2:abc"},
            ]
        if metodo == "DELETE":
            iid = url.split("/process-instance/", 1)[1].split("?", 1)[0]
            self.vivas = [v for v in self.vivas if v["id"] != iid]
            return 204, None
        raise AssertionError(f"chamada inesperada: {metodo} {url}")


def _bateria(mundo: Mundo) -> Any:
    return B.Bateria(
        canal="http://canal", motor="http://motor", http=mundo.http, dormir=mundo.dormir, agora=mundo.agora
    )


# ------------------------------------------------------------------ cancelamento
def test_so_cancela_quando_ha_exatamente_uma_instancia_com_a_chave_esperada() -> None:
    mundo = Mundo()
    mundo.vivas = [{"id": "inst-1", "businessKey": "ESC-amh-wa:amh:hk1_x"}]

    resultado = _bateria(mundo).cancelar("wa:amh:hk1_x")

    assert resultado == "cancelado_204"
    assert (
        "DELETE",
        "http://motor/process-instance/inst-1?skipCustomListeners=true&skipIoMappings=true",
    ) in mundo.chamadas


def test_recusa_cancelar_quando_a_consulta_devolve_mais_de_uma_instancia() -> None:
    """O defeito do filtro errado: a consulta devolve TODAS as instancias (77 em dev)."""
    mundo = Mundo()
    mundo.ignora_filtro = True
    mundo.vivas = [{"id": f"i{n}", "businessKey": f"ESC-amh-outra-{n}"} for n in range(77)]

    with pytest.raises(B.RecusaDeSegurancaError):
        _bateria(mundo).cancelar("wa:amh:hk1_x")

    assert not [c for c in mundo.chamadas if c[0] == "DELETE"], "nada pode ter sido apagado"


def test_recusa_cancelar_instancia_unica_de_outra_chave() -> None:
    mundo = Mundo()
    mundo.ignora_filtro = True
    mundo.vivas = [{"id": "i1", "businessKey": "ESC-amh-wa:amh:hk1_OUTRA"}]

    with pytest.raises(B.RecusaDeSegurancaError):
        _bateria(mundo).cancelar("wa:amh:hk1_x")

    assert not [c for c in mundo.chamadas if c[0] == "DELETE"]


def test_cancela_tambem_o_caso_clinico_paralelo_pela_chave_exata() -> None:
    """DL-0072: o alerta clinico grave com caso aberto abre `ESC-amh-<conversa>-clin`. Antes o runner nao
    o via nem o cancelava: 5 atendimentos P1 de teste ficaram abertos no dev em 05/10/2026."""
    mundo = Mundo()
    mundo.vivas = [
        {"id": "inst-1", "businessKey": "ESC-amh-wa:amh:hk1_x"},
        {"id": "inst-clin", "businessKey": "ESC-amh-wa:amh:hk1_x-clin"},
        {"id": "de-outra-conversa", "businessKey": "ESC-amh-wa:amh:hk1_y-clin"},
    ]

    resultado = _bateria(mundo).cancelar("wa:amh:hk1_x")

    assert resultado == "cancelado_204+clin_cancelado_204"
    assert [v["id"] for v in mundo.vivas] == ["de-outra-conversa"], "so' as duas chaves desta conversa"


def test_cancela_so_o_clinico_quando_o_principal_nao_existe() -> None:
    mundo = Mundo()
    mundo.vivas = [{"id": "inst-clin", "businessKey": "ESC-amh-wa:amh:hk1_x-clin"}]

    assert _bateria(mundo).cancelar("wa:amh:hk1_x") == "clin_cancelado_204"
    assert mundo.vivas == []


def test_se_uma_das_duas_chaves_recusa_nada_e_apagado() -> None:
    """A validacao das duas vem antes de qualquer DELETE."""
    mundo = Mundo()
    mundo.vivas = [
        {"id": "inst-1", "businessKey": "ESC-amh-wa:amh:hk1_x"},
        {"id": "clin-1", "businessKey": "ESC-amh-wa:amh:hk1_x-clin"},
        {"id": "clin-2", "businessKey": "ESC-amh-wa:amh:hk1_x-clin"},
    ]

    with pytest.raises(B.RecusaDeSegurancaError):
        _bateria(mundo).cancelar("wa:amh:hk1_x")

    assert not [c for c in mundo.chamadas if c[0] == "DELETE"], "o principal tambem nao foi apagado"


def test_sem_instancia_nao_ha_o_que_cancelar() -> None:
    assert _bateria(Mundo()).cancelar("wa:amh:hk1_x") == "nada_a_cancelar"


def test_toda_consulta_de_instancia_viva_usa_business_key_e_nunca_o_filtro_do_historico() -> None:
    mundo = Mundo()
    mundo.abrir_ao_enviar = {"abre": True}
    caso = {"id": "T1", "msgs": [{"texto": "dor no peito", "espera_s": 0}], "espera": {"prioridade": "P1"}}

    _bateria(mundo).rodar_caso(caso, 300)

    consultas = [url for metodo, url in mundo.chamadas if metodo == "GET" and "/process-instance?" in url]
    assert consultas
    assert all("businessKey=" in url and "processInstanceBusinessKey" not in url for url in consultas)


# ------------------------------------------------------------------ envio
@pytest.mark.parametrize("numero", ["5511999999999", "5511900000", "551190000012", "5511900000abc", ""])
def test_fora_da_faixa_sintetica_nada_e_enviado(numero: str) -> None:
    mundo = Mundo()

    with pytest.raises(B.RecusaDeSegurancaError):
        _bateria(mundo).enviar(numero, "oi")

    assert mundo.chamadas == []


def test_o_ritmo_respeita_o_limite_por_conversa() -> None:
    """A 7a mensagem por minuto volta vazia e parece defeito da Helena: o script espera."""
    mundo = Mundo()
    bateria = _bateria(mundo)

    bateria.enviar("5511900000200", "oi")
    bateria.enviar("5511900000200", "oii")

    assert max(mundo.dormidos) >= B.INTERVALO_NA_CONVERSA_S - 0.01
    assert 60 / B.INTERVALO_NA_CONVERSA_S <= 6, (
        "o ritmo cabe no limite de 6 mensagens por conversa por minuto"
    )
    assert 60 / B.INTERVALO_ENTRE_ENVIOS_S <= 120, "o ritmo cabe no limite de 120 por minuto no tenant"


# ------------------------------------------------------------------ caso
def test_um_caso_que_abre_atendimento_registra_o_roteamento_do_motor_e_limpa() -> None:
    mundo = Mundo()
    mundo.abrir_ao_enviar = {"abre": True}
    mundo.respostas = {"dor no peito": "Recebemos o seu relato."}
    caso = {
        "id": "T1",
        "msgs": [{"texto": "dor no peito", "espera_s": 0}],
        "espera": {"prioridade": "P1", "grupo": "plantao-clinico"},
    }

    linhas, proximo = _bateria(mundo).rodar_caso(caso, 300)

    envio = linhas[0]
    assert envio["resposta"] == "Recebemos o seu relato." and envio["abriu"] is True
    assert envio["motor"] == {
        "grupo": "plantao-clinico",
        "prioridade": "P1",
        "sla_ack": "PT5M",
        "sla_resolucao": "PT30M",
        "motivo": "red_flag_clinico",
        "severidade": "grave",
        "tabela": "triage_redflag_adult",
    }
    assert any(linha.get("acao") == "limpeza" and linha["resultado"] == "cancelado_204" for linha in linhas)
    assert linhas[-1]["bate"] is True
    assert proximo == 301
    assert mundo.vivas == [], "nao pode sobrar atendimento aberto"


def test_caso_com_clinico_paralelo_usa_a_rota_do_paralelo_e_limpa_os_dois() -> None:
    """I02: com um P3 aberto, 'dor no peito' abre um caso clinico paralelo (DL-0072); a rota observada e'
    a do paralelo (P1 plantao), e os dois casos sao cancelados no fim."""
    mundo = Mundo()
    mundo.abrir_ao_enviar = {"abre": True}
    mundo.abrir_clinico_ao_enviar = True
    caso = {
        "id": "T0",
        "msgs": [{"texto": "dor no peito", "espera_s": 0}],
        "espera": {"prioridade": "P1", "grupo": "plantao-clinico"},
    }

    linhas, _ = _bateria(mundo).rodar_caso(caso, 300)

    envio = linhas[0]
    assert envio["motor"] is not None and envio["motor_paralelo"] is not None
    assert linhas[-1]["bate"] is True
    assert any(
        linha.get("acao") == "limpeza" and "clin_cancelado_204" in linha["resultado"] for linha in linhas
    )
    assert mundo.vivas == [], "nao pode sobrar nenhum dos dois"


def test_rota_diferente_da_esperada_nao_bate() -> None:
    mundo = Mundo()
    mundo.abrir_ao_enviar = {"abre": True}
    caso = {
        "id": "T2",
        "msgs": [{"texto": "oi", "espera_s": 0}],
        "espera": {"prioridade": None, "grupo": None},
    }

    linhas, _ = _bateria(mundo).rodar_caso(caso, 300)

    assert linhas[-1]["bate"] is False, "abriu atendimento onde nao devia"


def test_caso_que_nao_abre_e_nao_deve_abrir_bate() -> None:
    mundo = Mundo()
    caso = {
        "id": "T3",
        "msgs": [{"texto": "oi", "espera_s": 0}],
        "espera": {"prioridade": None, "grupo": None},
    }

    linhas, _ = _bateria(mundo).rodar_caso(caso, 300)

    assert linhas[0]["abriu"] is False and linhas[-1]["bate"] is True


def test_a_limpeza_roda_mesmo_quando_o_envio_explode() -> None:
    mundo = Mundo()
    mundo.abrir_ao_enviar = {"abre": True}
    original = mundo.http
    chamadas_de_envio = {"n": 0}

    def explode_no_segundo_envio(metodo: str, url: str, corpo: Any, timeout: float) -> tuple[int, Any]:
        if url.endswith("/receptor/simular"):
            chamadas_de_envio["n"] += 1
            if chamadas_de_envio["n"] == 2:
                raise ConnectionError("canal caiu")
        return original(metodo, url, corpo, timeout)

    bateria = B.Bateria(
        canal="http://canal",
        motor="http://motor",
        http=explode_no_segundo_envio,
        dormir=mundo.dormir,
        agora=mundo.agora,
    )
    caso = {
        "id": "T4",
        "msgs": [{"texto": "dor no peito", "espera_s": 0}, {"texto": "e agora?", "espera_s": 0}],
        "espera": {"prioridade": "P1"},
    }

    with pytest.raises(ConnectionError):
        bateria.rodar_caso(caso, 300)

    assert mundo.vivas == [], "o atendimento aberto pelo 1o envio foi cancelado mesmo assim"


# ------------------------------------------------------------------ corpus
def _casos() -> list[dict[str, Any]]:
    return list(
        json.loads((_RAIZ / "tools" / "scripts" / "bateria_helena_casos.json").read_text(encoding="utf-8"))[
            "casos"
        ]
    )


def test_o_corpus_tem_os_casos_da_bateria_de_01_10_e_ids_unicos() -> None:
    casos = _casos()
    ids = [c["id"] for c in casos]

    assert len(ids) == len(set(ids))
    assert len(casos) >= 80
    for esperado in ("A_SEQ", "B07", "C08", "D01", "D17", "E06", "E07", "F01", "I02", "J07", "J09"):
        assert esperado in ids, esperado


def test_todo_caso_declara_mensagens_e_a_rota_esperada() -> None:
    for caso in _casos():
        assert caso["msgs"] and all(m["texto"].strip() for m in caso["msgs"]), caso["id"]
        assert set(caso["espera"]) == {"prioridade", "grupo"}, caso["id"]
        assert (caso["espera"]["prioridade"] is None) == (caso["espera"]["grupo"] is None), caso["id"]
        assert caso["espera"]["prioridade"] in (None, "P1", "P2", "P3"), caso["id"]


def test_o_corpus_tem_os_casos_k_e_as_decisoes_interinas_de_02_10() -> None:
    por_id = {c["id"]: c for c in _casos()}

    for esperado in ("K01", "K02", "K03", "K04", "K05", "K06", "K07"):
        assert esperado in por_id, esperado
    # DL-0068 (falta de ar leve), DL-0069 (panico P1), DL-0070 (reclamacao na ANS), DL-0072 (paralelo).
    assert por_id["C06"]["espera"] == {"prioridade": "P1", "grupo": "plantao-clinico"}
    assert por_id["E08"]["espera"] == {"prioridade": "P1", "grupo": "plantao-clinico"}
    assert por_id["B13"]["espera"] == {"prioridade": "P3", "grupo": "atendimento-humano"}
    assert por_id["K06"]["espera"] == {"prioridade": "P1", "grupo": "plantao-clinico"}


def test_carregar_casos_filtra_por_id_e_recusa_id_inexistente() -> None:
    caminho = _RAIZ / "tools" / "scripts" / "bateria_helena_casos.json"

    assert [c["id"] for c in B.carregar_casos(caminho, ["B07", "D01"])] == ["B07", "D01"]
    with pytest.raises(SystemExit):
        B.carregar_casos(caminho, ["ZZ99"])

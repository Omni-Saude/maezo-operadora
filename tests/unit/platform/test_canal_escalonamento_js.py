"""O JavaScript de `paginas/escalonamento.html`, exercitado de verdade.

Porque um teste de Python roda JavaScript: a pagina e' HTML+JS ES5 puro servido de dentro da
imagem, sem build, sem bundler e sem suite de front. Ate 21/09/2026 a unica cerca que ela tinha era
`node --check` (sintaxe) e o olho de quem abria a tela — e a revisao defect-first do dono do repo
encontrou quatro afirmacoes falsas que sintaxe nenhuma pega:

  * todo erro do portal virava `recusado`, incluindo 503 (EFEITO INCERTO por contrato) e 409;
  * "Os dois caminhos conferem" saia com os dois processos ainda ACTIVE;
  * `classificarValor` rodava antes dos regex e engolia CPF pontuado dentro de referencia opaca;
  * a fila lia so' a primeira pagina e o 404 pos-conclusao virava diagnostico de isolamento.

Todos os quatro sao regra de decisao em funcao pura o suficiente para ser chamada direto. Este
arquivo extrai o `<script>` da pagina, roda no `node` que a maquina de build ja' tem (o runbook
`docs/runbooks/publicar-pagina-canal-teste.md` manda rodar `node --check` antes de construir a
imagem), com um DOM de mentira e `fetch` de mentira, e faz asserção sobre o retorno das funcoes.

O DOM DE MENTIRA E' DE PROPOSITO MINIMO: `getElementById` devolve um objeto por id, memoizado, com
`innerHTML`/`textContent`/`style`/`disabled`. Nao existe layout, nao existe evento. O que se mede
aqui e' DECISAO — o que a tela afirma —, nao pintura. Pintura se confere na tela.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]
PAGINA = RAIZ / "src" / "maezo" / "platform" / "testchannel" / "paginas" / "escalonamento.html"

# O `node` e' o mesmo que o runbook de publicacao exige antes de construir a imagem. Sem ele na
# maquina, estes testes sao pulados em vez de reprovados: a cerca vive no CI, que tem node.
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node nao encontrado nesta maquina")

BANCADA = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const partes = html.match(/<script[^>]*>([\s\S]*?)<\/script>/gi) || [];
let js = partes.map(function(p){ return p.replace(/^<script[^>]*>/i, '').replace(/<\/script>$/i, ''); })
               .join('\n');
// A pagina ainda tem o marcador do servidor; sem trocar por vazio, `PORTAL` seria a string do
// marcador — TRUTHY — e o carregamento sairia chamando `https://__PORTAL_.../session`.
js = js.replace('"__PORTAL_PUBLIC_ORIGIN__"', JSON.stringify(process.env.PORTAL_FAKE || ''));

const nos = new Map();
function no(){ return {innerHTML:'', textContent:'', value:'', style:{}, disabled:false,
                       focus:function(){}, parentNode:{id:''}}; }
const document = {
  getElementById: function(id){ if (!nos.has(id)) nos.set(id, no()); return nos.get(id); },
  querySelectorAll: function(){ return []; }
};
const chamadas = [];
const contexto = {
  document: document,
  window: {open: function(){}},
  navigator: {clipboard: {writeText: function(){ return Promise.resolve(); }}},
  fetch: function(url){ chamadas.push(String(url));
                        return Promise.reject(new Error('fetch desligado na bancada')); },
  // Somente o que o contexto do `vm` nao tem por si: temporizador, saida e um `alert` mudo. Os
  // intrinsics (Object, Promise, Date, JSON...) ficam sendo os do proprio contexto — injetar os
  // de fora criaria dois realms e `instanceof` passaria a mentir.
  setTimeout: setTimeout, clearTimeout: clearTimeout, console: console,
  alert: function(){}, __chamadas: chamadas, __nos: nos
};
contexto.globalThis = contexto;
vm.createContext(contexto);
vm.runInContext(js, contexto, {filename: 'escalonamento.js'});
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), contexto, {filename: 'caso.js'});
"""


def _rodar(caso_js: str, tmp_path: Path, portal: str = "") -> dict:
    """Roda `caso_js` dentro do contexto da pagina; ele imprime uma linha JSON com o resultado."""
    bancada = tmp_path / "bancada.js"
    bancada.write_text(BANCADA, encoding="utf-8", newline="")
    caso = tmp_path / "caso.js"
    caso.write_text(caso_js, encoding="utf-8", newline="")
    assert NODE is not None
    # Bytes, e nao `text=True`: o node escreve UTF-8 e o texto desta tela tem acento. Decodificar
    # pela pagina de codigo do Windows trocaria o acento e a assercao falharia por encoding.
    saida = subprocess.run(
        [NODE, str(bancada), str(PAGINA), str(caso)],
        capture_output=True,
        timeout=60,
        env=os.environ | {"PORTAL_FAKE": portal},
        check=False,
    )
    out = saida.stdout.decode("utf-8", "replace")
    err = saida.stderr.decode("utf-8", "replace")
    assert saida.returncode == 0, f"stdout={out}\nstderr={err}"
    linhas = [ln for ln in out.splitlines() if ln.startswith("{")]
    assert linhas, f"o caso nao imprimiu JSON: {out!r} / {err!r}"
    return json.loads(linhas[-1])


def _desfecho(**campos: object) -> str:
    """Monta, em JS, um registro de `S.desfechos` com os defaults de um caso concluido."""
    base: dict[str, object] = {
        "caminho": "motor",
        "resultado": "resolvido_humano",
        "http": {"status": 204, "detalhe": "204"},
        "turno": 1,
        "numero": "5511900000001",
        "pid": "pid-1",
        "tarefaId": "tarefa-1",
        "atividades": [
            {"id": "UT_TratarEscalonamento", "nome": "Tratar", "inicio": "2026-09-21T10:00:00Z"},
            {"id": "End_ResolvidoPorHumano", "nome": "Fim", "inicio": "2026-09-21T10:01:00Z"},
        ],
        "fim": ["End_ResolvidoPorHumano"],
        "estado": "COMPLETED",
        "endTime": "2026-09-21T10:01:01Z",
        "esperando": [],
        "incidentes": [],
        "tarefasAbertas": [],
        "relidoEm": None,
    }
    base.update(campos)
    return "Object.assign(" + json.dumps(base) + ", {quando: new Date()})"


# ============================================================ P1: erro nao e' recusa


@pytest.mark.parametrize(
    ("status", "classe"),
    [
        (400, "recusado"),
        (401, "recusado"),
        (403, "recusado"),
        (404, "recusado"),
        (422, "recusado"),
        (501, "recusado"),
        (0, "recusado"),  # `bloqueado`: preflight recusado, requisicao nao emitida
        (409, "desconhecido"),
        (503, "desconhecido"),
    ],
)
def test_503_e_409_nao_sao_recusa(tmp_path: Path, status: int, classe: str) -> None:
    """O achado P1 em uma linha: 503 e' EFEITO INCERTO pelo contrato do endpoint e 409 exige
    releitura. Gravar os dois como `recusado` fazia a tela afirmar encerramento seguro e oferecer
    repeticao de um efeito que pode ter commitado.
    """
    r = _rodar(
        f"console.log(JSON.stringify({{classe: classeDaFalhaPortal({{status: {status}}})}}));",
        tmp_path,
    )
    assert r["classe"] == classe


def test_incerteza_nao_libera_retry_quando_a_tarefa_saiu_da_fila(tmp_path: Path) -> None:
    """A exigencia do review: releia ANTES de habilitar retry, e se a tarefa estiver concluida o
    retry nao aparece. `tarefasAbertas` sem a tarefa = a conclusao commitou.
    """
    caso = f"""
      S.desfechos = [{
        _desfecho(caminho="portal", http={"status": 503, "detalhe": "503"}, tarefasAbertas=["outra-tarefa"])
    }];
      S.desfechos[0].http.desfecho = "desconhecido";
      decidirRetry();
      console.log(JSON.stringify({{
        aberta: tarefaAindaAberta(S.desfechos[0]),
        aviso: document.getElementById("avisoPortal").innerHTML
      }}));
    """
    r = _rodar(caso, tmp_path)
    assert r["aberta"] is False
    assert "o efeito ACONTECEU" in r["aviso"]
    assert "Não há o que repetir" in r["aviso"]


def test_incerteza_com_releitura_que_falhou_tambem_nao_libera_retry(tmp_path: Path) -> None:
    """`null` nao e' `false`: leitura que falhou nao autoriza repeticao sobre efeito incerto."""
    caso = f"""
      S.desfechos = [{
        _desfecho(caminho="portal", http={"status": 503, "detalhe": "503"}, tarefasAbertas=None)
    }];
      S.desfechos[0].http.desfecho = "desconhecido";
      decidirRetry();
      console.log(JSON.stringify({{
        aberta: tarefaAindaAberta(S.desfechos[0]),
        aviso: document.getElementById("avisoPortal").innerHTML
      }}));
    """
    r = _rodar(caso, tmp_path)
    assert r["aberta"] is None
    assert "a releitura não mediu" in r["aviso"]


def test_incerteza_com_tarefa_ainda_aberta_libera_retry_e_diz_o_que_esperar(
    tmp_path: Path,
) -> None:
    caso = f"""
      S.desfechos = [{
        _desfecho(caminho="portal", http={"status": 503, "detalhe": "503"}, tarefasAbertas=["tarefa-1"])
    }];
      S.desfechos[0].http.desfecho = "desconhecido";
      decidirRetry();
      console.log(JSON.stringify({{
        aberta: tarefaAindaAberta(S.desfechos[0]),
        aviso: document.getElementById("avisoPortal").innerHTML
      }}));
    """
    r = _rodar(caso, tmp_path)
    assert r["aberta"] is True
    assert "segue aberta no motor" in r["aviso"]
    assert "409" in r["aviso"]


def test_cartao_de_incerto_e_visualmente_distinto_de_recusado(tmp_path: Path) -> None:
    """Distincao visual verificavel: selo proprio, classe propria, e nenhuma palavra do
    estado vizinho — RECUSADO nao pode sobrar num cartao incerto."""
    caso = f"""
      S.desfechos = [{
        _desfecho(caminho="portal", http={"status": 503, "detalhe": "503"}, tarefasAbertas=["tarefa-1"])
    }];
      S.desfechos[0].http.desfecho = "desconhecido";
      pintarDesfechos();
      console.log(JSON.stringify({{html: document.getElementById("vDesfechos").innerHTML}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "RESULTADO DESCONHECIDO" in html
    assert "RECUSADO" not in html
    assert 'class="desfecho por inc"' in html
    assert 'class="incerto"' in html


# ====================================== P1: equivalencia exige estado terminal


def test_dois_cartoes_active_nao_declaram_equivalencia(tmp_path: Path) -> None:
    """O achado P1 tal como o review o descreve: dois cartoes ACTIVE, aguardando worker, sem fim
    alcancado, satisfaziam todas as comparacoes de igualdade e geravam "Os dois caminhos conferem".
    """
    ativo = {
        "atividades": [
            {"id": "UT_TratarEscalonamento", "nome": "Tratar", "inicio": "2026-09-21T10:00:00Z"},
            {"id": "ST_PublishResolved", "nome": "Publicar", "inicio": "2026-09-21T10:00:30Z"},
        ],
        "fim": [],
        "estado": "ACTIVE",
        "endTime": None,
        "esperando": ["operadora.events.publish em ST_PublishResolved"],
    }
    caso = f"""
      S.desfechos = [{_desfecho(caminho="motor", **ativo)},
                     {_desfecho(caminho="portal", pid="pid-2", **ativo)}];
      console.log(JSON.stringify({{html: compararCaminhos()}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "conferem" not in html
    assert "Ainda comparando" in html
    # A armadilha documentada pela propria pagina tem de aparecer no texto, senao alguem le'
    # "ainda comparando" como divergencia.
    assert "ST_PublishResolved" in html
    assert "aguardando o worker" in html


def test_encerrado_sem_o_fim_previsto_tambem_nao_declara_equivalencia(tmp_path: Path) -> None:
    caso = f"""
      S.desfechos = [{_desfecho(caminho="motor", fim=["End_SupervisorAlertado"])},
                     {_desfecho(caminho="portal", pid="pid-2")}];
      console.log(JSON.stringify({{html: compararCaminhos()}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "Ainda comparando" in html
    assert "End_ResolvidoPorHumano" in html


def test_os_dois_terminais_e_iguais_conferem(tmp_path: Path) -> None:
    """A contraprova: com os dois COMPLETED e no fim que o BPMN preve, o verde volta — senao a
    correcao teria trocado um falso positivo por um falso negativo permanente.
    """
    caso = f"""
      S.desfechos = [{_desfecho(caminho="motor")}, {_desfecho(caminho="portal", pid="pid-2")}];
      console.log(JSON.stringify({{html: compararCaminhos()}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "Os dois caminhos conferem" in html


def test_emergencia_acionada_termina_em_resolvido_por_humano(tmp_path: Path) -> None:
    """Cerca do mapa que a pagina leu do BPMN: com o mapa "intuitivo" (emergencia -> supervisor)
    a comparacao ficaria presa em "ainda comparando" sobre um processo correto."""
    caso = f"""
      S.desfechos = [{_desfecho(caminho="motor", resultado="emergencia_acionada")},
                     {_desfecho(caminho="portal", pid="pid-2", resultado="emergencia_acionada")}];
      console.log(JSON.stringify({{
        esperado: FIM_ESPERADO["emergencia_acionada"], html: compararCaminhos()}}));
    """
    r = _rodar(caso, tmp_path)
    assert r["esperado"] == "End_ResolvidoPorHumano"
    assert "Os dois caminhos conferem" in r["html"]


def test_desconhecido_nao_entra_na_comparacao(tmp_path: Path) -> None:
    caso = f"""
      S.desfechos = [{_desfecho(caminho="motor")},
                     {
        _desfecho(
            caminho="portal", pid="pid-2", http={"status": 503, "detalhe": "503", "desfecho": "desconhecido"}
        )
    }];
      console.log(JSON.stringify({{html: compararCaminhos()}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "conferem" not in html
    assert "resultado desconhecido" in html


# ============================== P1: CPF/telefone dentro de referencia opaca


def test_cpf_pontuado_em_referencia_opaca_e_achado(tmp_path: Path) -> None:
    """O caso exato do review: `classificarValor` rotulava isto como `opaco` e `opaco` suprimia
    os regex de CPF e telefone."""
    caso = """
      var saida = {achados:[], valores:0, digests:0, graves:0};
      varrer({ref: "beneficiario-cpf-123.456.789-09"}, "fonte", saida);
      console.log(JSON.stringify({
        tipo: classificarValor("beneficiario-cpf-123.456.789-09"),
        graves: saida.graves,
        rotulos: saida.achados.map(function(a){ return a.rotulos.join(","); }),
        marcas: saida.achados.map(function(a){ return a.marcas.join(","); })
      }));
    """
    r = _rodar(caso, tmp_path)
    # O valor CONTINUA sendo classificado como opaco: o defeito era a ordem, nao a classificacao.
    assert r["tipo"] == "opaco"
    assert r["graves"] == 1
    assert r["rotulos"] == ["CPF"]
    assert r["marcas"] == ["123.456.789-09"]


def test_telefone_pontuado_em_referencia_opaca_e_achado(tmp_path: Path) -> None:
    caso = """
      var saida = {achados:[], valores:0, digests:0, graves:0};
      varrer({ref: "contato-benef-ref-(11) 98765-4321"}, "fonte", saida);
      console.log(JSON.stringify({graves: saida.graves,
        rotulos: saida.achados.map(function(a){ return a.rotulos.join(","); })}));
    """
    r = _rodar(caso, tmp_path)
    assert r["graves"] == 1
    assert r["rotulos"] == ["telefone"]


def test_digest_sha256_continua_fora_da_busca_numerica(tmp_path: Path) -> None:
    """A contraprova que impede a correcao de reintroduzir o falso positivo que custou o
    comentario original: 64 hex com corrida de 11 digitos por acaso nao e' telefone.

    A corrida `29876543210` esta' no meio do digest de proposito — e' o padrao `\\d{4,5}-?\\d{4}`
    casando por acaso, que era o motivo de existir a classificacao.
    """
    digest = "a1b2c3" + "29876543210" + "f" * 47
    caso = f"""
      var v = {json.dumps(digest)};
      var saida = {{achados:[], valores:0, digests:0, graves:0}};
      varrer({{form_digest: v}}, "fonte", saida);
      console.log(JSON.stringify({{n: v.length, tipo: classificarValor(v),
        digests: saida.digests, graves: saida.graves, achados: saida.achados.length}}));
    """
    r = _rodar(caso, tmp_path)
    assert r["n"] == 64
    assert r["tipo"] == "digest"
    assert r["digests"] == 1
    assert (r["graves"], r["achados"]) == (0, 0)


def test_timestamp_do_contrato_nao_vira_telefone(tmp_path: Path) -> None:
    """`snapshot_at`, `observed_at` e `engine_due_at` aparecem em toda leitura. Um falso positivo
    aqui apareceria em CADA varredura e apagaria o sinal do painel."""
    caso = """
      var saida = {achados:[], valores:0, digests:0, graves:0};
      varrer({snapshot_at:"2026-09-21T18:04:05.123456Z",
              observed_at:"2026-09-21T18:04:05.123456+00:00",
              engine_due_at:"2026-09-21T19:04:05Z",
              task_id:"1a2b3c4d-5678-1234-9012-abcdef123456",
              task_revision:"2", refresh_after_seconds:10}, "fonte", saida);
      console.log(JSON.stringify({graves: saida.graves, achados: saida.achados.length}));
    """
    r = _rodar(caso, tmp_path)
    assert (r["graves"], r["achados"]) == (0, 0)


def test_cpf_em_narrativa_e_vazamento_e_nao_suspeita(tmp_path: Path) -> None:
    """Achado nao pedido pelo review, da MESMA familia: `classificarValor` chamava de referencia
    `opaco` qualquer coisa com letra, digito e 20+ caracteres — e uma frase em portugues satisfaz
    isso. O CPF cru dentro dela era rebaixado de VAZAMENTO para "corrida de digitos em referencia
    opaca". Referencia opaca do portal nao tem espaco, por definicao de `OpaqueRef` no contrato.
    """
    caso = """
      var saida = {achados:[], valores:0, digests:0, graves:0};
      varrer({nota: "paciente 12345678909 ligou"}, "fonte", saida);
      console.log(JSON.stringify({graves: saida.graves,
        rotulos: saida.achados.map(function(a){ return a.rotulos.join(","); })}));
    """
    r = _rodar(caso, tmp_path)
    assert r["graves"] == 1
    assert "11 dígitos" in r["rotulos"][0]


# =============================== P2: 404 pos-conclusao e' saida normal da fila


def test_404_depois_de_concluir_nao_e_diagnostico(tmp_path: Path) -> None:
    caso = f"""
      S.tarefaId = "tarefa-1";
      S.desfechos = [{_desfecho(caminho="portal")}];
      S.portal.tarefa = {{ok:false, status:404, corpo:{{code:"resource_unavailable"}}}};
      pintarTarefaPortal();
      console.log(JSON.stringify({{html: document.getElementById("pTarefa").innerHTML}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "SAIU DA FILA" in html
    assert "isolamento" not in html
    assert "outro identificador" not in html


def test_404_sem_conclusao_continua_sendo_diagnostico(tmp_path: Path) -> None:
    """A contraprova: sem conclusao desta tarefa, 404 continua sendo as duas leituras de sempre
    (isolamento ou chave de juncao) — e o passo 3 do roteiro depende disso."""
    caso = """
      S.tarefaId = "tarefa-1";
      S.desfechos = [];
      S.portal.tarefa = {ok:false, status:404, corpo:{code:"resource_unavailable"}};
      pintarTarefaPortal();
      console.log(JSON.stringify({html: document.getElementById("pTarefa").innerHTML}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "NÃO APARECE PARA ESTE COLABORADOR" in html
    assert "isolamento" in html


def test_404_depois_de_recusa_continua_sendo_diagnostico(tmp_path: Path) -> None:
    """Desfecho recusado deixa o processo aberto; ali a ausencia continua sendo achado."""
    caso = f"""
      S.tarefaId = "tarefa-1";
      S.desfechos = [{
        _desfecho(caminho="portal", http={"status": 403, "detalhe": "403", "desfecho": "recusado"})
    }];
      S.portal.tarefa = {{ok:false, status:404, corpo:{{code:"resource_unavailable"}}}};
      pintarTarefaPortal();
      console.log(JSON.stringify({{html: document.getElementById("pTarefa").innerHTML}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "NÃO APARECE PARA ESTE COLABORADOR" in html


def test_fila_sem_a_tarefa_depois_de_concluir_nao_acusa(tmp_path: Path) -> None:
    caso = f"""
      S.tarefaId = "tarefa-1";
      S.desfechos = [{_desfecho(caminho="portal")}];
      S.portal.filas = {{
        team:{{ok:true, status:200, paginas:1, truncado:false,
               corpo:{{queue:"team", items:[{{task_id:"outra",
                       process_definition_key:"SP-OP-ESCALATION-001", ownership:"unassigned"}}],
                       next_cursor:null}}}},
        mine:{{ok:true, status:200, paginas:1, truncado:false,
               corpo:{{queue:"mine", items:[], next_cursor:null}}}}}};
      pintarFilas();
      console.log(JSON.stringify({{html: document.getElementById("pFila").innerHTML}}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "SAIU DA FILA" in html
    assert "NÃO APARECE" not in html


# ========================================== P2: percorrer `next_cursor` ate o fim


def test_fila_percorre_next_cursor_e_acha_tarefa_da_segunda_pagina(tmp_path: Path) -> None:
    """O achado P2: com uma pagina so', tarefa da pagina 2 virava falso "nao aparece". A bancada
    troca `portalChamar` por uma fila de duas paginas e mede as URLs pedidas.
    """
    caso = """
      var pedidos = [];
      portalChamar = function(caminho){
        pedidos.push(caminho);
        var p1 = {ok:true, status:200, corpo:{queue:"team", next_cursor:"CUR2",
                  freshness:null, items:[{task_id:"aaa"}]}};
        var p2 = {ok:true, status:200, corpo:{queue:"team", next_cursor:null,
                  freshness:null, items:[{task_id:"tarefa-1"}]}};
        return Promise.resolve(caminho.indexOf("cursor=") >= 0 ? p2 : p1);
      };
      lerFilaCompleta("team").then(function(r){
        console.log(JSON.stringify({pedidos: pedidos, paginas: r.paginas,
          truncado: !!r.truncado, ids: r.corpo.items.map(function(i){ return i.task_id; }),
          achou: !!acharItem(r, "tarefa-1")}));
      });
    """
    r = _rodar(caso, tmp_path)
    assert r["paginas"] == 2
    assert r["ids"] == ["aaa", "tarefa-1"]
    assert r["achou"] is True
    assert r["truncado"] is False
    assert "limit=100" in r["pedidos"][0] and "cursor=" not in r["pedidos"][0]
    assert "cursor=CUR2" in r["pedidos"][1]


def test_pagina_que_falha_invalida_a_leitura_inteira(tmp_path: Path) -> None:
    """Devolver meia fila como se fosse a fila e' o mesmo defeito com outra roupa."""
    caso = """
      var n = 0;
      portalChamar = function(caminho){
        n += 1;
        if (n === 1) return Promise.resolve({ok:true, status:200,
          corpo:{queue:"team", next_cursor:"CUR2", freshness:null, items:[{task_id:"aaa"}]}});
        return Promise.resolve({ok:false, status:503, corpo:{code:"read_dependency_unavailable"}});
      };
      lerFilaCompleta("team").then(function(r){
        console.log(JSON.stringify({ok:r.ok, status:r.status, paginas:r.paginas,
                                    lidosAntes:r.lidosAntes}));
      });
    """
    r = _rodar(caso, tmp_path)
    assert r["ok"] is False
    assert r["status"] == 503
    assert r["paginas"] == 2
    assert r["lidosAntes"] == 1


def test_leitura_truncada_nao_afirma_ausencia(tmp_path: Path) -> None:
    """No teto de paginas a tela declara leitura INCOMPLETA em vez de dizer que a tarefa nao
    aparece — ausencia medida em meia fila nao e' ausencia."""
    caso = """
      S.tarefaId = "tarefa-1";
      S.desfechos = [];
      S.portal.filas = {
        team:{ok:true, status:200, paginas:20, truncado:true,
              corpo:{queue:"team", items:[{task_id:"outra"}], next_cursor:"CUR21"}},
        mine:{ok:true, status:200, paginas:1, truncado:false,
              corpo:{queue:"mine", items:[], next_cursor:null}}};
      pintarFilas();
      console.log(JSON.stringify({html: document.getElementById("pFila").innerHTML}));
    """
    html = _rodar(caso, tmp_path)["html"]
    assert "LEITURA INCOMPLETA" in html
    assert "NÃO APARECE" not in html


# ============================ a cerca de CORS do portal e' por caminho


def test_a_pagina_so_chama_caminhos_que_o_cors_do_portal_cobre() -> None:
    """O portal responde preflight credenciado apenas em `/api/v1/portal/session` e
    `/api/v1/portal/tasks` (e subcaminhos). Um `fetch` de outra origem para qualquer outro caminho
    do portal volta rejeicao SEM status, indistinguivel de portal fora do ar — e a tela ficaria
    culpando CORS por um defeito que e' dela.

    A cerca e' textual de proposito: toda chamada de outra origem sai de `portalChamar`, e o que
    se mede aqui e' o conjunto de caminhos que ela pode receber. `entrarNoPortal` usa
    `window.open` (navegacao de topo, sem CORS) e por isso nao entra na conta.
    """
    texto = PAGINA.read_text(encoding="utf-8")
    js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", texto, re.S | re.I))

    assert 'var CAMINHO_SESSAO = "/api/v1/portal/session";' in js
    assert 'var CAMINHO_FILA = "/api/v1/portal/tasks";' in js

    # Nenhum literal `/api/v1/portal/...` fora dos dois cobertos pelo CORS, exceto o login, que
    # e' navegacao.
    literais = set(re.findall(r'"(/api/v1/portal/[^"]*)"', js))
    permitidos = {
        "/api/v1/portal/session",
        "/api/v1/portal/tasks",
        "/api/v1/portal/tasks/",  # prefixo de `/{id}` e `/{id}/completion`, ambos subcaminhos
        "/api/v1/portal/auth/login",  # window.open, nao fetch
    }
    assert literais <= permitidos, literais - permitidos

    # E o login e' mesmo navegacao: a unica linha que o menciona e' a do `window.open`.
    (linha,) = [ln for ln in js.splitlines() if "/api/v1/portal/auth/login" in ln]
    assert "window.open" in linha

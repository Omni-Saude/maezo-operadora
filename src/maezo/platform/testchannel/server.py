"""Canal de Teste — formulário de lançamento + visor de evidência, numa página.

Etapa 4 do Plano de Teste. Serve uma página única e faz proxy de `/engine/*` para o
CIB Seven, evitando CORS. **Nada além da stdlib** — roda em qualquer container que
tenha Python, sem instalar dependência.

    # local
    ENGINE_REST_URL=http://127.0.0.1:8080/engine-rest python -m maezo.platform.testchannel

    # AWS: é um serviço ECS atrás do túnel Cloudflare + Access
    # (deploy/aws-ecs/envs/dev-sa-east-1/service-canal-teste.tf)

O aviso "nunca implantar" que estava aqui foi escrito quando isto só existia local, e
a razão dada era "o motor local não tem auth". Na AWS a razão mudou, e a substituição
honesta é esta:

    ESTE CANAL NÃO TEM AUTENTICAÇÃO PRÓPRIA. Ele faz proxy de caminho ARBITRÁRIO para
    o `engine-rest`. Quem alcança a página dirige o motor. Só é aceitável atrás de uma
    fronteira de identidade (Cloudflare Access), e mesmo assim a autorização continua
    sendo do engine — que hoje tem o usuário `demo` do CIB Seven ativo. Não exponha
    sem o Access na frente.

Configuração por ambiente, nada cravado:
    ENGINE_REST_URL  — REST do engine (obrigatório na prática; default é o local)
    CANAL_PORT       — porta HTTP (default 8500)
    CANAL_BIND       — interface (default 0.0.0.0; em container tem de ser 0.0.0.0)
    COCKPIT_URL      — link do Cockpit mostrado na página
    RECEPTOR_URL          — base do receptor de webhook (só para `/receptor/simular`)
    WHATSAPP_APP_SECRET   — segredo da Meta, usado para ASSINAR o envelope sintético
    CANAL_SIMULAR_RECEPTOR — "1" LIGA `/receptor/simular`; ausente = rota inexistente

A rota `/receptor/simular` é a única coisa aqui que fabrica uma mensagem de beneficiário
assinada. Ela está DESLIGADA por padrão e as três cercas estão descritas em
`_simular_whatsapp` — leia lá antes de mexer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: REST do engine. O default local existe para `python -m` na maquina do dev; em
#: container a variavel e' sempre passada pela task definition.
ENGINE = os.environ.get("ENGINE_REST_URL", "http://127.0.0.1:8080/engine-rest").rstrip("/")

PORT = int(os.environ.get("CANAL_PORT", "8500"))

#: 0.0.0.0 e' obrigatorio em container: ligado em 127.0.0.1 o processo sobe, o health
#: check falha e o servico entra em ciclo de substituicao sem dizer por que.
BIND = os.environ.get("CANAL_BIND", "0.0.0.0")  # ver comentario acima

#: Link do Cockpit exibido na pagina. Local aponta para localhost; na AWS aponta para
#: o hostname publico atras do Access.
COCKPIT_URL = os.environ.get("COCKPIT_URL", "http://localhost:8080")

#: Ingresso do agente (a rota que EXECUTA um turno). Em container vem do Cloud Map:
#: `http://agent-rafael.maezo-operadora-dev.internal:8000`. Vazio desliga o painel do
#: agente na pagina — melhor do que um botao que sempre falha.
AGENTE = os.environ.get("AGENT_INGRESS_URL", "").rstrip("/")

#: Base do receptor de webhook. Vazio = `/receptor/simular` nao funciona (503).
RECEPTOR = os.environ.get("RECEPTOR_URL", "").rstrip("/")

#: Segredo da Meta, LIDO DO AMBIENTE e nunca servido. E' o que assina o envelope
#: sintetico. Se estiver vazio a rota recusa — nunca assina com string vazia, que
#: produziria uma assinatura valida para quem soubesse que o segredo e' "".
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")

#: TERCEIRA CERCA, e a mais importante: a rota nao existe a menos que alguem a LIGUE
#: explicitamente. Ter o segredo e a URL no ambiente NAO basta. Assim um ambiente que
#: herde as duas variaveis por descuido continua sem a rota, e ligar e' um ato visivel
#: na task definition — que e' onde a cerca de CI (`scripts/ci/check_canal_simular.py`)
#: consegue ver e reprovar fora de `dev-sa-east-1`.
SIMULAR_LIGADO = os.environ.get("CANAL_SIMULAR_RECEPTOR", "") == "1"

#: SEGUNDA CERCA: a faixa de telefone de teste, ancorada nas DUAS pontas e com
#: comprimento exato. `startswith` sozinho aceitaria `55119000000` seguido de qualquer
#: coisa, inclusive de um numero real mais longo.
FAIXA_TESTE = re.compile(r"^55119000000\d{2}$")

#: Teto do texto aceito. O receptor tem os seus proprios limites; este existe para a
#: rota nao ser um caminho barato de empurrar megabytes para dentro do cluster.
TEXTO_MAX = 2000

#: Diretorio das paginas externas servidas em `/p/<arquivo>`. ADJACENTE AO MODULO de
#: proposito, e nao um caminho configuravel: `resolve` + comparacao de pai e' o que fecha
#: travessia de diretorio, e um diretorio vindo de variavel de ambiente reabriria isso.
PAGINAS = Path(__file__).parent / "paginas"

#: Extensoes servidas. Lista fechada: sem ela, um `.py` ou um `.env` esquecido na pasta
#: passaria a ser publico atras do Access — que autentica, mas nao decide o que pode sair.
TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

HTML = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canal de Teste — Maezo</title>
<style>
:root{--paper:#F6F8F8;--card:#fff;--ink:#1D2C33;--muted:#57696F;--accent:#0E655D;
--soft:#E3EFED;--line:#D8E0E0;--ok:#1E6E3C;--warn:#8A5D00;--bad:#9E3226;
--mono:Consolas,Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--paper:#131B1E;--card:#1B2529;--ink:#DCE6E6;
--muted:#93A6AB;--accent:#5FB8AC;--soft:#1E3330;--line:#31424A;--ok:#7CC98F;--warn:#E3B34C;--bad:#E08578}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 'Segoe UI',system-ui,sans-serif}
header{padding:1rem 1.4rem;border-bottom:2px solid var(--accent);display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap}
header h1{font-size:1.15rem;margin:0}
header .sub{color:var(--muted);font-size:.85rem}
main{display:grid;grid-template-columns:minmax(320px,440px) 1fr;gap:1.2rem;padding:1.2rem 1.4rem;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:1rem 1.1rem}
h2{font-size:.95rem;color:var(--accent);margin:0 0 .6rem;text-transform:uppercase;letter-spacing:.06em}
label{display:block;font-size:.78rem;color:var(--muted);margin:.6rem 0 .15rem}
select,input,button{font:inherit;color:inherit}
select,input{width:100%;padding:.42rem .5rem;border:1px solid var(--line);border-radius:4px;background:var(--paper)}
button{cursor:pointer;border:none;border-radius:4px;padding:.5rem 1rem;font-weight:600}
.primary{background:var(--accent);color:#fff}
.ghost{background:var(--soft);color:var(--accent)}
.vars{width:100%;border-collapse:collapse;margin-top:.4rem;font-size:.86rem}
.vars td{padding:.15rem .25rem 0 0}
.vars input,.vars select{padding:.3rem .4rem;font-size:.86rem}
.vars .del{background:none;color:var(--bad);font-size:1rem;padding:0 .4rem}
.row{display:flex;gap:.6rem;margin-top:.8rem;align-items:center;flex-wrap:wrap}
.hint{font-size:.78rem;color:var(--muted);margin-top:.6rem}
#resultado{margin-top:.8rem;font-size:.86rem}
#resultado .okmsg{color:var(--ok);font-weight:600}
#resultado .errmsg{color:var(--bad);white-space:pre-wrap;font-family:var(--mono);font-size:.8rem}
.ev h3{font-size:.85rem;margin:1rem 0 .3rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.trail{font-family:var(--mono);font-size:.8rem;overflow-x:auto;white-space:pre;padding:.3rem 0}
table.data{border-collapse:collapse;width:100%;font-size:.84rem}
table.data td,table.data th{border-bottom:1px solid var(--line);padding:.3rem .5rem .3rem 0;text-align:left;vertical-align:top}
table.data th{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.mono{font-family:var(--mono);font-size:.82em}
.pill{display:inline-block;font-size:.72rem;font-weight:700;padding:.1rem .5rem;border-radius:3px;background:var(--soft);color:var(--accent)}
.pill.stop{background:#0000;outline:1px solid var(--line);color:var(--muted)}
.dmn{border-left:3px solid var(--accent);padding:.4rem 0 .4rem .8rem;margin:.5rem 0;font-size:.85rem}
.dmn .out{font-weight:600}
.inst{cursor:pointer}
.inst:hover td{background:var(--soft)}
.stopbox{border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:0 4px 4px 0;padding:.6rem .8rem;margin:.5rem 0;font-size:.86rem}
.stopbox.done{border-left-color:var(--ok)}
.stopbox.inc{border-left-color:var(--bad)}
a{color:var(--accent)}
</style></head><body>
<header><h1>Canal de Teste — Maezo Operadora</h1>
<span class="sub">Etapa 4 do plano · entrega a solicitação na mesma porta que um canal real usaria · Cockpit: <a href="http://localhost:8080" target="_blank">localhost:8080</a></span></header>
<main>
<section class="card" id="cardAgente">
  <h2>Pedir ao Rafael
    <span class="hint" style="font-weight:400">— o agente executa um turno e ele mesmo inicia o processo</span></h2>
  <p class="hint">Diferente do bloco abaixo: ali você inicia o processo direto no motor. Aqui o
  pedido vai ao <b>agente</b>, que avalia e decide — e o processo nasce da decisão dele.</p>
  <div id="agForm" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.6rem">
    <label>tenant_id<input id="ag_tenant" value="amh"></label>
    <label>numero_guia_tiss<input id="ag_guia" placeholder="ex.: 90000001"></label>
    <label>codigo_procedimento_tuss<input id="ag_tuss" placeholder="ex.: 40901114"></label>
    <label>categoria_procedimento<input id="ag_categoria" placeholder="ex.: exame"></label>
    <label>prestador_id<input id="ag_prestador" placeholder="ex.: PREST-001"></label>
    <label>beneficiario_pseudo_id<input id="ag_benef" placeholder="pseudônimo, nunca CPF"></label>
    <label>carater_atendimento<input id="ag_carater" placeholder="eletivo | urgencia"></label>
    <label>cid10<input id="ag_cid" placeholder="ex.: M545"></label>
    <label>valor_estimado_brl<input id="ag_valor" placeholder="ex.: 480"></label>
  </div>
  <div style="margin:.7rem 0;display:flex;gap:.5rem;flex-wrap:wrap">
    <button class="ghost" onclick="exemploFicticio()">preencher exemplo fictício</button>
    <button class="primary" onclick="pedirAoRafael()">Enviar ao Rafael</button>
  </div>
  <div id="saidaAgente"></div>
</section>

<section class="card">
  <h2>Lançar direto no motor — removido</h2>
  <p class="hint">Este formulário iniciava processo chamando o motor direto do navegador,
  e isso <b>contorna a trava auditada de início de processo</b> (ADR-0007/T-C2: auditar
  antes do efeito). O portão <code>check-start-process-fence</code> reprova o repo por
  causa dele, e com razão: um início não auditado é um efeito no mundo sem registro.</p>
  <p class="hint">O caminho sancionado é o painel acima: o pedido vai ao <b>agente</b>, que
  avalia e inicia o processo por <code>start_process_idempotent</code> — com auditoria e
  idempotência. Para o SP-OP-AUTH-001 ele cobre o mesmo teste.</p>
  <p class="hint">Os outros 14 processos ficaram sem atalho de teste por aqui. Devolver essa
  capacidade é construir um endpoint de início que passe pela trava — trabalho próprio,
  não um botão.</p>
</section>
<section class="card ev">
  <h2>Evidência da instância <button class="ghost" style="float:right" onclick="verEvidencia()">atualizar</button></h2>
  <label>ID da instância</label>
  <input id="iid" onchange="verEvidencia()">
  <div id="evidencia"><p class="hint">Lance uma solicitação ou clique numa instância recente abaixo.</p></div>
  <h3>Instâncias recentes</h3>
  <div id="recentes" style="overflow-x:auto"></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);

// ---- painel do agente -------------------------------------------------------
// O proxy do canal tira o prefixo /agente e repassa para AGENT_INGRESS_URL.
if (!"__AGENTE_CONFIGURADO__") { const c=$("cardAgente"); if(c) c.style.display="none"; }

function exemploFicticio(){
  // Caso FICTICIO de proposito: numero de guia fora de qualquer faixa real e pseudonimo
  // explicito. O canal nao tem como saber se um caso e' real — quem preenche sabe.
  const n = 90000000 + Math.floor(Math.random()*99999);
  $("ag_tenant").value="amh"; $("ag_guia").value=String(n);
  $("ag_tuss").value="40901114"; $("ag_categoria").value="exame";
  $("ag_prestador").value="PREST-FICTICIO-001";
  $("ag_benef").value="pseudo-ficticio-"+n;
  $("ag_carater").value="eletivo"; $("ag_cid").value="M545"; $("ag_valor").value="480";
}

function agCampo(id){const v=$(id).value.trim();return v===""?null:v}

async function pedirAoRafael(){
  const out=$("saidaAgente");
  const corpo={tenant_id:agCampo("ag_tenant"),numero_guia_tiss:agCampo("ag_guia"),
    codigo_procedimento_tuss:agCampo("ag_tuss"),categoria_procedimento:agCampo("ag_categoria"),
    prestador_id:agCampo("ag_prestador"),beneficiario_pseudo_id:agCampo("ag_benef"),
    carater_atendimento:agCampo("ag_carater"),cid10:agCampo("ag_cid"),canal:"portal_tiss"};
  const valor=agCampo("ag_valor"); if(valor!==null) corpo.valor_estimado_brl=Number(valor);
  for(const k of Object.keys(corpo)) if(corpo[k]===null) delete corpo[k];

  out.innerHTML="<p class='hint'>executando um turno do agente… (com modelo real pode levar dezenas de segundos)</p>";
  const t0=performance.now();
  try{
    const r=await fetch("/agente/v1/autorizacoes",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(corpo)});
    const j=await r.json();
    const ms=Math.round(performance.now()-t0);
    if(!r.ok){
      out.innerHTML=`<div class="errmsg">HTTP ${r.status} em ${ms} ms\n${esc(JSON.stringify(j.detail||j,null,2))}</div>`;
      return;
    }
    const res=j.resultado||{};
    const pr=res.process_ref||{};
    let h=`<div class="okmsg">Turno executado em <b>${j.duracao_ms} ms</b> (${ms} ms ida e volta)</div>`;
    h+=`<table class="vars"><tr><td>rota</td><td class="mono">${esc(res.route||"—")}</td></tr>`;
    h+=`<tr><td>desfecho</td><td class="mono">${esc(res.desfecho||"—")}</td></tr>`;
    h+=`<tr><td>recomendação</td><td class="mono">${esc(res.recomendacao_auto||"—")}</td></tr>`;
    h+=`<tr><td>admissibilidade</td><td class="mono">${esc(String(res.admissibilidade??"—"))}</td></tr>`;
    h+=`<tr><td>SLA de análise</td><td class="mono">${esc(String(res.sla_analise||"—"))}</td></tr>`;
    h+=`<tr><td>business key</td><td class="mono">${esc(res.business_key||"—")}</td></tr>`;
    h+=`<tr><td>thread do checkpoint</td><td class="mono">${esc((j.thread_id||"").slice(0,24))}…</td></tr>`;
    h+=`<tr><td>processo iniciado</td><td class="mono">${res.process_started?"sim":"NÃO"}</td></tr>`;
    if(pr.instance_id){
      h+=`<tr><td>instância</td><td class="mono">${esc(pr.instance_id)}`;
      h+=` <button class="ghost" onclick="$('iid').value='${esc(pr.instance_id)}';verEvidencia()">ver evidência</button>`;
      h+=`${pr.already_existed?" <span class='hint'>(já existia — idempotência)</span>":""}</td></tr>`;
    }
    h+=`</table>`;
    if(res.dossier){
      h+=`<h3 style="margin:.8rem 0 .3rem">Dossiê escrito pelo agente</h3>`;
      h+=`<pre class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(res.dossier,null,2))}</pre>`;
    }
    if(res.error){h+=`<div class="errmsg">${esc(res.error)}</div>`}
    out.innerHTML=h;
  }catch(e){
    out.innerHTML=`<div class="errmsg">falha ao falar com o agente:\n${esc(e.message)}</div>`;
  }
}

async function api(path,opts){const r=await fetch("/engine"+path,opts);
 if(!r.ok)throw new Error(r.status+" "+await r.text());return r.status==204?null:r.json()}
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function verEvidencia(){const id=$("iid").value.trim();if(!id)return;const box=$("evidencia");
 box.innerHTML="<p class='hint'>carregando…</p>";
 try{
  const inst=await api(`/history/process-instance/${id}`);
  const acts=await api(`/history/activity-instance?processInstanceId=${id}&sortBy=startTime&sortOrder=asc&maxResults=200`);
  const vars=await api(`/history/variable-instance?processInstanceId=${id}&maxResults=200`);
  const decs=await api(`/history/decision-instance?processInstanceId=${id}&includeInputs=true&includeOutputs=true&maxResults=50`);
  const tasks=await api(`/task?processInstanceId=${id}&maxResults=20`).catch(()=>[]);
  const incs=await api(`/history/incident?processInstanceId=${id}&maxResults=20`);
  const ext=await api(`/external-task?processInstanceId=${id}&maxResults=50`).catch(()=>[]);
  let h=`<p><span class="pill">${esc(inst.processDefinitionKey)}</span> estado <b>${esc(inst.state)}</b>
   · bk <span class="mono">${esc(inst.businessKey)}</span></p>`;
  h+=`<h3>Passos</h3><div class="trail">`+acts.map(a=>` [${esc(a.activityType)}] ${esc(a.activityId)}  ${a.durationInMillis==null?"(aberto)":a.durationInMillis+"ms"}`).join("\n")+`</div>`;
  h+=`<h3>Decisões DMN — qual regra disparou</h3>`;
  if(!decs.length)h+=`<p class="hint">nenhuma decisão avaliada ainda</p>`;
  for(const d of decs){h+=`<div class="dmn"><b>${esc(d.decisionDefinitionKey)}</b><br>`;
   for(const i of d.inputs||[])h+=`recebeu ${esc(i.clauseName||i.clauseId)} = <span class="mono">${esc(JSON.stringify(i.value))}</span><br>`;
   for(const o of d.outputs||[])h+=`<span class="out">concluiu ${esc(o.clauseName||o.variableName)} = ${esc(JSON.stringify(o.value))}</span> <span class="hint">(linha ${esc(o.ruleOrder)})</span><br>`;
   h+=`</div>`}
  h+=`<h3>Variáveis</h3><div style="max-height:260px;overflow:auto"><table class="data">`;
  for(const v of vars.sort((a,b)=>a.name.localeCompare(b.name))){let s=JSON.stringify(v.value);if(s&&s.length>80)s=s.slice(0,77)+"…";
   h+=`<tr><td class="mono">${esc(v.name)}</td><td class="mono">${esc(s)}</td></tr>`}
  h+=`</table></div><h3>Onde parou</h3>`;
  for(const t of tasks)h+=`<div class="stopbox"><b>TAREFA HUMANA ABERTA:</b> ${esc(t.name)} <span class="mono">(${esc(t.taskDefinitionKey)})</span> — abra no <a href="http://localhost:8080" target="_blank">Cockpit/Tasklist</a>, leia o dossiê e decida (papel do humano no plano).</div>`;
  for(const i of incs)h+=`<div class="stopbox inc"><b>INCIDENTE:</b> ${esc(i.activityId)} — <span class="mono">${esc(i.incidentMessage)}</span></div>`;
  for(const e2 of ext)h+=`<div class="stopbox"><b>aguardando worker:</b> <span class="mono">${esc(e2.topicName)}</span> em ${esc(e2.activityId)} (retries ${esc(e2.retries)})</div>`;
  if(inst.state=="COMPLETED"&&!incs.length)h+=`<div class="stopbox done">Instância COMPLETA — chegou a um evento de fim.</div>`;
  box.innerHTML=h}
 catch(e){box.innerHTML=`<div class="errmsg">${esc(e.message)}</div>`}}
async function recentes(){try{
 const rows=await api(`/history/process-instance?sortBy=startTime&sortOrder=desc&maxResults=15`);
 let h=`<table class="data"><tr><th>início</th><th>processo</th><th>business key</th><th>estado</th></tr>`;
 for(const r of rows)h+=`<tr class="inst" onclick="$('iid').value='${r.id}';verEvidencia()">
  <td class="mono">${esc((r.startTime||"").slice(5,19).replace("T"," "))}</td><td>${esc(r.processDefinitionKey)}</td>
  <td class="mono">${esc(r.businessKey)}</td><td>${esc(r.state)}</td></tr>`;
 $("recentes").innerHTML=h+"</table>"}catch(e){}}
recentes();setInterval(recentes,15000);
</script></body></html>"""


def _paginas_disponiveis() -> list[str]:
    """Nomes servidos hoje. Vai na resposta 404 para quem errou o nome nao ter de adivinhar."""
    if not PAGINAS.is_dir():
        return []
    return sorted(f.name for f in PAGINAS.iterdir() if f.suffix.lower() in TIPOS)


class Handler(BaseHTTPRequestHandler):
    def _proxy(self, base: str, prefixo: str, timeout: int = 30) -> None:
        """Repassa a requisicao para `base`, tirando `prefixo` do caminho.

        `timeout` e' parametro porque as duas pontas tem ordens de grandeza diferentes: o
        motor responde em milissegundos, e um turno de agente com modelo real leva dezenas
        de segundos. Um timeout de 30s no agente cortaria justamente o caso que se quer ver.
        """
        path = self.path[len(prefixo) :]
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = urllib.request.Request(
            base + path,
            data=body,
            method=self.command,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                self.send_response(r.status)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
        except OSError as e:
            data = json.dumps({"erro": f"destino inacessível ({base}): {e}"}).encode()
            self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _servir_pagina(self) -> None:
        """Serve um arquivo de `paginas/`. Existe para a pagina de quem ja tem uma tela.

        Uma pagina servida daqui e' MESMA ORIGEM que `/engine` e `/agente`, entao ela dirige
        o ambiente sem token de servico da Cloudflare, sem CORS, e continua atras do Access
        que protege este hostname. Ver `paginas/README.md` para o porque disso ser melhor que
        emitir uma credencial que contorna autenticacao humana.
        """
        pedido = self.path[len("/p/") :].split("?")[0]
        # `basename` remove qualquer `../`; o `resolve` + comparacao de pai abaixo fecha
        # tambem link simbolico. Dois controles porque um so' e' um controle.
        nome = os.path.basename(pedido)
        alvo = (PAGINAS / nome).resolve()
        tipo = TIPOS.get(alvo.suffix.lower())
        if not nome or tipo is None or alvo.parent != PAGINAS.resolve() or not alvo.is_file():
            self._responder_json(
                404, {"erro": f"pagina nao encontrada: {nome!r}", "disponiveis": _paginas_disponiveis()}
            )
            return
        dados = alvo.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.startswith("/engine"):
            self._proxy(ENGINE, "/engine")
            return
        if self.path.startswith("/p/"):
            self._servir_pagina()
            return
        # Os links do Cockpit no HTML foram escritos para o ambiente local. Trocar na
        # hora de servir mantem o template intocado e evita duas copias da pagina.
        page = (
            HTML.replace("http://localhost:8080", COCKPIT_URL)
            .replace("__AGENTE_CONFIGURADO__", "1" if AGENTE else "")
            .encode()
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/engine"):
            self._proxy(ENGINE, "/engine")
            return
        # Rota ESTREITA e comparacao EXATA: `startswith` deixaria `/receptor/simular/algo`
        # cair aqui tambem, e esta e' a ultima rota onde se quer folga.
        if self.path == "/receptor/simular":
            self._simular_whatsapp()
            return
        if self.path.startswith("/agente"):
            if not AGENTE:
                self._responder_json(503, {"erro": "AGENT_INGRESS_URL nao configurada neste canal"})
                return
            # 180s: um turno com modelo real pode levar dezenas de segundos, e o que se quer
            # ver e' exatamente esse caso.
            self._proxy(AGENTE, "/agente", timeout=180)
            return
        self.send_response(404)
        self.end_headers()

    def _simular_whatsapp(self) -> None:
        """Assina um envelope sintetico de WhatsApp e o encaminha ao receptor.

        POR QUE ISTO EXISTE AQUI E NAO NA PAGINA. O receptor valida
        `X-Hub-Signature-256: sha256=HMAC(app_secret, corpo_bruto)` em
        `whatsapp/security.py::verify_hub_signature` e devolve 401 antes de olhar o corpo —
        e esta certo, e' o que prova que a mensagem veio da Meta. Uma pagina no navegador
        so' conseguiria assinar carregando o segredo da Meta no JavaScript, o que e' PIOR
        do que o problema que resolve: o segredo passaria a viver num arquivo servido.
        Entao a assinatura mora no servidor, e o segredo nunca sai do container.

        O RISCO, DITO SEM RODEIO: esta rota produz assinaturas INDISTINGUIVEIS das da Meta.
        Quem alcanca este canal consegue fabricar uma mensagem de beneficiario. Tres cercas
        contem isso, e as tres precisam continuar valendo:

          1. `CANAL_SIMULAR_RECEPTOR=1` — a rota nao existe sem isso (`SIMULAR_LIGADO`), e
             `scripts/ci/check_canal_simular.py` reprova quem a ligar fora de dev.
          2. `FAIXA_TESTE` — so' `55119000000xx`, ancorado nas duas pontas. Sem isso o canal
             forjaria mensagem em nome de um numero real.
          3. O Cloudflare Access na frente. E' a mesma fronteira de identidade que o proxy
             arbitrario de `/engine` ja depende, e nao e' menos necessaria aqui.

        NADA do que o cliente envia entra no calculo da assinatura sem passar por este
        molde: o envelope e' construido AQUI, campo por campo, a partir de dois valores
        validados. Um envelope vindo do navegador seria assinado como veio.
        """
        if not SIMULAR_LIGADO:
            self.send_response(404)
            self.end_headers()
            return
        if not RECEPTOR or not APP_SECRET:
            self._responder_json(503, {"erro": "RECEPTOR_URL ou WHATSAPP_APP_SECRET ausente"})
            return

        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            pedido = json.loads(self.rfile.read(tamanho) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._responder_json(400, {"erro": "corpo nao e' JSON valido"})
            return
        if not isinstance(pedido, dict):
            self._responder_json(400, {"erro": "corpo deve ser um objeto JSON"})
            return

        texto = str(pedido.get("texto") or "").strip()
        telefone = str(pedido.get("telefone") or "").strip()

        if not texto:
            self._responder_json(400, {"erro": "texto vazio"})
            return
        if len(texto) > TEXTO_MAX:
            self._responder_json(400, {"erro": f"texto acima de {TEXTO_MAX} caracteres"})
            return
        if not FAIXA_TESTE.match(telefone):
            self._responder_json(400, {"erro": "telefone fora da faixa de teste 55119000000xx"})
            return

        envelope = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "type": "text",
                                        "from": telefone,
                                        "id": "wamid.CANAL-TESTE-" + uuid.uuid4().hex[:20],
                                        "text": {"body": texto},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ],
        }

        # OS MESMOS BYTES QUE VIAJAM SAO OS BYTES ASSINADOS. Serializar uma vez para assinar
        # e outra para enviar produz assinatura invalida por uma virgula de diferenca — e o
        # sintoma e' um 401 que parece "segredo errado". Este e' o bug obvio desta rota.
        corpo = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        assinatura = "sha256=" + hmac.new(APP_SECRET.encode("utf-8"), corpo, hashlib.sha256).hexdigest()

        req = urllib.request.Request(
            RECEPTOR + "/webhook",
            data=corpo,
            method="POST",
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": assinatura},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                self._responder_json(r.status, {"receptor": r.read().decode("utf-8", "replace")})
        except urllib.error.HTTPError as e:
            self._responder_json(e.code, {"receptor": e.read().decode("utf-8", "replace")})
        except OSError as e:
            self._responder_json(502, {"erro": f"receptor inacessivel ({RECEPTOR}): {e}"})

    def _responder_json(self, status: int, corpo: dict[str, object]) -> None:
        data = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # silencioso


def main() -> None:
    """Sobe o servidor. Log de uma linha, para aparecer no CloudWatch no boot."""
    print(
        f"canal de teste ouvindo em {BIND}:{PORT} | motor: {ENGINE} | cockpit: {COCKPIT_URL} "
        f"| agente: {AGENTE or '(nao configurado)'} | paginas: {_paginas_disponiveis() or '(nenhuma)'} "
        f"| /receptor/simular: {'LIGADA -> ' + RECEPTOR if SIMULAR_LIGADO and RECEPTOR and APP_SECRET else 'desligada'}",
        flush=True,
    )
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

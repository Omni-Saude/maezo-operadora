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
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: REST do engine. O default local existe para `python -m` na maquina do dev; em
#: container a variavel e' sempre passada pela task definition.
ENGINE = os.environ.get("ENGINE_REST_URL", "http://127.0.0.1:8080/engine-rest").rstrip("/")

PORT = int(os.environ.get("CANAL_PORT", "8500"))

#: 0.0.0.0 e' obrigatorio em container: ligado em 127.0.0.1 o processo sobe, o health
#: check falha e o servico entra em ciclo de substituicao sem dizer por que.
BIND = os.environ.get("CANAL_BIND", "0.0.0.0")  # noqa: S104 - ver comentario acima

#: Link do Cockpit exibido na pagina. Local aponta para localhost; na AWS aponta para
#: o hostname publico atras do Access.
COCKPIT_URL = os.environ.get("COCKPIT_URL", "http://localhost:8080")

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
<section class="card">
  <h2>Lançar solicitação</h2>
  <label>Processo</label>
  <select id="proc" onchange="preset()"></select>
  <label>Business key (auto)</label>
  <input id="bk">
  <label>Variáveis de entrada <span style="text-transform:none;letter-spacing:0">— deixe vazio p/ testar a Rodada A</span></label>
  <table class="vars" id="vars"></table>
  <div class="row">
    <button class="ghost" onclick="addRow('','','String')">+ campo</button>
    <button class="primary" onclick="lancar()">Lançar no motor</button>
  </div>
  <div id="resultado"></div>
  <p class="hint">Valores vazios não são enviados. Tipos: texto → String, true/false → Boolean, número → Integer/Double. Dado real: use os IDs pseudonimizados da amostra (patient-…), nunca nome/CPF.</p>
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
const PRESETS = {
 "SP-OP-AUTH-001":{pre:"AUTH",vars:{numero_guia_tiss:"",prestador_id:"",codigo_procedimento_tuss:"",carater_atendimento:""}},
 "SP-OP-CONTAS-001":{pre:"CONTAS",vars:{numero_lote_tiss:"",prestador_id:"",competencia:"",tipo_lote:""}},
 "SP-OP-RECURSO-001":{pre:"RECURSO",vars:{numero_guia_tiss:"",glosa_id:"",prestador_id:"",glosa_type:""}},
 "SP-OP-NIP-001":{pre:"NIP",vars:{protocolo_ans:"",beneficiario_id:""}},
 "SP-OP-INADIMPLENCIA-001":{pre:"INAD",vars:{numero_contrato:"",beneficiario_id:""}},
 "SP-OP-CRED-001":{pre:"CRED",vars:{prestador_id:"",tipo_prestador:""}},
 "SP-OP-REEMBOLSO-001":{pre:"REEMB",vars:{beneficiario_id:"",protocolo:""}},
 "SP-OP-CANCEL-001":{pre:"CANCEL",vars:{numero_contrato:"",beneficiario_id:"",motivo:""}},
 "SP-OP-ADEQUACAO-001":{pre:"ADEQ",vars:{beneficiario_id:"",municipio:""}},
 "SP-OP-FRAUDE-001":{pre:"FRAUDE",vars:{prestador_id:""}},
 "SP-OP-PROGRAMA-001":{pre:"PROG",vars:{beneficiario_id:"",programa:""}},
 "SP-OP-PAGTO-001":{pre:"PAGTO",vars:{prestador_id:"",competencia:""}},
 "SP-OP-ESCALATION-001":{pre:"ESC",vars:{origem:"",motivo:""}},
 "SP-OP-LGPD-DSR-001":{pre:"DSR",vars:{titular_pseudo_id:"",tipo_solicitacao:""}},
 "SP-OP-ANS-SUBMIT-001":{pre:"ANSSUB",vars:{report_type:"",competencia:""}}
};
const $=id=>document.getElementById(id);
function opt(){const s=$("proc");for(const k of Object.keys(PRESETS)){const o=document.createElement("option");o.value=o.textContent=k;s.appendChild(o)}}
function addRow(n,v,t){const tr=document.createElement("tr");
 tr.innerHTML=`<td><input placeholder="nome" value="${n}"></td><td><input placeholder="valor" value="${v}"></td>
 <td><select><option>String</option><option>Boolean</option><option>Integer</option><option>Double</option></select></td>
 <td><button class="del" onclick="this.closest('tr').remove()">×</button></td>`;
 tr.cells[2].firstElementChild.value=t;$("vars").appendChild(tr)}
function preset(){const p=PRESETS[$("proc").value];$("vars").innerHTML="";addRow("tenant_id","amh","String");
 for(const k of Object.keys(p.vars))addRow(k,"","String");
 $("bk").value=`${p.pre}-amh-TESTE-${new Date().toISOString().slice(5,16).replace(/[-:T]/g,"")}`}
async function api(path,opts){const r=await fetch("/engine"+path,opts);
 if(!r.ok)throw new Error(r.status+" "+await r.text());return r.status==204?null:r.json()}
function coerce(v,t){if(t=="Boolean")return v.trim().toLowerCase()=="true";
 if(t=="Integer")return parseInt(v,10);if(t=="Double")return parseFloat(v);return v}
async function lancar(){const vars={};
 for(const tr of $("vars").rows){const[n,v]=[tr.cells[0].firstElementChild.value.trim(),tr.cells[1].firstElementChild.value.trim()];
  const t=tr.cells[2].firstElementChild.value;if(n&&v!=="")vars[n]={value:coerce(v,t),type:t}}
 const body={businessKey:$("bk").value,variables:vars};const out=$("resultado");
 try{const r=await api(`/process-definition/key/${$("proc").value}/start`,
  {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  out.innerHTML=`<div class="okmsg">Instância iniciada: <span class="mono">${r.id}</span></div>`;
  $("iid").value=r.id;setTimeout(verEvidencia,2500);setTimeout(verEvidencia,9000);recentes()}
 catch(e){out.innerHTML=`<div class="errmsg">Falha ao iniciar:\n${e.message}</div>`}}
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
opt();preset();recentes();setInterval(recentes,15000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _proxy(self) -> None:
        path = self.path[len("/engine") :]
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = urllib.request.Request(
            ENGINE + path,
            data=body,
            method=self.command,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                self.send_response(r.status)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
        except OSError as e:
            data = json.dumps({"erro": f"motor inacessível: {e}"}).encode()
            self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.startswith("/engine"):
            self._proxy()
            return
        # Os links do Cockpit no HTML foram escritos para o ambiente local. Trocar na
        # hora de servir mantem o template intocado e evita duas copias da pagina.
        page = HTML.replace("http://localhost:8080", COCKPIT_URL).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/engine"):
            self._proxy()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # silencioso


def main() -> None:
    """Sobe o servidor. Log de uma linha, para aparecer no CloudWatch no boot."""
    print(f"canal de teste ouvindo em {BIND}:{PORT} | motor: {ENGINE} | cockpit: {COCKPIT_URL}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

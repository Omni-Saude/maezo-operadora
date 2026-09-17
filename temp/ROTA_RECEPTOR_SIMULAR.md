# Rota `/receptor/simular` no Canal de Teste — o que falta para a página do escalonamento

Entrega de duas peças:

1. **`escalonamento.html`** — pronta. Vai para `src/maezo/platform/testchannel/paginas/escalonamento.html`.
   Nenhuma configuração: `server.py` lista o diretório em tempo de requisição.
2. **A rota `/receptor/simular`** — descrita aqui. É a única coisa a construir.

---

## Por que não dá para a página falar direto com o receptor

O receptor valida `X-Hub-Signature-256` em `security.py::verify_hub_signature`:

```
sha256=<hmac_sha256(WHATSAPP_APP_SECRET, corpo_bruto)>
```

Sem assinatura válida ele devolve **401** antes de olhar o corpo — e está certo: é o que
prova que a mensagem veio da Meta.

Uma página no navegador só conseguiria assinar carregando o segredo da Meta no JavaScript.
Isso é pior do que o problema que resolve: o segredo passaria a viver num arquivo servido,
legível por qualquer pessoa que abrisse a página.

**A assinatura tem que acontecer no servidor.** O Canal roda no cluster, então é o lugar certo.

---

## O que a rota faz

`POST /receptor/simular` com `{"texto": "...", "telefone": "5511900000001"}`

1. monta o envelope da Meta (formato que `dispatch.py::extract_inbound_messages` já parseia);
2. serializa **uma vez** e assina **exatamente esses bytes** — assinar um objeto e serializar
   de novo depois produz assinatura inválida por uma vírgula de diferença;
3. encaminha para `RECEPTOR_URL/webhook` com o cabeçalho;
4. devolve o status do receptor para a página.

O segredo nunca sai do container.

---

## Mudanças no Terraform (`service-canal-teste.tf`)

```hcl
environment = [
  # ... o que já existe ...
  { name = "RECEPTOR_URL", value = "http://webhook-receiver.maezo-operadora-dev.internal:8080" },
]

secrets = [
  { name = "WHATSAPP_APP_SECRET", valueFrom = "<arn do maezo/dev/whatsapp/meta>:app_secret::" },
]
```

O Canal hoje tem `secrets: []` — este seria o primeiro.

---

## Código, em `server.py`

Duas constantes junto das que já existem:

```python
RECEPTOR = os.environ.get("RECEPTOR_URL", "").rstrip("/")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
```

E o tratamento em `do_POST`, antes do bloco de `/agente`:

```python
if self.path == "/receptor/simular":
    self._simular_whatsapp()
    return
```

```python
def _simular_whatsapp(self) -> None:
    """Assina um envelope sintético e encaminha ao receptor.

    A assinatura mora AQUI porque o segredo não pode ir para o navegador. A rota é
    deliberadamente estreita: só mensagem de texto, só telefone de teste, e nada do que
    chega do cliente entra no cálculo da assinatura sem passar por este molde.
    """
    if not RECEPTOR or not APP_SECRET:
        self._responder_json(503, {"erro": "RECEPTOR_URL ou WHATSAPP_APP_SECRET ausente"})
        return

    tamanho = int(self.headers.get("Content-Length") or 0)
    pedido = json.loads(self.rfile.read(tamanho) or b"{}")
    texto = str(pedido.get("texto") or "").strip()
    telefone = str(pedido.get("telefone") or "").strip()

    if not texto:
        self._responder_json(400, {"erro": "texto vazio"})
        return

    # CERCA: só número de teste. Impede que este canal forje uma mensagem em nome de
    # um telefone real — a assinatura que ele produz é indistinguível da da Meta.
    if not telefone.startswith("55119000000"):
        self._responder_json(400, {"erro": "telefone fora da faixa de teste 55119000000xx"})
        return

    envelope = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [{
            "type": "text",
            "from": telefone,
            "id": "wamid.CANAL-TESTE-" + uuid.uuid4().hex[:20],
            "text": {"body": texto},
        }]}}]}],
    }

    # Os MESMOS bytes que viajam são os bytes assinados.
    corpo = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    assinatura = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"), corpo, hashlib.sha256
    ).hexdigest()

    req = urllib.request.Request(
        RECEPTOR + "/webhook",
        data=corpo,
        method="POST",
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": assinatura},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            self._responder_json(r.status, {"receptor": r.read().decode("utf-8", "replace")})
    except urllib.error.HTTPError as e:
        self._responder_json(e.code, {"receptor": e.read().decode("utf-8", "replace")})
```

Importações a acrescentar: `hmac`, `hashlib`, `uuid`.

---

## O risco, dito sem rodeio

Esta rota produz assinaturas **indistinguíveis das da Meta**. Quem alcança o Canal consegue
fabricar uma mensagem de beneficiário.

Três coisas contêm isso, e as três precisam continuar valendo:

1. **O Cloudflare Access na frente** — mesma porta pela qual as pessoas já entram. É a mesma
   exposição que o `README.md` das páginas já aceita conscientemente para o proxy do motor.
2. **A cerca do telefone** — só a faixa `55119000000xx`. Sem ela, o Canal forja mensagem em
   nome de um número real.
3. **Só em dev.** Em produção esta rota não deve existir. Vale uma cerca de CI que falhe se
   `_simular_whatsapp` aparecer num ambiente que não seja `dev-sa-east-1`.

Se algum de vocês achar que isso não se sustenta, a alternativa é a página disparar o processo
direto no motor e abrir mão de testar a triagem. Perde-se metade do teste, mas é uma troca
legítima — e a decisão é de vocês, não minha.

---

## Como saber que funcionou

Com a rota no ar, abrir a página e enviar *"estou com uma dor muito forte no peito e falta de ar"*
deve produzir, em poucos segundos: bandeira vermelha **SIM**, escalonamento aberto, prioridade
**P1**, fila `plantao-clinico`, prazos de 5 e 30 minutos, e uma tarefa esperando.

Se aparecer **401** na tela, a assinatura não bateu — quase sempre é o corpo ter sido
serializado duas vezes.

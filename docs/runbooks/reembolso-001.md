# Reembolso pelo WhatsApp e pelo portal (dev): `MAEZO_REEMBOLSO_V1`

**Status:** rascunho, SÓ no ambiente de desenvolvimento (dev). Decisões do dono de 28 e 29/10/2026
(`docs/decisions-log.md`, DL-0159, DL-0160 e DL-0161), a partir da planilha `Reembolso_Pronto_x_Falta_v2.xlsx`.
**Revisão jurídica dos textos, DPO (retenção dos anexos e as cinco perguntas de PHI) e operação antes de
produção.** Contrato: `docs/processes/contracts/SP-OP-REEMBOLSO-001.md` v0.2.0 (rascunho, sem sign-off).

## 1. O que é, em linguagem simples

**Todo pedido de reembolso vai para análise humana.** Não há cálculo, tabela, teto nem aprovação automática
(DL-0159). O sistema só **organiza** o pedido, os documentos e a resposta.

1. A pessoa escreve "quero um reembolso" no WhatsApp. O **Lucas** conduz, sem modelo, com perguntas fechadas:
   - tem a nota fiscal? Se não tiver, encerra e **nenhum processo é aberto**;
   - para o titular ou um dependente?;
   - consulta, exame ou terapia? Se for exame ou terapia, pergunta se tem o pedido médico;
   - como foi o atendimento;
   - a data e o valor pago;
   - fora da rede: o motivo.
2. **Sem OCR.** O Lucas pede a foto ou o PDF do pedido médico (exame ou terapia) e da nota fiscal. Aceita
   **vários arquivos por tipo** e pergunta "Você terminou de enviar o …?". Os arquivos vão para o **S3 da Zona
   PHI**, cifrados com SSE-KMS.
3. Resumo, "Sim", e o processo **SP-OP-REEMBOLSO-001** abre com protocolo `REE-XXXX-XXXX`. O Lucas **nunca** diz
   se há direito, nunca calcula e nunca promete prazo ou pagamento.
4. **Portal: o analista** (`UT_AnaliseReembolso`, grupo `analise-reembolso`) abre a tarefa e vê:
   - os dados do pedido;
   - o prazo informativo;
   - os documentos (botão para baixar cada um).

   Ele decide: **Aprovar** (valor), **Aprovar parcial** (valor + justificativa + fundamentação + texto à
   beneficiária), **Negar** (justificativa + fundamentação + texto), **Pedir mais informações** (o que pedir) ou
   **Escalar ao médico auditor**.
5. **Pedir mais informações:**
   - o Lucas avisa a beneficiária com o texto do analista;
   - ela responde por texto e/ou arquivos e termina com **TERMINEI**;
   - o pedido volta à análise com a resposta e os arquivos novos visíveis no portal.
6. Aprovado: o **financeiro** (`UT_RegistrarPagamento`, grupo `financeiro-reembolso`) paga **fora do sistema** e
   registra **"Pago em"**. A beneficiária recebe "o pagamento foi feito em DD/MM/AAAA".
7. **Avisos à beneficiária** (retomada `agent-resume`, histórico do motor conferido):
   - os cinco avisos são aprovado, parcial, negado, pedido de informação e pago;
   - nos avisos de negado, parcial e pedido de informação vai o texto **escrito pelo analista**, entre aspas, como escrito;
   - esse texto passa pelas cercas de saída; se for barrado, o aviso sai sem ele e diz isso.

**Janela de 24 h da Meta:** o aviso só sai se a última mensagem da pessoa tiver menos de 24 h. Fora disso, nada sai
pelo WhatsApp (precisaria de template aprovado) e a equipe recebe o aviso para contatar por outro canal.

## 2. As chaves e a configuração

| | |
|---|---|
| Chave do canal | `MAEZO_REEMBOLSO_V1` (`reembolso_v1`, default `false` = canal de antes, byte a byte) |
| Custódia dos anexos | `reembolso_anexos_enabled` (Terraform, default `false`; exige `recipient_vault_enabled = true`) |
| Receptor recebe | `MAEZO_REEMBOLSO_ANEXOS_BUCKET`, `MAEZO_REEMBOLSO_ANEXOS_KMS_KEY_ARN` (só gravar) |
| Portal recebe | `MAEZO_PORTAL_REEMBOLSO_ANEXOS_BUCKET` (só ler); conclusão e leituras pelo portão da DL-0049 (`MAEZO_PORTAL_DIRECT_COMPLETION`, já ligado em dev) |
| Pré-condições do canal | Lucas (`roteador_lucas`), acesso do beneficiário (`acesso_beneficiario`), custódia de anexos, `database_url` |
| Sem pré-condição | o receptor sobe **sem** o reembolso e registra `reembolso_v1_sem_pre_condicoes` com o que falta |

**Credencial no portal: não há segredo novo.** Quem usa o portal precisa de:
- um **usuário no Cognito**;
- uma **linha em `portal_memberships`** com grupo e papel do reembolso.

| Pessoa de teste | Grupo | Papel |
|---|---|---|
| analista | `analise-reembolso` | `reembolso-analisar` |
| coordenação | `coordenacao-reembolso` | `reembolso-analisar` |
| médico auditor | `medico-auditor` | `reembolso-auditar` |
| financeiro | `financeiro-reembolso` | `reembolso-pagar` |

Criação: `docs/runbooks/portal-dev-provisionamento.md` §3.8 e `scripts/ops/portal_semear_membership.py`
(`PESSOAS='[{"subject":"<sub>","groups":["analise-reembolso"],"roles":["reembolso-analisar"]}]'`). Um usuário de
teste que já existe pode ganhar os grupos e papéis do reembolso, sem criar outro.

## 3. Ordem de subida (execução na AWS é do dono)

Nenhum destes passos foi executado por quem implementou. **O plugin do motor sobe ANTES do catálogo**; senão
o plugin não conhece as tarefas novas e **todas as filas do portal caem com 503**.

1. **Terraform** (`deploy/aws-ecs/envs/dev-sa-east-1`): `reembolso_anexos_enabled = true` cria bucket, chave KMS
   e as duas políticas (receptor grava, portal lê). Rodar `terraform fmt`/`validate`/`plan` (não rodados localmente).
2. **Banco:** migration `0031_reembolso_coleta`.
3. **BPMN/DMN:**
   - publicar o BPMN v2, que agora traz `camunda:formData` nas cinco tarefas e `aguardando_resposta`, e `reembolso_prazo.dmn`;
   - `reembolso_calculo` e `reembolso_coverage` deixam de existir;
   - conferir com GET que a definição nova está ativa.
4. **Imagens:**
   1. o **plugin do motor** (`PortalReadCommand.COMPILED`: as cinco tarefas como `BPMN_FORMDATA`, campos novos e `UT_RegistrarPagamento`);
   2. o worker;
   3. o `agent-resume`;
   4. o receptor do WhatsApp;
   5. o **portal** (tela do reembolso, conclusão com campos e leitura de anexos).
5. **Catálogo do portal:** `scripts/ops/staff_material_reembolso_catalogo.py --process-id … --bpmn-sha256 … --out
   <dir>`.
   - Lê o segredo `maezo-operadora/dev/staff-job/materials` (só leitura), monta as 5 tarefas como `BPMN_FORMDATA` e imprime só digests.
   - Validar com `review_admission` antes de aplicar.
   - Aplicar pelo `docs/runbooks/material-staff-na-aws.md`, passo 5.
6. **Pessoas e memberships** (seção 2).
7. **Ligar a chave** `reembolso_v1 = true`.

## 4. Roteiro de teste em dev

Pelo WhatsApp real (número de teste, já verificado no acesso do beneficiário):

1. **Sem documentos:** "quero um reembolso" → "Não" na nota fiscal → encerra, **nenhum processo**.
2. **Pedido completo:**
   - consulta, titular, livre escolha, data, valor;
   - 2 fotos da NF e "Sim" para terminou;
   - resumo e "Sim";
   - resultado: protocolo `REE-…`.
3. **Portal (analista):**
   - a tarefa aparece na fila de `analise-reembolso`;
   - dados do pedido e **2 documentos** que abrem;
   - escolher **Pedir mais informações** e escrever o que pedir.
4. **WhatsApp:**
   - chega o pedido de informação com o texto do analista;
   - responder com um texto e uma foto, depois **TERMINEI**;
   - resultado: "Enviei a sua resposta para a nossa equipe".
5. **Portal:**
   - a tarefa volta;
   - aparecem a resposta e o arquivo novo ("Arquivo da resposta");
   - **Aprovar parcial** com valor e os três textos.
6. **WhatsApp:** aviso de parcial com o texto do analista.
7. **Portal (financeiro):**
   - `UT_RegistrarPagamento` mostra decisão e valor aprovado;
   - preencher **Pago em** e Registrar.
8. **WhatsApp:** "o pagamento foi feito em DD/MM/AAAA".
9. **Pedido repetido:** confirmar de novo o mesmo pedido devolve o **mesmo protocolo**.

Conclua cada passo dentro de 24 h da última mensagem da pessoa. Conferir o motor só com GET (task `bateria`,
`scripts/ops/bateria_vpc_run.sh`).

## 5. O que garante que nada sai do desenho

- **L0 hard:** negativa e redução só por humano.
  - O worker recusa sem `texto_ao_beneficiario` e sem `analista_id`/`auditor_id`, e esses dois quem preenche é o servidor do portal.
  - `GW_DecisaoReembolso` não tem caminho padrão.
- **Arquivo:**
  - só JPG, PNG ou PDF, até 10 MiB, conferido antes e depois de baixar;
  - chave aleatória, sem nome de arquivo;
  - sha256 registrado no processo e conferido pelo portal antes de entregar (princípio do ADR-0020);
  - o receptor não lê de volta e o portal não grava (IAM).
- **PHI:**
  - os textos do analista e a resposta da beneficiária nunca viajam em evento;
  - a retomada lê os textos do analista por nome no histórico;
  - o parser recusa evento que os carregue;
  - a trilha do portal guarda só o digest dos campos;
  - a coleta guarda só enums, data, valor e referências; a resposta da beneficiária fica só até o "TERMINEI".
- **Portal:**
  - as leituras do reembolso e do anexo passam pela mesma autorização da tarefa (grupo e papel), com o mesmo portão e a mesma cerca de ambiente da DL-0049/0050;
  - cada leitura vira um elo na trilha (`portal_read.reembolso_*`).

## 6. Pendências que bloqueiam produção

| Pendência | Quem |
|---|---|
| Revisão jurídica de todos os textos (`domain/reembolso/pedido.py`, versão `reembolso-v2`) | jurídico |
| Retenção dos anexos (5 anos é proposta) e as cinco perguntas de PHI (`DISPOSITIONS`) | DPO |
| Prazo de reembolso (`reembolso_prazo.dmn`) e base RN 566/2022 | jurídico |
| Template Meta para aviso fora da janela de 24 h | operação |
| Pessoas reais nos grupos | operação |
| Imutabilidade dos anexos (Object-Lock), se o jurídico exigir: o destino é a Zona PHI do lake, por contrato com o datalake (DL-0161) | jurídico + datalake |
| A leitura e a conclusão interinas do portal saem quando o modelo de leitura assinado carregar o reembolso (dívida da DL-0049/0050) | engenharia |

## 7. Onde está o código

| Peça | Arquivo |
|---|---|
| Máquina de estados, textos, protocolo | `src/maezo/domain/reembolso/pedido.py` |
| Turno (coleta, arquivo, abertura, resposta) | `src/maezo/platform/webhooks/whatsapp/reembolso_turno.py` |
| Coleta (CAS) | `src/maezo/platform/webhooks/whatsapp/reembolso_store.py` + migration `0031_reembolso_coleta.py` |
| Custódia dos anexos | `src/maezo/gateway/reembolso_anexos.py` + `deploy/aws-ecs/envs/dev-sa-east-1/reembolso-anexos.tf` |
| Download da mídia da Meta | `src/maezo/tools/mcp_whatsapp/server.py::WhatsAppMediaClient` |
| Retorno (histórico, janela, cercas) | `platform/integrations/agent_resume.py`, `webhooks/whatsapp/lucas_retomada.py::retomar_reembolso` |
| Portal: contexto, anexo, conclusão | `gateway/human/reembolso_context.py`, `gateway/human/gateway.py`, `portal/api/tasks.py`, `portal/contracts/reembolso.py` |
| Portal: tela | `portal/web/src/ReembolsoTaskPanel.tsx`, `reembolsoClient.ts` |
| Catálogo do portal | `scripts/ops/staff_material_reembolso_catalogo.py` |

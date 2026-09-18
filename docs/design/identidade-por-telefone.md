# Identidade por telefone — o levantamento, medido

**Status: LEVANTAMENTO, 15/09/2026. Passo 1.1 de `HELENA_EM_PRODUCAO_O_QUE_FALTA.md`.**
O documento do diretor pede, textualmente, *"as respostas medidas, não estimadas"*, e avisa:
*"se o telefone não estiver no ERP com qualidade, a Frente 1 muda de forma"*.

**Ela muda.** Não pela qualidade do telefone — **o telefone não está no lago, e o MPI não é dos
beneficiários da operadora.** As duas coisas foram medidas, não deduzidas, e estão abaixo com a
consulta que as produziu.

| Peça | Estado medido | Quem fecha |
|---|---|---|
| `amh_mpi.patient.phone_hash` | **0 preenchidos em 252.094** | engenharia, depois da decisão de esquema |
| Pipeline que escreveria o telefone | **não existe** — `build_mpi_hmac.py` produz cpf/nome/nascimento e mais nada | engenharia |
| `amh_mpi.patient_source` | **não tem coluna de telefone nenhuma** | engenharia |
| Fonte do MPI | **`tasy_hospital` / tenant `austa_clinicas`** — o hospital, não a operadora | dono do produto |
| Demografia da operadora no lago | **não ingerida** — 32 tabelas em `amh_omni_bronze`, nenhuma de pessoa física | plataforma de dados |
| Frescor | **parado em 24/07/2026** (`last_seen_at`) | plataforma de dados (CDC parado em 17/07) |
| `amh_mpi.consent_log` | **0 linhas** | DPO + engenharia |
| Esquema de hash | **três incompatíveis entre si** (abaixo) | decisão de arquitetura |

---

## Como foi medido

Athena, `sa-east-1`, conta `203312548462`, workgroups `amh-mpi-analytics-dev` e
`amh-omni-analytics-dev`, em 15/09/2026. Cada número abaixo é o resultado de uma consulta, não uma
leitura de documentação.

---

## 1 · O `phone_hash` do MPI usaria a mesma chave que o receptor?

**Não, e a pergunta é menos grave do que a resposta.** Não há uma chave diferente — há **três
esquemas diferentes**, e nenhum dos três produz o valor que o outro produz.

| Onde | Como | Entrada |
|---|---|---|
| Receptor da Helena (`webhooks/whatsapp/security.py::hash_phone`) | **HMAC-SHA256 com `PHI_HMAC_KEY`**, saída marcada `hk1_` | `"{tenant}:{telefone}"` |
| Casador do MPI (`mpi/deterministic_matcher.py::hash_identifier`) | **`sha256(salt + valor)`** — salt por tenant no KMS, **não é HMAC** | telefone só com dígitos |
| Construtor do MPI (`mpi/build_mpi_hmac.py`) | `HMAC-SHA256(chave_do_tenant, "prefixo:valor")` | **não trata telefone** |

Três consequências, em ordem de importância:

1. **Popular a coluna não resolve nada sozinho.** Escrever `phone_hash` com o esquema do casador
   produziria um valor que o receptor nunca gera. A decisão que falta não é de pipeline, é de
   esquema: **qual dos três vale**, e quem reescreve os outros dois.
2. **O esquema do casador tem o defeito que o maezo recusou por escrito.** `security.py` documenta
   por que um `sha256` de telefone brasileiro é reversível — cerca de 6,7×10⁹ chaves, uma tabela
   pré-computada em GPU resolve. O `hash_identifier` é `sha256(salt+valor)`: com o salt
   comprometido, ou entre duas pessoas que o conhecem, volta a ser reversível. É a mesma razão pela
   qual `cpf_hash` e `cns_hash` **já foram migrados** para HMAC (comentário do próprio
   `patient.sql`, ADR-041 §6) — **o telefone ficou para trás nessa migração.**
3. **O `tenant:` dentro do HMAC do receptor não é decoração.** Ele mantém o pseudônimo distinto por
   tenant sob uma chave compartilhada. Qualquer esquema escolhido tem de preservar essa propriedade,
   ou o mesmo telefone em duas PJs vira a mesma chave de cruzamento.

---

## 2 · Onde o telefone vive no ERP, e em que qualidade?

**Não vive no lago.** `amh_omni_bronze` tem **32 tabelas** e nenhuma é de pessoa física:

```
tasy_pls_segurado, tasy_pls_contrato, tasy_pls_plano, tasy_pls_mensalidade,
tasy_pls_guia_plano, tasy_pls_requisicao, tasy_pls_prestador*, tasy_ctb_*, tasy_titulo_*
```

`tasy_pls_segurado` (293.215 linhas) tem `cd_pessoa_fisica` — **a chave estrangeira para a tabela
que não foi ingerida.** Nome, CPF, telefone, e-mail e endereço moram em `pessoa_fisica`, no Tasy, e
nada disso chegou aqui. A camada FHIR da operadora tem **uma** tabela: `fhir_coverage`.

O próprio `build_mpi_hmac.py` já registra esse buraco em comentário: *"Sem demografia, o perfil
`tasy_pls` deixa os hashes e os campos em claro NULOS."* Ou seja: **está documentado no código que
o caminho da operadora nasce sem demografia.**

> **Um achado colateral que vale conferir com a plataforma de dados:** `SHOW COLUMNS` em
> `tasy_pls_segurado` devolve, para o meu principal, apenas `_ingestion_ts`, `_ingestion_date` e
> `_load_type` — as 40 colunas de negócio declaradas no Glue não resolvem na consulta. Ou é permissão
> por coluna do Lake Formation, ou os arquivos não carregam o que o catálogo declara. Nos dois casos,
> **quem for popular o telefone esbarra nisso antes de esbarrar no telefone.**

---

## 3 · O MPI é dos beneficiários da Helena?

**Não.** Esta é a descoberta que mais muda a forma da frente, e ela é de uma consulta só:

```sql
SELECT source_system, tenant_id, count(*) FROM amh_mpi.patient_source GROUP BY 1,2
-- tasy_hospital | austa_clinicas | 252.809
```

**Uma única fonte, e é o hospital.** Os 252.094 registros e os 218.985 `subject_ref` ativos são
pacientes do `austa_clinicas`. Os beneficiários com quem a Helena fala são segurados da operadora —
outro tenant, outro sistema, e **não estão no MPI como fonte**.

A interseção não é vazia (quem tem plano e se trata na rede própria aparece nos dois), mas **ninguém
mediu essa interseção**, e ela não é derivável hoje: fazer o cruzamento exige a demografia da
operadora, que é justamente o que falta.

Isso reordena a Frente 1. A pergunta original era *"como ligar telefone a sujeito"*. A pergunta
medida é **"o sujeito da operadora existe no MPI?"** — e hoje a resposta é não.

---

## 4 · Frescor

| Relógio | Última marca |
|---|---|
| `patient_source.last_seen_at` | **2026-07-24 18:44Z** |
| `patient.updated_at` | **2026-07-26 00:19Z** |

Sete semanas parado, e bate com o CDC estacionado em 17/07 por corte de custo (ADR-036) e com a VPN
ao Tasy sem tráfego entre 27/08 e 10/09. **Mesmo que o telefone existisse, o cadastro estaria com
sete semanas de atraso** — e telefone é o campo de cadastro que mais muda.

---

## 5 · Um telefone pode casar com mais de uma pessoa?

**Não medível hoje**, porque não há telefone para medir. Mas a resposta de desenho não depende da
medição, e é **sim**: titular e dependentes num aparelho é o caso comum, não a exceção — a própria
`tasy_pls_segurado` traz `nr_seq_titular` e `cd_segurado_familia`, que é o formato de uma família.

**Consequência que precisa estar no desenho desde já:** a resolução por telefone não pode devolver
*uma pessoa*. Ela devolve **um conjunto**, e um conjunto com mais de um elemento é um caso de
**perguntar**, nunca de escolher o primeiro. A conversa pediátrica de 13/09 é o exemplo vivo: a mãe
escreve do telefone dela sobre o filho.

---

## 6 · E quando o número não casa com ninguém?

O documento chama isto de *"o caso mais frequente e o mais perigoso de tratar mal"*. Com o medido
acima, ele deixa de ser o caso mais frequente e passa a ser **o único caso**: hoje, nenhum número
casa, porque não há com o quê casar.

Isso tem um lado bom que vale explorar: **o caminho degradado não é a exceção a ser testada depois —
é o caminho de produção.** Ele pode (e deve) ser construído e exercitado agora, antes de qualquer
ligação existir, o que inverte a ordem de risco usual.

O comportamento correto já está decidido pelo próprio documento e não precisa de mais discussão:
*"segue funcionando com o que a mensagem trouxer. A identificação melhora a triagem; a ausência dela
não pode travar o atendimento."*

---

## O que eu recomendo, e em que ordem

**Não construir 1.2 (popular a ligação) agora.** Popular uma coluna cujo esquema está indefinido, a
partir de uma fonte que não existe, num MPI que não é da população certa, seria a quinta casca vazia
deste projeto — e o documento nomeia as outras quatro.

Três decisões, nesta ordem, cada uma com dono diferente:

1. **Esquema de hash do telefone** *(arquitetura)*. Um dos três, e a migração dos outros dois. Minha
   recomendação: o HMAC com `PHI_HMAC_KEY` sobre `"{tenant}:{telefone}"`, porque é o único que já
   roda em produção contra tráfego real, e porque o lado do receptor não pode mudar sem quebrar
   todo `conversation_id` e `thread_id` de checkpoint já persistido.
2. **Ingerir a demografia da operadora** *(plataforma de dados)*. É pré-requisito de tudo, e passa
   por religar o CDC do Tasy — que tem dependência de rede fora do nosso alcance.
3. **Consentimento** *(DPO)*. Zero linhas, e os quatro endpoints de contexto o exigem. Pode ser
   desenhado em paralelo, porque não depende de 1 nem de 2.

**O que dá para fazer sem nenhuma das três, e é o que proponho fazer já:** o caminho degradado —
a Helena funcionando bem sem saber quem é a pessoa. É a Frente 2.1 (memória entre turnos), que
**não depende de identidade nenhuma** e resolve o defeito que realmente machucou um beneficiário:
o bebê de 11 meses triado pela tabela de adulto.

---

## A decisão de arquitetura que continua aberta

Independente de tudo acima, o ADR-0037 diz que a **única** costura de leitura clínica é o
`ClinicalContextPort` sobre a API de contexto. A plataforma de dados documentou e liberou **outro**
caminho: acesso direto ao HAPI FHIR com credencial por empresa.

São duas portas para a mesma sala, e a repo declara que só uma deveria existir. Não resolvi isso
aqui porque não é achado de medição — é escolha de dono. **Mas ela ficou mais barata de tomar
agora**, porque o medido acima mostra que nenhuma das duas portas tem, hoje, o dado que a Helena
precisa: escolher uma não atrasa nada, e adiar deixa a divergência crescer.

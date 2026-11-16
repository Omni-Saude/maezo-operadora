# ADR-0077: Ativação das regras do pacote K1 por classe de risco — estado efetivo por regra, registro de decisões, amostragem e desligamento por reversão (emenda ao ADR-0075 §(e))

**Status:** Proposed — redigido por AGENTE (T-MZO-A, Onda 2 item 5); o desenho foi aprovado pela orquestradora ROOT em 16/11/2026. Um agente não aceita ADR e nenhum campo de assinatura foi preenchido (ADR-0007). `/docs/adr/` é CODEOWNERS.
**Data:** 2026-11-16
**Área:** Integração / Regras de negócio / Motor de processos / Auditoria

## Contexto

O pacote K1 do `regras-contratos-operadora` (rco), com `formato_manifesto` 2.0.0, traz em cada item de `regras[]` dois campos novos:
- `ativacao` ∈ {`plena`, `conferida`, `em_correcao`};
- `exige_aditivo` (booleano).

Fonte: contrato `docs/programa/contrato-k1.md` §7 e §11.8 do rco, branch `prog/rco/ativacao-k1` @75015304, decisão PROG-COORD-16 do dono de 16/11/2026. O rco calcula esses campos em `scripts/pacote.py` (`calcular_ativacao`, `calcular_exige_aditivo`) com esta precedência:
1. regra listada na triagem de reprovadas: `em_correcao`;
2. senão, `classe_adverso` = `administrativo`: `conferida`;
3. senão (`contas`, `clinico`, sem classe): `plena`.

`exige_aditivo` é verdadeiro quando o changelog da regra tem uma mudança do tipo `restricao`.

Os parâmetros numéricos vêm no bloco `ativacao.parametros` de `vocabulario.json`. O código do Maezo os lê do pacote pinado e nunca os fixa. A tabela mostra os valores do pacote em 16/11/2026, todos provisórios no registro de calibração do rco:

| Parâmetro | Valor em 16/11 | Uso no contrato K1 §7 |
|---|---|---|
| `revisao_inicial_decisoes` | 30 | revisão de 100% das primeiras glosas de contas (D1), por regra |
| `revisao_inicial_prazo_dias` | 7 | prazo para concluir essa revisão |
| `amostra_posterior_pct` | 5 | amostra aleatória de D1 depois da revisão inicial |
| `limiar_reversao_pct` | 5 | taxa de reversão acima da qual a regra cai para conferência |
| `limiar_reversao_min_decisoes` | 30 | mínimo de decisões para o limiar valer |
| `conferencia_negativa_admin_decisoes` | 30 | conferência de um clique nas primeiras negativas administrativas (D2), por regra |

Hoje o leitor do Maezo (`src/maezo/platform/rco_manifesto.py`, #1244) já valida esses campos. Mesmo assim, recusa um pacote 2.0.0 com regra fora de `plena` ou com `exige_aditivo`: é o erro `ativacao_nao_suportada`, que existe até este ADR estar todo implementado.

O ADR-0075 §(e) propôs:
- um registro de sombra por `idRegra`;
- um interruptor por regra com os estados `sombra | ativa_l2 | desligada`, lido como `spec/processes/dmn/auth-criteria-ratification.yaml` (`src/maezo/tools/workers/auth_criteria.py`, fail-closed).

O ADR-0076 §2 manteve em Proposed:
- a saída adversa executável;
- as ações L2 automáticas (Onda 3);
- a aprovação em lote por versão (P7).

Este ADR detalha o §(e) para as três ativações. Não decide nada do que o ADR-0076 §2 deixou para a Onda 3 ou para a P7.

## Decisão

### 1. Escopo e limite
- Este ADR não altera `spec/policies/autonomy/` nem `_hard_frozen.yaml`, e não liga efeito adverso automático.
- O que muda o efeito de um caso é o estado manual `ativa_l2` (§2). Ele só passa a valer com a Onda 3 (worker guardado, emenda ao ADR-0018 parte 2) e com a decisão P7.
- Até lá, a função de estado efetivo recebe o indicador `efeito_k1_habilitado`, falso. Com ele falso, todo estado manual se comporta como `sombra`: só registro, nenhuma User Task nova, nenhum efeito. Um `ativa_l2` ou `desligada` lido no arquivo gera alerta.
- **O indicador é por ambiente.** Vem da configuração do ambiente, como a chave nomeada do K2 da Helena (`MAEZO_ENV` vivo e lista de permissão em `src/maezo/agents/helena/habilidades/cobertura/manual.py`):
  - é falso por padrão em todos os ambientes;
  - só pode valer verdadeiro em ambiente de uma lista de permissão no código, para dev e homologação. Hoje o deploy tem `dev` e `prod`; homologação entra na lista quando existir;
  - produção, ambiente vazio e ambiente desconhecido ficam fora por construção. A configuração ligada ali é recusada com alerta;
  - abrir produção é código, e é da unidade da Onda 3.
- Assim, a decisão do dono de rodar dev e homologação com as regras ligadas (PROG-COORD-16) se aplica por configuração quando ele liberar, sem código novo. Nenhum ambiente é ligado por este ADR nem pelas unidades 5a–5e.
- **Fora deste ADR:** a decisão do dono de 16/11/2026 (PROG-COORD-16) de rodar as regras do rco em enforcing em dev e homologação, e de redefinir os aprovadores (dono + auditor neutro, ratificação em lote por versão do pacote) depende de mudança em caminho protegido. O desenho dessa mudança está no §8, para quem tiver autorização para alterá-lo.

### 2. Estado efetivo por regra
A unidade é o par (`idRegra`, `versaoRegra`). Três fontes entram no cálculo:

1. **Estado manual:** um arquivo irmão de `auth-criteria-ratification.yaml`, com chave (`idRegra`, `versaoRegra`) e estados `sombra | ativa_l2 | desligada`.
   - Chave ausente = `sombra`.
   - Arquivo ilegível = `desligada` para todas as regras, com alerta à coordenação de cada classe. Depois da Onda 3, isso põe toda saída adversa do K1 nas filas humanas de uma vez; é intencional, porque é o lado seguro, e o alerta existe para que a correção do arquivo seja imediata.
   - O estado manual diz se o K1 age sobre o caso.
2. **`ativacao` do pacote pinado:** diz como o K1 age quando age.
3. **Override automático:** gravado só pelo monitor de reversão (§5). Seu único valor é `conferencia_integral`. Nunca promove.

Combinação (uma função pura e determinística):

| Estado manual | Efeito no caso | Registro (§4) |
|---|---|---|
| `sombra` (padrão) | Nenhum. O caso segue o processo atual do Maezo, decidido por humano. A concordância vem do desfecho humano desse processo. Nenhuma User Task nova. | sim |
| `desligada` ou ilegível (com `efeito_k1_habilitado`) | Toda saída adversa vira `revisao_humana` na `fila` da linha. Nunca "sem efeito", que pagaria a glosa de contas automaticamente. | sim |
| `ativa_l2` (com `efeito_k1_habilitado`) | Aplica a ativação efetiva, a mais restritiva entre a `ativacao` do pacote e o override, na ordem `em_correcao` > `conferencia_integral` > `conferida` > `plena`. O efeito de cada uma está no §3. | sim |

Regras de mudança de estado:
- Retirar um override exige mudança humana no arquivo de estado, por PR. O sistema nunca promove uma regra sozinho.
- Uma `versaoRegra` nova é uma regra nova: zera contadores e override e reabre a revisão inicial.

### 3. Efeito de cada ativação sob `ativa_l2`
O worker aplica este filtro antes de qualquer efeito. Fila e grupo por classe, conforme K6 e ADR-0075 §(h):
- `contas` → fila `faturamento` → `auditoria-contas`, coordenação `coordenacao-contas`;
- `administrativo` → fila `cadastro` → `cadastro-beneficiarios`, coordenação pendente: não há grupo de coordenação de cadastro em `bootstrap.py`, decisão do dono junto com o K6;
- `clinico` → filas `auditoria_medica` → `medico-auditor`, coordenação `coordenacao-auditoria-medica`, ou `junta_medica` → `junta-medica`.

**`em_correcao`:** toda saída adversa (`negar`, `glosar`, `recomendar_negativa`) vira `revisao_humana` na `fila` da linha, com a marca `regra_em_correcao`.

**`conferencia_integral`** (só por override): toda decisão adversa da regra espera a confirmação humana de um clique, na fila da sua classe, antes do efeito. Vale até um PR humano retirar o override.

**`conferida`** (D2, negativa administrativa):
- As primeiras `conferencia_negativa_admin_decisoes` de cada (`idRegra`, `versaoRegra`) esperam a confirmação de um clique antes do efeito.
- Depois delas, a negativa segue o caminho normal e entra na amostragem do §4 com o mesmo `amostra_posterior_pct`, revista depois do efeito. Essa é uma escolha do Maezo para alimentar o monitor do §5; o contrato K1 §7 só prevê a conferência inicial.

**`plena`:**
- `negar` e `glosar` (contas, D1): o efeito segue o caminho normal. A amostragem não bloqueia o efeito (contrato K1 §9): as primeiras `revisao_inicial_decisoes` são 100% revistas, em até `revisao_inicial_prazo_dias`, e depois a amostra é de `amostra_posterior_pct`.
- `recomendar_negativa` (clínico): é caso para decisão humana na fila da linha (K6 §9), sem amostragem. O médico decide; a recomendação não tem efeito próprio.
- Efeitos favoráveis ou de encaminhamento: seguem sem revisão.

**Prazo da conferência:**
- O prazo é o que o Maezo já calcula para o caso (`spec/processes/dmn/auth_sla.dmn` e tabelas de prazo de cada processo).
- Conferência que não sai no prazo vai à revisão humana com o prazo mais curto da classe.
- Prazo nulo ou ausente segue a mesma regra.
- Nunca há negativa nem aprovação por decurso de prazo.

**`exige_aditivo: true`:**
- O efeito adverso só vale para prestador com aditivo aceito que cubra a regra, vigente na data do atendimento, ou com contrato no modelo OMNI.
- A entrada é um registro por prestador, a pinar como artefato versionado:
  - identificador do prestador;
  - versão do aditivo;
  - data de aceite;
  - início de vigência;
  - lista de (`idRegra`, `versaoRegra`) cobertos, ou versão mínima do pacote K1 coberta. Um aceite antigo não cobre uma restrição nova;
  - indicador de contrato no modelo OMNI.
- Qualquer campo nulo, inclusive a data do atendimento, conta como "sem aditivo". A saída adversa vira `revisao_humana` e o caso entra no painel de sinistro. Nunca há negativa por dado ausente.
- **LACUNA:** o Maezo não tem esse dado hoje. A fonte é decisão do dono: o controle de aceites foi pedido ao responsável pela rede em 16/11/2026. Até ela existir, nenhuma regra com `exige_aditivo` produz efeito adverso.

### 4. Registro de decisões e amostragem
Uma tabela append-only nova, `rco_decisao_regra`, criada por migration nova sem alterar tabela existente. Uma linha por avaliação que casou regra do K1, em qualquer estado manual, com:
- `idRegra`, `versaoRegra`, `shaDmn`;
- `decisao`, `classeAdverso`, `motivoTab38`, `fila`;
- `estado_manual`, `ativacao_pacote`, `ativacao_efetiva`;
- chave de negócio pseudonimizada (Zona Geral, sem dado pessoal);
- `amostrada`, `motivo_amostra` ∈ {`inicial`, `aleatoria`, `chave_ausente`} e `id_chave_amostra`;
- o desfecho humano, ligado depois;
- `concordou` ∈ {`sim`, `nao`, `sem_desfecho`}.

O desfecho humano vem de onde o caso de fato se encerra:
- em `sombra`, do processo atual do Maezo;
- sob `ativa_l2`, da User Task de conferência ou de revisão.

O registro alimenta o K5 (desfechos mzo → adp → rco) e o painel de concordância por regra. A auditoria do ADR-0075 §(f) passa a gravar também, em cada decisão do K1, as chaves `estadoManual`, `ativacaoPacote`, `ativacaoEfetiva` e `exigeAditivo`. Elas seguem o camelCase das chaves que já existem (`shaDmn`, `versaoRegra`, `idRegra`), com valores em texto; as colunas do registro ficam em snake_case.

**Amostragem determinística e não previsível pelo prestador:**
- Entrada: `HMAC-SHA256(chave, campos)`. Cada campo (`chave_negocio`, `idRegra`, `versaoRegra`) é codificado em UTF-8 e prefixado pelo seu tamanho em 4 bytes big-endian, para que a concatenação não seja ambígua.
- Decisão: os 8 primeiros bytes do HMAC, lidos como inteiro sem sinal, módulo 1.000.000, ficam abaixo de `amostra_posterior_pct` × 10.000. Isso dá granularidade de 0,0001 ponto percentual; um parâmetro com mais casas decimais é recusado pelo leitor.
- A chave é segredo do ambiente, provisionado por humano.
  - `id_chave_amostra` registra qual chave decidiu cada linha.
  - Na rotação, uma chave nova passa a valer para decisões novas; as anteriores ficam guardadas pelo mesmo prazo do registro, para a auditoria reproduzir a amostra.
- **Sem chave:** toda decisão adversa é amostrada (`motivo_amostra = chave_ausente`), com alerta à coordenação da fila. A amostragem não bloqueia efeito, então falhar para mais revisão é o lado seguro.

### 5. Monitor de reversão e desligamento automático
**Definições:**
- **Reversão:** decisão com desfecho humano contrário (`concordou = nao`).
- **Denominador:** as decisões com desfecho humano (`sim` ou `nao`), nunca todas as decisões. Com a amostra parcial, dividir por todas diluiria a taxa e esconderia uma regra errada. O contrato K1 §7 não define o denominador; esta é a interpretação do Maezo, aprovada pela ROOT.

**Duas janelas, por (`idRegra`, `versaoRegra`). Dispara a primeira que passar do limiar:**
- **acumulada:** todas as decisões com desfecho, desde o início da versão;
- **móvel:** as últimas `limiar_reversao_min_decisoes` decisões com desfecho. Pega a degradação tardia que um histórico bom esconderia na acumulada. A segunda janela é escolha do Maezo, aprovada pela ROOT.

**Disparo:** pelo menos `limiar_reversao_min_decisoes` decisões com desfecho e taxa estritamente maior que `limiar_reversao_pct`, como no contrato.

**Efeito do disparo:**
- override `conferencia_integral`;
- alerta à coordenação da classe (§3);
- evento por regra no K5.

**Funcionamento:**
- Roda em todos os estados manuais. Em `sombra`, mede a concordância com o processo humano e só alerta; o override fica gravado e passa a valer quando a regra for para `ativa_l2`.
- Avaliação a cada desfecho registrado, não em lote.
- Vale para D1 e para D2. A extensão a D2 em relação ao contrato K1 §7 é comunicada à trilha rco pela ROOT.
- Prazo da revisão inicial vencido gera alerta à coordenação e não muda a ativação.

### 6. Provas do pacote antes do pin
- O contrato K1 da PROG-RCO-8 ainda não define um campo de provas. O mais próximo publicado são os `exemplos_sinteticos[]`, que ficam fora do lock e servem só de teste (contrato K1 §3).
- A trilha rco vai publicar o formato de `provas[]`. A primeira prova prevista é a tabela-verdade do teste unário booleano, da branch `prog/rco/teste-unario-booleano` do rco.
- Quando o formato existir, a validação terá duas partes:
  - **gate estático** (`verify_rco_rules_pin`, sem rede): as provas estão listadas, os hashes conferem e os arquivos ficam na pasta que o contrato definir;
  - **teste de integração** no motor real, na versão do runtime implantado (CIB Seven 2.1.0, perfil `deployed-runtime-compatibility` de `src/maezo/portal/engine/java/pom.xml`): roda cada prova contra os bytes pinados.
- Qualquer divergência reprova o PR que move o pin para `fixado`.

### 7. Ordem de implementação
Um PR por unidade, um executor por vez, revisão independente em cada:
1. este ADR;
2. **5a:** leitura e checagem de faixa dos parâmetros. A checagem é escolha do Maezo: inteiros > 0, percentuais em (0, 100] com no máximo 4 casas decimais, `limiar_reversao_min_decisoes` ≥ 1. A auditoria grava `ativacaoPacote` e `exigeAditivo`. A recusa `ativacao_nao_suportada` continua;
3. **5c:** arquivo de estado manual com leitor fail-closed, migration do registro, função de estado efetivo, o indicador `efeito_k1_habilitado` fixo em falso (toda regra em `sombra`), o registro em todos os estados, a amostragem do §4 e, na auditoria, `estadoManual` e `ativacaoEfetiva`. Não cria User Task: as rotas de `desligada` e do §3 ficam escritas e testadas com o indicador ligado só nos testes. A recusa continua;
4. **5d:** User Tasks de conferência e revisão (D1, D2, `conferencia_integral`);
5. **5e:** monitor de reversão, override e alertas. Só neste PR, com 5c e 5d na `main`, sai a recusa `ativacao_nao_suportada`;
6. **5b:** provas, quando o formato for publicado.

### 8. Mudança em caminho protegido: desenho, não aplicada
A decisão do dono de 16/11/2026 (PROG-COORD-16) pede duas mudanças em `spec/policies/autonomy/`. Este ADR não as faz: a pasta está fora do escopo autorizado para agentes. O desenho fica aqui para quem tiver a liberação explícita do dono.

**1. Enforcing por ambiente para as ações do rco.**
- Hoje `spec/policies/autonomy/action-approvals.yaml` tem um `modo` global (`shadow`). A entrada `avaliacao_dmn` tem `enforcement: shadow` e `dominios_exigidos: [medica, ans, seguranca]`, todos `aprovado: false`. Não há chave por ambiente.
- A mudança cria uma sobreposição por ambiente, só para as ações cujo efeito vem de decisão sob `spec/processes/dmn/rco/`:
  - `enforcing` em dev e homologação;
  - produção sem a chave, e o leitor recusa a chave para `prod`;
  - o leitor da política e o gateway (`src/maezo/gateway/action_execution.py`, `src/maezo/gateway/effect_pep.py`) passam a resolver o modo por (ação, `MAEZO_ENV`).
- Ela anda junto com o `efeito_k1_habilitado` do §1, que é a chave do lado do worker.

**2. Aprovadores por versão do pacote (a antiga P7, ADR-0075 §(g)):**
- Em `spec/aprovacoes-de-regras.yaml`, uma entrada por `sha256` do `config/integrations/rco/rules.lock.json`, com papéis `dono` e `auditor-neutro` e ratificação em lote por versão.
- O checker `scripts/ci/check_aprovacao_de_regras.py` aceita essa entrada como cobertura de todo arquivo listado no pin.
- Em `avaliacao_dmn`, os domínios exigidos para tabela sob `rco/` passam a ser os mesmos dois papéis.
- Médico, jurídico e DPO viram revisão assíncrona registrada, sem bloquear.
- Nenhum agente grava `ratificado: true` ou `aprovado: true`.

**3. Teste que nasce junto com a mudança, não antes:**
- com `MAEZO_ENV=prod`, a política resolvida, o modo de cada ação e o resultado do gateway são idênticos byte a byte aos de antes da mudança, comparados contra um instantâneo gerado da `main` anterior;
- com `MAEZO_ENV=dev`, só as ações do rco mudam de modo;
- a chave de enforcing para `prod` é recusada;
- `_hard_frozen.yaml` e os 5 itens hard ficam intactos, e `authorization_denial` continua L0.

## Consequências

**Positivas:**
- A regra que o rco marca como errada nunca tem efeito adverso automático.
- A regra que começa a errar é rebaixada sem intervenção, nas duas janelas.
- A amostragem resiste a prestador que tenta adivinhar quais contas serão revistas.
- O registro mede a concordância por regra já em `sombra`, que é o que o backtest e o K5 exigem.

**Negativas (aceitas):**
- Toda regra com `exige_aditivo` fica sem efeito adverso até existir a fonte de aceites.
- A conferência bloqueia a negativa administrativa nas primeiras decisões de cada regra e cria carga na fila `cadastro`.
- A chave da amostragem é um segredo novo, a provisionar por humano.
- O pacote 2.0.0 com regra não `plena` só passa no pin depois do 5e.

## Supersedes
— (emenda o ADR-0075 §(e); não substitui nenhum ADR)

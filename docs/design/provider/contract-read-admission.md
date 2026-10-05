# Admissão canônica provider_contract_read

## Decisão e proveniência

Decisão de software sob a autorização original de implementação e a ampliação do dono que delegou análise/decisão de escolhas técnicas/produto/negócio no PROVIDER-IMPL-2026-10. Pedido de autoria delimitada recebido do ROOT; não houve nova solicitação ao dono. Autor `pw2_contract_resolver_specialist`; verificação independente e integração são gates posteriores. Não é assinatura, mandato, AgentCard ou ato externo produtivo.

Classificar a ação canônica **provider_contract_read em L3, não-hard**, para a leitura OP12 de contrato bilateral de um único prestador na zona general. Alteração mínima: versão da matriz 1→2 e uma entrada `provider_contract_read: { level: L3 }`. Nenhuma outra action, HARD_ACTIONS, _hard_frozen, action-approvals, PEP ou registry foi alterado.

Publicação candidata canônica:

- caminho: `spec/policies/autonomy/L0-core.yaml`;
- `revision_ref`: `core-autonomy-v2`;
- SHA256 dos bytes UTF-8 exatos: `a268ab3870610c52fe6f4531c66515802dbe6a758fb8b64e96aa4075a4eae1d8`;
- `action`: `provider_contract_read`; `level`: `L3`; `hard`: false efetivo; sem params;
- `policy_ratification_ref`: `PROVIDER-IMPL-2026-10/provider_contract_read/L0-core-v2`, proveniência de decisão de software sob delegação; não recibo/assinatura de terceiro.

O hash é dos bytes do arquivo, sem normalizar/reordenar YAML para fazer igualdade passar. Os artefatos/delta/before/manifest próprios ficam em `evidence/pw2-c-read-policy`. O bundle R2 admitido tem digest `285b1b586c4040745be6f007bdcaf7b108d665a4e5a98f721f2c338f4c282b3d`; seus pareceres são para construção, não ativação. Esta publicação nova permanece sujeita ao gate independente no seu SHA/hash antes de ser consumida como interface habilitada.

## Análise do nível

ADR-0008 accepted define L3 como execução autônoma com telemetria para operações informativas; a matriz real já usa L3 em query_process_status, query_decision_engine e leituras. OP12 é leitura administrativa, sem escrita no instrumento, emissão de direitos, alteração contratual, decisão de glosa/fraude/negativa ou aprovação/liquidação de pagamento. As cláusulas/refs/resultados só se tornam fatos depois de origem/mandato/revisão/evidência atuais comprovados.

L0/L1 exigiriam ato humano por consulta, embora o efeito protegido continue ausente nessa action; isso acrescentaria um bloqueio genérico já substituído pela delegação e não resolveria falsificação de fonte. L2 também retorna ALLOW no PEP, e sua antiga amostragem foi descopada pelo ADR-0034; rotular essa leitura L2 não adicionaria o controle necessário. L3 expresso é apropriado para a leitura estreita com audit/currentness/source gates, mantendo possibilidade de restrição por tenant.

ADR-0005 mantém HITL/credenciais segregadas/recusa auditada nos efeitos mandatórios humanos. ADR-0008/0025 exigem vocabulário canônico/YAML real, hard-set não reduzível e unknown→DENY; sem alias mcp_dmn, hardcoded ActionPolicy, flag de startup ou default L3. A emenda ADR-0034 registra o histórico PEP de readiness; ela não prova request-level enforcement por si. O novo caminho CapabilityAdmission/source R2 deve efetivamente chamar PEP e verificar suas próprias fontes antes da leitura/divulgação, como seu contrato já exige. Este delta não altera ADR aceito nem reintroduz sampler.

ADR-0063 accepted admite construção de fontes/controlos sob delegação, preserva fronteiras hard/AMH/financeiras e a distinção construção/ato externo/ativação. O R2 fecha action, zona, source identity, TaskDeclaration, Card/definition, política, assinatura, engine e validade; acrescentar a action à matriz não materializa qualquer um desses elementos.

## Alcance e controles preservados

A matriz só decide nível de autonomia da action. **PEP.evaluate agent_context é metadata de audit/log, não enforcement de tenant/purpose/actor/instrument/task.** Não acrescentar params fictícios que pareçam implementar esse binding. O source verifier/CapabilityAdmission reconstroem identidade e verificam tenant/legal entity/provider único, finalidade/cláusula, instrumento/business revision factual, actor/task/workload delegados, Card/definition/signatures/chaves, policy actual/hash/revision, receipt, revogação/validade, currentness antes IO e disclosure. Nenhum preço entre prestadores/dado paciente/contexto clínico pode ser incluído por essa action.

Os cinco hard continuam L0/DENY, high_value_payment e provider_decredentialing continuam REQUIRE_HUMAN, e as alçadas/glosa/negativa/clinical/fraud/rescisão não ganham autoridade por L3. publisher/validator de instrumento são separados do reader; a nova action não concede INSERT/UPDATE/TRUNCATE, publicação de regra, emissão administrativa, aprovação de dinheiro ou assinatura contratual.

Tenant overlay pode restringir essa action a L2/L1/L0, pelos loaders existentes; L1/L0 produzem REQUIRE_HUMAN e o source read automatizado exige ALLOW, portanto deve recusar. Overlay não cria action ausente nem modifica hard-set ou afrouxa uma base mais restritiva. Política/overlay ausente, malformada, hash/revision divergentes ou action desconhecida continuam recusados.

## Consumo pelo source C e reversibilidade

O source C precisa resolver/reler os bytes canônicos admitidos e compará-los ao `TaskDeclaration.policy_file_sha256` e à publication/ref de ratificação; carregar `load_matrix` real para o tenant e os overlays efetivamente instalados, cuja proveniência/hash também devem ser qualificados quando usados. Construir PEP sobre essa matriz e exigir ALLOW da action exata com tenant correspondente. Refs iguais ou um PEP fabricado a partir de ActionPolicy não são publicação/ratificação.

`build_pep` possui cache existente por path/tenant/overlay, não por hash. Nenhuma mudança de shared PEP foi necessária/realizada. Fonte dinâmica deve rehash e carregar a publicação exata ou recompor/reiniciar o runtime; não declarar matriz antiga em cache como a política de bytes novos. Alteração de policy/overlay/key/source head invalida provas em voo e exige revalidação, sem ampliar ceiling original.

Rollback é publicar nova revisão restritiva e desabilitar novos bindings/leitura dependentes, preservando contratos/receipts/audit anteriores. Não reutilizar revision_ref/hash de conteúdo anterior ou apagar evidência. Risco residual: compromisso de publisher/key/source/gateway/engine pode invalidar prova; exige qualifiers/crypto/revogação e revisão independente, não é mitigado pelo nome da action.

## Validação e limites

Testes usam load_matrix/PEP reais: action ausente DENY, arquivo canônico revisado L3/ALLOW, aliases unknown, overlays L2/L1/L0 e proibição de loosening/alteração hard, arquivo ausente sem startup default e hard/finance unchanged. Um negativo explícito demonstra que agent_context errado não é guard de binding, evitando confundir matriz com fonte. Snapshot before preserva SHA dos hard/approvals/PEP e igualdade de todos os outros entries.

Source/card/task/mandato/instalação produtivos, assinatura humana/profissional e ativação não são declarados existentes. A action publicada é condição necessária de software para construir/verificar OP12, não prova suficiente de permissão, autoridade ou completude de PW2.

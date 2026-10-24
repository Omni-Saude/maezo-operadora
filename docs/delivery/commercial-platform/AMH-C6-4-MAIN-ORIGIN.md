# AMH C6-4 — origem main e pin do consumidor

O contrato proposto C6-4 foi publicado no repositório AMH pelo merge normal do [PR282](https://github.com/Omni-Saude/amh-data-platform/pull/282), commit `a4f2ac237f02ea54873e245de3dceac922b8891d`. A árvore `11ee7dd7367e608284cc9e00492c91d5bd421e35` é idêntica ao candidato56beb20 qualificado pelo gatekeeper independente e pelos oito workflows.

O connector GitHub forneceu os320163 bytes completos dessa revisão; ROOT comparou-os byte a byte com o objeto Git obtido da origem. Blob `08e4d70dae5605d9000ad23db7df355eaa5fc940`, SHA-256 `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`: os42 comandos,41 recuperações e3 GETs permanecem exatos.

O [novo pin de fonte](../../../config/integrations/amh/commercial-c6-4.source.lock.json) registra essa origem sem editar o lock canônico histórico AMH1.x nem a proveniência TEST-only importada do candidato. O codec existente confere o mesmo digest dos bytes injetados pela composição confiável; não lê a cópia TEST em runtime. O JSON anexo registra o hash do pin histórico preservado.

Publicação da extensão proposta no Git não significa homologação Tasy, habilitação PSP ou admissão operacional. A composição permanece UNBOUND por default; publicação atual, identidade/representação, tenant, finalidade e lifecycle exigem providers próprios antes de dados REAL. O próximo HEAD consumidor exige CI e gate independente novos.

# Custodia do telefone (ADR-0061) + consumidor de retomada (GAP-XHITL-4), LIGADOS so' em dev.
#
# Decisao do dono, 27/09/2026 (emenda D-P, `docs/plans/portal-autoridade-nativa-dev.md`):
# ciencia do DPO PENDENTE; autorizado pela diretoria como TESTE INTERNO em dev, so' com os
# celulares do time e da diretoria (nenhum beneficiario real). Registro em ADR-0061
# ("Excecao de dev, 27/09/2026"). Voltar a `false`/`0` destroi a chave (janela de 30 dias) e as
# roles; o dado cifrado ja gravado fica ilegivel — que e' o comportamento desejado no rollback.
#
# Versionado de proposito (negacao no `.gitignore`, como `portal.auto.tfvars`): um valor
# escolhido so' no `apply` seria invisivel para a revisao e o proximo apply da main desligaria.
recipient_vault_enabled    = true
agent_resume_desired_count = 1

# EXCECAO REGIONAL da zona PHI do DEV autorizada pelo DONO como DPO em 21/10/2026 — temporaria, so' dev.
#
# "se nao funcionar o 1... tem que ir pro 2, desde que funcione e resolva": nenhum modelo regional de Sao Paulo
# passou no criterio do classificador e o GLM-5/Mistral Large 3 de sa-east-1 estao fora (CloudTrail 21/10/2026).
# Com `phi_excecao_regional` igual ao rotulo literal, a zona PHI do dev (e o roteador) usa o modelo em us-east-1:
# `MAEZO_PHI_EXCECAO_REGIONAL` liga a excecao no codigo (`runtime/inference/excecao_regional.py`), o endpoint e o
# ARN das policies mudam para us-east-1, e a resposta e' atestada como us-east-1 (nunca como Brasil).
#
# VOLTAR A SAO PAULO (runbook `phi-inference-ops.md` §8): `phi_excecao_regional = ""`, `phi_endpoint_url` de volta a
# sa-east-1 e `phi_model_id`/`helena_roteador_modelo` ao modelo regional, PR + apply direcionado dos QUATRO papeis.
# Producao fora de escopo.

variable "phi_excecao_regional" {
  description = "Excecao regional da zona PHI do dev autorizada pelo DPO (21/10/2026). Vazio = sa-east-1 (padrao)."
  type        = string
  default     = ""
  nullable    = false
  validation {
    condition     = contains(["", "excecao_us_east_1_autorizada_dpo_2026-10-21"], var.phi_excecao_regional)
    error_message = "phi_excecao_regional: vazio ou o rotulo literal da excecao autorizada pelo DPO."
  }
}

locals {
  phi_regiao = var.phi_excecao_regional == "" ? "sa-east-1" : "us-east-1"
}

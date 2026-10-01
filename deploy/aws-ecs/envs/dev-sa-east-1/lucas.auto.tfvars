// Numero unico: roteador Helena -> Lucas (ADR-0062, `docs/plans/lucas-numero-unico.md` §4/§6g).
//
// Por que versionado e `.auto.tfvars` (negacao no `.gitignore`, como `portal.auto.tfvars` e
// `retomada.auto.tfvars`): um valor escolhido so' no terminal de quem aplicou seria invisivel
// para a revisao, e o proximo apply da main o desfaria sem aviso.
//
// O DEFAULT DESCREVE O QUE RODA: `false` aqui e' o estado de dev hoje. Desligado, o roteador nem
// e' construido no receptor e o despacho segue byte a byte o de antes desta frente. Ligar e' um
// PR que troca ESTA linha para `true`, seguido de `terraform apply` pelo time (§7 do plano) —
// e, nesta onda, ligado roda em SOMBRA: grava o agente ativo e o motivo, nao muda resposta.
//
// So' dev: a cerca `scripts/ci/check_roteador_lucas.py` le' este arquivo (e o `terraform.tfvars`
// / `*.auto.tfvars` de cada diretorio de ambiente) e reprova `true` em qualquer ambiente que nao
// seja `dev-sa-east-1`. O que ela NAO ve' e' `-var` na linha de comando: nao use.
//
// A janela (60 min) e a fonte (`simulada`) ficam nos defaults de `variables.tf`.
roteador_lucas_enabled = false

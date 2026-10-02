// Numero unico: roteador Helena -> Lucas (ADR-0062, `docs/plans/lucas-numero-unico.md` §4/§6g).
//
// Por que versionado e `.auto.tfvars` (negacao no `.gitignore`, como `portal.auto.tfvars` e
// `retomada.auto.tfvars`): um valor escolhido so' no terminal de quem aplicou seria invisivel
// para a revisao, e o proximo apply da main o desfaria sem aviso.
//
// O DEFAULT DESCREVE O QUE RODA: `true` aqui e' o estado de dev (ligado em 01/10/2026, decisao do
// Diretor de Tecnologia). Com as ondas (a)-(g) do plano mergeadas, ligado o roteador entrega a
// conversa de COBRANCA ao Lucas (fonte `simulada`) e a Helena segue com tudo que for saude. Para
// desligar: um PR que troca ESTA linha para `false`, seguido de `terraform apply` pelo time (§7 do
// plano); desligado, o roteador nem e' construido no receptor e o despacho volta a ser byte a byte
// o de antes desta frente.
//
// So' dev: a cerca `scripts/ci/check_roteador_lucas.py` le' este arquivo (e o `terraform.tfvars`
// / `*.auto.tfvars` de cada diretorio de ambiente) e reprova `true` em qualquer ambiente que nao
// seja `dev-sa-east-1`. O que ela NAO ve' e' `-var` na linha de comando: nao use.
//
// A janela (60 min) e a fonte (`simulada`) ficam nos defaults de `variables.tf`.
roteador_lucas_enabled = true

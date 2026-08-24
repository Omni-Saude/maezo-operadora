#!/usr/bin/env bash
# Confere, no lago, as afirmacoes de dado do documento "Autorizacao Previa — testado com
# sucesso" (23/08/2026). Cada bloco imprime OK/FALTA e a evidencia.
#
# POR QUE ESTE SCRIPT EXISTE
#
# As afirmacoes de CODIGO do documento foram reverificadas linha a linha. As de DADO nao:
# o perfil `amh-data-dev` exige codigo MFA (escolha deliberada registrada no proprio
# ~/.aws/config — e' o unico segundo fator entre a chave estatica e a conta que guarda o
# dado de paciente). Em vez de contornar, o trabalho fica aqui, para rodar com UM codigo.
#
#   ./valida-lago-auth.sh            # pede o codigo MFA uma vez
#
# NAO altera nada. Sao 8 consultas de leitura + 2 de permissao.

set -uo pipefail
PERFIL="${PERFIL:-amh-data-dev}"
REGIAO="${REGIAO:-sa-east-1}"
SAIDA="${SAIDA:-s3://amh-athena-results-dev-sa-east-1/valida-auth/}"

az() { aws "$@" --profile "$PERFIL" --region "$REGIAO"; }

# Athena sincrono: dispara, espera, devolve a primeira coluna da primeira linha de dados.
q() {
  local sql="$1" id estado
  id=$(az athena start-query-execution --query-string "$sql" \
        --result-configuration "OutputLocation=$SAIDA" \
        --query 'QueryExecutionId' --output text 2>/dev/null) || { echo "ERRO_DISPARO"; return 1; }
  for _ in $(seq 1 60); do
    estado=$(az athena get-query-execution --query-execution-id "$id" \
              --query 'QueryExecution.Status.State' --output text 2>/dev/null)
    case "$estado" in
      SUCCEEDED) az athena get-query-results --query-execution-id "$id" \
                   --query 'ResultSet.Rows[1].Data[0].VarCharValue' --output text 2>/dev/null; return 0 ;;
      FAILED|CANCELLED)
        az athena get-query-execution --query-execution-id "$id" \
          --query 'QueryExecution.Status.StateChangeReason' --output text 2>/dev/null | head -c 200
        return 1 ;;
    esac
    sleep 2
  done
  echo "TIMEOUT"; return 1
}

linha() { printf '%s\n' "------------------------------------------------------------"; }

echo "conta: $(az sts get-caller-identity --query Account --output text)"
linha

# --- 1. As tabelas que o documento cita existem? ------------------------------------------
# Se alguma NAO existir, a tarefa correspondente muda de "publicar no gold" para "ingerir".
echo "1) EXISTENCIA DAS TABELAS"
for t in amh_omni_gold.guias_historico \
         amh_omni_gold.guias_procedimento \
         amh_omni_gold.carencia_beneficiario \
         amh_omni_gold.rede_credenciada \
         amh_omni_gold.rol_procedimento_historico \
         amh_omni_fhir.fhir_coverage; do
  db="${t%%.*}"
  if az glue get-table --database-name "$db" --name "${t##*.}" >/dev/null 2>&1; then
    echo "   OK    $t"
  else
    echo "   FALTA $t"
  fi
done
linha

# --- 2. Passo 4: cobertura NA DATA -------------------------------------------------------
# O documento pede uma visao que responda cobertura na data de referencia, usando
# period_start/period_end. Aqui so' confirmamos que as COLUNAS existem — sem elas a tarefa
# e' outra (ingerir vigencia), nao "criar visao".
echo "2) PASSO 4 — vigencia de cobertura (period_start / period_end)"
cols=$(az glue get-table --database-name amh_omni_fhir --name fhir_coverage \
        --query 'Table.StorageDescriptor.Columns[].Name' --output text 2>/dev/null)
for c in period_start period_end; do
  case " $cols " in *" $c "*) echo "   OK    coluna $c" ;; *) echo "   FALTA coluna $c" ;; esac
done
linha

# --- 3. Passo 5: carater do atendimento --------------------------------------------------
# O documento afirma: NAO existe no gold da guia, e a fonte (bronze) esta' bloqueada por
# permissao de COLUNA. Um erro de acesso aqui CONFIRMA a afirmacao — nao e' falha do script.
echo "3) PASSO 5 — carater do atendimento"
cols=$(az glue get-table --database-name amh_omni_gold --name guias_historico \
        --query 'Table.StorageDescriptor.Columns[].Name' --output text 2>/dev/null)
echo "   colunas do gold da guia: $(echo "$cols" | tr '\t' ' ' | head -c 300)"
case " $cols " in
  *carater*|*ie_carater*) echo "   OK    ha coluna de carater no gold" ;;
  *) echo "   FALTA carater no gold (confirma o documento)" ;;
esac
echo -n "   bronze tasy_pls_guia_plano legivel? "
r=$(q "SELECT count(*) FROM amh_omni_bronze.tasy_pls_guia_plano LIMIT 1" 2>&1)
if [[ "$r" =~ ^[0-9]+$ ]]; then echo "SIM ($r linhas) — o bloqueio caiu, reavaliar a tarefa ACESSO"
else echo "NAO — $r"; fi
linha

# --- 4. Passo 6: data de adesao ----------------------------------------------------------
echo "4) PASSO 6 — data de adesao do beneficiario"
echo -n "   bronze tasy_pls_segurado legivel? "
r=$(q "SELECT count(*) FROM amh_omni_bronze.tasy_pls_segurado LIMIT 1" 2>&1)
if [[ "$r" =~ ^[0-9]+$ ]]; then echo "SIM ($r linhas)"; else echo "NAO — $r"; fi
echo -n "   carencia_beneficiario.dt_inicio_vigencia serve de aproximacao? nao-nulos: "
q "SELECT count(*) FROM amh_omni_gold.carencia_beneficiario WHERE dt_inicio_vigencia IS NOT NULL" 2>&1
linha

# --- 5. Passo 5/6: o de-para de categoria ------------------------------------------------
# O documento diz: 1.818 linhas em branco, e o grupo do Rol e' descricao clinica e nao
# categoria. Isto mede o tamanho real do trabalho do medico auditor.
echo "5) PASSO 5/6 — cobertura do de-para de categoria"
echo -n "   procedimentos distintos no Rol: "; q "SELECT count(DISTINCT cd_procedimento) FROM amh_omni_gold.rol_procedimento_historico" 2>&1
echo -n "   linhas sem grupo:               "; q "SELECT count(*) FROM amh_omni_gold.rol_procedimento_historico WHERE nm_grupo IS NULL OR trim(nm_grupo)=''" 2>&1
echo -n "   TUSS distintos ja decididos:    "; q "SELECT count(DISTINCT cd_procedimento) FROM amh_omni_gold.guias_procedimento" 2>&1
linha

echo "FIM. Cada FALTA acima e' uma tarefa; cada OK derruba uma."

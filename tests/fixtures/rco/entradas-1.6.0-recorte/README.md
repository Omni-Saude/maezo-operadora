# Recortes dos dicionarios de entradas K1 1.5.1 e 1.6.0 do rco

Copia SOMENTE LEITURA, so do dicionario de entradas (`k1/entradas.json`): nenhum DMN, manifesto ou anexo do
pacote K1 esta aqui, e nada daqui entra no pin (`config/integrations/rco/rules.lock.json` segue `sem_pacote`).
Origem: `Omni-Saude/regras-contratos-operadora`, commits de `main` em `ORIGEM.json` (1.5.1: `1c5a2f2d`, PR #187;
1.6.0: `b8444f52`, PR #189). Como os arquivos foram gerados esta em `ORIGEM.json` (`gerador`); o `sha256_conteudo`
de cada recorte e o do dicionario COMPLETO (353 entradas) daquele commit, e o sha256 do `entradas.json` inteiro esta
em `entradas_json_sha256`. Nao edite: gere de novo a partir de um commit novo do rco e regrave `ORIGEM.json`.

Diferencas por versao (das 353 entradas, nenhuma entra ou sai; os campos de cada entrada sao os mesmos; o
publicador `scripts/k1_entradas.py` tem o mesmo blob, `54d03c50`, em `aed187d4` e em `b8444f52`):

- 1.5.0 -> 1.5.1 (PATCH, PROG-RCO-38): muda so o texto de `de_onde` e de `formula.formula` de
  `cidSecundarioInformado` (zero diagnosticos passa de falso a nulo) e de `regime` (o regime vem da guia do item;
  regimes divergentes entre guias combinadas dao nulo; o termo `regimeAtendimento` cita tambem a guia de consulta);
- 1.5.1 -> 1.6.0 (MINOR, PROG-RCO-39): muda so `prazoComprovacaoUsoVencido` (dominio `urgencia`, nao mapeado no
  montador). A formula passa a derivar do termo de cadastro `prazoApresentacaoContratualDias` (antes, o termo
  `parametro` `prazoEnvioContaDias` com `valor_por_contrato`) e do termo de cadastro novo `autorizacaoExtemporanea`
  (concedida = falso; em analise ou ausente = nulo); termo ausente = nulo (pendente, nunca glosa). Os dois termos de
  cadastro trazem a chave `referencia` (`arquivo#chave`), que ate a 1.5.1 so existia em termo `parametro`.

O que ha aqui: `entradas-1.5.1-recorte.json` e `entradas-1.6.0-recorte.json`, cada um com as 39 entradas dos
dominios do montador (as mesmas de `../k1-2.3.0-recorte/entradas-recorte.json`) e `prazoComprovacaoUsoVencido`.
Testes: `tests/unit/platform/test_rco_entradas_1_6_0.py`.

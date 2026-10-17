# Base de conhecimento de saúde da Helena

> **Modelo de teste (dev).** Aprovado pelo dono em 17/10/2026 apenas para dev.
> **Revisão médica e revisão jurídica pendentes** antes de qualquer uso em produção.

A Helena usa estes textos para **informar**: o que é uma especialidade, um remédio, um exame,
um procedimento, uma doença ou uma vacina, e para onde ir quando a pessoa conta um sintoma.
Ela **não** diagnostica, **não** indica conduta, **não** fala de dose e **não** promete cobertura do plano.

## Estrutura

```
spec/conhecimento/saude/<categoria>/<id>.yaml
```

| categoria | prefixo do id |
|---|---|
| `especialidade` | `esp-` |
| `medicamento` | `med-` |
| `exame` | `exa-` |
| `procedimento` | `pro-` |
| `cirurgia` | `cir-` |
| `material` | `mat-` |
| `condicao` | `con-` |
| `vacina` | `vac-` |
| `sintoma` | `sin-` |

Campos de cada arquivo:

- `id`: kebab-case com o prefixo da categoria; igual ao nome do arquivo.
- `categoria`, `titulo`.
- `sinonimos`: lista em minúsculas (nomes populares, comerciais, abreviações, erros comuns).
  Um sinônimo pertence a **um único** id.
- `texto`: até 700 caracteres, frases de até 20 palavras, linguagem simples.
  Sem dose, mg, posologia, "tome", "pode tomar", "não é grave", "é normal", diagnóstico ou promessa de cobertura.
- `tipo_texto`: `proprio` ou `citacao_literal`.
- `fontes`: lista de `{nome, url, licenca, coletado_em, sha256_trecho}`.
  `sha256_trecho` só existe em citação literal e é o SHA-256 (UTF-8) do `texto`.
- `link_leitura` (opcional): página para a pessoa ler mais.
- `revisao`: `{status, autor, aprovado_por, revisao_medica, revisar_ate}`.
- Só em `sintoma`: `perguntas_alerta` (1 a 3 perguntas sim/não sobre sinais de alarme),
  `sinais_alarme` (termos que escalam na hora) e `populacao` (`adulto`, `crianca`, `gestante` ou `todos`).
  Nesse caso, o `texto` é a orientação para quando **não** há sinal de alarme.
  Os sinais de alarme vêm das tabelas `spec/processes/dmn/triage_redflag_*.dmn`.
  Não se cria sinal clínico novo aqui.

Saúde mental (depressão, ansiedade): o texto termina com o CVV 188 e com a oferta de falar com a equipe.

## Como adicionar ou alterar um item

1. Confira o fato numa fonte permitida (lista abaixo). O que não tiver fonte não entra.
2. Citação literal: copie um trecho curto **exato** da página, sem mudar nenhuma vírgula.
   Se precisar cortar, corte frases inteiras. Grave o SHA-256 do trecho e `licenca: "CC BY-ND 3.0 (rodapé gov.br)"`.
3. Texto próprio: escreva de forma correta, neutra e simples, com `licenca: "texto próprio da operadora"`,
   e cite em `fontes` a referência oficial que embasa o texto.
4. Verifique que nenhum sinônimo já existe em outro arquivo.
5. Marque `revisao.revisao_medica: pendente` até a revisão médica assinar.

## Revisão

Cada item tem `revisar_ate` (seis meses após a coleta). A cada **6 meses**, ou antes se a fonte mudar:
reabrir a página da fonte, conferir o texto e o hash da citação literal, atualizar `coletado_em` e `revisar_ate`.

## Fontes

**Permitidas para citação literal** (CC BY-ND 3.0, uso comercial com crédito, sem alterar o texto):

- Saúde de A a Z — https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z
- Páginas web do INCA — https://www.gov.br/inca (não os PDFs nem as cartilhas)
- Calendário Nacional de Vacinação — https://www.gov.br/saude/pt-br/vacinacao/calendario
- ANS — RN 566, Rol de Procedimentos e atos oficiais

**Referências para texto próprio:** Resolução CFM de especialidades médicas (hoje a nº 2.380/2024);
leis federais das profissões de saúde (Planalto); Padrão TISS/TUSS e Rol da ANS;
bula do paciente no Bulário Eletrônico da ANVISA (https://consultas.anvisa.gov.br/#/bulario/).

**Proibidas:** BVS Dicas em Saúde; Caderno de Atenção Básica nº 28; cartilhas e PDFs "não comercial" do INCA ou do MS;
OPAS/OMS; Fiocruz; Manual MSD; sites de hospitais e de sociedades privadas; Wikipedia.

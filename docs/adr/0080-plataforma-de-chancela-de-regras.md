# ADR-0080: Plataforma de chancela de regras pelos responsáveis técnicos

**Status:** Proposto — redigido por AGENTE (onda 0, T0.2, do blueprint da chancela de regras de 19/11/2026 — documento de trabalho fora do git; as citações "blueprint §N" abaixo apontam para ele). Um agente não aceita ADR e nenhum campo de assinatura foi preenchido (ADR-0007). `/docs/adr/` é CODEOWNERS. Aceite = ato do dono.
**Data:** 2026-11-19
**Área:** Governança de regras / Portal humano / Segurança

> **Numeração.** O blueprint chamava este registro de "ADR-0079". O número 0079 já está tomado na repo
> (citado pela DL-0190..0192 e por `src/maezo/agents/*/graph.py` como "ADR-0079" da privacidade; arquivo no
> ramo `prog/mzo/adr-zona-geral-dossie`). Seguindo a repo, este é o **ADR-0080**. A DL companheira é a
> **DL-0194** (o blueprint dizia DL-0193, que já está tomada no ramo `prog/mzo/autonomia-chave-repetida`).

## Contexto

Pendência 27 da planilha do diretor e DL-0148: a repo não diz, por regra, quem aprovou o quê e em que
versão dos bytes. O registro `spec/aprovacoes-de-regras.yaml` cobre o artefato inteiro (`.bpmn`/`.dmn`),
com um papel por entrada, e hoje tem `aprovados: []`. A proposta aprovada (v3.1 de 17/11 e Frente B de
18/11) põe a decisão nos responsáveis técnicos (setores: médico-auditor, jurídico, DPO, regulatório,
finanças, PO e dono) por meio de uma área "Regras" no portal humano, com recibo, e deixa o git como a
autoridade do que vai a produção.

Fatos medidos que mudam o desenho (blueprint §1): o relé assinado D5/D6 do ADR-0049 não existe (conclusão
interina DL-0049, só dev); `pull_request` roda o workflow do HEAD do PR, então dar poder de assinatura ao CI
deixaria qualquer PR assinar (`iam-github-supply-chain.tf:36-42`); o exportador comita no PR depois da
decisão, então "commit do recibo = head" é impossível; o CI não alcança o Postgres do portal; o gate de
signoff não aceitava `dono` e aceitava um único `approved` como sign-off; o egresso do portal é lista /32
fail-closed (`portal-network.tf:55-75`).

Alternativas consideradas e descartadas: (1) aprovação só por CODEOWNERS — CODEOWNERS só requisita revisor e
todos os times têm um único membro, que é quem abre os PRs do agente (deadlock; blueprint D-03); (2) S3 como
autoridade de produção — produção passaria a depender de um bucket mutável em tempo de execução;
(3) assinatura no CI — ver acima; (4) BPMN multi-instância com `candidateGroups` por expressão — o catálogo
humano D2 fixa grupos por tarefa (ADR-0049) e a publicação de expressão dinâmica não foi verificada.

## Decisão

Adotamos a plataforma de chancela de regras com as decisões B1–B12 do blueprint:

- **B1 — autoridade.** A decisão do setor vale pelo registro append-only no Postgres do portal + o recibo
  assinado por KMS no cofre. Concluir a tarefa no motor é efeito derivado, não prova.
- **B2 — o CI não assina nada.** Snapshots do catálogo (PR, `main`, release) são assinados pelo job
  confiável `regras-sync`, que lê o git como dado com um GitHub App só-leitura. O CI só verifica.
- **B3 — verificação do recibo.** Offline no CI (chave pública pinada em `spec/regras/chaves-publicas.yaml`,
  ECDSA P-256) como controle primário; custódia conferida online (objeto existe no cofre com retenção e
  legal hold) por role só-leitura.
- **B4 — amarração.** Recibo amarrado a `regra_sha256` (hash por regra, `maezo-regra-hash.v1`) + `rodada_id`;
  na liberação, também ao nº do PR. `commit` é trilha.
- **B5 — vínculo na data** é asserção assinada pelo portal no recibo (`membership_revision`,
  `membership_reviewed_until`); a assinatura KMS é do WORKLOAD, não pessoal (ADR-0049 D4).
- **B6 — prazos e avisos** (5 dias úteis; avisos no dia 3 e no dia 5) agendados pelo Postgres
  (`dias_uteis`); o BPMN genérico não tem timers. Nada é aprovado por decurso de prazo.
- **B7 — BPMN genérico** `SP-OP-VALIDACAO-REGRA-001` com gateway inclusivo e grupos estáticos
  `validacao-regras-<papel>`.
- **B8 — separação.** Quem decidiu como setor numa rodada não libera essa rodada; o liberador também não é
  autor do PR nem proponente.
- **B9 — legado.** Regra que já roda antes do catálogo entra como `legado_pendente` e é validada no
  snapshot do `main`. "`main` nunca tem regra sem aprovação" vale para regra nova e para mudança.
- **B10 — dois GitHub Apps**, `maezo-regras-leitor` (só leitura) e `maezo-regras-exportador` (escrita em
  conteúdo/PR/issue; nunca faz merge).
- **B11 — KMS e SES fora do BFF**, em tasks agendadas (`regras-cofre`, `regras-sync`, `regras-exportador`).
  O BFF só grava Postgres (decisão + outbox) e lê S3; o portal não ganha egresso novo.
- **B12 — MFA no ato** por step-up do Cognito (`acr_values` + `max_age=300`); o BFF exige `amr ∋ "mfa"`,
  `acr ∈ {loa:3, loa:4}` e `agora − auth_time ≤ 300 s`. Sem prova, sem decisão (fail-closed).

E, como restrições de produto já decididas pelo usuário:

- **S3 é cofre de prova e vitrine, não autoridade de produção.** Produção confere o manifesto de regras
  aprovadas que veio com a imagem (digest pinado), relendo-o a cada uso, sem cache (formato 3).
- **Object Lock em modo GOVERNANCE no dev**, com legal hold e deny explícito de Delete/BypassGovernance;
  o modo COMPLIANCE em produção é decisão do DPO.
- **O perfil Liberação NÃO entra em `.github/production-approvers.yaml`**: liberar uma regra é conferir
  que os setores certos decidiram sobre o hash certo, não aprovar deploy.
- **O catálogo vive em `spec/regras/catalogo/<PROCESSO>.yaml`**, uma entrada por regra com ID estável,
  hash por regra (CRLF→LF, como a DL-0148) e, só por acréscimo, `aprovacoes`/`liberacoes`/`comprovacoes`
  — escritas exclusivamente pelo exportador (transcrição de recibo) ou, na fase 1, por transcrição manual
  de uma pessoa do time técnico com evidência. Nenhum agente escreve aprovação (DL-0194).

Fases (blueprint §7): 0 governança · 1 catálogo em git + CI + piloto (Finanças, FIN-01..FIN-08,
`legado_pendente`) por transcrição manual · 2 cofre + snapshots + área Regras só leitura · 3 decisão com
MFA, recibo, e-mail, Liberação · 4 exportador + CI exigindo recibo · 5 manifesto + conferência em cada uso ·
6 propor alteração, remoção, demais setores.

## Consequências

**Positivas:** a pergunta "quem aprovou esta regra, em que versão" passa a ter resposta por regra, com
prova verificável offline; o CI não ganha poder de assinatura; o portal não ganha egresso; a fase 1 roda
sem portal; aprovação parcial deixa de passar nos gates (`check_aprovacao_de_regras.py` multi-papel e
`signoff.py` com o conjunto exato do tracker).

**Negativas (aceitas):**
- A assinatura é do workload: comprometer o workload do portal permite gravar decisão falsa assinada
  (blueprint R-07). Controle: KMS só para a role, trilha de auditoria encadeada.
- O checker roda do HEAD do PR; um PR que altere checker e catálogo passaria nos próprios checks. Proteção:
  `scripts/ci/` é caminho de dono (`flip-path-review-gate`) e o mesmo check roda no `main` (R-08).
- CODEOWNERS só tem efeito com dono HUMANO qualificado ≠ autor dos PRs do agente (pergunta P1 aberta).
- O material staff vence a cada 14 dias; vencido, o motor não sobe e a validação para (R-04).
- O egresso `0.0.0.0/0` dos tasks expira em 2026-12-19; `regras-sync`/`regras-exportador` precisam de
  regra própria para o GitHub antes disso (R-05).
- MFA depende do plano do pool Cognito e do managed login — NÃO VERIFICADO; smoke obrigatório na fase 3.

## Pendências que este ADR não resolve (perguntas ao dono)

- **P1** — handle GitHub humano dono de `spec/regras/` e dos registros de aprovação no CODEOWNERS.
- **P2** — titular e substituto de `financas` no piloto (nome, e-mail, login GitHub).
- **P3** — FIN-02/03/05/07 (teto e exposição): a chancela por `financas` + `dono` substitui o preenchimento
  do §6 do R-070 ("exclusivo do dono e da atuária") ou só o importa?

## Supersedes

—. Complementa a DL-0148 (registro de aprovações), o ADR-0007 (sem autocertificação) e o ADR-0049
(portal humano); não altera nenhum deles.

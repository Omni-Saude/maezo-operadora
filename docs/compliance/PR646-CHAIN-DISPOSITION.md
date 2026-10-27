# Disposição item-a-item da cadeia PR646 (PL-01…PL-05) — GOV-0.7

**Status:** registro executado · **Data:** 2026-10-27 · **Base:** `origin/main` @ `edd7910b` ·
**Fecha:** GOV-0.7 do plano `docs/plans/maezo-completion-program.md` (tabela W0), parte do finding
**F-P05** ("PLANS.md unchecked ×5 — PR646 close-out chain `:1227-1231`", bundle
JOURNEYS-COMPLETENESS-2026-10, `INVENTORY-C-LEDGER.md` §1.1 e `FINDINGS.md` linha 59) conforme o
encaminhamento do `W6-RATIFICATION-PACKAGE-v1.md` §2.c, grupo "Cadeia PR646 + drafts v2":
"#646 merged (squash `05e9e9f6`, ANCESTOR reconfirmado hoje); GOV-0.7 fecha por apontamento".

**Escopo:** apenas PL-01…PL-05 (as 5 caixas abertas de `PLANS.md`). PL-06/PL-07 (drafts v2, PR #633)
estão no mesmo grupo do §2.c mas fora do escopo da linha GOV-0.7 do plano.

**Nota de linhas:** as citações `:1227-1231` vêm do snapshot pinado de 2026-10-22; em
`origin/main` @ `edd7910b` as mesmas 5 caixas estão hoje em `PLANS.md:1223-1227` (drift de numeração,
conteúdo idêntico, verificado byte-a-byte contra `INVENTORY-C-LEDGER.md` §1.1).

## 0. Confirmação do âncora (re-executada hoje)

```text
$ git merge-base --is-ancestor 05e9e9f6 origin/main; echo "exit=$?"
exit=0
$ git show -s --format='%H%n%s%n%aI' 05e9e9f6
05e9e9f63a26c683106d88334444007461b9e9d9
feat(capabilities): add durable administrative journeys and aggregate shape validation (#646)
2026-10-05T21:48:23-03:00
$ gh pr view 646 --repo Omni-Saude/maezo-operadora --json number,title,state,mergedAt,headRefName,baseRefName,mergeCommit
{"baseRefName":"main","headRefName":"codex/v21-journey-effect-boundary-20261005",
 "mergeCommit":"05e9e9f63a26c683106d88334444007461b9e9d9","mergedAt":"2026-10-06T00:48:24Z",
 "number":646,"state":"MERGED",
 "title":"feat(capabilities): add durable administrative journeys and aggregate shape validation"}
```

O PR #646 está **MERGED** em `main` (squash `05e9e9f6` é o próprio merge commit), e o squash é
ancestral da `origin/main` atual — a cadeia que as 5 caixas descreviam foi executada até o fim antes
de qualquer registro; o que faltava era a disposição escrita.

## 1. Tabela de disposição

| Item (snapshot `:1227-1231` / hoje `:1223-1227`) | Disposição | Prova / apontamento |
|---|---|---|
| **PL-01** — "Compor `main 76e0094d…` (PR657, TISS) no PR646" | **EXECUTADO** (apontamento ao merge do #646) | A composição exigida foi absorvida: `76e0094d` (PR #657, `feat(provider): ship pinned bilateral TISS schema validation (#657)`) é ancestral da `origin/main` que contém o squash do #646 — `git merge-base --is-ancestor 76e0094d055b8a60a470a5db6a96dcea8883d4f0 origin/main` → **exit=0**. O PR646 foi publicado e mergeado já sobre essa linhagem (merge 2026-10-06, §0). |
| **PL-02** — "Verificar wheel instalado fora do checkout, assets TISS/agregado, build/fences e testes focais; criar checkpoints coesos e publicar no mesmo PR646" | **EXECUTADO** (apontamento) | As verificações foram executadas e publicadas na própria cadeia, registradas no bloco WIP do `PLANS.md` da era: "Wheel2e197ba2 instalado19af2ba6 e imagemLinuxAMD64cd7ee828/bedb27d0 verificam978arquivos atuais, dois perfis TISS/seis negativos/cinco carriers-hook0" e "Focais460 PASS sem skips/xfails; lint1392/type557/artifacts0erros PASS" (`PLANS.md:1231` e `:1233` em `edd7910b`), seguido do candidato publicado no mesmo PR646 e mergeado (§0). Re-executar hoje verificações de artefato de 2026-10-05 não é exigível — o apontamento fecha o item. |
| **PL-03** — "Concluir novo CI/custódia e gates finais no head/base atualizados" | **EXECUTADO** (apontamento) | Gates da cadeia concluídos e registrados no mesmo bloco: "[x] CI37349288758 e Security37349288793 SUCCESS na base `298c89eb`: 27.994 UNIT PASS…; 958 integrações PASS…; Custódia: 34 ZIPs, 1.363 arquivos, zero divergências; recibos `8fd960b1`/`42e02b2c`" (`PLANS.md:1220` em `edd7910b`); o merge do #646 (2026-10-06T00:48:24Z) só ocorreu com o CI do candidato aplicado. |
| **PL-04** — "Fazer merge autorizado do PR646 com CI e gates aplicáveis verdes" | **EXECUTADO** | PR #646 **state MERGED**, mergedAt 2026-10-06T00:48:24Z, mergeCommit = squash `05e9e9f6` (GitHub API, §0); `git merge-base --is-ancestor 05e9e9f6 origin/main` → **exit=0**. O merge autorizado é o ato registrado no GitHub; o "recibo humano `a9b13ada`" citado no item pertence à custódia da era v2 (`/Users/familia/.codex/private-evidence/v21-dur-successor-20261005/`, citada em `PLANS.md:1229`), fora do repositório por política. |
| **PL-05** — "Conferir main e executar as sete etapas de CONTRIBUTING. Worktree do agregado fica retida até igualdade dos três arquivos em main e liberação de consumidores; depois snapshot e archive conservador. Preservar checkout principal sujo e worktrees de terceiros." | **DIVIDIDO** — (a) EXECUTADO; (b) **DEFERIDO** | **(a) "Conferir main"** — executado hoje (2026-10-27): `05e9e9f6` e `76e0094d` ancestrais de `origin/main` @ `edd7910b`, ambos **exit=0** (§0 e PL-01). **(b) "sete etapas de CONTRIBUTING" + retenção/snapshot/archive do agregado** — **DEFERIDO** ao teardown da frota Codex: (i) é ato vivo sobre worktrees de terceiros, que a política da casa reserva ao dono (cerca "worktrees alheias"; adjudicação DEFER-OWNER da frota Codex — prompt de teardown v2 pronto-não-executado, cercas C7–C12); (ii) `git worktree list` de 2026-10-27 ainda mostra worktrees ativas da frota (ex.: `/Users/familia/.codex/worktrees/provider-native-network/maezo-operadora` `[codex/provider-native-build-resource-origin-repair]`), confirmando que a etapa de retenção/liberação segue viva e é do domínio owner; (iii) a sequência "sete etapas" citava o CONTRIBUTING da era v2 (2026-10-05) e não corresponde byte-a-byte a seção numerada do CONTRIBUTING atual — reexecutá-la literalmente hoje não teria referência estável. **Destino do defer:** adjudicação/teardown da frota Codex (owner), junto do grupo PL-43…PL-54 ("Cadeia PR644/PR646 build") do mesmo §2.c. |

## 2. Reconciliação

| Disposição | Itens |
|---|---|
| EXECUTADO (apontamento ao merge do #646) | PL-01, PL-02, PL-03 |
| EXECUTADO (ato vivo já registrado) | PL-04 |
| EXECUTADO (parte a) / DEFERIDO (parte b → teardown frota Codex, owner) | PL-05 |

Cobertura: 5/5 itens dispostos; nenhum item fica sem estado. Com esta disposição, o grupo
"Cadeia PR646 + drafts v2 (PL-01…PL-07)" do §2.c resta íntegro para aposentadoria no registro W6:
PL-01…PL-05 dispostos aqui; PL-06/PL-07 seguem no grupo (draft #633 da era v2, fora do escopo
desta linha).

## 3. Limites

1. `PLANS.md` NÃO foi editado (arquivo intocável — WIP do orquestrador paralelo / custódia do dono);
   a disposição vive neste doc e a linha GOV-0.7 do plano aponta para ele.
2. Nada aqui decide de novo: o merge do #646 é fato registrado no GitHub desde 2026-10-06; a tabela
   acima apenas o documenta por item.
3. O defer de PL-05(b) não cria prazo novo; segue o calendário da adjudicação da frota Codex
   (DEFER-OWNER), sem onda de engenharia associada.

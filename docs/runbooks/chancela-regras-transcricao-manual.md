# Runbook — transcrição manual da chancela de regras (fase 1, sem portal)

**Quando usar:** enquanto a área "Regras" do portal não existe (fases 1 a 3 da chancela; ADR-0080 e
DL-0194, ambos **PROPOSTOS**). A partir da fase 4, aprovação no git só entra por recibo transcrito pelo
exportador; as transcrições manuais desta fase ficam numa lista congelada no corte e nenhuma nova é aceita.

**Quem executa:** uma **pessoa** do time técnico. **Nunca um agente** — nenhum agente cria, edita ou
antedata aprovação, chancela, liberação ou signoff (ADR-0007; regra 5 do `docs/sme-dispatch/README.md`).
Quem transcreve não pode ser quem respondeu como setor.

**Onde:** `spec/regras/catalogo/<PROCESSO>.yaml` (piloto: `SP-OP-REEMBOLSO-001.yaml`, FIN-01..FIN-08).

## 1. Antes de pedir a resposta

1. Confira que a regra está no `main` com o hash atual: `make check-catalogo-regras` verde.
2. Anote, por regra, o `id`, o `alias` e o `sha256` declarado. **A resposta vale para esse hash.**
   Se o hash mudar antes da transcrição, a resposta não vale mais e a pergunta é refeita.
3. Mande ao titular do setor (P2: titular e substituto de `financas` ainda **a definir** pelo dono) o
   texto da regra **exatamente como está no catálogo** (`titulo`, `regra`, `exemplo`, `orientacao`,
   `o_que_e`, `o_que_nao_e`) e o `sha256`. Peça uma de três respostas: *De acordo*, *De acordo com
   ressalva* (observação ou condição, por escrito) ou *Não* (com o que deve mudar).
4. FIN-02, FIN-03, FIN-05 e FIN-07 têm `aprovam: [financas, dono]` como **default do blueprint (P3)**.
   Não transcreva nenhuma das quatro antes de o dono responder a P3 (a chancela substitui o §6 do
   R-070 ou só o importa?).

## 2. A evidência

A resposta tem de existir fora da cabeça de alguém: e-mail arquivado ou documento assinado, num lugar
com URL `https` estável e acesso restrito. Calcule o `sha256` do arquivo da evidência (o e-mail
exportado em `.eml` ou o PDF):

```
sha256sum resposta-financas-FIN-01.eml
```

A evidência **não** pode conter dado de beneficiário. Exemplos do catálogo usam nomes fictícios.

## 3. A transcrição (um PR por lote de respostas)

1. Branch nova a partir do `main`. Edite **só** o fim da lista `aprovacoes` da regra (só acréscimo;
   `make check-regras-so-acrescimo` reprova qualquer edição do que já existe):

   ```yaml
   aprovacoes:
     - papel: financas
       aprovador: "Nome como está no cadastro"
       decisao: de_acordo            # de_acordo | de_acordo_com_ressalva
       # ressalva: {tipo: condicao, texto: "..."}   # só com de_acordo_com_ressalva
       regra_sha256: "<o sha256 da regra que a pessoa leu>"
       data: 2026-11-25              # dia da resposta
       valida_ate: 2027-11-25        # data + 1 ano (P1Y); o CI confere
       recibo: null
       transcricao_manual:
         evidencia_url: "https://..."
         evidencia_sha256: "<sha256 do arquivo da evidência>"
         transcrito_por: "Nome de quem transcreve"
   ```

2. Resposta *Não*: **não** entra em `aprovacoes`. Abra uma issue com o `id` da regra e o texto do que
   deve mudar; a correção vem em PR próprio, que muda o hash e reabre a pergunta.
3. Condição: entra como `de_acordo_com_ressalva` + `ressalva.tipo: condicao`. A regra só fica
   "aprovada" quando a comprovação entrar em `comprovacoes` (mesma forma: `recibo: null` +
   `transcricao_manual`).
4. Regenere o controle de alterações e rode os checks:

   ```
   python -m maezo.regras.controle gerar
   make check-chancela-regras
   ```

5. Abra o PR citando a evidência. **Não altere `sha256`, `aprovam` nem o texto da regra no mesmo PR.**
   O merge é de um revisor humano diferente de quem transcreveu.

## 4. O que esta transcrição NÃO faz

- Não liga a faixa automática do reembolso: ela segue desligada (teto R$ 0) até o ato do dono e da
  atuária no §6 do R-070.
- Não substitui o signoff do contrato (`docs/processes/contracts/signoffs/`), que tem gate próprio.
- Não move nada em `spec/aprovacoes-de-regras.yaml` (registro por artefato, DL-0148).

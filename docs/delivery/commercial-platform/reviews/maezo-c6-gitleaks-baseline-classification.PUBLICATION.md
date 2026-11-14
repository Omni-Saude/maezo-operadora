# Publicação derivada da classificação de baseline

Este recibo acompanha `maezo-c6-gitleaks-baseline-classification.public.json`, uma projeção de publicação produzida por terceiro. A admissão pública se aplica ao derivado; ele **não representa os bytes originais** do relatório congelado nem uma nova revisão de sua classificação.

O JSON original tem SHA-256 `c804604a0df11e2f9f2b4a5050f3b4a23dd449301109bec0ead95745e0c34cde`. Seus bytes exatos estão preservados em `../repair-preimages.local/maezo-c6-baseline-publication-projection/originals/maezo-c6-gitleaks-baseline-classification.json`. O Markdown original permanece inalterado, SHA-256 `73f2f98ca74a6aca0edcbaa038cd59a21a05cd2d356a1f0c8ebc3a2e2de1abfa`; uma cópia exata também está nessa custódia privada. O autor desta projeção não editou nem moveu os originais. ROOT efetua separadamente a transferência ordinária do JSON ainda não publicado para a área privada, após verificar custódia/readback.

## Transformação e prova inversa

A única alteração de estrutura entre os fatos originais é `/files`: o objeto de oito entradas, indexado por caminho, passa a uma lista de registros com campos neutros `path` e `file_digest`. Todos os caminhos e digests são preservados exatamente, na mesma ordem. Nenhum registro, resultado, classificação, valor, contagem, referência ou conclusão foi omitido ou corrigido. O campo adicional `publication_projection` identifica explicitamente origem, custódia, forma e algoritmo inverso.

Para reconstruir o JSON original decodificado:

1. Fazer uma cópia do derivado e remover somente `publication_projection`.
2. Exigir registros de `files` com exatamente `path`/`file_digest` e caminhos únicos.
3. Substituir essa lista pelo objeto que mapeia cada `path` para seu mesmo `file_digest`.
4. Comparar integralmente com o JSON original decodificado: a igualdade profunda foi comprovada. A identidade dos bytes originais é uma prova separada, obtida pela cópia exata e pelo SHA-256 antes/depois; não é inferida da reserialização do JSON.

As referências históricas do Markdown original ao nome do JSON congelado continuam sendo referências àquela origem. Este recibo explicita o derivado público e a localização privada da origem sem reescrever o relatório original.

## Verificação delimitada

Gitleaks nativo **8.30.1**, modo `dir .`, regras padrão, sem histórico, exceções novas ou configuração alterada: **rc0, zero findings** sobre um snapshot sintético contendo somente o novo JSON público. Nenhum checkout privado real ou árvore inteira foi varrido. Esse resultado se limita à projeção e não substitui nem reduz os resultados/conclusões de baseline preservados no relatório original. Os valores do inventário são digests de arquivos públicos Git; nenhum valor de credencial foi introduzido ou reproduzido.

Projeção pública SHA-256: `10c62239cfc8f06b81052476afb794c941da427009de8057160ecfe95e4fde84`. Evidência privada em `../repair-preimages.local/maezo-c6-baseline-publication-projection/`: `inverse-proof.json`, `final-readback-proof.json`, receipt/log/report da verificação delimitada e originais exatos. Diretórios 0700, arquivos 0600. Recibo de custódia SHA-256: `9360f7d2058baa2f2138be4014f5e242aa96f77184d5f22338b1ae29e95e1d92`.

Somente o novo `.public.json`, o Markdown original inalterado e este `PUBLICATION.md` compõem a publicação admitida por ROOT. A cópia raw congelada permanece privada. Próximo gate: rechecagem independente do ARCH original e disposição de publicação por ROOT; nenhum Git, scanner ou outro arquivo existente foi alterado por esta autoria.

"""Ferramentas de QA que exercitam o produto por fora (so' dev).

Nada em `maezo.platform` (nem em qualquer outro pacote de producao) importa este pacote: a cerca
`scripts/ci/check_qa_isolada.py` reprova o import. O caminho e' sempre o inverso — o QA compoe o
produto real (`platform.webhooks.service.build_live_dispatcher`) e troca so' as bordas de saida.
"""

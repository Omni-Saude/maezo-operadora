"""Saida a pedido do beneficiario (E-SF-09): arrependimento e cancelamento a pedido.

Variante executavel, portao fechado por construcao: o pacote so VALIDA, CLASSIFICA e ENCAMINHA
(`route_to_human`). Nao envia mensagem, nao emite evento, nao decide dinheiro nem desfecho, nao recusa
por prazo e nao usa dado de saude. Prazos e textos regulatorios sao DADO em
`spec/policies/saida-a-pedido/*.yaml` (nunca Python). Contrato: `SP-OP-ARREPENDIMENTO-001`.
"""

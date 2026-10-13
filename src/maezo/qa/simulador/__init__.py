"""Simulador de beneficiario: conversas sinteticas contra o despachante REAL do receptor, no dev.

Runbook: `docs/runbooks/simulador-beneficiario.md`. Visao geral:

  roster (S3)  ->  preflight na AMH  ->  composicao (despachante real + bordas trocadas)
     ->  condutor (persona LLM <-> Helena/Lucas, um telefone sintetico por pessoa)
     ->  checagens D1..D12 + juiz (mesmo modelo, sa-east-1)  ->  relatorio mascarado no S3
     ->  limpeza (checkpoint, roteador, acesso; motor sem caso ESC)

O que NUNCA acontece: envio pela Meta (o cliente do WhatsApp e' trocado por captura), caso real no
motor (SP-OP-ESCALATION-001 e' interceptado), gravacao de consentimento de terceiro na AMH no modo
`somente-leitura` (o padrao), artefato fora da AWS.
"""

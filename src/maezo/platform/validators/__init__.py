"""Production-validators de plataforma — leitores fail-closed, nunca atores (VW5+).

Um validator verifica um ALVO descrito (config, estado de migrations, readiness, store, ledger)
e produz relatório PASS/FAIL por item, fail-closed em unknown. Nenhum validator aqui liga flag,
aplica migration, publica projection ou decide deploy — o go/no-go permanece HUMANO.
"""

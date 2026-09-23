"""Gerador offline dos materiais do plano staff (plano `portal-autoridade-nativa-dev`, T1.3).

Duas ferramentas, dois donos, dois comandos:

* ``python -m tools.staff_materials`` — **engenharia**. Gera as chaves Ed25519 dos papeis
  designados, as duas CAs nativas, os certificados TLS (servidor e clientes), os DSNs com
  verificador SCRAM e o **rascunho** da designacao, sem assinatura. Confere o pacote montado
  com o proprio loader do portal (`maezo.gateway.staff_cases.materials.decode_bundle`).
* ``python -m tools.staff_materials.approver`` — **aprovador humano, na maquina dele**. Gera a
  raiz Ed25519 `installation-root`, assina a designacao (`installation-proof.json`) e a admissao
  Q2. E o unico codigo deste repositorio que toca a chave privada raiz.

Regra D-F (ADR-0060 e plano §2): nenhum caminho de engenharia gera, guarda ou usa a chave privada
raiz. O modulo de engenharia nao importa o do aprovador, e o teste
`tests/unit/staff_materials/test_separation.py` prova isso pela arvore de imports. Sem a
assinatura do aprovador o loader recusa o pacote (`materials.py:111-122`): a engenharia nao tem
como produzir um pacote aceito sozinha.

Nada aqui fala com a AWS, com banco ou com rede. A saida vai para um diretorio NOVO, 0700, fora do
repositorio.
"""

"""Instalador da Onda 3 (banco) que roda DENTRO da VPC, na imagem `deploy/ops/staff-install.Dockerfile`.

Nenhum segredo viaja por `containerOverrides` nem por argumento: as senhas vem do Secrets Manager
por `GetSecretValue` (task role dedicada, ARNs exatos) e os verificadores SCRAM sao calculados aqui
dentro. A saida e so o JSON PUBLICO de pins. Ver `installer.py` para a ordem e o porque de cada passo.
"""

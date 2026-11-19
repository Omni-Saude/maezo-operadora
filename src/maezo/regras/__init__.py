"""Chancela de regras pelos responsáveis técnicos (ADR-0080 e DL-0194, PROPOSTOS).

Biblioteca ÚNICA usada igual pelo CI, pelo `regras-sync`, pelo portal, pelo exportador e pelo
leitor do manifesto: catálogo (`catalogo/`), JSON canônico (`canonico`) e controle de alterações
(`controle`). Pura: stdlib + `yaml` (+ `cryptography` quando o recibo entrar, onda 3/4).

Nenhuma função daqui cria, edita ou infere aprovação (ADR-0007; regra 5 do sme-dispatch).
"""

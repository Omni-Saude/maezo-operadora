# Saida 7844 — a porta do tunel Cloudflare.
#
# Separado de security_groups.tf de proposito: estas duas regras existem por uma
# medicao especifica (o tunel nao registrava com so' a 443 liberada) e ficam ao lado
# do servico que as exige, nao no meio das regras de banco e engine.
#
# QUIC (UDP) e' o protocolo preferido do cloudflared; TCP e' o fallback HTTP/2. As
# duas ficam abertas porque uma borda que so' funciona por UDP e' uma borda que cai
# quando alguem endurece a rede — e o modo de falha e' um tunel que nao sobe, nao um
# aviso.
#
# ATENCAO ao texto de `description`: a AWS aceita apenas
# `a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*` ai. Um travessao (o caractere longo) faz o
# AuthorizeSecurityGroupEgress falhar com "Invalid rule description" — foi o que
# aconteceu na primeira tentativa, e o erro nao diz QUAL caractere ofendeu.
#
# Destino 0.0.0.0/0 porque os endpoints do Cloudflare sao anycast globais
# (region1/region2.v2.argotunnel.com) e a lista de IPs muda sem aviso. Fixar CIDR
# aqui seria trocar uma regra larga por uma regra que quebra em silencio.

resource "aws_vpc_security_group_egress_rule" "cloudflared_quic" {
  security_group_id = aws_security_group.tasks.id
  description       = "Tunel Cloudflare: QUIC (protocolo preferido)"
  ip_protocol       = "udp"
  from_port         = 7844
  to_port           = 7844
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "cloudflared_http2" {
  security_group_id = aws_security_group.tasks.id
  description       = "Tunel Cloudflare: fallback HTTP/2 sobre TCP"
  ip_protocol       = "tcp"
  from_port         = 7844
  to_port           = 7844
  cidr_ipv4         = "0.0.0.0/0"
}

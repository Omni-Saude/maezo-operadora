# Validação estrutural bilateral TISS 04.03

Estado: implementação de software para revisão independente; sem admissão operacional,
fonte de dados produtiva, assinatura, envio, política clínica/financeira ou recibo.
Rastreio: ADR-0063 §4/§7, ADR-0006, DL-0029; SP-OP-CONTAS-001 (conta TISS),
SP-OP-RECURSO-001 (intake bilateral e resposta). Não integra nem substitui
SP-OP-ANS-SUBMIT-001, cujo monitoramento ANS 01.06 permanece recusado.

## Fonte pública fechada

ANS, componente Comunicação 202511, catálogo setembro/202609:
[catálogo oficial](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/padrao-tiss-setembro-2026),
[ZIP oficial](https://www.gov.br/ans/pt-br/assuntos/prestadores/padrao-para-troca-de-informacao-de-saude-suplementar-2013-tiss/PadroTISSComunicao_202511.zip).
ZIP SHA256 `db8640e1c3b87085892f54f838bfcea9934439ff365798c8428559f88c13d62d`.
São sete arquivos XSD originais de `Padr╞o TISS Comunicaç╞o 040300`, preservados
byte a byte em `spec/schemas/tiss/04.03.00/`, sem reescrever encoding ou nomes.

O manifesto `provider-source-pin.json` tem SHA256
`d071d5ec3f3ed4d0b93526262e32539e046c7b21d51a3173bf2b3c42f14e6a97`.
Relaciona original/final URL, recuperação, tamanho/hash do ZIP, versões, entrypoints,
mapas transitivos, qualificação independente da fonte e atribuição/licenças declaradas.
O rodapé gov.br declara CC BY-ND 3.0; o XMLDSIG preserva sua declaração W3C
Software License 19980720. Atribuição/proveniência são registros factuais, sem parecer
jurídico sobre licenciamento. Os hashes da fonte são públicos; payload privado não é
hashado nem publicado por este módulo.

Perfil XML (`tissV4_03_00.xsd`): seis membros transitivos, mapa SHA256
`e76b264ee9a2b0069d8cf6cd4906f5267a365abb61e44b37e1edd3aa6515aa51`.
Perfil WS body (`tissWebServicesV4_03_00.xsd`): sete membros transitivos, mapa SHA256
`667f822947cdaacf47af4991566b64c07103d365171083a9b33aa1feb5388fe6`.
O perfil WS recebe somente o elemento de body da operação, não envelope SOAP.
Envelope/action/endpoint/WSDL e transporte exigem composição própria qualificada.

Publicação da competência: 30/09/2026; vigência: 01/10/2026; limite de implantação:
31/12/2026, conforme evidência primária congelada. Essas datas não são prazos de
análise de conta, recurso, pagamento ou credenciamento. A versão de catálogo
`04.03.00` e o literal wire `4.03.00` são distintos e comparados exatamente.
Ausência da dependência oficial `tissSimpleTypesMonitoramentoV1_05_01.xsd` impede
01.06; não há alias, remapeamento nem fallback para outro arquivo.

## Contrato de software e integração

Export fechado: `maezo.gateway.tiss.ProviderSchemaValidator`. Input obrigatório:
`payload: bytes`, `profile: Profile`, `direction: Direction`, `operation: str` pertencente
à tabela imutável `OPERATIONS`, `catalogue_version: str` exata. Não há seleção de XSD
por caller, path/URL de storage, override de package root no construtor, default de
versão/direção/operação ou variável nova de ambiente. A resolução da árvore segue
`resolve_spec_dir()` existente; toda fonte resolvida precisa dos mesmos hashes fixos.
As 27 transações da enum oficial estão explicitamente mapeadas a direção, wrapper XML,
raiz WS/body e, quando inequívoco, subtype. `envioDOcumento` conserva a grafia genuína.
Fault WS não é uma transação bilateral e é recusado pelo catálogo. Mapeamento de
`situacaoDemonstrativoRetorno` nos demonstrativos permanece conservador: sem subtype
correspondente ou `mensagemErro`, a mensagem é recusada, sem inferir equivalência.

Antes de chamar este componente, a composição precisa comprovar source/tenant/actor/
purpose/revision/correlation e acordo de versão quando aplicável, verificar custódia do
body_ref opaco e obter os bytes por reader autorizado com ceiling. Depois de I/O e antes
de efeito, revalidar currentness/revogação e a autorização. Este componente é uma função
técnica, sem API de publicação ou transmissão e sem comprovar esses fatos externos.
Não está registrado em worker, tool, registry, engine ou endpoint nesta fatia.

Output `ProviderSchemaResult`: disponibilidade do pacote público verificado, `schema_valid`
(XSD e contrato estrutural completos), `xsd_valid` separado, tuple de motivos fixos,
pin SHA256, versão catálogo/wire e digest do grafo quando selecionado. `available=False`
inclui seleção recusada, source pin ausente/alterado ou payload recusado antes de load.
O resultado não contém XML, valor, QName recebido, path, error_log, stack/causa,
hash de payload privado, identidade/source revision ou certificado. Todos os diagnósticos
usam tokens fechados; nenhum nome extraído de mensagem libxml é considerado seguro.

`schema_valid` não afirma `tiss_transmitido`, validade de hash de epílogo, assinatura,
signatário, envio, recebimento real, decisão humana, cobertura, glosa ou pagamento.
As fontes reais e os verificadores desses fatos continuam exigidos para seus efeitos.
Receipts de portal/publicação não equivalem a envio TISS. Nenhuma autorização SPIFFE,
PHI, PEP ou autonomy hard é mudada pelo parser.

## Controles e prova

Cada chamada revalida manifesto e todos os sete arquivos antes do cache. Rejeita arquivo
extra, ausente, symlink, fonte não regular, bytes acima do ceiling, hash/namespace inválido
ou include/import/redefine fora dos nomes genuínos. Cache privado por pin SHA256, grafo
e perfil; falhas não são cacheadas e troca de qualquer dependência recusa cache antigo.
O resolver só recebe bytes já hashados e resolve basenames allowlisted via `tiss-pin:///`;
não acessa rede, file URI, path absoluto, traversal ou host externo. DOCTYPE interno do
XMLDSIG é permitido exclusivamente nos bytes públicos pinados, sem DTD externo.

Parser privado: `resolve_entities=False`, `load_dtd=False`, `no_network=True`,
`huge_tree=False`; DOCTYPE, entidade e XInclude recusados, schemaLocation ignorado.
Ceilings fixos: 2 MiB, 50.000 nodes (inclui comments/PI), profundidade 64 e deadline
de 2 segundos. O deadline verifica tempo decorrido ao redor do trabalho limitado libxml;
não interrompe uma chamada nativa em execução. Não aceita XML privado via filesystem.
XSD não altera a árvore ou os lexicais; não normaliza versão/texto para fazê-los passar.

Após XSD: raiz, presença/corpo, literal wire, transação selecionada, origem/destino por
direção, wrapper/operation/subtype precisam concordar. Isso fecha os contraexemplos
genuinamente XSD-valid de versão antiga, body ausente, wrapper vazio, header transação
trocado e origem/destino trocados. Não insere regra determinística de negócio em Python.

Prova focal: `tests/unit/gateway/tiss/test_provider_schema.py` compila os XSD genuínos
e usa apenas payloads **sintéticos de estrutura**, nunca exemplos oficiais ou recibos.
Cobertura inclui XML/WS, ambas direções, versões antigas, cardinalidade/data/namespace,
header-body-operation, XXE/DTD/UTF-16/XInclude, schemaLocation externo, size/nodes/depth/
deadline, canários em nomes/valores/namespaces, ausência/alteração de cada pin após cache,
symlink/extra/traversal/URI e correspondência da tabela aos elementos/enum do schema.
Engine/PG não são necessários para este componente sem efeito. Composição posterior que
afetar processo precisará de teste CIB Seven real e gates de source/currentness próprios.
Empacotamento wheel/container é uma alteração coordenada do ROOT; a revisão final deve
verificar os sete XSD e manifesto dentro do artefato efetivamente construído.

"""Guardas negativas EXECUTÁVEIS do GP11-wiring (VW4/OP20) — o teste fica VERMELHO se o firewall ceder.

Suíte irmã de `tests/unit/gateway/test_vendor_suppression.py` (comportamento no head) e de
`tests/unit/tools/workers/test_suppression.py` (wiring dos workers). Aqui cada teste INJETA a
violação e AFIRMA que o sistema RECUSA, e cada recusa vem com uma PROVA DE SENSIBILIDADE: um
segundo teste aplica, IN-BODY (`monkeypatch.setattr` — nenhum byte de `src/` é editado), a
mutação que quebraria o firewall e afirma que o MESMO probe fica vermelho. Sem `xfail`
fabricado, sem engine mockada: a DMN é o artefato versionado lido como XML, o chokepoint é o
módulo real e o store é o Protocol.

Autoridade normativa (SNAPSHOT, não de memória): `VW4-RATIFICATION-ANSWERS-V1.md` §GP11 —
sha256 `ab262f7b1bea8eb98a4c6e4515b846b689f7b29e214ad96a68f47be247edf18a` conferido no ato da
escrita desta suíte. NÃO existe diretório `evidence/regulatory/` nesta worktree: as citações
verbatim abaixo saem do insumo canônico sha-verificado, cuja cadeia SOURCED aponta para os
snapshots brutos em `docs/audits/VENDOR-XP-2026-10/vw4-answers-workspace/facts/tmp-GP11/`
(`adh-privacy-checks-extract.txt` k=50; `li-matched-audiences*` audiência ≥300) e para o
extract Planalto `l13709-extract.txt` (sha `e288595b…`) para os arts. 10 §1º e 11 §5º da
LGPD. Pedir ao br-regulatory-analyst o snapshot de `evidence/regulatory/` é item aberto do
relatório desta suíte — não foi fabricado aqui.

Mapa guarda → teste → como falha:

- **GATE 1 — PHI (central).** §4b, linha C3 (verbatim): "PROIBIDO como input de targeting e no
  egresso, em qualquer granularidade (G-PHI absoluto; art. 11 §5º; PAT-20 catálogo Limit)".
  (i/ii/iii) C3/C1/C4 no payload ⇒ recusa tipada `PHI_IN_COMMERCIAL_INPUT` do egresso INTEIRO,
  ZERO efeito a jusante, ANTES do gate de audiência (ordem mecânica §4a); (iv) célula de TUPLA
  C2/C6 com < k=100 ⇒ LINHA suprimida, contada e nunca exportada; (v) classe DESCONHECIDA ⇒
  recusa — nunca passa como comercial. Sobre "conta no KPI": §4e critério 4 (verbatim)
  "chokepoint recusou corretamente — isso NÃO é violação (a recusa tipada é o firewall
  funcionando)" — o que conta é o contador presence-only de TENTATIVAS (visibilidade do
  firewall); o contador de VIOLAÇÃO permanece 0. Gate 4 abaixo testa os dois lados.
- **GATE 2 — CADE (estrutural).** §4b, linha C5 (verbatim): "rate/contagem nunca atravessa
  payload (G-CADE)". Assert estrutural: o payload do egresso só tem os campos do contrato —
  nenhuma rate/percentual/preço médio/comissão tem onde existir, e INJETAR um é recusado
  (`TypeError` da forma fechada).
- **GATE 3 — N/A-documentado.** RN 659/2025 (venda coletiva), RN 518/2022 (relato/disclosure de
  remuneração) e alçada financeira (comissão calculada/decidida por IA ou DMN não ratificada)
  são N/A para este pacote POR CONSTRUÇÃO, não por omissão: o chokepoint opera sobre listas de
  NÃO-contato (registro preventivo art. 7º IX + 18 §2º) — não forma coletivo, não relata, não
  calcula nem paga comissão. A DMN `suppression_routing` só emite tokens de roteamento de
  ESTADO; o vocabulário fechado do worker exclui os verbos de autoridade financeira
  (`LIBERAR`/`AUTORIZAR`/`PAGAR`/`CALCULAR` — molde classificar-rotear-nunca-decidir). As
  guardas daqui são estruturais e provam a ausência da superfície (gate 2 + testes de DMN/vocab).
- **GATE 4 — semântica do KPI §4e.** Dois lados: recusa tipada NÃO soma em
  `phi_egress_violations` (firewall funcionando); violação é C1/C3 que PASSOU, célula < k que
  SAIU ou egresso fora do chokepoint — e cada um dos 4 critérios soma. Além disso `k_piso` é
  INPUT/PARAM injetado VIVO pelo worker e a tabela nunca carrega a cifra.
- **GATE 5 — audiência ≥300 (§4a).** Verbatim: "o correto é **recusar a exportação inteira**
  (fail-closed), não \"completar\" a lista". Testa os DOIS lados do gate cumulativo: audiência
  declarada < 300 E audiência PÓS-k-anon < 300 — e o evento de recusa fica contado.

Cada `test_*_sensibilidade_*` é a prova de que o probe correspondente não é vacuamente verde.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

from maezo.gateway import vendor_suppression
from maezo.gateway.vendor_suppression import (
    EGRESS_AUDIENCE_GATE_MINIMUM,
    K_ANON_FLOOR,
    PROHIBITED_FIELD_CLASSES,
    DeclaredField,
    EgressLine,
    FieldClass,
    PhiEgressViolationClass,
    SuppressionEgressAttempt,
    SuppressionEgressTelemetry,
    SuppressionError,
    SuppressionKey,
    SuppressionRefusalReason,
    prepare_egress_export,
)
from maezo.tools.workers import suppression as suppression_worker
from maezo.tools.workers.suppression import (
    SUPPRESSION_ROUTING_DMN,
    SuppressionContractMismatchError,
    make_route_handler,
)

# ----------------------------------------------------------------------------------
# Snapshots de head — as cifras ratificadas CONGELADAS neste namespace.
#
# Os probes passam `k=vendor_suppression.K_ANON_FLOOR` (leitura VIVA) exatamente para que a
# mutação `K_ANON_FLOOR -> 1` mordida: o default `k=K_ANON_FLOOR` da assinatura é ligado na
# definição da função, e patchar o módulo não o altera. O lote viável, ao contrário, é
# construído sobre o snapshot de head — senão a mutação encolheria o próprio controle.
# ----------------------------------------------------------------------------------
K_PISO_HEAD = K_ANON_FLOOR
AUDIENCIA_HEAD = EGRESS_AUDIENCE_GATE_MINIMUM

_TODAS_AS_CLASSES_PROIBIDAS = (
    FieldClass.C1_IDENTIFICATIVO,
    FieldClass.C3_CLINICO_PROTEGIDO,
    FieldClass.C4_CLINICO_AREGREGADO,
)

# ----------------------------------------------------------------------------------
# Artefato DMN `suppression_routing` — lido do repo, sem engine (evaluator local é proibido
# por `dmn_transport.py:1-8`; engine real é lane de integração). A partição lógica da tabela
# É a guarda: nenhuma row pode honrar classe clínica, e a catch-all é a ÚLTIMA row.
# ----------------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DMN_PATH = _REPO_ROOT / "spec" / "processes" / "dmn" / "suppression_routing.dmn"
_DMN_NS = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"
#: Ordem congelada das colunas de entrada (contrato por NOME da admissão GP11).
_DMN_COLUNAS = ("canal", "categoria_sujeito", "classe_campo", "celula_tamanho", "k_piso")
_ROTA_HONRADA = "REGISTRO_HONRADO"
_ROTA_HUMANA = "RECLAMACAO_ENCARREGADO"
#: Verbos de AUTORIDADE — a tabela de roteamento nunca decide (molde classificar-rotear-nunca-decidir).
_VERBOS_DE_AUTORIDADE = ("LIBERAR", "AUTORIZAR", "PAGAR", "CALCULAR")
_TOKENS_DE_CLASSE = tuple(classe.value for classe in FieldClass)


def _dmn_root() -> ET.Element:
    return ET.fromstring(_DMN_PATH.read_bytes())


def _dmn_textos(rule: ET.Element, tag: str) -> list[str]:
    """Valores das entries da row SEM as aspas FEEL (a tabela escreve `"C2"` com aspas literais)."""
    textos: list[str] = []
    for entry in rule.findall(f"{_DMN_NS}{tag}"):
        valor = entry.find(f"{_DMN_NS}text")
        textos.append(("" if valor is None or valor.text is None else valor.text).strip().strip('"'))
    return textos


def _probe_dmn_particao_nunca_honra_clinica(root: ET.Element) -> None:
    """GATE 1(v)/GATE 3/GATE 4 sobre o ARTEFATO: nenhuma row honra classe clínica, catch-all é humana,
    k_piso é parâmetro (a tabela nunca carrega a cifra) e nenhum verbo de autoridade é emitido."""
    table = root.find(f"{_DMN_NS}decision/{_DMN_NS}decisionTable")
    if table is None:
        raise AssertionError("suppression_routing sem decisionTable")
    if table.attrib.get("hitPolicy") != "FIRST":
        raise AssertionError(
            "hitPolicy deixou de ser FIRST — a ordem fail-closed das rows deixou de ser semântica"
        )
    rows = table.findall(f"{_DMN_NS}rule")
    if not rows:
        raise AssertionError("suppression_routing sem rows — tabela vazia não é firewall")
    for indice, rule in enumerate(rows):
        rotulo = rule.attrib.get("id", f"row#{indice}")
        entradas = _dmn_textos(rule, "inputEntry")
        saidas = _dmn_textos(rule, "outputEntry")
        if len(entradas) != len(_DMN_COLUNAS) or len(saidas) != 2:
            raise AssertionError(f"{rotulo}: linha fora do contrato de colunas {entradas}/{saidas}")
        rota, grupo = saidas
        if rota in _VERBOS_DE_AUTORIDADE:
            raise AssertionError(f"{rotulo}: a DMN emite o verbo de autoridade {rota!r} — DMN nunca decide")
        if rota not in (_ROTA_HONRADA, _ROTA_HUMANA):
            raise AssertionError(f"{rotulo}: rota {rota!r} fora do vocabulário fechado de ESTADO")
        if not grupo:
            raise AssertionError(f"{rotulo}: sem grupo_decisao — decisão sem destino/ratificação humana")
        classe, celula, categoria = entradas[2], entradas[3], entradas[1]
        if classe != "-" and classe not in _TOKENS_DE_CLASSE:
            raise AssertionError(
                f"{rotulo}: coluna classe_campo carrega token fora do catálogo fechado C1-C6 "
                f"({classe!r}) — é por aqui que conteúdo clínico entraria na tabela"
            )
        if "100" in celula:
            raise AssertionError(
                f"{rotulo}: a célula carrega a CIFRA ({celula!r}) — k deixou de ser input `k_piso`"
            )
        if rota == _ROTA_HONRADA:
            # Partição §4b: só C5 (livre, presence-only) e C2/C6 (com célula >= k) honram.
            if classe in ("C1", "C3", "C4"):
                raise AssertionError(
                    f"{rotulo}: REGISTRO_HONRADO para classe {classe!r} — classe clínica (C3/C4) "
                    "ou identificativa (C1) foi HONRADA (G-PHI absoluto/art. 11 §5º)"
                )
            if classe == "-" or classe not in _TOKENS_DE_CLASSE:
                raise AssertionError(
                    f"{rotulo}: REGISTRO_HONRADO para classe {classe!r} — classe não declarada/fora "
                    "do catálogo foi honrada (nunca por inferência)"
                )
            if classe in ("C2", "C6"):
                if ">=" not in celula:
                    raise AssertionError(
                        f"{rotulo}: honra sem célula >= k_piso ({celula!r}) — k-anon da tupla burlado"
                    )
            elif celula != "-":
                raise AssertionError(f"{rotulo}: C5 honrada com partição de célula ({celula!r}) — drift")
            if categoria == "desconhecido":
                raise AssertionError(f"{rotulo}: categoria não declarada foi honrada — primeira row violada")
    ultima = _dmn_textos(rows[-1], "inputEntry")
    if any(entrada != "-" for entrada in ultima):
        raise AssertionError(
            f"a ÚLTIMA row deixou de ser catch-all ({ultima}) — honra por omissão volta a existir"
        )
    saida_ultima = _dmn_textos(rows[-1], "outputEntry")
    if saida_ultima[0] != _ROTA_HUMANA:
        raise AssertionError(f"catch-all devolve {saida_ultima[0]!r} — o default deixou de ir ao humano")


# ----------------------------------------------------------------------------------
# Fábricas — chaves opacas e linhas de egresso (nenhum conteúdo de pessoa existe aqui)
# ----------------------------------------------------------------------------------


def _linha(n: str, tupla: tuple[str, ...], *campos: DeclaredField) -> EgressLine:
    return EgressLine(
        key=SuppressionKey(subject_ref=f"subj-{n}", contact_channel="chan-A"),
        fields=campos or (DeclaredField("presenca", FieldClass.C5_COMPORTAMENTAL),),
        tuple_value=tupla,
    )


def _lote_viavel(celulas: int = 3) -> list[EgressLine]:
    """`celulas` células de EXATAMENTE k linhas cada (snapshot de head) — passa o gate de linha
    e fecha a audiência do canal. Campos C6 por carteira: a tupla é C2/C6 nos dois casos."""
    return [
        _linha(
            f"l{celula}-{i}",
            (f"carteira-{celula}",),
            DeclaredField("producao_por_carteira", FieldClass.C6_CONTRATUAL_ADMINISTRATIVO),
        )
        for celula in range(celulas)
        for i in range(K_PISO_HEAD)
    ]


# ----------------------------------------------------------------------------------
# GATE 1 (i) — C3 (clínico-protegido) no payload de egresso
# ----------------------------------------------------------------------------------


def _probe_c3_recusa_o_egresso_inteiro(telemetria: SuppressionEgressTelemetry) -> None:
    """Lote viável (3 células de k) + UMA linha com CID-10 declarado C3 no meio do lote."""
    contaminada = _linha("alvo", ("carteira-0",), DeclaredField("cid10", FieldClass.C3_CLINICO_PROTEGIDO))
    try:
        prepare_egress_export(
            [*_lote_viavel(), contaminada],
            audience_size=AUDIENCIA_HEAD,
            k=vendor_suppression.K_ANON_FLOOR,
            telemetry=telemetria,
        )
    except SuppressionError as exc:
        if exc.reason is not SuppressionRefusalReason.PHI_IN_COMMERCIAL_INPUT:
            raise AssertionError(
                f"recusou, mas por {exc.reason} — o gate de classe não é a 1ª barreira"
            ) from None
        if telemetria.phi_egress_violations != 0:
            raise AssertionError("a RECUSA foi contada como violação — §4e critério 4 invertido") from None
        if telemetria.window()[SuppressionEgressAttempt.EGRESS_REFUSED_PROHIBITED_CLASS.value] != 1:
            raise AssertionError(
                "a recusa não ficou visível no contador presence-only de tentativas"
            ) from None
        return
    raise AssertionError("payload com CID (C3) PASSOU o chokepoint e produziu egresso — G-PHI inerte")


def test_gate1_i_c3_no_payload_recusa_o_egresso_inteiro_sem_efeito_a_jusante() -> None:
    _probe_c3_recusa_o_egresso_inteiro(SuppressionEgressTelemetry())


def test_gate1_i_sensibilidade_taxonomia_afrouxada_e_detectada(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO (a violação realista: alguém tira C3 da lista de proibidas para destravar o funil):
    sem a classe proibida o egresso clínico PASSA — o probe precisa ficar vermelho."""
    monkeypatch.setattr(vendor_suppression, "PROHIBITED_FIELD_CLASSES", frozenset())
    with pytest.raises(AssertionError):
        _probe_c3_recusa_o_egresso_inteiro(SuppressionEgressTelemetry())


# ----------------------------------------------------------------------------------
# GATE 1 (ii)/(iii) — C1 (identificativo) e C4 (clínico-agregado), e a ORDEM das barreiras
# ----------------------------------------------------------------------------------


def _probe_classe_proibida_e_a_primeira_recusa(classe: FieldClass) -> None:
    """`audience_size=0` é a isca: se a checagem de classe fosse pulada, a recusa viraria
    AUDIENCE_BELOW_GATE. A razão PHI é o que prova que a classe é avaliada PRIMEIRO."""
    telemetria = SuppressionEgressTelemetry()
    try:
        prepare_egress_export(
            [_linha("1", ("tupla-unica",), DeclaredField("campo", classe))],
            audience_size=0,
            k=vendor_suppression.K_ANON_FLOOR,
            telemetry=telemetria,
        )
    except SuppressionError as exc:
        if exc.reason is not SuppressionRefusalReason.PHI_IN_COMMERCIAL_INPUT:
            raise AssertionError(f"{classe} recusada por {exc.reason} — a ordem mecânica §4a cedeu") from None
        if telemetria.phi_egress_violations != 0:
            raise AssertionError(f"{classe}: recusa contada como violação") from None
        return
    raise AssertionError(
        f"{classe.value} PASSOU o chokepoint com audiência 0 — classe proibida virou comercial"
    )


@pytest.mark.parametrize("classe", _TODAS_AS_CLASSES_PROIBIDAS)
def test_gate1_ii_iii_classe_proibida_recusa_antes_de_qualquer_outro_gate(classe: FieldClass) -> None:
    _probe_classe_proibida_e_a_primeira_recusa(classe)


@pytest.mark.parametrize("classe", _TODAS_AS_CLASSES_PROIBIDAS)
def test_gate1_ii_iii_sensibilidade_cada_classe_e_load_bearing(
    classe: FieldClass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUTAÇÃO por classe: retirar EXATAMENTE essa classe da proibição precisa acender o probe —
    prova que nenhuma das três é decorativa (C1 fora do registro, C3 G-PHI, C4 proibido na v1)."""
    restantes = frozenset(PROHIBITED_FIELD_CLASSES) - {classe}
    monkeypatch.setattr(vendor_suppression, "PROHIBITED_FIELD_CLASSES", restantes)
    with pytest.raises(AssertionError):
        _probe_classe_proibida_e_a_primeira_recusa(classe)


# ----------------------------------------------------------------------------------
# GATE 1 (v) — classe DESCONHECIDA nunca passa como comercial (OP20 §3)
# ----------------------------------------------------------------------------------


def _probe_classe_desconhecida_nunca_passa() -> None:
    if len(list(FieldClass)) != 6:
        raise AssertionError("o catálogo C1-C6 deixou de ser fechado (6 classes)")
    telemetria = SuppressionEgressTelemetry()
    # Construído via o símbolo do MÓDULO de propósito: é o nome que um construtor de lista
    # consume — e o único ponto onde um classificador conivente conseguiria relabelar.
    campo = vendor_suppression.DeclaredField("campo_sem_classe_declarada", None)
    try:
        prepare_egress_export(
            [_linha("1", ("tupla",), campo)],
            audience_size=AUDIENCIA_HEAD,
            k=vendor_suppression.K_ANON_FLOOR,
            telemetry=telemetria,
        )
    except SuppressionError as exc:
        if exc.reason is not SuppressionRefusalReason.UNKNOWN_FIELD_CLASS:
            raise AssertionError(f"classe desconhecida recusada por {exc.reason} — razão trocada") from None
        if telemetria.window()[SuppressionEgressAttempt.EGRESS_REFUSED_UNKNOWN_CLASS.value] != 1:
            raise AssertionError("recusa de classe desconhecida não ficou contada") from None
        return
    raise AssertionError("campo SEM classe declarada passou como comercial — recusa por inferência violada")


def test_gate1_v_classe_desconhecida_e_recusa_nunca_comercial() -> None:
    _probe_classe_desconhecida_nunca_passa()


def test_gate1_v_sensibilidade_classificador_conivente_e_detectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO (o classificador aceita o que não veio declarado — `None` vira C5): o egresso
    clínico passa como comportamental-comercial e o probe precisa acusar."""

    real = vendor_suppression.DeclaredField  # capturada ANTES do patch — sem recursão

    def classificador_conivente(name: str, field_class: FieldClass | None) -> DeclaredField:
        return real(name, field_class or FieldClass.C5_COMPORTAMENTAL)

    monkeypatch.setattr(vendor_suppression, "DeclaredField", classificador_conivente)
    with pytest.raises(AssertionError):
        _probe_classe_desconhecida_nunca_passa()


# ----------------------------------------------------------------------------------
# GATE 1 (iv) — célula de TUPLA C2/C6 abaixo de k=100
# ----------------------------------------------------------------------------------


def _probe_celula_subk_suprimida_e_contada(telemetria: SuppressionEgressTelemetry) -> None:
    k = vendor_suppression.K_ANON_FLOOR
    subk = [_linha(f"s{i}", ("carteira-fraca",)) for i in range(K_PISO_HEAD - 1)]  # 99 < k
    chaves_fracas = {linha.key for linha in subk}
    try:
        export = prepare_egress_export(
            [*subk, *_lote_viavel()],
            audience_size=AUDIENCIA_HEAD,
            k=k,
            telemetry=telemetria,
        )
    except SuppressionError as exc:
        raise AssertionError(
            f"célula de 99 (< k) derrubou o egresso INTEIRO ({exc.reason}) — o k suprime LINHA"
        ) from None
    if export.suppressed_lines != K_PISO_HEAD - 1:
        raise AssertionError(f"suprimiu {export.suppressed_lines}, esperado {K_PISO_HEAD - 1}")
    if any(linha.key in chaves_fracas for linha in export.exported):
        raise AssertionError("linha de CÉLULA < k SAIU no egresso — §4e critério 3 consumado")
    if export.below_gate_cells != K_PISO_HEAD - 1:
        # o contador é POR LINHA suprimida (célula de 99), não por célula.
        raise AssertionError(f"below_gate_cells={export.below_gate_cells}, esperado {K_PISO_HEAD - 1}")
    if telemetria.window()[SuppressionEgressAttempt.EGRESS_LINE_SUPPRESSED_SUBK.value] != K_PISO_HEAD - 1:
        raise AssertionError("a supressão sub-k não ficou contada presence-only")
    if telemetria.phi_egress_violations != 0:
        raise AssertionError("suprimir a linha contou como violação — o k-anon é o firewall FUNCIONANDO")


def test_gate1_iv_celula_subk_suprime_a_linha_conta_e_nunca_exporta() -> None:
    _probe_celula_subk_suprimida_e_contada(SuppressionEgressTelemetry())


def test_gate1_iv_sensibilidade_k_virando_1_deixa_a_celula_sair(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO `K_ANON_FLOOR -> 1` (o piso lido VIVO pelo chokepoint): a célula de 99 fica >= k,
    a linha SAI, e o probe — que afirma que ela nunca exportou — precisa ficar vermelho.
    O lote viável usa o snapshot de head justamente para a mutação só afetar o piso em teste."""
    monkeypatch.setattr(vendor_suppression, "K_ANON_FLOOR", 1)
    with pytest.raises(AssertionError):
        _probe_celula_subk_suprimida_e_contada(SuppressionEgressTelemetry())


# ----------------------------------------------------------------------------------
# GATE 1 (targeting) / GATE 3 / GATE 4 — a partição da DMN sobre o ARTEFATO versionado
# ----------------------------------------------------------------------------------


def test_gate1_dmn_nunca_honra_classe_clinica_e_o_k_e_parametro() -> None:
    _probe_dmn_particao_nunca_honra_clinica(_dmn_root())


def test_gate1_sensibilidade_dmn_relabel_catchall_e_cifra_sao_detectados(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Três MUTAÇÕES no artefato parseado (o drift realista de tabela): relabel C2->C3 na row de
    honra; catch-all passando a honrar; cifra `100` escrita no lugar do parâmetro `k_piso`."""
    del monkeypatch  # as mutações são sobre o DOM parseado — in-body, sem tocar `src/`

    def relabel_c3() -> ET.Element:
        root = _dmn_root()
        for rule in root.iter(f"{_DMN_NS}rule"):
            if rule.attrib.get("id") == "r_c2_ok":
                entradas = rule.findall(f"{_DMN_NS}inputEntry")
                entradas[2].find(f"{_DMN_NS}text").text = '"C3"'  # a classe clínica passa a honrar
        return root

    def catch_all_honra() -> ET.Element:
        root = _dmn_root()
        regras = list(root.iter(f"{_DMN_NS}rule"))
        saidas = regras[-1].findall(f"{_DMN_NS}outputEntry")
        saidas[0].find(f"{_DMN_NS}text").text = f'"{_ROTA_HONRADA}"'  # honra por omissão
        return root

    def cifra_na_tabela() -> ET.Element:
        root = _dmn_root()
        for rule in root.iter(f"{_DMN_NS}rule"):
            if rule.attrib.get("id") == "r_c2_ok":
                entradas = rule.findall(f"{_DMN_NS}inputEntry")
                entradas[3].find(f"{_DMN_NS}text").text = ">= 100"  # a cifra mora na tabela
        return root

    mutacoes = (
        ("relabel C2->C3 na row de honra", relabel_c3),
        ("catch-all passando a honrar", catch_all_honra),
        ("cifra 100 escrita no lugar de k_piso", cifra_na_tabela),
    )
    for nome, mutante in mutacoes:
        try:
            _probe_dmn_particao_nunca_honra_clinica(mutante())
        except AssertionError:
            continue
        raise AssertionError(f"a mutação '{nome}' NÃO acendeu a guarda — probe vacuamente verde")


# ----------------------------------------------------------------------------------
# GATE 3 / autoridade — a DMN e o worker nunca emitem verbo de decisão (classificar-rotear)
# ----------------------------------------------------------------------------------


class _SpyDmn:
    """Transport mínimo (o seam `dmn=` do worker) — registra os inputs que a tabela recebeu."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def evaluate(
        self, decision_key: str, variables: dict[str, object], *, tenant: str | None = None
    ) -> tuple[list[dict[str, object]], object]:
        del tenant
        self.calls.append((decision_key, dict(variables)))
        return [dict(row) for row in self.rows], type("Versao", (), {"version": 1})()


class _Tarefa:
    """ExternalTask mínimo — só o que o handler fetcha por contrato de tópico."""

    def __init__(self, variables: dict[str, object]) -> None:
        self.id = "guard-probe"
        self.variables: dict[str, object] = variables
        self.business_key = "GUARD-probe"


def _tarefa_de_rota(**extras: object) -> _Tarefa:
    base: dict[str, object] = {
        "tenant_id": "t-1",
        "canal": "whatsapp",
        "categoria_sujeito": "lead",
        "classe_campo": "C2",
        "celula_tamanho": K_PISO_HEAD + 1,
    }
    return _Tarefa(base | extras)


async def _probe_verbo_de_autoridade_nunca_vira_saida() -> None:
    handler = make_route_handler(_SpyDmn([{"rota": "PAGAR", "grupo_decisao": "comercial"}]))
    try:
        saida = await handler(_tarefa_de_rota())
    except SuppressionContractMismatchError as exc:
        if exc.code != "CONTRACT_MISMATCH":
            raise AssertionError(
                f"incidente com código {exc.code!r} — drift deixou de ser CONTRACT_MISMATCH"
            ) from None
        return
    raise AssertionError(f"o handler devolveu {saida!r} — a tabela PAGOU por fora da alçada humana")


@pytest.mark.asyncio
async def test_gate3_verbo_financeiro_na_dmn_vira_incidente_nunca_saida() -> None:
    """RN 518/659/alçada são N/A aqui POR CONSTRUÇÃO: a tabela de roteamento não tem poder de
    decisão — um `PAGAR` vindo da DMN é incidente humano-visível, nunca variável de processo."""
    await _probe_verbo_de_autoridade_nunca_vira_saida()


@pytest.mark.asyncio
async def test_gate3_sensibilidade_vocabulario_alargado_e_detectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO (alguém inclui `PAGAR` no vocabulário fechado para destravar um pagamento): o
    handler passa a DEVOLVER a decisão da tabela e o probe precisa ficar vermelho."""
    monkeypatch.setattr(
        suppression_worker, "_ROUTE_VOCABULARY", frozenset({_ROTA_HONRADA, _ROTA_HUMANA, "PAGAR"})
    )
    with pytest.raises(AssertionError):
        await _probe_verbo_de_autoridade_nunca_vira_saida()


@pytest.mark.asyncio
async def test_gate4_k_piso_e_injetado_vivo_pelo_worker() -> None:
    spy = _SpyDmn([{"rota": _ROTA_HONRADA, "grupo_decisao": "dpo"}])
    await make_route_handler(spy)(_tarefa_de_rota())
    decisao, inputs = spy.calls[0]
    if decisao != SUPPRESSION_ROUTING_DMN:
        raise AssertionError(f"a tabela avaliada é {decisao!r}, não {SUPPRESSION_ROUTING_DMN!r}")
    if inputs["k_piso"] != K_PISO_HEAD:
        raise AssertionError(
            f"k_piso={inputs['k_piso']!r} — o worker deixou de injetar a constante ratificada"
        )


@pytest.mark.asyncio
async def test_gate4_sensibilidade_k_piso_trocado_no_worker_e_detectado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTAÇÃO (a constante do worker é re-atribuída): o k que chega à tabela muda e o probe,
    que compara contra o snapshot ratificado, precisa ficar vermelho."""
    monkeypatch.setattr(suppression_worker, "K_ANON_FLOOR", 1)
    with pytest.raises(AssertionError):
        await test_gate4_k_piso_e_injetado_vivo_pelo_worker()


# ----------------------------------------------------------------------------------
# GATE 2 — CADE estrutural: o payload do egresso só carrega os campos do contrato
# ----------------------------------------------------------------------------------

_CONTRATO_LINHA = ("key", "fields", "tuple_value")
_CONTRATO_CAMPO = ("name", "field_class")
_CONTRATO_EXPORT = ("exported", "suppressed_lines", "below_gate_cells", "audience_size", "k")
#: G-CADE — nenhum destes termos pode existir como campo de payload de egresso vendor.
_TERMOS_DE_MERCADO = ("rate", "taxa", "percent", "share", "preco", "comissao", "quota", "aliquota", "bonus")


def _probe_payload_so_carrega_o_contrato() -> None:
    for tipo, contrato in (
        (vendor_suppression.EgressLine, _CONTRATO_LINHA),
        (vendor_suppression.DeclaredField, _CONTRATO_CAMPO),
        (vendor_suppression.EgressExport, _CONTRATO_EXPORT),
    ):
        nomes = tuple(campo.name for campo in dataclass_fields(tipo))
        if nomes != contrato:
            raise AssertionError(f"{tipo.__name__} carrega {nomes}, contrato {contrato} — payload alargado")
        for nome in nomes:
            if any(termo in nome.lower() for termo in _TERMOS_DE_MERCADO):
                raise AssertionError(f"{tipo.__name__}.{nome} é rate/medida de mercado — G-CADE (§4b, C5)")
    linhas = [
        vendor_suppression.EgressLine(
            key=SuppressionKey(subject_ref=f"subj-cade-{i}", contact_channel="chan-A"),
            fields=(vendor_suppression.DeclaredField("presenca", FieldClass.C5_COMPORTAMENTAL),),
            tuple_value=("carteira-cade",),
        )
        for i in range(3 * K_PISO_HEAD)
    ]
    export = prepare_egress_export(linhas, audience_size=AUDIENCIA_HEAD, k=vendor_suppression.K_ANON_FLOOR)
    for linha in export.exported:
        nomes = tuple(campo.name for campo in dataclass_fields(type(linha)))
        if nomes != _CONTRATO_LINHA:
            raise AssertionError(f"o EGRESSO devolveu linha com {nomes} — a taxa atravessou o payload")
    try:
        vendor_suppression.EgressLine(
            key=SuppressionKey(subject_ref="subj-cade", contact_channel="chan-A"),
            fields=(vendor_suppression.DeclaredField("presenca", FieldClass.C5_COMPORTAMENTAL),),
            tuple_value=("carteira-cade",),
            taxa_mercado=0.37,  # type: ignore[call-arg] — a VIOLAÇÃO injetada de propósito
        )
    except TypeError:
        return
    raise AssertionError("o payload aceitou `taxa_mercado` — a forma fechada deixou de recusar")


def test_gate2_cade_o_payload_so_carrega_os_campos_do_contrato() -> None:
    _probe_payload_so_carrega_o_contrato()


@dataclass(frozen=True, slots=True)
class _LinhaComTaxa:
    """A VIOLAÇÃO CADE materializada: alguém alarga o payload com uma taxa de mercado."""

    key: SuppressionKey
    fields: tuple[DeclaredField, ...]
    tuple_value: tuple[str, ...] = ()
    taxa_mercado: float = 0.37


def test_gate2_sensibilidade_payload_alargado_com_taxa_e_detectado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vendor_suppression, "EgressLine", _LinhaComTaxa)
    with pytest.raises(AssertionError):
        _probe_payload_so_carrega_o_contrato()


# ----------------------------------------------------------------------------------
# GATE 4 — semântica do KPI §4e: recusa ≠ violação; violação é o que PASSOU
# ----------------------------------------------------------------------------------


def _probe_kpi_violacao_e_o_que_passou() -> None:
    telemetria = SuppressionEgressTelemetry()
    if telemetria.phi_egress_violations != 0:
        raise AssertionError("o KPI nasce != 0 — ele é CONSTANTE, não computado (§4e)")
    for criterio in PhiEgressViolationClass:
        antes = telemetria.phi_egress_violations
        telemetria.record_violation(criterio)
        if telemetria.phi_egress_violations != antes + 1:
            raise AssertionError(
                f"o critério {criterio.value} não soma no KPI — a invariante == 0 ficaria vacuamente verde"
            )
    if telemetria.window()[PhiEgressViolationClass.SUBK_CELL_EXPORTED.value] != 1:
        raise AssertionError("célula < k que SAIU não fica contada na janela (§4e critério 3)")
    # Lado A (a recusa é o firewall funcionando): o MESMO chokepoint que recusa deixa o KPI em 0.
    _probe_c3_recusa_o_egresso_inteiro(SuppressionEgressTelemetry())


def test_gate4_recusa_nao_conta_violacao_e_a_violacao_e_o_que_passou() -> None:
    _probe_kpi_violacao_e_o_que_passou()


def test_gate4_sensibilidade_kpi_silenciado_e_detectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO (o contador de violação é silenciado para o gate ficar verde): com o KPI preso em
    zero a invariante `== 0` fica vacuamente verdadeira — o probe precisa acusar."""
    monkeypatch.setattr(
        vendor_suppression.SuppressionEgressTelemetry,
        "record_violation",
        lambda self, violation, /: None,
    )
    with pytest.raises(AssertionError):
        _probe_kpi_violacao_e_o_que_passou()


# ----------------------------------------------------------------------------------
# GATE 5 — audiência do canal ≥ 300, nos DOIS lados do gate cumulativo (§4a)
# ----------------------------------------------------------------------------------


def _probe_audiencia_abaixo_do_gate_recusa_o_egresso_inteiro(
    telemetria: SuppressionEgressTelemetry, *, linhas: list[EgressLine], audiencia: int
) -> None:
    try:
        export = prepare_egress_export(
            linhas, audience_size=audiencia, k=vendor_suppression.K_ANON_FLOOR, telemetry=telemetria
        )
    except SuppressionError as exc:
        if exc.reason is not SuppressionRefusalReason.AUDIENCE_BELOW_GATE:
            raise AssertionError(
                f"recusou por {exc.reason} — o gate de audiência não é o que mandou"
            ) from None
        if telemetria.window()[SuppressionEgressAttempt.EGRESS_REFUSED_AUDIENCE_GATE.value] != 1:
            raise AssertionError("a recusa de audiência não ficou contada como evento") from None
        return
    raise AssertionError(
        f"exportação COMPLETADA com audiência {export.audience_size} e {len(export.exported)} linhas "
        "— a lista foi 'completada' para fechar o canal (§4a)"
    )


def test_gate5_audiencia_abaixo_de_300_recusa_a_exportacao_inteira_nos_dois_lados() -> None:
    """Lado A — audiência declarada do canal: 300 linhas viáveis, `audience_size=299` ⇒ recusa.
    Lado B — audiência PÓS-k-anon: 200 linhas em 2 células de k, `audience_size=300` (o canal
    fecharia) ⇒ AINDA recusa, porque só 200 sobrevivem ao k-anon. Nunca se completa lista."""
    telemetria_a = SuppressionEgressTelemetry()
    _probe_audiencia_abaixo_do_gate_recusa_o_egresso_inteiro(
        telemetria_a, linhas=_lote_viavel(3), audiencia=AUDIENCIA_HEAD - 1
    )
    telemetria_b = SuppressionEgressTelemetry()
    _probe_audiencia_abaixo_do_gate_recusa_o_egresso_inteiro(
        telemetria_b, linhas=_lote_viavel(2), audiencia=AUDIENCIA_HEAD
    )


def test_gate5_sensibilidade_piso_de_audiencia_abaixado_e_detectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTAÇÃO (o piso do canal é abaixado para o funil fechar): as duas pernas passam a
    exportar e o probe precisa ficar vermelho NA PRIMEIRA."""
    monkeypatch.setattr(vendor_suppression, "EGRESS_AUDIENCE_GATE_MINIMUM", 1)
    with pytest.raises(AssertionError):
        _probe_audiencia_abaixo_do_gate_recusa_o_egresso_inteiro(
            SuppressionEgressTelemetry(), linhas=_lote_viavel(3), audiencia=AUDIENCIA_HEAD - 1
        )


# ----------------------------------------------------------------------------------
# Fecho da suíte — a partição de guardas cobre os 5 gates declarados no briefing
# ----------------------------------------------------------------------------------


def test_as_guardas_negativas_estao_todas_presentes_neste_arquivo() -> None:
    """Inventário estrutural: nenhum gate pode ser apagado em silêncio por uma edição futura.
    Probes e testes são checados por NOME DEFINIDO (globals — imune a reflow de formatação); as
    provas de sensibilidade são checadas no FONTE, porque a prova É a chamada de mutação."""
    definidos = dict(globals())
    for gate in (
        "test_gate1_i_",
        "test_gate1_ii_iii_",
        "test_gate1_iv_",
        "test_gate1_v_",
        "test_gate1_dmn_",
        "test_gate2_",
        "test_gate3_",
        "test_gate4_",
        "test_gate5_",
    ):
        if not any(nome.startswith(gate) for nome in definidos if callable(definidos[nome])):
            raise AssertionError(f"guarda ausente: {gate}* — a suíte negativa foi esvaziada")
    for probe in (
        "_probe_c3_recusa_o_egresso_inteiro",
        "_probe_classe_proibida_e_a_primeira_recusa",
        "_probe_classe_desconhecida_nunca_passa",
        "_probe_celula_subk_suprimida_e_contada",
        "_probe_dmn_particao_nunca_honra_clinica",
        "_probe_payload_so_carrega_o_contrato",
        "_probe_kpi_violacao_e_o_que_passou",
        "_probe_audiencia_abaixo_do_gate_recusa_o_egresso_inteiro",
    ):
        if not callable(definidos.get(probe)):
            raise AssertionError(f"probe load-bearing removido: {probe}")
    fontes = Path(__file__).read_text(encoding="utf-8")
    for mutacao in (
        'K_ANON_FLOOR", 1',
        'PROHIBITED_FIELD_CLASSES", frozenset()',
        'EGRESS_AUDIENCE_GATE_MINIMUM", 1',
    ):
        if mutacao not in fontes:
            raise AssertionError(f"prova de sensibilidade removida: mutação {mutacao}")

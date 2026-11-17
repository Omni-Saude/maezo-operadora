"""EP-REG-19a (4): estorno 'concluido' so' com receipt de restituicao E ack Tasy, ambos de fonte."""

from maezo.platform.saida_a_pedido.estorno import ReceiptDeFonte, estado_do_estorno, receipts_do_fio_c6_4


def _restituicao(ref: str = "r-1") -> ReceiptDeFonte:
    return ReceiptDeFonte(ref=ref, tipo="restituicao", origem="fonte")


def _ack(ref: str = "t-1", origem: str = "fonte") -> ReceiptDeFonte:
    return ReceiptDeFonte(ref=ref, tipo="tasy_ack", origem=origem)  # type: ignore[arg-type]


def test_refund_completed_requires_two_receipts() -> None:
    so_restituicao = estado_do_estorno(estado_fonte="completed", receipts=[_restituicao()])
    assert so_restituicao == "ESTORNO_PENDENTE"
    ack_webhook = estado_do_estorno(
        estado_fonte="completed", receipts=[_restituicao(), _ack(origem="webhook_nao_autenticado")]
    )
    assert ack_webhook == "ESTORNO_PENDENTE"
    mesma_ref = estado_do_estorno(estado_fonte="completed", receipts=[_restituicao("x"), _ack("x")])
    assert mesma_ref == "ESTORNO_PENDENTE"
    ref_vazia = estado_do_estorno(estado_fonte="completed", receipts=[_restituicao(), _ack(" ")])
    assert ref_vazia == "ESTORNO_PENDENTE"
    sem_completed = estado_do_estorno(estado_fonte="pending", receipts=[_restituicao(), _ack()])
    assert sem_completed == "ESTORNO_PENDENTE"
    # positivo sintetico: dois receipts de fonte, distintos
    concluido = estado_do_estorno(estado_fonte="completed", receipts=[_restituicao(), _ack()])
    assert concluido == "ESTORNO_CONCLUIDO"


def test_wire_c6_4_never_yields_tasy_ack() -> None:
    projecao = {"state": "completed", "source_receipt_ref": "r1", "action_ref": "a", "revision": 3}
    receipts = receipts_do_fio_c6_4(projecao)
    assert [r.tipo for r in receipts] == ["restituicao"]
    assert all(r.tipo != "tasy_ack" for r in receipts)
    assert estado_do_estorno(estado_fonte="completed", receipts=receipts) == "ESTORNO_PENDENTE"
    assert receipts_do_fio_c6_4({"state": "completed", "source_receipt_ref": None}) == ()


def test_refund_not_requested_and_refused_or_uncertain_goes_to_human() -> None:
    assert estado_do_estorno(estado_fonte=None, receipts=[]) == "ESTORNO_NAO_SOLICITADO"
    for estado in ("refused", "uncertain"):
        resultado = estado_do_estorno(estado_fonte=estado, receipts=[_restituicao(), _ack()])  # type: ignore[arg-type]
        assert resultado == "ESTORNO_EM_ANALISE_HUMANA"

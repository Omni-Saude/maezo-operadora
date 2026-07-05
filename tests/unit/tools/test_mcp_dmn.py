"""Unit tests for maezo.tools.mcp_dmn (ADR-0012, ADR-0022)."""

import pytest

# ---------------------------------------------------------------------------
# DMN Server: evaluate_decision(dmn_key, inputs) -> outputs
# ---------------------------------------------------------------------------


def test_dmn_tools_registered() -> None:
    """DMN server should expose exactly 1 tool: evaluate_decision."""
    from maezo.tools.mcp_dmn import DmnServer

    server = DmnServer()

    tools = server.list_tools()

    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}: {tools}"
    assert tools[0]["name"] == "evaluate_decision"


def test_dmn_settings_defaults() -> None:
    """DmnSettings should default to spec/processes/dmn/ relative to project root."""
    from maezo.tools.mcp_dmn.server import DmnSettings

    settings = DmnSettings()

    assert settings.dmn_dir is not None
    assert "spec/processes/dmn" in settings.dmn_dir


def test_dmn_evaluate_auth_auto_approval() -> None:
    """evaluate_decision with auth_auto_approval should return AUTO_APROVAR for valid inputs.

    Uses the real DMN file from spec/processes/dmn/auth_auto_approval.dmn.
    Inputs: dut_atendida=true, dentro_teto_l2=true, rede_credenciada=true, carater_atendimento='eletivo'
    Expected: recomendacao='AUTO_APROVAR'
    """
    from maezo.tools.mcp_dmn.server import DmnServer, DmnSettings

    settings = DmnSettings()
    server = DmnServer(settings=settings)

    result = server.evaluate_decision(
        "auth_auto_approval",
        {
            "dut_atendida": True,
            "dentro_teto_l2": True,
            "rede_credenciada": True,
            "carater_atendimento": "eletivo",
        },
    )

    assert isinstance(result, dict), f"Expected dict, got {type(result)}: {result}"
    assert "recomendacao" in result
    assert result["recomendacao"] == "AUTO_APROVAR"


def test_dmn_evaluate_auth_auto_approval_dut_false() -> None:
    """evaluate_decision with dut_atendida=false should return ANALISE_HUMANA."""
    from maezo.tools.mcp_dmn.server import DmnServer, DmnSettings

    settings = DmnSettings()
    server = DmnServer(settings=settings)

    result = server.evaluate_decision(
        "auth_auto_approval",
        {
            "dut_atendida": False,
            "dentro_teto_l2": True,
            "rede_credenciada": True,
            "carater_atendimento": "eletivo",
        },
    )

    assert result["recomendacao"] == "ANALISE_HUMANA"
    assert "DUT/ROL nao atendida" in result["motivo"]


def test_dmn_evaluate_decision_unknown_key() -> None:
    """evaluate_decision with unknown dmn_key should raise FileNotFoundError."""
    from maezo.tools.mcp_dmn.server import DmnServer, DmnSettings

    settings = DmnSettings()
    server = DmnServer(settings=settings)

    with pytest.raises(FileNotFoundError):
        server.evaluate_decision("nonexistent_dmn", {})


def test_dmn_evaluate_decision_missing_input() -> None:
    """evaluate_decision with missing required input should raise ValueError."""
    from maezo.tools.mcp_dmn.server import DmnServer, DmnSettings

    settings = DmnSettings()
    server = DmnServer(settings=settings)

    with pytest.raises(ValueError, match="dut_atendida"):
        server.evaluate_decision("auth_auto_approval", {})

"""DEPRECATED MCP DMN server — DO NOT use for any runtime decision (T1.5, ADR-0028).

This in-process XML evaluator:
- used to fail OPEN on no-match (donor's `return {}`); AF-08 made `evaluate_decision`'s tail
  raise instead (fail-closed per ADR-0028 §3) so an accidental re-adoption cannot pass silently;
- implements only boolean + exact-equality matching (`_match_condition`) — FEEL comparison
  (`>=`,`<=`,`>`,`<`), ranges (`[a..b]`), lists (`"A","B"`) and `not(...)` are unsupported, so
  13 of the 55 deployed decisions cannot be evaluated correctly here at all, and 13 more
  list-disjunction tables silently return the wrong (catch-all) row (ADR-0028, live-verified);
- never had a production consumer (ADR-0028 Contexto, verified).

Runtime evaluation is ENGINE-SIDE ONLY via `maezo.tools.workers.dmn_transport` (ADR-0028 §1).
Importing this module (directly or via the package) triggers the package-level
`DeprecationWarning` in `maezo/tools/mcp_dmn/__init__.py` — Python executes the package
`__init__` before any submodule import, so there is no warning-free import path.

Kept (unchanged below this docstring) only for the deprecation cycle; scheduled for removal.
Its unit tests were reduced to the deprecation contract (`tests/unit/tools/test_mcp_dmn.py`).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import structlog
from pydantic_settings import BaseSettings

logger = structlog.get_logger(__name__)

# DMN 1.3 XML namespace
DMN_NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"


class DmnSettings(BaseSettings):
    """Configuration for the DMN decision engine.

    Environment variables prefixed with DMN_ (default).
    """

    model_config = {"env_prefix": "DMN_", "extra": "ignore"}

    dmn_dir: str = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "spec", "processes", "dmn")


class DmnServer:
    """MCP server for DMN decision evaluation — in-process (ADR-0022).

    Parses DMN 1.3 decision table XML files and evaluates rules
    using FIRST hit policy. Inputs are matched against rule conditions;
    outputs are returned as a dict mapping output names to values.

    Usage:
        server = DmnServer()
        result = server.evaluate_decision("auth_auto_approval", {
            "dut_atendida": True,
            "dentro_teto_l2": True,
        })
        # -> {"recomendacao": "AUTO_APROVAR", "motivo": "..."}
    """

    NS = {"dmn": DMN_NS}

    def __init__(self, settings: DmnSettings | None = None) -> None:
        """Initialize the DMN server.

        Args:
            settings: Optional DmnSettings; defaults to spec/processes/dmn/.
        """
        self._settings = settings or DmnSettings()
        self._dmn_dir = Path(self._settings.dmn_dir).resolve()
        logger.info(
            "dmn_server_initialized",
            dmn_dir=str(self._dmn_dir),
        )

    def list_tools(self) -> list[dict[str, str]]:
        """Return tool definitions for registration with ToolRegistry.

        Returns:
            List of tool dicts with 'name' and 'description' keys.
        """
        return [
            {
                "name": "evaluate_decision",
                "description": "Evaluate a DMN decision table by key with input variables.",
            },
        ]

    def register_tools(self, registry: Any) -> None:
        """Register DMN tools with the given ToolRegistry (ADR-0022, ADR-0016).

        Args:
            registry: A ToolRegistry instance that accepts register(name, handler).
        """
        registry.register("evaluate_decision", self.evaluate_decision)
        logger.info("dmn_tools_registered", count=1)

    def evaluate_decision(
        self,
        dmn_key: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate a DMN decision table by its key (filename without .dmn).

        Loads the DMN file from the configured dmn_dir, parses the
        decision table, matches rules using FIRST hit policy, and
        returns the output values.

        Args:
            dmn_key: The DMN file key (e.g., "auth_auto_approval").
            inputs: Mapping of input variable names to their values.

        Returns:
            Dict mapping output names to their computed values.

        Raises:
            FileNotFoundError: If the DMN file does not exist.
            ValueError: If a required input variable is missing.
        """
        dmn_file = self._dmn_dir / f"{dmn_key}.dmn"
        if not dmn_file.exists():
            raise FileNotFoundError(f"DMN file not found: {dmn_file}")

        logger.info("dmn_evaluate_decision", dmn_key=dmn_key)

        tree = ET.parse(str(dmn_file))
        root = tree.getroot()

        # Find the decision table
        decision_table = root.find(".//dmn:decisionTable", self.NS)
        if decision_table is None:
            raise ValueError(f"No decisionTable found in DMN file: {dmn_file}")

        # Parse inputs
        input_exprs: list[dict[str, Any]] = []
        for inp in decision_table.findall("dmn:input", self.NS):
            expr_el = inp.find("dmn:inputExpression", self.NS)
            if expr_el is not None:
                text_el = expr_el.find("dmn:text", self.NS)
                var_name = text_el.text.strip() if text_el is not None and text_el.text else ""
            else:
                var_name = ""
            input_exprs.append(
                {
                    "id": inp.get("id", ""),
                    "label": inp.get("label", ""),
                    "variable": var_name,
                }
            )

        # Parse outputs
        output_defs: list[dict[str, Any]] = []
        for out in decision_table.findall("dmn:output", self.NS):
            output_defs.append(
                {
                    "id": out.get("id", ""),
                    "label": out.get("label", ""),
                    "name": out.get("name", ""),
                }
            )

        # Validate required inputs
        for inp_def in input_exprs:
            var_name = inp_def["variable"]
            if var_name and var_name not in inputs:
                raise ValueError(f"Missing required input '{var_name}' for DMN '{dmn_key}'")

        # Parse rules (FIRST hit policy)
        rules = decision_table.findall("dmn:rule", self.NS)
        for rule in rules:
            input_entries = rule.findall("dmn:inputEntry", self.NS)
            output_entries = rule.findall("dmn:outputEntry", self.NS)

            # Check if all input entries match
            match = True
            for i, entry in enumerate(input_entries):
                text_el = entry.find("dmn:text", self.NS)
                condition = text_el.text.strip() if text_el is not None and text_el.text else ""

                if not condition or condition == "-":
                    continue  # wildcard, matches anything

                var_name = input_exprs[i]["variable"] if i < len(input_exprs) else ""
                actual = inputs.get(var_name)

                match = _match_condition(condition, actual)
                if not match:
                    break

            if match:
                # Build output dict
                outputs: dict[str, Any] = {}
                for j, entry in enumerate(output_entries):
                    text_el = entry.find("dmn:text", self.NS)
                    raw = text_el.text.strip() if text_el is not None and text_el.text else ""
                    value = _parse_output_value(raw)
                    out_name = output_defs[j]["name"] if j < len(output_defs) else f"output_{j}"
                    outputs[out_name] = value

                logger.info(
                    "dmn_rule_matched",
                    dmn_key=dmn_key,
                    rule_id=rule.get("id", ""),
                    outputs=outputs,
                )
                return outputs

        # No rule matched (should not happen with proper catch-all rule). AF-08: this donor
        # evaluator has no production caller (ADR-0028 Contexto, enforced by
        # test_no_production_module_imports_mcp_dmn) and MUST NOT fail open if it is ever
        # re-adopted by mistake — DMN evaluation is fail-closed by platform invariant (ADR-0028
        # §3). The donor's original `return {}` here was the exact fail-open shape this module's
        # own top docstring warns about; raising instead is the one behavioural change this
        # deprecation-only file carries.
        logger.warning("dmn_no_rule_matched", dmn_key=dmn_key)
        raise RuntimeError(
            f"deprecated in-process DMN evaluator found no matching rule for '{dmn_key}' — "
            "refuses to fail open (ADR-0028 §3). This evaluator has no production caller and "
            "must not gain one; use maezo.tools.workers.dmn_transport.DmnTransport instead."
        )


def _match_condition(condition: str, actual: Any) -> bool:
    """Match a DMN condition text against an actual value.

    Args:
        condition: The text content of the inputEntry/dmn:text element.
        actual: The actual input value provided.

    Returns:
        True if the condition matches the actual value.
    """
    condition = condition.strip()

    # Boolean matching
    if condition.lower() == "true":
        return bool(actual)
    if condition.lower() == "false":
        return not bool(actual)

    # String equality
    if isinstance(actual, str):
        # Strip quotes for comparison
        clean = condition.strip('"').strip("'")
        return actual == clean

    # Numeric comparison placeholders (future: >=, <=, >, <)
    try:
        cond_num = float(condition)
        return float(actual) == cond_num
    except (ValueError, TypeError):
        pass

    # Default: string equality
    return str(actual).strip() == condition.strip('"').strip("'")


def _parse_output_value(raw: str) -> Any:
    """Parse a DMN output value from raw text.

    Args:
        raw: The raw text content of the outputEntry/dmn:text element.

    Returns:
        Parsed value (str, bool, int, float).
    """
    raw = raw.strip()
    if not raw:
        return ""

    # Quoted string
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]

    # Boolean
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False

    # Integer
    try:
        return int(raw)
    except ValueError:
        pass

    # Float
    try:
        return float(raw)
    except ValueError:
        pass

    return raw

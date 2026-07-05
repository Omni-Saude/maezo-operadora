"""Unit tests for maezo.runtime package initialization."""


def test_runtime_package_is_importable() -> None:
    """Runtime package should be importable and expose a docstring."""
    import maezo.runtime  # noqa: F401

    assert maezo.runtime.__doc__ is not None


def test_runtime_package_exports_inference_provider() -> None:
    """Runtime __init__ should export InferenceProvider."""
    from maezo.runtime import InferenceProvider  # noqa: F401


def test_runtime_package_exports_checkpointer() -> None:
    """Runtime __init__ should export Checkpointer."""
    from maezo.runtime import Checkpointer  # noqa: F401


def test_runtime_package_exports_harness() -> None:
    """Runtime __init__ should export Harness."""
    from maezo.runtime import Harness  # noqa: F401


def test_runtime_package_exports_metrics_collector() -> None:
    """Runtime __init__ should export MetricsCollector."""
    from maezo.runtime import MetricsCollector  # noqa: F401

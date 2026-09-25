import os

import pytest

import model_guard
from helpers import TEST_API_KEY

os.environ["ENGINE_API_KEY"] = TEST_API_KEY

_model_tests_run: list[str] = []


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "models(det=True, emb=True): test needs the pinned ONNX model(s); "
        "skipped when absent, or failed when FACETECH_REQUIRE_MODELS=1",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    marker = item.get_closest_marker("models")
    if marker is None:
        return
    absent = model_guard.resolve(marker)
    if not absent:
        _model_tests_run.append(item.nodeid)
        return
    message = model_guard.unavailable_message(absent)
    if model_guard.STRICT:
        pytest.fail(f"FACETECH_REQUIRE_MODELS=1 but {message}", pytrace=False)
    pytest.skip(message)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not model_guard.STRICT:
        return

    if session.config.option.keyword or session.config.option.markexpr:
        return
    ran = len(_model_tests_run)
    if ran < model_guard.MIN_MODEL_TESTS:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"ERROR: only {ran} model-dependent test(s) ran; expected at "
                f"least {model_guard.MIN_MODEL_TESTS}. The face path did not "
                f"really get tested - see engine/tests/model_guard.py.",
                red=True,
                bold=True,
            )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

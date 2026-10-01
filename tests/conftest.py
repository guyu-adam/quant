"""验收模式（Q6_REQUIRE_SNAPSHOT=1）下任何 skip 都判失败：静默跳过等于没测。"""

import os

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get("Q6_REQUIRE_SNAPSHOT") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter else []
    if skipped:
        for report in skipped:
            print(f"SKIP NOT ALLOWED: {report.nodeid}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

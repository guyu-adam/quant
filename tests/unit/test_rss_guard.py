import re
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).parents[2] / "scripts" / "rss_guard.py"


def run_guard(limit: int, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GUARD), "--limit-mb", str(limit), "--", sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=False,
    )


def reported_peak(output: str) -> float:
    match = re.search(r"PEAK_RSS ([\d.]+) MiB", output)
    assert match, output
    return float(match.group(1))


def test_allows_child_below_limit_and_reports_peak() -> None:
    code = (
        "import time; data = bytearray(50 * 1024 * 1024); "
        "data[::4096] = b'x' * len(data[::4096]); time.sleep(0.2)"
    )
    result = run_guard(512, code)
    assert result.returncode == 0, result.stderr
    assert reported_peak(result.stdout) >= 50


def test_rejects_child_over_limit() -> None:
    code = (
        "import time; data = bytearray(300 * 1024 * 1024); "
        "data[::4096] = b'x' * len(data[::4096]); time.sleep(0.2)"
    )
    result = run_guard(200, code)
    assert result.returncode != 0
    assert "RSS LIMIT EXCEEDED" in result.stderr
    assert reported_peak(result.stdout) > 200


def test_propagates_child_exit_code() -> None:
    result = run_guard(512, "raise SystemExit(3)")
    assert result.returncode == 3
    assert "PEAK_RSS " in result.stdout

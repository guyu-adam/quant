from pathlib import Path

from q6.lint.lookahead_ast import scan_paths, scan_source

SAMPLES = Path(__file__).with_name("samples.py")


def test_acceptance_samples_by_function():
    findings = scan_source(SAMPLES.read_text())
    by_func = {}
    for finding in findings:
        by_func.setdefault(finding.func, set()).add(finding.rule)
    assert "LA001" in by_func["leaky_shift_negative"]
    assert "LA002" in by_func["leaky_center_rolling"]
    assert "LA004" in by_func["leaky_fullsample_zscore"]
    assert "LA003" in by_func["leaky_bfill"]
    # 同根成交的语义依赖策略执行时序，静态 AST 扫描查不出；由截断测试负责。
    assert by_func.get("leaky_same_bar_exec", set()) == set()
    clean_names = (
        "clean_momentum",
        "clean_rolling_zscore",
        "clean_expanding_zscore",
        "clean_ffill_cs_rank",
        "clean_next_bar_exec",
    )
    for name in clean_names:
        assert by_func.get(name, set()) == set()


def test_fit_and_private_access():
    assert {f.rule for f in scan_source("model.fit(x, y)\nmodel.fit_transform(x)\nobj._panel\n")} == {
        "LA005",
        "LA006",
    }
    assert scan_source("model.fit(x, y)\n", "src/q6/ml/train.py") == []
    assert scan_source("obj._fields\n", "src/q6/core/example.py") == []


def test_axis_columns_and_comment_exemptions():
    findings = scan_source(
        'x.mean(axis=1)\ny.mean(axis="columns")\n'
        "z.mean() # lookahead: ok cross-sectional\nw.mean() # lookahead: ok\n"
    )
    assert [(f.rule, f.line) for f in findings] == [("LA000", 4)]


def test_shift_negative_literal_forms():
    findings = scan_source("x.shift(-1)\nx.shift(periods=-(1))\nx.shift(1)\n")
    assert [f.rule for f in findings] == ["LA001", "LA001"]


def test_scan_paths_scans_explicit_samples_path(tmp_path):
    sample = tmp_path / "tests/lookahead/samples.py"
    sample.parent.mkdir(parents=True)
    sample.write_text("x.shift(-1)\n")
    assert [f.rule for f in scan_paths([sample])] == ["LA001"]

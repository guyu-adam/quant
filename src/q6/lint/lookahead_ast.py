"""AST checks for common lookahead patterns in Python/pandas code."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    col: int
    rule: str
    message: str
    func: str | None


_STATS = {"mean", "std", "var", "min", "max", "median", "quantile", "rank"}
_SAFE_CHAIN = {"rolling", "expanding", "ewm", "groupby", "resample"}
_PRIVATE = {"_panel", "_fields", "_dates"}


def _chain_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    while isinstance(node, (ast.Call, ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            node = node.value
    return names


def scan_source(src: str, filename: str = "<string>") -> list[Finding]:
    tree = ast.parse(src, filename=filename)
    lines = src.splitlines()
    findings: list[Finding] = []
    func_stack: list[str] = []
    exempt_lines: dict[int, str] = {}
    for n, line in enumerate(lines, 1):
        if "# lookahead:" in line:
            tail = line.split("# lookahead:", 1)[1].strip()
            if tail.startswith("ok"):
                exempt_lines[n] = tail[2:].strip()

    class Visitor(ast.NodeVisitor):
        def add(self, node: ast.AST, rule: str, msg: str) -> None:
            line = getattr(node, "lineno", 1)
            col = getattr(node, "col_offset", 0) + 1
            if line in exempt_lines:
                if exempt_lines[line]:
                    return
                findings.append(
                    Finding(
                        filename, line, col, "LA000", "豁免必须写理由", func_stack[-1] if func_stack else None
                    )
                )
                return
            findings.append(Finding(filename, line, col, rule, msg, func_stack[-1] if func_stack else None))

        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            func_stack.append(node.name)
            self.generic_visit(node)
            func_stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name == "shift":
                negative = any(
                    isinstance(a, ast.UnaryOp)
                    and isinstance(a.op, ast.USub)
                    and isinstance(a.operand, ast.Constant)
                    and isinstance(a.operand.value, (int, float))
                    for a in node.args
                )
                negative |= any(
                    k.arg == "periods"
                    and isinstance(k.value, ast.UnaryOp)
                    and isinstance(k.value.op, ast.USub)
                    and isinstance(k.value.operand, ast.Constant)
                    and isinstance(k.value.operand.value, (int, float))
                    for k in node.keywords
                )
                if negative:
                    self.add(node, "LA001", "负向 shift 使用未来数据")
            if any(
                k.arg == "center" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords
            ):
                self.add(node, "LA002", "center=True 可能包含未来数据")
            if (
                name in {"bfill", "backfill"}
                or (
                    name == "fillna"
                    and any(
                        k.arg == "method"
                        and isinstance(k.value, ast.Constant)
                        and k.value.value in {"bfill", "backfill"}
                        for k in node.keywords
                    )
                )
                or (
                    name == "interpolate"
                    and any(
                        k.arg == "limit_direction"
                        and isinstance(k.value, ast.Constant)
                        and k.value.value in {"backward", "both"}
                        for k in node.keywords
                    )
                )
            ):
                self.add(node, "LA003", "向后填充或插值使用未来数据")
            if (
                name in _STATS
                and not (_chain_names(node.func.value) & _SAFE_CHAIN)
                and not any(
                    k.arg == "axis"
                    and (isinstance(k.value, ast.Constant) and k.value.value in {1, "columns"})
                    for k in node.keywords
                )
            ):
                self.add(node, "LA004", "全样本统计可能使用未来数据")
            if name in {"fit", "fit_transform"} and not any(
                part in filename.replace("\\", "/").split("/") for part in ("ml", "cv")
            ):
                self.add(node, "LA005", "全样本拟合调用")
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in _PRIVATE and "/core/" not in filename.replace("\\", "/"):
                self.add(node, "LA006", f"访问私有属性 .{node.attr}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return sorted(findings, key=lambda f: (f.line, f.col, f.rule))


def scan_paths(paths: list[str | Path]) -> list[Finding]:
    out: list[Finding] = []
    for path in paths:
        p = Path(path)
        files = [p] if p.is_file() else sorted(p.rglob("*.py"))
        for file in files:
            rel = file.as_posix()
            if rel.endswith("src/q6/ml/labels.py") or "/src/q6/core/" in f"/{rel}":
                continue
            try:
                out.extend(scan_source(file.read_text(encoding="utf-8"), str(file)))
            except (SyntaxError, UnicodeDecodeError) as exc:
                out.append(
                    Finding(
                        str(file),
                        getattr(exc, "lineno", 1) or 1,
                        1,
                        "LA000",
                        f"无法解析 Python 源码: {exc}",
                        None,
                    )
                )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["src"])
    args = parser.parse_args(argv)
    findings = scan_paths(args.paths)
    if findings:
        for f in findings:
            print(f"{f.file}:{f.line}:{f.col} {f.rule} {f.message}")
        return 1
    print("lookahead-ast: 0 findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

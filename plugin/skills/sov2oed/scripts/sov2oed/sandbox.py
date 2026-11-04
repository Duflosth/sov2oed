"""Run the script that groups a PDF's lines into rows, without trusting it.

The script is written by a model, possibly after reading text an attacker put in the PDF. It only computes: it
defines `rows(lines)`, gets the page lines as data and returns the rows; it opens no file, imports only pure
modules and calls nothing that reaches the system. Three layers hold it to that:

- before it runs, its syntax tree is checked: imports from an allowlist, no name or attribute that reaches the
  interpreter's internals (`open`, `eval`, `__class__`...);
- it runs in a separate interpreter (`-I -S`: no user site, no environment), with builtins stripped of `open`
  and `__import__` replaced by the allowlist, under a time limit and, where the system allows, CPU and memory caps;
- its output is data: a list of rows that the guards then check against the PDF, value by value.

This keeps a mistaken or injected script from touching files or the network; it is not an operating-system
sandbox. A hosted service runs it in an isolated container as well.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys

ALLOWED_IMPORTS = {"bisect", "collections", "csv", "decimal", "functools", "itertools", "json", "math", "operator",
                   "re", "statistics", "string", "unicodedata"}  # fmt: skip
FORBIDDEN_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr",
                   "setattr", "delattr", "breakpoint", "input", "help", "exit", "quit", "memoryview"}  # fmt: skip
TIMEOUT = 60  # seconds
MAX_OUTPUT = 50_000_000  # bytes of rows returned

_RUNNER = r"""
import builtins, json, sys
data = json.loads(sys.stdin.read())
allowed = set(data["allowed"])
real_import = builtins.__import__
def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in allowed:
        raise ImportError(f"import of {name} is not allowed")
    return real_import(name, globals, locals, fromlist, level)
safe = {k: v for k, v in vars(builtins).items() if k not in data["forbidden"]}
safe["__import__"] = guarded_import
namespace = {"__builtins__": safe, "__name__": "rows_script"}
exec(compile(data["script"], "rows_script", "exec"), namespace)
out = namespace["rows"](data["lines"])
sys.stdout.write(json.dumps([[("" if v is None else str(v)) for v in row] for row in out]))
"""


def check(script: str) -> list[str]:
    """What the script does that a rows script may not; empty when it may run."""
    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        return [f"syntax error line {e.lineno}: {e.msg}"]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            out += [f"line {node.lineno}: import of {n} (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})"
                    for n in names if n.split(".")[0] not in ALLOWED_IMPORTS]  # fmt: skip
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            out.append(f"line {node.lineno}: {node.id} is not allowed (the script gets the lines as data)")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            out.append(f"line {node.lineno}: attribute {node.attr} is not allowed")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "rows" for n in tree.body):
        out.append("the script must define rows(lines)")
    return out


def _limits() -> None:  # pragma: no cover - runs in the child process
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
        resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    except (ImportError, ValueError, OSError):
        pass  # not on this system (Windows; macOS refuses RLIMIT_AS): the time limit still holds


def run(script: str, lines: list[dict]) -> list[list[str]]:
    """The rows the script returns for these lines: a header row, then one row per location or total."""
    if problems := check(script):
        raise ValueError("rows script refused: " + "; ".join(problems[:8]))
    payload = json.dumps({"script": script, "lines": lines, "allowed": sorted(ALLOWED_IMPORTS),
                          "forbidden": sorted(FORBIDDEN_NAMES)})  # fmt: skip
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-S", "-c", _RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env={},
            preexec_fn=_limits if sys.platform != "win32" else None,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError(f"rows script ran more than {TIMEOUT} s") from e
    if done.returncode != 0:
        last = done.stderr.strip().splitlines()[-1][:500] if done.stderr.strip() else f"exit code {done.returncode}"
        raise ValueError("rows script failed: " + last)
    if len(done.stdout) > MAX_OUTPUT:
        raise ValueError(f"rows script returned more than {MAX_OUTPUT:,} bytes")
    rows = json.loads(done.stdout)
    if not rows or not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
        raise ValueError("rows(lines) must return a list of rows: the header, then one list per row")
    return rows

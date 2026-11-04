"""Check that this Python can run sov2oed, without installing anything.

Prints one JSON object: the Python version, each required package with its version or "missing", and
whether the vendored package was found. Exit 0 when sov2oed can run, 1 otherwise. Runs on any Python 3.

    python3 check_env.py
"""

import importlib
import json
import sys
from pathlib import Path

REQUIRED = {
    "openpyxl": "openpyxl",
    "pdfplumber": "pdfplumber",
    "pydantic": "pydantic",
    "yaml": "pyyaml",
    "jinja2": "jinja2",
}  # import name -> pip name


def _probe(name):
    try:
        module = importlib.import_module(name)
    except Exception as e:  # noqa: BLE001 - a broken install is as good as a missing one
        return f"missing ({type(e).__name__})"
    return str(getattr(module, "__version__", "installed"))


def main():
    here = Path(__file__).resolve().parent
    packages = {pip: _probe(name) for name, pip in REQUIRED.items()}
    missing = [pip for pip, v in packages.items() if v.startswith("missing")]
    python_ok = sys.version_info >= (3, 11)
    report = {
        "python": sys.version.split()[0],
        "python_ok": python_ok,
        "packages": packages,
        "missing": missing,
        "vendored_package": (here / "sov2oed" / "__init__.py").is_file()
        or (here.parent.parent.parent / "sov2oed" / "__init__.py").is_file(),
        "ok": python_ok and not missing,
    }
    if missing:
        report["install"] = f"{sys.executable} -m pip install {' '.join(missing)}"
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

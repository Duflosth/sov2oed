"""Run the sov2oed command line from the skill: the vendored package when the skill ZIP carries one
(scripts/sov2oed), else the repository's (Claude Code plugin), else an installed sov2oed.

    python3 sov2oed_cli.py inspect FILE
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for base in (HERE, HERE.parent.parent.parent):  # skills/sov2oed/scripts -> the repository root
    if (base / "sov2oed" / "__init__.py").is_file():
        sys.path.insert(0, str(base))
        break

if sys.version_info < (3, 11):  # noqa: UP036 - said in one line on an older python, before any newer construct is imported
    sys.exit(
        f"sov2oed: error: Python 3.11 or later is needed (this is {sys.version.split()[0]}): "
        "run python3 check_env.py for the install command"
    )

from sov2oed.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

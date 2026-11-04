"""Build the skill ZIP that a person uploads in Claude (claude.ai, desktop), dist/sov2oed-skill.zip, and the same
skill in plugin/skills/, the self-contained plugin that Claude's directory and the Claude Code marketplace install.

The ZIP holds the skill folder at its top level, with the sov2oed package vendored under scripts/ so the
skill runs where sov2oed is not installed (the code-execution sandbox may have no network):

    sov2oed/SKILL.md, LICENSE.txt, NOTICE.txt
    sov2oed/scripts/check_env.py, sov2oed_cli.py
    sov2oed/scripts/sov2oed/...       (the package: .py files and its data only)

Only an allowlist is packed: regular files, never a symlink or a dotfile, with an expected suffix. The ZIP is
deterministic: sorted entries, fixed timestamps and permissions, so the same sources give the same bytes.

plugin/ holds only the plugin: its manifest and icon (written by hand) and the skill (written here, never edited
by hand). `--check` fails when plugin/skills/ is not what the sources give.

Usage: python tools/build_skill.py [--out other.zip] [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "sov2oed"
PACKAGE = ROOT / "sov2oed"
LICENSE = {"LICENSE.txt": ROOT / "LICENSE", "NOTICE.txt": ROOT / "NOTICE"}
SKILL_SUFFIXES = (".md", ".py", ".yaml")
PACKAGE_SUFFIXES = (".py", ".json", ".csv", ".j2", ".md")
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".DS_Store"}
PLUGIN_SKILLS = ROOT / "plugin" / "skills"
STAMP = (1980, 1, 1, 0, 0, 0)  # the earliest date a ZIP can store


def _files(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Regular files under base, reached without a symlink, not hidden, with an allowed suffix."""
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if any(part.startswith(".") for part in rel.parts) or SKIP_PARTS.intersection(rel.parts):
            continue
        if any((base / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
            continue
        if p.is_file() and p.suffix in suffixes:
            out.append(p)
    return out


def entries() -> dict[str, Path]:
    """Archive name -> source file, for the whole skill."""
    out: dict[str, Path] = {}
    for p in _files(SKILL, SKILL_SUFFIXES):
        out[f"sov2oed/{p.relative_to(SKILL).as_posix()}"] = p
    for p in _files(PACKAGE, PACKAGE_SUFFIXES):
        out[f"sov2oed/scripts/sov2oed/{p.relative_to(PACKAGE).as_posix()}"] = p
    for name, p in LICENSE.items():
        out[f"sov2oed/{name}"] = p
    return dict(sorted(out.items()))


def build(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        for name, src in entries().items():
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16  # a regular file, rw-r--r--
            info.create_system = 3  # Unix, so the permissions above are read
            z.writestr(info, src.read_bytes(), compresslevel=9)
    return out


def plugin_differences() -> list[str]:
    """Files of plugin/skills/ that are missing, extra or not the bytes the sources give."""
    want = {name: src.read_bytes() for name, src in entries().items()}
    have = {p.relative_to(PLUGIN_SKILLS).as_posix(): p.read_bytes()
            for p in PLUGIN_SKILLS.rglob("*") if p.is_file() and "__pycache__" not in p.parts}  # fmt: skip
    return sorted(n for n in want.keys() | have.keys() if want.get(n) != have.get(n))


def write_plugin() -> None:
    shutil.rmtree(PLUGIN_SKILLS, ignore_errors=True)
    for name, src in entries().items():
        target = PLUGIN_SKILLS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write only the ZIP, here (default: dist/sov2oed-skill.zip and plugin/)")
    ap.add_argument("--check", action="store_true", help="fail if plugin/skills/ is not up to date")
    args = ap.parse_args(argv)
    if args.check:
        if diff := plugin_differences():
            print(f"plugin/skills/ is out of date ({len(diff)} files, e.g. {diff[0]}): run python tools/build_skill.py")
            return 1
        print("plugin/skills/ is up to date")
        return 0
    if args.out is None:
        write_plugin()
    path = build(args.out or ROOT / "dist" / "sov2oed-skill.zip")
    data = path.read_bytes()
    with zipfile.ZipFile(path) as z:
        n = len(z.namelist())
    print(f"{path}: {len(data):,} bytes, {n} files, sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

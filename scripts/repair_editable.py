"""Make `narrate` importable from the venv.

**The problem.** This project lives under `~/Desktop`, which macOS syncs to
iCloud Drive. iCloud sets the `UF_HIDDEN` flag on files in the virtualenv —
including the `.pth` path files that put `src/` on `sys.path`. Python 3.12's
`site.addpackage` deliberately skips hidden `.pth` files, so none of them are
processed and every `import narrate` fails.

The failure is unusually misleading: `uv sync` reports success, the file is
present, `cat` shows the right contents, and `open()` reads it fine. Only
`site` refuses it, silently. iCloud also leaves `dataless` conflict copies
(`_narrate_dev_path 2.pth`) behind, which add noise but do nothing.

**What this does.** Clears the hidden flag and removes the conflict copies.
Idempotent, and a no-op on any platform without the problem.

**The permanent fix** is to keep the virtualenv out of the synced folder:

    export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/narrate"
    uv sync

or move the project somewhere iCloud does not sync. Until then, iCloud may
re-flag the files at any time; re-run this (`just repair`) if imports break.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import sysconfig
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
PTH_NAME = "_narrate_dev_path.pth"

# iCloud names its duplicates "foo 2.pth", "foo 3.pth", ...
CONFLICT_COPY = re.compile(r" \d+\.pth$")


def clear_hidden_flag(path: Path) -> bool:
    """Unset UF_HIDDEN so `site` will process the file. True if changed."""
    hidden = getattr(stat, "UF_HIDDEN", 0)
    if not hidden:
        return False
    try:
        flags = os.lstat(path).st_flags  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    if not flags & hidden:
        return False
    try:
        os.chflags(path, flags & ~hidden)  # type: ignore[attr-defined]
    except OSError:
        return False
    return True


def repair(site_packages: Path, src: Path = SRC) -> tuple[int, int]:
    """Returns `(unhidden, removed)` counts."""
    removed = 0
    for pth in list(site_packages.glob("*.pth")):
        if CONFLICT_COPY.search(pth.name):
            clear_hidden_flag(pth)
            pth.unlink(missing_ok=True)
            removed += 1

    # Our own path file, separate from the one uv manages so a re-sync cannot
    # replace it with a fresh hidden copy.
    target = site_packages / PTH_NAME
    wanted = f"{src}\n"
    if not target.exists() or target.read_text(encoding="utf-8") != wanted:
        target.unlink(missing_ok=True)
        target.write_text(wanted, encoding="utf-8")

    unhidden = sum(clear_hidden_flag(p) for p in site_packages.glob("*.pth"))
    return unhidden, removed


def main() -> int:
    if not SRC.is_dir():
        print(f"source tree not found at {SRC}", file=sys.stderr)
        return 1

    site_packages = Path(sysconfig.get_paths()["purelib"])
    unhidden, removed = repair(site_packages)

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        import narrate
    except ImportError as exc:
        print(f"narrate is still not importable: {exc}", file=sys.stderr)
        return 1

    detail = []
    if unhidden:
        detail.append(f"un-hid {unhidden} .pth file(s)")
    if removed:
        detail.append(f"removed {removed} iCloud conflict copy/copies")
    suffix = f" — {', '.join(detail)}" if detail else ""
    print(f"workspace OK — narrate {narrate.__version__} importable{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry point for `python -m narrate`.

Useful as a fallback when the venv's path files are broken — see
`scripts/repair_editable.py` — because `PYTHONPATH=src python -m narrate`
bypasses `.pth` processing entirely.
"""

from narrate.cli import app

app()

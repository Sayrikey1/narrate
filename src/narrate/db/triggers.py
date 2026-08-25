"""Schema-level guards.

Kept in their own module so both `init_db` and the Alembic baseline apply the
identical statements — a rule that lives in two hand-copied places is a rule
that eventually differs between them.
"""

from __future__ import annotations

# `RAISE(ABORT, ...)` rolls the statement back and surfaces the message, so an
# accidental mutation fails loudly at the point of the mistake rather than
# silently corrupting a cost history that reconciliation depends on.
APPEND_ONLY_TRIGGERS: tuple[str, ...] = (
    """
    CREATE TRIGGER IF NOT EXISTS ledger_entry_no_update
    BEFORE UPDATE ON ledger_entry
    BEGIN
        SELECT RAISE(ABORT,
            'ledger_entry is append-only — record a compensating entry instead of editing');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS ledger_entry_no_delete
    BEFORE DELETE ON ledger_entry
    BEGIN
        SELECT RAISE(ABORT,
            'ledger_entry is append-only — record a compensating entry instead of deleting');
    END;
    """,
)

# =============================================================================
# PH Agent Hub — Subagent Tool-Enum Migration Guard Tests (Issue #574)
# =============================================================================
# The enum MODIFY in migration c9d0e1f2a3b4 replaces the column definition, so
# a drifted database would lose values.  These tests pin the safety rule that
# makes the migration refuse to run instead.
# =============================================================================

import importlib.util
import pathlib

import pytest

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "db"
    / "migrations"
    / "versions"
    / "c9d0e1f2a3b4_add_subagent_to_tool_type_enum.py"
)

pytestmark = [pytest.mark.unit]


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "subagent_enum_migration", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration()


def test_migration_file_exists():
    assert _MIGRATION_PATH.is_file()


def test_revision_chain(migration):
    assert migration.revision == "c9d0e1f2a3b4"
    assert migration.down_revision == "b7c8d9e0f1a2"


def test_new_values_is_prior_plus_subagent(migration):
    assert migration.NEW_VALUES == migration.PRIOR_VALUES + ("subagent",)
    assert "subagent" not in migration.PRIOR_VALUES
    assert len(migration.PRIOR_VALUES) == 31


def test_parse_enum_values_round_trip(migration):
    column_type = f"enum({migration.enum_sql(migration.NEW_VALUES)})"
    assert migration.parse_enum_values(column_type) == list(migration.NEW_VALUES)


def test_parse_enum_values_ignores_case_and_suffix(migration):
    parsed = migration.parse_enum_values("enum('a','b') NOT NULL")
    assert parsed == ["a", "b"]


def test_parse_enum_values_handles_empty(migration):
    assert migration.parse_enum_values("") == []
    assert migration.parse_enum_values(None) == []


def test_enum_sql_quotes_values(migration):
    assert migration.enum_sql(["a", "b"]) == "'a','b'"


def test_missing_values_is_order_preserving(migration):
    assert migration.missing_values(["a"], ["a", "b", "c"]) == ["b", "c"]
    assert migration.missing_values(["a", "b", "c"], ["a", "b"]) == []


def test_guard_accepts_exact_prior_definition(migration):
    definition = f"enum({migration.enum_sql(migration.PRIOR_VALUES)})"
    migration.assert_enum_is_superset(definition)  # must not raise


def test_guard_accepts_already_migrated_definition(migration):
    """A database that already has the new value must not be blocked."""
    definition = f"enum({migration.enum_sql(migration.NEW_VALUES)})"
    migration.assert_enum_is_superset(definition)  # must not raise


def test_guard_accepts_extra_unknown_values(migration):
    """Extra values are tolerated — only *dropping* a prior value is fatal."""
    definition = f"enum({migration.enum_sql(migration.PRIOR_VALUES + ('future_type',))})"
    migration.assert_enum_is_superset(definition)  # must not raise


def test_guard_rejects_drifted_definition(migration):
    drifted = migration.PRIOR_VALUES[:-1]  # drop 'a2a'
    definition = f"enum({migration.enum_sql(drifted)})"
    with pytest.raises(RuntimeError) as excinfo:
        migration.assert_enum_is_superset(definition)
    message = str(excinfo.value)
    assert "REFUSES" in message
    assert "a2a" in message


def test_guard_rejects_empty_definition(migration):
    with pytest.raises(RuntimeError):
        migration.assert_enum_is_superset("")


def test_migration_has_real_downgrade(migration):
    """The downgrade must be a real body, not a bare pass."""
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    marker = "def downgrade() -> None:"
    assert marker in source
    body = source.split(marker, 1)[1]
    assert "MODIFY COLUMN" in body
    assert "pass" not in body


def test_downgrade_enum_excludes_subagent(migration):
    """Downgrade restores exactly the prior enum list, without 'subagent'."""
    rendered = migration.enum_sql(migration.PRIOR_VALUES)
    assert "subagent" not in rendered
    assert rendered != migration.enum_sql(migration.NEW_VALUES)
    assert migration.enum_sql(migration.NEW_VALUES) == f"{rendered},'subagent'"

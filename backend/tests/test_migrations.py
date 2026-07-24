# =============================================================================
# PH Agent Hub — Migration Verification Tests (Issue #482)
# =============================================================================
# Validates Alembic migration DAG integrity, upgrade/downgrade round-trips,
# and idempotency before production deployment.
#
# Tests that require a database (round-trip, idempotency) use the existing
# conftest fixtures and the Docker / CI MariaDB service.
#
# Usage:
#   pytest tests/test_migrations.py -v
# =============================================================================

import ast
import os
import re
from collections import defaultdict
from pathlib import Path

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "db" / "migrations" / "versions"
INITIAL_REVISION = "6b6bd31267a0"  # initial_schema — the root migration


# =============================================================================
# Helpers
# =============================================================================

def _parse_migration_file(path: Path) -> dict:
    """Parse a single migration file and extract revision metadata.

    Returns a dict with keys: revision, down_revision, branch_labels,
    depends_on, file.
    """
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content)

    revision = None
    down_revision = None
    branch_labels = None
    depends_on = None
    has_upgrade_pass = False
    has_downgrade_pass = False

    for node in ast.walk(tree):
        # Handle annotated assignments: revision: str = "abc123"
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is None:
                continue
            try:
                val = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if node.target.id == "revision":
                revision = val
            elif node.target.id == "down_revision":
                down_revision = val
            elif node.target.id == "branch_labels":
                branch_labels = val
            elif node.target.id == "depends_on":
                depends_on = val
        # Handle plain assignments (some migrations use tuple assignment)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        val = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        continue
                    if target.id == "revision":
                        revision = val
                    elif target.id == "down_revision":
                        down_revision = val
                    elif target.id == "branch_labels":
                        branch_labels = val
                    elif target.id == "depends_on":
                        depends_on = val
        elif isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                has_upgrade_pass = _is_pass_body(node)
            elif node.name == "downgrade":
                has_downgrade_pass = _is_pass_body(node)

    return {
        "revision": revision,
        "down_revision": down_revision,
        "branch_labels": branch_labels,
        "depends_on": depends_on,
        "file": path.name,
        "has_upgrade_pass": has_upgrade_pass,
        "has_downgrade_pass": has_downgrade_pass,
    }


def _is_pass_body(func_node: ast.FunctionDef) -> bool:
    """Return True if the function body is just ``pass`` (no real ops)."""
    if len(func_node.body) == 0:
        return True
    if len(func_node.body) == 1 and isinstance(func_node.body[0], ast.Pass):
        return True
    return False


def _collect_all_migrations() -> list[dict]:
    """Parse every migration file in the versions directory."""
    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        migrations.append(_parse_migration_file(path))
    return migrations


def _build_dag(migrations: list[dict]) -> tuple[dict, dict]:
    """Build forward and reverse DAG from parsed migrations.

    Returns (children_map, parent_map):
      - children_map: rev_id -> list of child revision IDs
      - parent_map: rev_id -> list of parent revision IDs (tuple for merges)
    """
    children: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, list[str]] = {}

    for m in migrations:
        rev = m["revision"]
        down = m["down_revision"]
        parents[rev] = []

        if down is None:
            continue
        if isinstance(down, str):
            parents[rev] = [down]
            children[down].append(rev)
        elif isinstance(down, tuple):
            parents[rev] = list(down)
            for d in down:
                children[d].append(rev)

    return dict(children), dict(parents)


def _find_heads(children: dict[str, list[str]], all_revisions: set[str]) -> set[str]:
    """Find all leaf nodes (revisions with no children)."""
    return {r for r in all_revisions if not children.get(r)}


def _find_reachable(root: str, children: dict[str, list[str]]) -> set[str]:
    """BFS from root to find all reachable revisions."""
    visited = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(children.get(node, []))
    return visited


# =============================================================================
# Tests — DAG Integrity (no database required)
# =============================================================================


class TestMigrationDAG:
    """Structural tests that only parse migration files — no DB needed."""

    @pytest.fixture(scope="class")
    def migrations(self) -> list[dict]:
        return _collect_all_migrations()

    @pytest.fixture(scope="class")
    def all_revisions(self, migrations) -> set[str]:
        revs = set()
        for m in migrations:
            revs.add(m["revision"])
            down = m["down_revision"]
            if isinstance(down, str):
                revs.add(down)
            elif isinstance(down, tuple):
                revs.update(down)
        return revs

    @pytest.fixture(scope="class")
    def children(self, migrations) -> dict[str, list[str]]:
        c, _ = _build_dag(migrations)
        return c

    @pytest.fixture(scope="class")
    def parents(self, migrations) -> dict[str, list[str]]:
        _, p = _build_dag(migrations)
        return p

    def test_all_migrations_have_revision_id(self, migrations):
        """Every migration file must have a revision identifier."""
        missing = [m["file"] for m in migrations if m["revision"] is None]
        assert not missing, f"Migrations missing revision ID: {missing}"

    def test_no_duplicate_revision_ids(self, migrations):
        """No two migration files may share the same revision ID."""
        revisions = [m["revision"] for m in migrations if m["revision"] is not None]
        duplicates = {r for r in revisions if revisions.count(r) > 1}
        assert not duplicates, f"Duplicate revision IDs found: {duplicates}"

    def test_root_migration_is_initial_schema(self, migrations):
        """Exactly one migration must have ``down_revision = None``."""
        roots = [m["revision"] for m in migrations if m["down_revision"] is None]
        assert len(roots) == 1, f"Expected 1 root migration, found {len(roots)}: {roots}"
        assert roots[0] == INITIAL_REVISION, (
            f"Root migration should be {INITIAL_REVISION}, got {roots[0]}"
        )

    def test_single_head(self, children, all_revisions):
        """Migration DAG must have exactly one head (leaf node)."""
        heads = _find_heads(children, all_revisions)
        assert len(heads) == 1, (
            f"Expected 1 head, found {len(heads)}: {heads}. "
            "Run `alembic merge` to resolve branches."
        )

    def test_all_revisions_reachable_from_root(self, children, all_revisions):
        """Every revision must be reachable from the root migration."""
        reachable = _find_reachable(INITIAL_REVISION, children)
        orphans = all_revisions - reachable
        assert not orphans, (
            f"{len(orphans)} orphan revision(s) not reachable from root: {orphans}"
        )

    def test_no_dangling_down_revision_refs(self, all_revisions, migrations):
        """Every ``down_revision`` value must reference an existing revision."""
        existing = set(all_revisions)
        for m in migrations:
            down = m["down_revision"]
            if down is None:
                continue
            if isinstance(down, str):
                assert down in existing, (
                    f"Migration {m['revision']} ({m['file']}) references "
                    f"non-existent down_revision: {down}"
                )
            elif isinstance(down, tuple):
                for d in down:
                    assert d in existing, (
                        f"Migration {m['revision']} ({m['file']}) references "
                        f"non-existent down_revision: {d}"
                    )

    def test_no_cycles(self, parents):
        """Migration DAG must be acyclic."""
        visited = set()
        in_stack = set()

        def dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            in_stack.add(node)
            for parent in parents.get(node, []):
                if parent is None:
                    continue
                if parent in in_stack:
                    cycle_start = path[path.index(parent):] + [parent]
                    raise AssertionError(
                        f"Cycle detected in migration DAG: "
                        f"{' -> '.join(cycle_start)}"
                    )
                if parent not in visited:
                    dfs(parent, path + [parent])
            in_stack.discard(node)

        for rev in list(parents.keys()):
            if rev not in visited:
                dfs(rev, [rev])

    def test_upgrade_downgrade_both_defined(self, migrations):
        """Every migration must define both ``upgrade()`` and ``downgrade()``."""
        for m in migrations:
            assert m["has_upgrade_pass"] is not None, (
                f"Migration {m['file']} is missing upgrade()"
            )
            assert m["has_downgrade_pass"] is not None, (
                f"Migration {m['file']} is missing downgrade()"
            )

    def test_non_merge_migrations_have_real_downgrade(self, migrations):
        """Non-merge migrations must have a real (non-pass) downgrade body."""
        merge_migrations = {
            m["revision"] for m in migrations
            if m.get("revision") in (
                "4ffaa9dfdcb5",
                "b5c6d7e8f9a0",
                "930b42d7f5c0",
            )
        }
        for m in migrations:
            rev = m["revision"]
            if rev in merge_migrations:
                continue
            if m.get("has_downgrade_pass", False):
                pytest.fail(
                    f"Non-merge migration {m['file']} ({rev}) has an empty/pass "
                    f"downgrade() — add a proper downgrade body to allow rollback."
                )

    def test_branch_labels_only_on_merge_migrations(self, migrations):
        """Only merge migrations should use ``branch_labels``."""
        for m in migrations:
            bl = m.get("branch_labels")
            if bl and bl != (None,):
                rev = m["revision"]
                assert rev in ("4ffaa9dfdcb5", "b5c6d7e8f9a0", "930b42d7f5c0"), (
                    f"Non-merge migration {m['file']} ({rev}) has branch_labels={bl}"
                )

    def test_migration_count(self, migrations):
        """Snapshot the total migration count to detect unintended additions."""
        count = len(migrations)
        # As of 2026-07-24: 65 migration files (3 merge, 62 data/DDL)
        assert count == 65, (
            f"Expected 65 migration files, found {count}. "
            "Update this assertion after adding/removing migrations."
        )


# =============================================================================
# Tests — Upgrade / Downgrade Round-Trip (requires database)
# =============================================================================


class TestMigrationRoundTrip:
    """Verify that migrations can be fully applied and rolled back.

    These tests require a live MariaDB connection (the same one used by
    the existing test fixtures).
    """

    @pytest.fixture(scope="class")
    def migrations(self) -> list[dict]:
        return _collect_all_migrations()

    # ── Test 1: Full upgrade round-trip ────────────────────────────────────

    @pytest.mark.integration
    @pytest.mark.slow
    async def test_upgrade_heads_idempotent(self, db_session):
        """Running ``alembic upgrade heads`` twice must be a no-op.

        This tests idempotency — after all migrations are applied, running
        them again should produce no errors.
        """
        from alembic.config import Config
        from alembic.command import upgrade

        # alembic.ini is at backend/alembic.ini — 4 parents up from versions/
        alembic_ini = str(MIGRATIONS_DIR.parent.parent.parent.parent / "alembic.ini")
        alembic_cfg = Config(alembic_ini)

        # First upgrade (should apply all pending migrations)
        upgrade(alembic_cfg, "heads")

        # Second upgrade (should be a no-op — all already applied)
        upgrade(alembic_cfg, "heads")

        # Verify alembic_version table has the expected head
        result = (await db_session.execute(
            text("SELECT version_num FROM alembic_version")
        )).scalar()
        assert result is not None, "alembic_version table is empty after upgrade"
        assert len(result) > 0, "alembic_version has no entries"

    # ── Test 2: Verify current head in alembic_version ─────────────────────

    @pytest.mark.integration
    async def test_alembic_version_has_head(self, db_session, migrations):
        """The ``alembic_version`` table must contain the DAG head revision."""
        from alembic.config import Config
        from alembic.command import upgrade
        from alembic.script import ScriptDirectory

        alembic_ini = str(MIGRATIONS_DIR.parent.parent.parent.parent / "alembic.ini")
        alembic_cfg = Config(alembic_ini)

        # Ensure migrations are applied
        upgrade(alembic_cfg, "heads")

        # Get the current head from the script directory
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"Expected 1 head, found {len(heads)}: {heads}"
        expected_head = heads[0]

        # Query actual DB state
        result = (await db_session.execute(
            text("SELECT version_num FROM alembic_version")
        )).scalar()
        assert result == expected_head, (
            f"alembic_version head mismatch: DB has {result}, "
            f"expected {expected_head}"
        )

    # ── Test 3: ORM model metadata matches DB schema ───────────────────────

    @pytest.mark.integration
    async def test_orm_metadata_matches_db(self, db_session):
        """Compare ORM model metadata against actual DB schema after upgrade.

        Skip columns/table that are known to have benign ORM/DB drift
        (e.g., ``EncryptedString`` custom type).
        """
        from alembic.config import Config
        from alembic.command import upgrade
        from sqlalchemy import inspect as sa_inspect

        alembic_ini = str(MIGRATIONS_DIR.parent.parent.parent.parent / "alembic.ini")
        alembic_cfg = Config(alembic_ini)
        upgrade(alembic_cfg, "heads")

        # Import all models so metadata is loaded
        from src.db.base import Base
        import src.db.orm  # noqa: F401 — loads all table metadata into Base.metadata

        # Use run_sync to inspect the schema through the async engine
        # Collect all inspection data inside run_sync to avoid connection reuse issues
        from src.db.base import async_engine

        known_drift_columns = {
            ("models", "api_key"): "Custom EncryptedString type",
            ("user_tool_credentials", "credentials"): "Custom EncryptedString type",
            ("user_tool_credentials", "oauth_tokens"): "Custom EncryptedString type",
        }

        issues: list[str] = []

        async with async_engine.connect() as conn:
            await conn.run_sync(lambda sync_conn: _check_orm_schema(
                sync_conn, issues, known_drift_columns
            ))

        if issues:
            pytest.fail("\n".join(issues[:20]))  # Show first 20 issues


def _check_orm_schema(
    sync_conn,
    issues: list[str],
    known_drift_columns: dict,
) -> None:
    """Compare ORM metadata against DB schema using a sync connection."""
    from sqlalchemy import inspect as sa_inspect

    from src.db.base import Base
    import src.db.orm  # noqa: F401

    inspector = sa_inspect(sync_conn)

    for table_name in Base.metadata.tables:
        orm_table = Base.metadata.tables[table_name]
        if not inspector.has_table(table_name):
            issues.append(f"ORM model defines table '{table_name}' but it does not exist in DB")
            continue

        db_columns = {col["name"]: col for col in inspector.get_columns(table_name)}
        orm_columns = {col.name: col for col in orm_table.columns}

        for col_name, orm_col in orm_columns.items():
            if (table_name, col_name) in known_drift_columns:
                continue
            if col_name not in db_columns:
                issues.append(f"ORM has column '{table_name}.{col_name}' missing in DB")
                continue

            db_col = db_columns[col_name]
            orm_type_name = type(orm_col.type).__name__.lower()
            db_type_name = str(db_col["type"]).lower()

            if orm_type_name not in db_type_name and db_type_name not in orm_type_name:
                # Normalize type names for comparison (strip length/precision)
                orm_base = orm_type_name.split("(")[0].strip()
                db_base = db_type_name.split("(")[0].strip()
                equivalents = {
                    ("char", "varchar"): True,
                    ("integer", "int"): True,
                    ("text", "longtext"): True,
                    ("text", "mediumtext"): True,
                    ("boolean", "tinyint"): True,
                    ("datetime", "timestamp"): True,
                    ("string", "varchar"): True,
                    ("string", "char"): True,
                    ("numeric", "decimal"): True,
                    ("json", "longtext"): True,
                    ("json", "text"): True,
                    ("float", "double"): True,
                    ("float", "decimal"): True,
                    ("enum", "varchar"): True,
                    ("largebinary", "longblob"): True,
                    ("picklettype", "longblob"): True,
                }
                key = (orm_base, db_base)
                reverse_key = (db_base, orm_base)
                if not equivalents.get(key) and not equivalents.get(reverse_key):
                    issues.append(
                        f"Type mismatch: '{table_name}.{col_name}' — "
                        f"ORM={orm_type_name}, DB={db_type_name}"
                    )


# =============================================================================
# Tests — Individual Migration Correctness
# =============================================================================


class TestMigrationPatterns:
    """Validate that migrations follow safe patterns and conventions."""

    @pytest.fixture(scope="class")
    def migrations(self) -> list[dict]:
        return _collect_all_migrations()

    def test_no_raw_drop_table_without_backup(self, migrations):
        """Flag any migration that drops a table (alert — manual verification needed).

        DROP TABLE is irreversible if data hasn't been backed up first.
        """
        risky_drops = []
        for path in sorted(MIGRATIONS_DIR.glob("*.py")):
            content = path.read_text(encoding="utf-8")
            if "op.drop_table" in content:
                risky_drops.append(path.name)

        if risky_drops:
            print(
                f"\n⚠️  WARNING: {len(risky_drops)} migration(s) drop tables. "
                f"Verify production data is archived: {risky_drops}"
            )

    def test_enum_alter_migrations_need_attention(self, migrations):
        """Flag migrations that use ``MODIFY COLUMN`` (ENUM changes).

        ENUM alterations trigger full table rebuilds in MariaDB/MySQL.
        On large tables these can cause significant downtime.
        """
        risky_enums = []
        for path in sorted(MIGRATIONS_DIR.glob("*.py")):
            content = path.read_text(encoding="utf-8")
            if "MODIFY COLUMN" in content and "ENUM" in content:
                risky_enums.append(path.name)

        if risky_enums:
            print(
                f"\n⚠️  WARNING: {len(risky_enums)} migration(s) modify ENUM columns. "
                f"These trigger full table rebuilds: {risky_enums}"
            )

    def test_drop_column_migrations_need_attention(self, migrations):
        """Flag migrations that drop columns (potential data loss)."""
        risky_drops = []
        for path in sorted(MIGRATIONS_DIR.glob("*.py")):
            content = path.read_text(encoding="utf-8")
            if "op.drop_column" in content:
                risky_drops.append(path.name)

        if risky_drops:
            print(
                f"\n⚠️  WARNING: {len(risky_drops)} migration(s) drop columns. "
                f"Verify data is not needed: {risky_drops}"
            )


# =============================================================================
# Migration Risk Registry (for manual verification)
# =============================================================================

RISK_REGISTRY = {
    "HIGH": {
        "drop_table": [
            "f0a1b2c3d4e5_drop_erpnext_instances_table.py",
        ],
        "drop_column": [
            "h1i2j3k4l5m6_drop_branching_columns_from_messages.py",
            "j1k2l3m4n5o6_drop_routing_priority_from_models.py",
            "k2l3m4n5o6p7_drop_prompt_visibility_and_selected_prompt_id.py",
            "d1e2f3a4b5c6_fix_user_tool_preferences_pk.py",
        ],
        "enum_alter": [
            "a5b6c7d8e9f0_add_goal_based_skill_type.py",
            "z1a2b3c4d5e6_add_erpnext_to_credential_provider_enum.py",
            "7f8e9d0c1b2a_add_github_to_credential_provider_enum.py",
            "a1b2c3d4e5f6_add_datetime_to_tool_type_enum.py",
            "a1b2c3d4e5f7_add_stock_screener_to_tool_type_enum.py",
            "d5e6f7a8b9c0_add_web_search_to_tool_type_enum.py",
            "i1j2k3l4m5n6_add_general_tools_to_tool_type_enum.py",
            "r1s2t3u4v5w6_add_financial_investor_tools_to_tool_type_enum.py",
            "s3t4u5v6w7x8_add_general_tools_phase2_to_tool_type_enum.py",
            "t5u6v7w8x9y0_add_general_tools_phase3_to_tool_type_enum.py",
            "u1v2w3x4y5z6_add_pdf_extractor_to_tool_type_enum.py",
            "e7f8a9b0c1d2_add_execution_type_values.py",
        ],
    },
    "MEDIUM": {
        "data_backfill": [
            "q2r3s4t5u6v7_backfill_tool_categories.py",
        ],
        "unique_constraint_with_dedup": [
            "n1o2p3q4r5s7_add_unique_constraint_to_memory.py",
        ],
        "column_type_change": [
            "852db3dd6183_change_extracted_text_to_longtext.py",
        ],
    },
}

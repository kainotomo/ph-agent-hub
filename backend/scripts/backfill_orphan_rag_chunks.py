# =============================================================================
# PH Agent Hub — Orphaned RAG chunk backfill (Issue #628)
# =============================================================================
# Deleting a file upload used to leave its ``rag_documents`` rows behind with
# ``file_id = NULL`` (the FK was ``ON DELETE SET NULL``).  Those chunks can
# never be reclaimed through the application (``delete_document`` filters by
# ``file_id``) and still answer ``rag_search`` queries.  This script reports
# them and, with ``--apply``, deletes them.
#
# Chunks indexed from ad-hoc text (``file_id IS NULL`` and no
# ``original_filename`` in the metadata) are NOT touched.
#
# Usage (from /app inside the backend container):
#   python scripts/backfill_orphan_rag_chunks.py --dry-run
#   python scripts/backfill_orphan_rag_chunks.py --apply [--batch-size 500]
#   python scripts/backfill_orphan_rag_chunks.py --dry-run --tenant-id <uuid>
# =============================================================================

import argparse
import asyncio
import logging
import os
import sys
from collections import Counter
from typing import Any, Iterable

# Make src/ importable when running from /app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from src.db.base import AsyncSessionLocal, async_engine
from src.db.orm.rag import RAGDocument

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500


def is_upload_orphan(extra_metadata: Any) -> bool:
    """True when a NULL-file chunk carries the marker written by index_document.

    ``index_document`` always stores ``original_filename`` in the chunk
    metadata; ad-hoc indexed text does not.  Anything that is not a dict is
    treated as ad-hoc and kept.
    """
    if not isinstance(extra_metadata, dict):
        return False
    return bool(extra_metadata.get("original_filename"))


async def find_candidates(db, tenant_id: str | None = None):
    """Return (id, tenant_id, metadata) for every chunk with ``file_id`` NULL."""
    stmt = select(
        RAGDocument.id,
        RAGDocument.tenant_id,
        RAGDocument.extra_metadata,
    ).where(RAGDocument.file_id.is_(None))
    if tenant_id:
        stmt = stmt.where(RAGDocument.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return [tuple(row) for row in result.all()]


async def run(
    db,
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    tenant_id: str | None = None,
) -> dict:
    """Report orphan chunks and, when *apply*, delete only upload orphans.

    Returns a summary dict with keys ``null_total``, ``orphans``,
    ``adhoc_retained``, ``deleted`` and ``per_tenant``.
    """
    rows = await find_candidates(db, tenant_id=tenant_id)
    orphans = [(row_id, tid) for row_id, tid, meta in rows if is_upload_orphan(meta)]
    per_tenant = Counter(tid or "<null>" for _, tid in orphans)

    summary = {
        "null_total": len(rows),
        "orphans": len(orphans),
        "adhoc_retained": len(rows) - len(orphans),
        "deleted": 0,
        "per_tenant": dict(per_tenant),
    }

    logger.info("RAG chunks with file_id IS NULL: %d", summary["null_total"])
    logger.info("Upload orphans to delete: %d", summary["orphans"])
    logger.info("Ad-hoc chunks retained: %d", summary["adhoc_retained"])
    for tid, count in per_tenant.most_common(20):
        logger.info("  tenant %s: %d orphan chunk(s)", tid, count)

    if not apply:
        logger.info("DRY RUN — no rows deleted. Re-run with --apply to delete.")
        return summary

    ids = [row_id for row_id, _ in orphans]
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        await db.execute(delete(RAGDocument).where(RAGDocument.id.in_(batch)))
        await db.commit()
        summary["deleted"] += len(batch)
        logger.info("Deleted %d/%d orphan chunk(s)", summary["deleted"], len(ids))

    return summary


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("batch size must be >= 1")
    return parsed


async def _main_async(args) -> int:
    try:
        async with AsyncSessionLocal() as db:
            summary = await run(
                db,
                apply=args.apply,
                batch_size=args.batch_size,
                tenant_id=args.tenant_id,
            )
    finally:
        # Close pooled connections before the event loop shuts down, so
        # aiomysql does not warn about GC'd connections at interpreter exit.
        await async_engine.dispose()
    print(
        "summary: null_total={null_total} orphans={orphans} "
        "adhoc_retained={adhoc_retained} deleted={deleted}".format(**summary)
    )
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report and delete orphaned RAG chunks (Issue #628).",
    )
    parser.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="delete the upload-orphan rows (default: dry run)",
    )
    parser.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="report only (default)",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per DELETE batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help="restrict the report/cleanup to one tenant",
    )
    parser.set_defaults(apply=False)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
"""Quick E2E test for RAG."""
import asyncio, os, sys, uuid, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")
os.environ["DATABASE_URL"] = "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4"

async def e2e():
    from src.db.base import AsyncSessionLocal
    from src.db.orm.file_uploads import FileUpload
    from src.db.orm.rag import RAGDocument
    from src.db.orm.users import User
    from src.db.orm.tenants import Tenant
    from src.services.rag_service import index_document, search_documents, delete_document
    from sqlalchemy import select
    from unittest.mock import patch

    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).limit(1))).scalar_one()
        u = (await db.execute(select(User).limit(1))).scalar_one()
        print(f"1/8 tenant={t.name}")

        fid = str(uuid.uuid4())
        up = FileUpload(id=fid, tenant_id=t.id, user_id=u.id,
            original_filename="e2e.txt", content_type="text/plain",
            size_bytes=100, storage_key=f"t/{fid}.txt", bucket=f"phub-{t.id}",
            extracted_text="Python programming language for AI and data science.")
        db.add(up)
        await db.flush()
        print("2/8 FileUpload created")

        c = await index_document(db, up)
        print(f"3/8 Indexed {c} chunks")

        rows = (await db.execute(select(RAGDocument).where(RAGDocument.file_id == fid))).scalars().all()
        print(f"4/8 DB rows={len(rows)} all_embeddings={all(r.embedding_json for r in rows)}")

        async def me(t, **kw): return [[0.1]*256 for _ in t]
        with patch("src.services.rag_service._get_embeddings", me):
            r = await search_documents(db, "Python", tenant_id=t.id)
        print(f"5/8 Search results={len(r)}")

        d = await delete_document(db, fid)
        print(f"6/8 Deleted {d} chunks")

        rem = (await db.execute(select(RAGDocument).where(RAGDocument.file_id == fid))).scalars().all()
        print(f"7/8 Remaining={len(rem)}")

        await db.rollback()
        print("8/8 Rollback OK")
    print("PASS")

asyncio.run(e2e())

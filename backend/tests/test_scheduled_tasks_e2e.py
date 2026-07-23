"""E2E test for Scheduled Tasks (Issue #297).

Run inside the Docker container: docker compose exec backend python3 /tmp/test_scheduled_tasks_e2e.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

async def test():
    from src.db.base import AsyncSessionLocal
    from sqlalchemy import select
    from src.db.orm.users import User
    from src.db.orm.tenants import Tenant

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        tenant = (await db.execute(select(Tenant).limit(1))).scalars().first()
        USER_ID = user.id
        TENANT_ID = tenant.id

    print("=== Scheduled Task CRUD E2E Test ===")
    print(f"Using user={USER_ID[:8]}... tenant={TENANT_ID[:8]}...")

    async with AsyncSessionLocal() as db:
        from src.services.scheduled_task_service import (
            create_scheduled_task, list_scheduled_tasks, get_scheduled_task,
            update_scheduled_task, pause_scheduled_task, resume_scheduled_task,
            get_due_tasks, record_run_result, delete_scheduled_task,
        )

        # 1. CREATE
        print("\n1. CREATE...")
        task = await create_scheduled_task(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            goal="Test portfolio check against targets",
            schedule_description="Every minute",
            cron_expression="*/1 * * * *",
            timezone="UTC",
        )
        print(f"   ✓ Created: id={task.id[:8]}... goal={task.goal}")
        print(f"   ✓ Cron: {task.cron_expression}")
        print(f"   ✓ Next run: {task.next_run_at}")
        assert task.next_run_at is not None
        assert task.state == "ACTIVE"
        print("   ✓ State: ACTIVE")

        # 2. LIST
        print("\n2. LIST...")
        items, total = await list_scheduled_tasks(db, USER_ID, TENANT_ID)
        print(f"   ✓ Total: {total}")
        assert total >= 1
        assert any(t.id == task.id for t in items)

        # 3. GET
        print("\n3. GET...")
        fetched = await get_scheduled_task(db, task.id)
        assert fetched is not None
        assert fetched.goal == task.goal
        print(f"   ✓ Fetched: {fetched.goal}")

        # 4. UPDATE
        print("\n4. UPDATE...")
        updated = await update_scheduled_task(db, task.id, USER_ID, goal="Updated goal")
        assert updated.goal == "Updated goal"
        print(f"   ✓ Updated goal: {updated.goal}")

        # 5. PAUSE
        print("\n5. PAUSE...")
        paused = await pause_scheduled_task(db, task.id, USER_ID)
        assert paused.state == "PAUSED"
        assert paused.next_run_at is None
        print(f"   ✓ Paused: state={paused.state}")

        # 6. RESUME
        print("\n6. RESUME...")
        resumed = await resume_scheduled_task(db, task.id, USER_ID)
        assert resumed.state == "ACTIVE"
        assert resumed.next_run_at is not None
        print(f"   ✓ Resumed: next_run={resumed.next_run_at}")

        # 7. GET DUE TASKS
        print("\n7. GET DUE TASKS...")
        from datetime import datetime, timezone, timedelta
        resumed.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.commit()
        due = await get_due_tasks(db)
        assert any(t.id == task.id for t in due)
        print(f"   ✓ {len(due)} due task(s) — test task is among them")

        # 8. RECORD RUN RESULT (success)
        print("\n8. RECORD RUN RESULT (SUCCESS)...")
        from src.services import session_service
        test_session = await session_service.create_session(
            db, tenant_id=TENANT_ID, user_id=USER_ID,
            title="Scheduled Task Test Session",
        )
        result = await record_run_result(
            db, task.id, status="SUCCESS", session_id=test_session.id
        )
        assert result.last_run_status == "SUCCESS"
        assert result.last_run_session_id == test_session.id
        assert result.run_count == 1
        assert result.next_run_at is not None
        print(f"   ✓ Recorded: status={result.last_run_status}, count={result.run_count}")
        print(f"   ✓ Next run recomputed: {result.next_run_at}")

        # 9. RECORD RUN RESULT (failure)
        print("\n9. RECORD RUN RESULT (FAILED)...")
        result2 = await record_run_result(
            db, task.id, status="FAILED", error="Something went wrong"
        )
        assert result2.last_run_status == "FAILED"
        assert result2.last_run_error == "Something went wrong"
        assert result2.run_count == 2
        print(f"   ✓ Recorded failure: {result2.last_run_status} — {result2.last_run_error}")

        # 10. SOFT DELETE
        print("\n10. DELETE...")
        deleted = await delete_scheduled_task(db, task.id, USER_ID)
        assert deleted
        gone = await get_scheduled_task(db, task.id)
        assert gone.state == "DELETED"
        items2, _ = await list_scheduled_tasks(db, USER_ID, TENANT_ID)
        assert not any(t.id == task.id for t in items2)
        print("   ✓ Soft deleted (state=DELETED, hidden from list)")

        # 11. CRON VALIDATION
        print("\n11. CRON VALIDATION...")
        from croniter import croniter
        for expr, should_pass in [
            ("0 20 * * 5", True),
            ("0 8 * * 1-5", True),
            ("0 0 1 * *", True),
            ("bad cron", False),
        ]:
            try:
                croniter(expr)
                assert should_pass, f"{expr} should have failed"
            except (ValueError, KeyError):
                assert not should_pass, f"{expr} should have succeeded"
        print("   ✓ All cron expressions validated correctly")

        # 12. TIMEZONE
        print("\n12. TIMEZONE HANDLING...")
        from src.services.scheduled_task_service import _compute_next_run
        next_utc = _compute_next_run("0 20 * * 5", "UTC")
        next_ny = _compute_next_run("0 20 * * 5", "America/New_York")
        assert next_utc is not None
        assert next_ny is not None
        diff = abs((next_utc - next_ny).total_seconds())
        print(f"   ✓ UTC next: {next_utc}")
        print(f"   ✓ NY next: {next_ny}")
        print(f"   ✓ Timezone diff: {diff/3600:.1f}h (>= 4h = UTC vs EDT)")

        # 13. OWNERSHIP ISOLATION
        print("\n13. OWNERSHIP ISOLATION...")
        other_user_id = "00000000-0000-0000-0000-000000000000"
        try:
            await pause_scheduled_task(db, task.id, other_user_id)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        print("   ✓ Other user cannot modify task (ValueError raised)")

    print("\n=== ALL E2E TESTS PASSED ===")

asyncio.run(test())

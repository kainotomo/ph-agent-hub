# =============================================================================
# PH Agent Hub — Session Folders Tests (Issue #526)
# =============================================================================
# Covers the folder service and the /chat/folders API:
# create / list / rename / recolour / delete, moving sessions between folders,
# owner isolation, and temporary-session rejection.
#
# These require MariaDB (and Redis for the temporary-session paths), so they
# run in CI; see .github/workflows/ci.yml.
# =============================================================================

import uuid

import pytest

from src.core.exceptions import ConflictError, NotFoundError, ValidationError
from src.db.orm.folders import Folder
from src.db.orm.sessions import Session as SessionORM
from src.services import folder_service


def _folder(user, name="Work", color=None) -> Folder:
    return Folder(
        id=str(uuid.uuid4()),
        tenant_id=user.tenant_id,
        user_id=user.id,
        name=name,
        color=color,
    )


# =============================================================================
# Service layer
# =============================================================================


class TestFolderService:
    """Tests for src/services/folder_service.py."""

    async def test_create_and_list_orders_by_sort_order(self, db_session, test_user):
        """New folders are appended to the user's ordering."""
        first = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        second = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Personal"
        )

        folders = await folder_service.list_folders(db_session, user_id=test_user.id)
        assert [f.id for f in folders] == [first.id, second.id]
        assert first.sort_order == 0
        assert second.sort_order == 1

    async def test_create_trims_name(self, db_session, test_user):
        folder = await folder_service.create_folder(
            db_session,
            tenant_id=test_user.tenant_id,
            user_id=test_user.id,
            name="  Work  ",
        )
        assert folder.name == "Work"

    async def test_create_rejects_empty_name(self, db_session, test_user):
        with pytest.raises(ValidationError):
            await folder_service.create_folder(
                db_session,
                tenant_id=test_user.tenant_id,
                user_id=test_user.id,
                name="   ",
            )

    async def test_duplicate_name_is_case_insensitive(self, db_session, test_user):
        await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        with pytest.raises(ConflictError):
            await folder_service.create_folder(
                db_session,
                tenant_id=test_user.tenant_id,
                user_id=test_user.id,
                name="work",
            )

    async def test_same_name_allowed_for_different_users(
        self, db_session, test_user, second_user
    ):
        await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        other = await folder_service.create_folder(
            db_session,
            tenant_id=second_user.tenant_id,
            user_id=second_user.id,
            name="Work",
        )
        assert other.id

    async def test_update_renames_and_recolours(self, db_session, test_user):
        folder = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        updated = await folder_service.update_folder(
            db_session, folder.id, user_id=test_user.id, name="Projects", color="#ff0000"
        )
        assert updated.name == "Projects"
        assert updated.color == "#ff0000"

    async def test_update_to_existing_name_conflicts(self, db_session, test_user):
        await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        other = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Personal"
        )
        with pytest.raises(ConflictError):
            await folder_service.update_folder(
                db_session, other.id, user_id=test_user.id, name="Work"
            )

    async def test_update_unknown_folder_raises(self, db_session, test_user):
        with pytest.raises(NotFoundError):
            await folder_service.update_folder(
                db_session, str(uuid.uuid4()), user_id=test_user.id, name="Nope"
            )

    async def test_delete_moves_sessions_to_unfiled(
        self, db_session, test_user, test_session
    ):
        """Deleting a folder keeps its sessions and unfiles them."""
        folder = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        test_session.folder_id = folder.id
        await db_session.flush()

        moved = await folder_service.delete_folder(
            db_session, folder.id, user_id=test_user.id
        )
        assert moved == 1

        await db_session.refresh(test_session)
        assert test_session.folder_id is None

        remaining = await db_session.get(Folder, folder.id)
        assert remaining is None

    async def test_get_folder_is_scoped_to_owner(
        self, db_session, test_user, second_user
    ):
        folder = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        assert (
            await folder_service.get_folder(db_session, folder.id, user_id=test_user.id)
        ) is not None
        assert (
            await folder_service.get_folder(db_session, folder.id, user_id=second_user.id)
        ) is None

    async def test_count_sessions_in_folder(
        self, db_session, test_user, test_session
    ):
        folder = await folder_service.create_folder(
            db_session, tenant_id=test_user.tenant_id, user_id=test_user.id, name="Work"
        )
        assert await folder_service.count_sessions_in_folder(db_session, folder.id) == 0
        test_session.folder_id = folder.id
        await db_session.flush()
        assert await folder_service.count_sessions_in_folder(db_session, folder.id) == 1


# =============================================================================
# HTTP API
# =============================================================================


class TestFoldersApi:
    """Tests for /api/chat/folders and folder moves on /api/chat/session/{id}."""

    async def test_create_folder(
        self, async_client, auth_headers, test_user
    ):
        resp = await async_client.post(
            "/api/chat/folders",
            json={"name": "Work", "color": "#1677ff"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Work"
        assert body["color"] == "#1677ff"
        assert body["user_id"] == test_user.id
        assert body["sort_order"] == 0

    async def test_create_folder_empty_name_is_rejected(
        self, async_client, auth_headers, test_user
    ):
        resp = await async_client.post(
            "/api/chat/folders",
            json={"name": "  "},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 422

    async def test_create_duplicate_folder_conflicts(
        self, async_client, auth_headers, test_user
    ):
        headers = auth_headers(test_user)
        first = await async_client.post(
            "/api/chat/folders", json={"name": "Work"}, headers=headers
        )
        assert first.status_code == 201, first.text
        second = await async_client.post(
            "/api/chat/folders", json={"name": "work"}, headers=headers
        )
        assert second.status_code == 409

    async def test_list_folders_returns_only_own(
        self, async_client, auth_headers, test_user, second_user
    ):
        mine = await async_client.post(
            "/api/chat/folders",
            json={"name": "Mine"},
            headers=auth_headers(test_user),
        )
        assert mine.status_code == 201, mine.text

        theirs = await async_client.post(
            "/api/chat/folders",
            json={"name": "Theirs"},
            headers=auth_headers(second_user),
        )
        assert theirs.status_code == 201, theirs.text

        resp = await async_client.get(
            "/api/chat/folders", headers=auth_headers(test_user)
        )
        assert resp.status_code == 200, resp.text
        names = [f["name"] for f in resp.json()]
        assert names == ["Mine"]

    async def test_update_folder(
        self, async_client, auth_headers, test_user
    ):
        created = await async_client.post(
            "/api/chat/folders",
            json={"name": "Work"},
            headers=auth_headers(test_user),
        )
        folder_id = created.json()["id"]

        resp = await async_client.put(
            f"/api/chat/folders/{folder_id}",
            json={"name": "Projects", "color": "#52c41a"},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Projects"
        assert resp.json()["color"] == "#52c41a"

    async def test_update_other_users_folder_is_not_found(
        self, async_client, auth_headers, test_user, second_user
    ):
        created = await async_client.post(
            "/api/chat/folders",
            json={"name": "Work"},
            headers=auth_headers(test_user),
        )
        folder_id = created.json()["id"]

        resp = await async_client.put(
            f"/api/chat/folders/{folder_id}",
            json={"name": "Hijacked"},
            headers=auth_headers(second_user),
        )
        assert resp.status_code == 404

    async def test_delete_folder_keeps_sessions(
        self, async_client, auth_headers, test_user, test_session, db_session
    ):
        headers = auth_headers(test_user)
        created = await async_client.post(
            "/api/chat/folders", json={"name": "Work"}, headers=headers
        )
        folder_id = created.json()["id"]

        moved = await async_client.put(
            f"/api/chat/session/{test_session.id}",
            json={"folder_id": folder_id},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["folder_id"] == folder_id

        deleted = await async_client.delete(
            f"/api/chat/folders/{folder_id}", headers=headers
        )
        assert deleted.status_code == 204, deleted.text

        await db_session.refresh(test_session)
        assert test_session.folder_id is None
        # The session itself survives.
        assert (
            await db_session.get(SessionORM, test_session.id)
        ) is not None

    async def test_delete_other_users_folder_is_not_found(
        self, async_client, auth_headers, test_user, second_user
    ):
        created = await async_client.post(
            "/api/chat/folders",
            json={"name": "Work"},
            headers=auth_headers(test_user),
        )
        resp = await async_client.delete(
            f"/api/chat/folders/{created.json()['id']}",
            headers=auth_headers(second_user),
        )
        assert resp.status_code == 404

    async def test_move_session_back_to_unfiled(
        self, async_client, auth_headers, test_user, test_session
    ):
        headers = auth_headers(test_user)
        created = await async_client.post(
            "/api/chat/folders", json={"name": "Work"}, headers=headers
        )
        folder_id = created.json()["id"]

        await async_client.put(
            f"/api/chat/session/{test_session.id}",
            json={"folder_id": folder_id},
            headers=headers,
        )
        resp = await async_client.put(
            f"/api/chat/session/{test_session.id}",
            json={"folder_id": None},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["folder_id"] is None

    async def test_move_into_foreign_folder_is_rejected(
        self, async_client, auth_headers, test_user, second_user, test_session, db_session
    ):
        """A user cannot file a session into someone else's folder."""
        foreign = _folder(second_user, name="Theirs")
        db_session.add(foreign)
        await db_session.flush()

        resp = await async_client.put(
            f"/api/chat/session/{test_session.id}",
            json={"folder_id": foreign.id},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 422

    async def test_unknown_folder_id_is_rejected(
        self, async_client, auth_headers, test_user, test_session
    ):
        resp = await async_client.put(
            f"/api/chat/session/{test_session.id}",
            json={"folder_id": str(uuid.uuid4())},
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 422

    async def test_temporary_session_cannot_be_filed(
        self, async_client, auth_headers, test_user
    ):
        """Creating a temporary session with a folder is rejected up front."""
        created = await async_client.post(
            "/api/chat/folders",
            json={"name": "Work"},
            headers=auth_headers(test_user),
        )
        folder_id = created.json()["id"]

        resp = await async_client.post(
            "/api/chat/session",
            json={
                "title": "Temp",
                "is_temporary": True,
                "folder_id": folder_id,
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 422

    async def test_folder_id_persists_on_session_creation(
        self, async_client, auth_headers, test_user
    ):
        """A permanent session created with folder_id lands in that folder."""
        headers = auth_headers(test_user)
        created = await async_client.post(
            "/api/chat/folders", json={"name": "Work"}, headers=headers
        )
        folder_id = created.json()["id"]

        resp = await async_client.post(
            "/api/chat/session",
            json={
                "title": "Filed chat",
                "is_temporary": False,
                "folder_id": folder_id,
                "selected_model_id": None,
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["folder_id"] == folder_id

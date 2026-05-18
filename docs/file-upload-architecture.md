# File Upload Architecture — PH Agent Hub

This document defines the file upload storage backend, data model, API, lifecycle rules, and integration with the chat area and agent tools.

---

## 1. Storage Backend: MinIO

PH Agent Hub uses **MinIO** as the object storage backend for all file uploads.

- **License:** GNU AGPL v3 (free for internal platforms; no redistribution concern)
- **API:** S3-compatible — the backend uses `boto3` (the standard AWS S3 Python client) to communicate with MinIO
- **Deployment:** MinIO runs as a Docker container inside the same Docker Compose stack
- **Migration path:** Because the code uses `boto3` against the S3 API, migrating to AWS S3 or Cloudflare R2 in the future requires only changing environment variables, not code. Migration to Azure Blob Storage would require adding an Azure storage adapter — all storage calls are contained in `/backend/src/storage/s3.py` to make this tractable.

### Why MinIO over local disk
- Works correctly when the backend scales to multiple containers (shared object store)
- Supports presigned URLs — the frontend can download files directly from MinIO without proxying through the backend
- S3-compatible API from day one means zero code changes if the storage backend changes later

---

## 2. Bucket Structure

One bucket per tenant, created automatically when the tenant is provisioned:

```
phhub-tenant-{tenant_id}/
  uploads/
    {user_id}/
      {session_id}/
        {file_id}-{original_filename}
```

- Object key format: `uploads/{user_id}/{session_id}/{file_id}-{original_filename}`
- The `file_id` prefix prevents collisions if a user uploads identically-named files
- All objects in a bucket are private; access is always via presigned URLs, never via public bucket policy

---

## 3. Data Model

### Table: `file_uploads`

```
file_uploads
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- session_id (UUID, FK → sessions.id, nullable) — null if uploaded outside a session context
- message_id (UUID, FK → messages.id, nullable) — linked when the file is attached to a message
- original_filename (string) — filename as provided by the user
- content_type (string) — MIME type (e.g. application/pdf, image/png)
- size_bytes (int)
- storage_key (string) — full object key within the tenant bucket
- bucket (string) — MinIO bucket name
- is_temporary (boolean, default false) — true if the parent session is temporary
- created_at (timestamp)
```

**Constraints:**
- Files uploaded into a temporary session have `is_temporary = true` and are excluded from permanent storage and RAG embedding
- `storage_key` + `bucket` together uniquely identify the object in MinIO

---

## 4. API Endpoints

### Upload a file
```
POST /chat/session/:id/upload
Content-Type: multipart/form-data

→ 201 Created
{
  "file_id": "uuid",
  "original_filename": "report.pdf",
  "content_type": "application/pdf",
  "size_bytes": 204800
}
```

- Validates file type against the allowed list
- Enforces per-file size limit
- Stores the object in MinIO under the tenant bucket
- Inserts a `file_uploads` row with `session_id` set and `message_id` null
- Returns the `file_id` which the frontend includes when sending the next message

### Get a presigned download URL
```
GET /chat/session/:id/upload/:fileId/url

→ 200 OK
{
  "url": "https://minio:9000/phhub-tenant-.../uploads/.../report.pdf?X-Amz-Signature=..."
}
```

- URL is valid for 15 minutes (configurable)
- Backend validates that the requesting user owns the file and belongs to the correct tenant before issuing the presigned URL

### Delete a file
```
DELETE /chat/session/:id/upload/:fileId
```

- Deletes the object from MinIO
- Soft-marks the `file_uploads` row (or hard-deletes — TBD during implementation)
- Only the owning user or an admin can delete a file

### List files for a session
```
GET /chat/session/:id/uploads

→ 200 OK
[
  {
    "file_id": "uuid",
    "original_filename": "report.pdf",
    "content_type": "application/pdf",
    "size_bytes": 204800,
    "created_at": "..."
  }
]
```

---

## 5. Upload Flow

```
User selects file in chat UI
        │
        ▼
Frontend POSTs multipart/form-data to POST /chat/session/:id/upload
        │
        ▼
Backend validates:
  - JWT (auth + tenant scope)
  - File type is in allowed list
  - File size is within limit
  - Session belongs to user
        │
        ▼
Backend stores object in MinIO:
  bucket: phhub-tenant-{tenant_id}
  key:    uploads/{user_id}/{session_id}/{file_id}-{original_filename}
        │
        ▼
Backend inserts file_uploads row
        │
        ▼
Returns { file_id, original_filename, content_type, size_bytes }
        │
        ▼
Frontend attaches file_id(s) to the next message send request
        │
        ▼
Backend links message_id in file_uploads row after message is persisted
```

---

## 6. File Type and Size Limits

Enforced at the API layer before the object is written to MinIO:

| Setting | Default | Config key |
|---|---|---|
| Max file size | 100 MB | `UPLOAD_MAX_SIZE_BYTES` |
| Allowed MIME types | see below | `UPLOAD_ALLOWED_TYPES` |

**Default allowed MIME types:**
```
text/plain
text/csv
text/markdown
application/pdf
application/json
image/png
image/jpeg
image/gif
image/webp
```

Binary executables, archives, and video files are rejected by default. The allowed list is configurable per deployment via environment variable.

---

## 7. Temporary Sessions

- File uploads are **disabled** for temporary sessions at the API level — `POST /chat/session/:id/upload` returns `403` if the session is temporary
- This is consistent with the rule that temporary sessions write nothing to permanent storage
- The frontend hides the upload button when the session is in temporary mode

---

## 8. File Lifecycle

| Event | Action |
|---|---|
| Session deleted (permanent) | All `file_uploads` rows for the session are deleted; objects removed from MinIO |
| User account deleted | All files owned by the user are deleted from MinIO and `file_uploads` |
| Tenant deleted | All tenant bucket objects deleted; all `file_uploads` rows deleted |
| Temporary session expires (Redis TTL) | No file cleanup needed — uploads were blocked for temporary sessions |

A background cleanup job (see background jobs architecture) handles orphaned objects — files that have a MinIO object but no corresponding `file_uploads` row — as a safety net.

---

## 9. Agent Tool Integration

When a message includes attached files, the backend makes the file content available to the MAF agent as tool context:

- **Text-based files** (PDF, plain text, CSV, Markdown, JSON): extracted and injected into the agent's context window
- **Images**: passed as image parts in the multi-modal message content if the selected model supports vision
- File extraction is performed by the backend before the agent loop runs; the agent does not call MinIO directly

---

## 10. Storage Module

All MinIO interactions are contained in a single module:

```
/backend/src/storage/s3.py
```

This module is the only place in the codebase that calls `boto3`. No service, agent, or API handler calls `boto3` directly. This ensures a future migration to AWS S3, Cloudflare R2, or an abstraction layer requires changes in one file only.

```python
# /backend/src/storage/s3.py

import boto3

_client = None

def get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_ENDPOINT,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
        )
    return _client

async def upload_object(bucket: str, key: str, data: bytes, content_type: str): ...
async def delete_object(bucket: str, key: str): ...
async def generate_presigned_url(bucket: str, key: str, expires_in: int = 900) -> str: ...
async def ensure_bucket_exists(bucket: str): ...
```

---

## 11. Environment Variables

```
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_PREFIX=phhub-tenant

UPLOAD_MAX_SIZE_BYTES=104857600
UPLOAD_ALLOWED_TYPES=text/plain,text/csv,text/markdown,application/pdf,application/json,image/png,image/jpeg,image/gif,image/webp
```

---

## 12. References

- [MinIO Docker Hub](https://hub.docker.com/r/minio/minio)
- [MinIO Documentation](https://min.io/docs/)
- [boto3 S3 client docs](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
- [Data Model](data-model.md)
- [Deployment Guide](deployment.md)
- [Backend Architecture](backend-architecture.md)

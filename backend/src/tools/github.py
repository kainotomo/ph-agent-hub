# =============================================================================
# PH Agent Hub — GitHub Integration Tool Factory
# =============================================================================
# Search code, list issues/PRs, read files, create issues/PRs, comment,
# manage files, merge PRs, and label issues on GitHub/GitLab repos.
# PAT stored encrypted in tool.config. Repo allowlist support.
#
# Dependencies: httpx (already installed)
# =============================================================================

import logging
from typing import Any
from urllib.parse import quote

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 30.0
GITHUB_API_BASE: str = "https://api.github.com"
GITLAB_API_BASE: str = "https://gitlab.com/api/v4"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_token(tool_config: dict) -> str:
    """Resolve and decrypt the access token from config."""
    from ..core.encryption import decrypt

    token = tool_config.get("token", "") or tool_config.get("access_token", "")
    if not token:
        return ""

    try:
        return decrypt(token)
    except Exception:
        # Assume it's already plaintext
        return token


def _check_repo_allowed(repo: str, allowed_repos: list[str] | None) -> bool:
    """Check if a repo is in the allowlist. Empty list means all allowed."""
    if not allowed_repos:
        return True

    repo_lower = repo.lower()
    for pattern in allowed_repos:
        pattern_lower = pattern.lower().strip()
        if pattern_lower == "*" or pattern_lower == "*/*":
            return True
        if pattern_lower.endswith("/*"):
            org = pattern_lower[:-2]
            if repo_lower.startswith(org + "/"):
                return True
        if repo_lower == pattern_lower:
            return True

    return False


async def _github_request(
    endpoint: str,
    token: str,
    method: str = "GET",
    json_data: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    api_base: str = GITHUB_API_BASE,
) -> dict:
    """Make an authenticated request to the GitHub API."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ph-agent-hub/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{api_base.rstrip('/')}{endpoint}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            elif method == "POST":
                response = await client.post(url, headers=headers, json=json_data)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=json_data)
            elif method == "PATCH":
                response = await client.patch(url, headers=headers, json=json_data)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                return {"error": f"Unsupported HTTP method: {method}"}

            # Check rate limit
            remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
            limit = response.headers.get("X-RateLimit-Limit", "unknown")

            if response.status_code == 401:
                return {"error": "Authentication failed. Check your access token.", "rate_limit_remaining": remaining}
            elif response.status_code == 403:
                if "rate limit" in response.text.lower():
                    return {"error": "Rate limit exceeded. Please wait before making more requests.", "rate_limit_remaining": "0"}
                return {"error": f"Access forbidden: {response.text[:300]}", "rate_limit_remaining": remaining}
            elif response.status_code == 404:
                return {"error": "Resource not found. Check the repository name and path.", "rate_limit_remaining": remaining}
            elif response.status_code == 422:
                details = ""
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        errors = body.get("errors", [])
                        if errors:
                            detail_msgs = []
                            for err in errors:
                                code = err.get("code", "")
                                field = err.get("field", "")
                                msg = err.get("message", "")
                                parts = [p for p in [field, code, msg] if p]
                                if parts:
                                    detail_msgs.append(": ".join(parts))
                            if detail_msgs:
                                details = " — " + "; ".join(detail_msgs)
                        if not details:
                            details = body.get("message", "")
                except Exception:
                    pass
                return {
                    "error": f"GitHub API rejected the request (HTTP 422){details}",
                    "rate_limit_remaining": remaining,
                }

            response.raise_for_status()
            data = response.json()

            # Add rate limit info to response
            if isinstance(data, dict):
                data["_rate_limit_remaining"] = remaining
            elif isinstance(data, list):
                # Wrap list results
                pass  # We'll add rate limit in the caller

            return {"data": data, "rate_limit_remaining": remaining}

    except httpx.TimeoutException:
        return {"error": "GitHub API request timed out"}
    except Exception as exc:
        logger.error("GitHub API request failed: %s", exc)
        return {"error": f"GitHub API request failed: {str(exc)}"}


async def _gitlab_request(
    endpoint: str,
    token: str,
    method: str = "GET",
    json_data: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    api_base: str = GITLAB_API_BASE,
) -> dict:
    """Make an authenticated request to the GitLab API."""
    headers = {"User-Agent": "ph-agent-hub/1.0"}
    if token:
        headers["PRIVATE-TOKEN"] = token

    url = f"{api_base.rstrip('/')}{endpoint}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            elif method == "POST":
                response = await client.post(url, headers=headers, json=json_data)
            elif method == "PUT":
                response = await client.put(url, headers=headers, json=json_data)
            else:
                return {"error": f"Unsupported HTTP method: {method}"}
            if response.status_code == 401:
                return {"error": "Authentication failed. Check your access token."}
            elif response.status_code == 404:
                return {"error": "Resource not found. Check the repository path."}
            response.raise_for_status()
            return {"data": response.json()}
    except httpx.TimeoutException:
        return {"error": "GitLab API request timed out"}
    except Exception as exc:
        logger.error("GitLab API request failed: %s", exc)
        return {"error": f"GitLab API request failed: {str(exc)}"}


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_github_tools(
    tool_config: dict | None = None,
    user_credentials: list | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions for GitHub/GitLab.

    Args:
        tool_config: ``Tool.config`` JSON dict.  May include:
            - ``provider`` (str): "github" (default) or "gitlab"
            - ``token`` (str): Personal Access Token (encrypted or plaintext)
            - ``api_base`` (str): Custom API base URL (for self-hosted instances)
            - ``allowed_repos`` (list[str]): Repo allowlist patterns
            - ``timeout`` (float): Request timeout in seconds (default 30)
        user_credentials: Optional list of ``UserToolCredential`` ORM
            instances. Per-user tokens take precedence over the tenant-level
            ``tool_config`` token (Issue #434).

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    provider: str = config.get("provider", "github").lower()
    token: str = _resolve_token(config)
    api_base: str = config.get("api_base", "")
    allowed_repos: list[str] = config.get("allowed_repos", [])
    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))

    if not api_base:
        api_base = GITHUB_API_BASE if provider == "github" else GITLAB_API_BASE

    is_gitlab = provider == "gitlab"

    # ------------------------------------------------------------------
    # Per-user token resolution (Phase 1: user creds, Phase 2: tenant config)
    # ------------------------------------------------------------------
    def _resolve_github_token() -> str:
        """Return the effective token — per-user credential or tenant fallback."""
        if user_credentials:
            import json as _json

            for cred in user_credentials:
                if cred.status != "active":
                    continue
                raw = cred.credentials
                if not raw:
                    continue
                try:
                    parsed = _json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    continue
                access_token = parsed.get("access_token", "").strip()
                if access_token:
                    return access_token

        # Fall back to tenant-level token (already resolved above)
        return token

    # ------------------------------------------------------------------
    @tool
    async def search_code(query: str, repo: str) -> dict:
        """Search for code in a GitHub/GitLab repository.

        Args:
            query: The search query (e.g., "function calculate_total").
            repo: Repository in "owner/name" format (e.g., "facebook/react").

        Returns:
            A dict with:
            - ``total_count``: total number of matching results
            - ``items``: list of matching code items (path, sha, url, text_matches)
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if search failed
        """
        if not query or not query.strip():
            return {"error": "No search query provided"}
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            # GitLab search
            encoded_query = quote(query)
            encoded_repo = quote(repo.replace("/", "%2F"))
            result = await _gitlab_request(
                f"/projects/{encoded_repo}/search?scope=blobs&search={encoded_query}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )
        else:
            # GitHub code search
            encoded_query = quote(f"{query} repo:{repo}")
            result = await _github_request(
                f"/search/code?q={encoded_query}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )

        if "error" in result:
            return result

        data = result.get("data", {})
        items = data.get("items", [])

        # Simplify items for the agent
        simplified = []
        for item in items[:20]:  # Limit results
            simplified.append({
                "path": item.get("path", ""),
                "name": item.get("name", ""),
                "repository": item.get("repository", {}).get("full_name", repo) if not is_gitlab else repo,
                "html_url": item.get("html_url", ""),
                "score": item.get("score", item.get("_ranking", None)),
            })

        return {
            "total_count": data.get("total_count", len(simplified)),
            "items": simplified,
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def list_issues(repo: str, state: str = "open", per_page: int = 10) -> dict:
        """List issues in a GitHub/GitLab repository.

        Args:
            repo: Repository in "owner/name" format.
            state: Issue state — "open", "closed", or "all".
            per_page: Number of issues to return (max 100).

        Returns:
            A dict with:
            - ``total``: total number of issues (approximate)
            - ``issues``: list of issue dicts (number, title, state, url, labels)
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        per_page = min(max(per_page, 1), 100)

        if is_gitlab:
            encoded_repo = quote(repo.replace("/", "%2F"))
            result = await _gitlab_request(
                f"/projects/{encoded_repo}/issues?state={state}&per_page={per_page}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )
        else:
            result = await _github_request(
                f"/repos/{repo}/issues?state={state}&per_page={per_page}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )

        if "error" in result:
            return result

        data = result.get("data", [])
        if isinstance(data, dict):
            data = [data]

        issues = []
        for item in data:
            issues.append({
                "number": item.get("number", item.get("iid", "")),
                "title": item.get("title", ""),
                "state": item.get("state", ""),
                "html_url": item.get("html_url", item.get("web_url", "")),
                "labels": [
                    lbl.get("name", lbl) if isinstance(lbl, dict) else lbl
                    for lbl in item.get("labels", [])
                ],
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
                "author": (
                    item.get("user", {}).get("login", "")
                    if isinstance(item.get("user"), dict)
                    else item.get("author", {}).get("username", "")
                ),
            })

        return {
            "total": len(issues),
            "issues": issues,
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def get_file_content(repo: str, path: str, ref: str = "main") -> dict:
        """Read the content of a file from a GitHub/GitLab repository.

        Args:
            repo: Repository in "owner/name" format.
            path: Path to the file within the repository.
            ref: Branch, tag, or commit SHA (default "main").

        Returns:
            A dict with:
            - ``path``: the file path
            - ``content``: the decoded file content (truncated if > 50KB)
            - ``size_bytes``: file size in bytes
            - ``url``: URL to the file on the web
            - ``error``: error message if failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not path or not path.strip():
            return {"error": "No file path provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            encoded_repo = quote(repo.replace("/", "%2F"))
            encoded_path = quote(path, safe="")
            encoded_ref = quote(ref, safe="")
            result = await _gitlab_request(
                f"/projects/{encoded_repo}/repository/files/{encoded_path}/raw?ref={encoded_ref}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )
            if "error" in result:
                return result

            content = result.get("data", "")
            if isinstance(content, dict):
                content = content.get("content", str(content))
        else:
            result = await _github_request(
                f"/repos/{repo}/contents/{path}?ref={ref}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )
            if "error" in result:
                return result

            data = result.get("data", {})
            import base64
            content_b64 = data.get("content", "")
            content = ""
            if content_b64:
                try:
                    content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
                except Exception:
                    content = "[Binary file - content not displayed]"

            size = data.get("size", len(content))
            html_url = data.get("html_url", "")

        # Truncate if too large
        max_size = 50_000
        full_size = len(content)
        truncated = full_size > max_size
        if truncated:
            content = content[:max_size] + f"\n\n... (truncated, full size: {full_size} bytes)"

        return {
            "path": path,
            "content": content,
            "size_bytes": full_size if not is_gitlab else len(content),
            "truncated": truncated,
            "url": html_url if not is_gitlab else "",
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown") if not is_gitlab else "",
        }

    # ------------------------------------------------------------------
    @tool
    async def list_pull_requests(repo: str, state: str = "open", per_page: int = 10) -> dict:
        """List pull requests (GitHub) or merge requests (GitLab) in a repository.

        Args:
            repo: Repository in "owner/name" format.
            state: PR state — "open", "closed", or "all".
            per_page: Number of PRs to return (max 100).

        Returns:
            A dict with:
            - ``total``: number of PRs returned
            - ``pull_requests``: list of PR dicts
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        per_page = min(max(per_page, 1), 100)

        if is_gitlab:
            encoded_repo = quote(repo.replace("/", "%2F"))
            result = await _gitlab_request(
                f"/projects/{encoded_repo}/merge_requests?state={state}&per_page={per_page}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )
        else:
            result = await _github_request(
                f"/repos/{repo}/pulls?state={state}&per_page={per_page}",
                _resolve_github_token(),
                timeout=timeout,
                api_base=api_base,
            )

        if "error" in result:
            return result

        data = result.get("data", [])
        if isinstance(data, dict):
            data = [data]

        prs = []
        for item in data:
            prs.append({
                "number": item.get("number", item.get("iid", "")),
                "title": item.get("title", ""),
                "state": item.get("state", ""),
                "html_url": item.get("html_url", item.get("web_url", "")),
                "author": (
                    item.get("user", {}).get("login", "")
                    if isinstance(item.get("user"), dict)
                    else item.get("author", {}).get("username", "")
                ),
                "created_at": item.get("created_at", ""),
                "draft": item.get("draft", item.get("work_in_progress", False)),
            })

        return {
            "total": len(prs),
            "pull_requests": prs,
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown") if not is_gitlab else "",
        }

    # ------------------------------------------------------------------
    @tool
    async def create_issue(repo: str, title: str, body: str = "") -> dict:
        """Create a new issue in a GitHub repository.

        Note: Issue creation is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            title: Issue title.
            body: Issue body/description (Markdown supported).

        Returns:
            A dict with:
            - ``number``: the new issue number
            - ``title``: issue title
            - ``html_url``: URL to the created issue
            - ``error``: error message if creation failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not title or not title.strip():
            return {"error": "No issue title provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "Issue creation via GitLab is not yet supported"}

        result = await _github_request(
            f"/repos/{repo}/issues",
            _resolve_github_token(),
            method="POST",
            json_data={"title": title, "body": body},
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", {})
        return {
            "number": data.get("number", ""),
            "title": data.get("title", title),
            "html_url": data.get("html_url", ""),
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def create_pull_request(repo: str, title: str, head: str, base: str, body: str = "", draft: bool = False) -> dict:
        """Create a pull request in a GitHub repository.

        Note: Pull request creation is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            title: Pull request title.
            head: The name of the branch where your changes are implemented.
            base: The name of the branch you want the changes pulled into.
            body: Pull request body/description (Markdown supported).
            draft: Whether to create the pull request as a draft (default False).

        Returns:
            A dict with:
            - ``number``: the new pull request number
            - ``title``: PR title
            - ``html_url``: URL to the created PR
            - ``state``: PR state (e.g., "open")
            - ``draft``: whether the PR is a draft
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if creation failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not title or not title.strip():
            return {"error": "No pull request title provided"}
        if not head or not head.strip():
            return {"error": "No head branch provided"}
        if not base or not base.strip():
            return {"error": "No base branch provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "Pull request creation via GitLab is not yet supported"}

        payload: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "draft": draft,
        }
        if body:
            payload["body"] = body

        result = await _github_request(
            f"/repos/{repo}/pulls",
            _resolve_github_token(),
            method="POST",
            json_data=payload,
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", {})
        return {
            "number": data.get("number", ""),
            "title": data.get("title", title),
            "html_url": data.get("html_url", ""),
            "state": data.get("state", ""),
            "draft": data.get("draft", draft),
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def comment_on_issue(repo: str, issue_number: int, body: str) -> dict:
        """Add a comment to an issue or pull request in a GitHub repository.

        Note: Commenting is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            issue_number: The issue or pull request number.
            body: The comment text (Markdown supported).

        Returns:
            A dict with:
            - ``id``: the comment ID
            - ``html_url``: URL to the created comment
            - ``body``: the comment body
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if creation failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not isinstance(issue_number, int) or issue_number < 1:
            return {"error": "Invalid issue number"}
        if not body or not body.strip():
            return {"error": "No comment body provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "Commenting via GitLab is not yet supported"}

        result = await _github_request(
            f"/repos/{repo}/issues/{issue_number}/comments",
            _resolve_github_token(),
            method="POST",
            json_data={"body": body},
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", {})
        return {
            "id": data.get("id", ""),
            "html_url": data.get("html_url", ""),
            "body": data.get("body", body),
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def create_or_update_file(repo: str, path: str, message: str, content: str, branch: str = "main", sha: str | None = None) -> dict:
        """Create or update a single file in a GitHub repository.

        If ``sha`` is provided, the file will be updated. If ``sha`` is omitted,
        the file will be created (fails if the file already exists with a
        helpful error message asking you to provide the SHA).

        Note: File creation/update is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            path: Path to the file within the repository.
            message: Commit message describing the change.
            content: The file content (plain text — will be base64-encoded).
            branch: Branch to commit to (default "main").
            sha: The blob SHA of the existing file. Required for updates.
                  Omit when creating a new file.

        Returns:
            A dict with:
            - ``path``: the file path
            - ``html_url``: URL to the file on the web
            - ``commit_sha``: SHA of the created commit
            - ``action``: "created" or "updated"
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not path or not path.strip():
            return {"error": "No file path provided"}
        if not message or not message.strip():
            return {"error": "No commit message provided"}
        if not content:
            return {"error": "No file content provided"}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "File creation/update via GitLab is not yet supported"}

        import base64
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        payload: dict[str, Any] = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        result = await _github_request(
            f"/repos/{repo}/contents/{path}",
            _resolve_github_token(),
            method="PUT",
            json_data=payload,
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", {})
        # GitHub returns 201 for creates and 200 for updates
        action = data.get("commit", {}).get("sha", "") if data else ""
        return {
            "path": data.get("content", {}).get("path", path),
            "html_url": data.get("content", {}).get("html_url", ""),
            "commit_sha": data.get("commit", {}).get("sha", ""),
            "action": sha and "updated" or "created",
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def merge_pull_request(repo: str, pull_number: int, merge_method: str = "merge", commit_title: str = "", commit_message: str = "") -> dict:
        """Merge a pull request in a GitHub repository.

        Note: PR merging is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            pull_number: The pull request number to merge.
            merge_method: Merge method — "merge", "squash", or "rebase"
                          (default "merge").
            commit_title: Optional custom title for the merge commit.
            commit_message: Optional custom message for the merge commit.

        Returns:
            A dict with:
            - ``merged``: whether the merge was successful (bool)
            - ``message``: status message from the API
            - ``sha``: SHA of the merge commit (if merged)
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if merge failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not isinstance(pull_number, int) or pull_number < 1:
            return {"error": "Invalid pull request number"}
        if merge_method not in ("merge", "squash", "rebase"):
            return {"error": f"Invalid merge method '{merge_method}'. Choose 'merge', 'squash', or 'rebase'."}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "Pull request merging via GitLab is not yet supported"}

        payload: dict[str, Any] = {
            "merge_method": merge_method,
        }
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        result = await _github_request(
            f"/repos/{repo}/pulls/{pull_number}/merge",
            _resolve_github_token(),
            method="PUT",
            json_data=payload,
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", {})
        return {
            "merged": data.get("merged", False),
            "message": data.get("message", ""),
            "sha": data.get("sha", ""),
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    # ------------------------------------------------------------------
    @tool
    async def add_issue_labels(repo: str, issue_number: int, labels: list[str]) -> dict:
        """Add (replace) labels on an issue or pull request in a GitHub repository.

        **Note**: This **replaces** all existing labels with the provided list.
        To add labels incrementally, include the existing labels you want to keep.

        Note: Label management is currently only supported for GitHub.
        GitLab support may be added in a future update.

        Args:
            repo: Repository in "owner/name" format.
            issue_number: The issue or pull request number.
            labels: List of label names to set (e.g., ["bug", "urgent"]).
                    This replaces all existing labels.

        Returns:
            A dict with:
            - ``issue_number``: the issue/PR number
            - ``labels``: the final list of labels on the issue
            - ``html_url``: URL to the issue
            - ``rate_limit_remaining``: remaining API requests
            - ``error``: error message if failed
        """
        if not repo or not repo.strip():
            return {"error": "No repository provided"}
        if not isinstance(issue_number, int) or issue_number < 1:
            return {"error": "Invalid issue number"}
        if not labels or not isinstance(labels, list) or len(labels) == 0:
            return {"error": "No labels provided. Provide a list of at least one label."}
        if not _check_repo_allowed(repo, allowed_repos):
            return {"error": f"Repository '{repo}' is not in the allowed list"}

        if is_gitlab:
            return {"error": "Label management via GitLab is not yet supported"}

        result = await _github_request(
            f"/repos/{repo}/issues/{issue_number}/labels",
            _resolve_github_token(),
            method="POST",
            json_data={"labels": labels},
            timeout=timeout,
            api_base=api_base,
        )

        if "error" in result:
            return result

        data = result.get("data", [])
        if isinstance(data, dict):
            data = [data]

        final_labels = [
            lbl.get("name", lbl) if isinstance(lbl, dict) else lbl
            for lbl in data
        ]

        return {
            "issue_number": issue_number,
            "labels": final_labels,
            "html_url": f"{api_base.rstrip('/')}/repos/{repo}/issues/{issue_number}",
            "rate_limit_remaining": result.get("rate_limit_remaining", "unknown"),
        }

    return [
        search_code,
        list_issues,
        get_file_content,
        list_pull_requests,
        create_issue,
        create_pull_request,
        comment_on_issue,
        create_or_update_file,
        merge_pull_request,
        add_issue_labels,
    ]

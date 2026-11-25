# Dependency review — backend Python packages (2026-11-25)

> **Status:** implemented in branch `chore/deps-2026-11` (see PR). Every direct
> requirement was checked against PyPI and the upgrade set was validated with the
> unit, integration and migration suites; the numbers are recorded below.

Scope: every direct requirement in [`backend/requirements.txt`](../backend/requirements.txt).

## Method / evidence

| Step | What was done | Result |
| --- | --- | --- |
| 1 | PyPI JSON API: latest release + `requires_python` for every direct dep | 46 direct deps checked |
| 2 | Newest release installable on **Python 3.11** (Docker/CI image) | nothing has to be dropped; see "Python version" |
| 3 | Throwaway venv (py3.12) with **all deps at latest**, MAF family unchanged ("candidate A") | full resolution, no conflicts |
| 4 | Same venv with **agent-framework 1.26.0 + openai 3.59** ("candidate B") | full resolution, no conflicts |
| 5 | Unit suite (`-m unit`, 1 336 tests) on baseline vs candidate A vs candidate B | candidate A/B at parity |
| 6 | Integration suite (`integration and not e2e/live_api/slow`, 1 303 tests) baseline vs candidate A | identical outcome |
| 7 | Migration tests (`tests/test_migrations.py`, alembic 1.20 + SQLAlchemy 2.1) | 17 passed |
| 8 | Live/smoke probes: passlib 1.9.5 + bcrypt 5 (incl. verifying hashes from the old stack), croniter 7, a2a-sdk 1.3 API, weasyprint PDF render, markitdown conversion, **edgartools 6 against the live SEC API** | all OK |

Test results (services = local docker-compose stack, `127.0.0.1`, same env for all runs):

| Run | Unit (1 336) | Integration (1 303) |
| --- | --- | --- |
| Baseline (current stack) | 1 330 passed, 4 failed | 1 294 passed, 1 failed, 4 skipped, 4 xpassed |
| Candidate A (all latest, MAF unchanged) | 1 330 passed, 4 failed | 1 294 passed, 1 failed, 4 skipped, 4 xpassed |
| Candidate B (A + MAF 1.26) | 1 330 passed, 4 failed | not run (needs live model e2e) |

The 4 failures are **pre-existing on the unmodified stack** and fail identically
before and after the upgrade:

- `test_agent_budgets.py::TestStreamingTokenBudget::test_normal_short_run_unchanged`
- `test_agent_runner.py::TestRunAgentStream::test_stream_exhaustion_ends_normally`
- `test_agent_runner.py::TestPersistAssistantMessage::test_temp_path_preserves_order`
- `test_ollama_client.py::test_build_custom_endpoint_client_defaults_placeholders_and_requires_url`

→ The upgrade set introduces **zero regressions** in unit + integration + migration tests.

## What changed

### Versions (all latest as of 2026-11-25)

| Package | was | now |
| --- | --- | --- |
| fastapi | 0.136.1 | 0.165.0 (no longer capped — see MAF below) |
| uvicorn | 0.46.0 | 0.54.0 |
| sqlalchemy | 2.0.49 | 2.1.9 |
| pymysql | 1.1.3 | 1.2.4 |
| alembic | 1.18.4 | 1.20.1 |
| cryptography | 48.0.0 | 50.0.2 |
| passlib | 1.7.4 | 1.9.5 |
| bcrypt | 4.0.1 (`<4.1` pin) | 5.0.0 (pin dropped) |
| redis | 7.1.1 | 8.2.0 |
| boto3 | 1.43.6 | 1.44.11 |
| sse-starlette | 3.4.4 | 3.7.1 |
| a2a-sdk | 1.1.0 | 1.3.1 |
| ddgs | 9.14.2 | 9.16.1 |
| pydantic-settings | 2.14.1 | 2.16.3 |
| python-multipart | 0.0.28 | 0.0.34 |
| markitdown | 0.1.5 | 0.1.9 |
| pandas | 3.0.3 | 3.1.1 |
| mammoth | 1.12.0 | 1.13.0 |
| pdfplumber | 0.11.9 | 0.11.11 |
| pdfminer-six | 20251230 | 20260107 |
| pypdfium2 | 5.8.0 | 5.15.2 |
| feedparser | 6.0.12 | 6.0.14 |
| slowapi | 0.1.9 | 0.1.10 |
| weasyprint | 68.1 | 70.0 |
| markdown | 3.10.2 | 3.11.3 |
| playwright | 1.59.0 | 1.65.2 |
| aioimaplib | 2.0.1 | 2.0.3 |
| msal | 1.36.0 | 1.39.0 |
| yfinance | 1.3.0 | 1.8.0 |
| numpy | 2.4.4 | 2.5.5 (py3.11 image resolves 2.4.6) |
| scipy | 1.17.1 | 1.18.1 (py3.11 image resolves 1.17.1) |
| beautifulsoup4 | 4.14.3 | 4.15.0 |
| croniter | 6.2.4 | 7.0.0 |
| edgartools | 5.31.1 | 6.4.0 |
| pytest | 8.4.2 | 9.2.0 |
| pytest-asyncio | 0.26.0 | 1.5.0 |
| agent-framework(-core) | 1.19.0 | 1.26.0 |
| openai | `>=2.25,<3` | `>=2.25,<4` |

Already current, unchanged: `aiomysql` 0.3.2, `httpx` 0.28.1, `python-jose` 3.5.0,
`openpyxl` 3.1.5, `python-pptx` 1.0.2, `html2text` 2025.4.15, `pytest-cov` 7.1.0.

### bcrypt / passlib

`bcrypt<4.1` existed because passlib 1.7.4 reads `bcrypt.__about__`, removed in
bcrypt 4.1. Verified empirically:

- `passlib 1.9.5 + bcrypt 5.0.0`: `hash()` / `verify()` OK.
- A hash produced by the **old** stack (`passlib 1.7.4 + bcrypt 4.0.1`) verifies
  with the new stack and `needs_update()` is `False` → **no password reset or
  rehash migration needed**.

### agent-framework: 1.19.0 → 1.26.0, and `[all]` → core + plugins

- MAF is pinned on purpose (`[BREAKING]` changes inside 1.x minors). Bump core
  and the provider plugins together and re-run the suites.
- The aggregate `agent-framework==1.19.0` package installs
  `agent-framework-core[all]` — ~35 plugins (azure, foundry, ag-ui, chatkit,
  devui, declarative, …) of which the app imports only
  `agent_framework.openai` and `agent_framework.anthropic`. The aggregate was
  replaced by:

  ```
  agent-framework-core==1.26.0
  agent-framework-openai>=1.18.2,<2
  agent-framework-anthropic>=1.0.0b261112,<2
  mcp>=2.2.0,<3        # MCP tools; core imports it lazily
  tenacity>=9.1.4,<10  # used by core's MCP client path
  ```

- Side effect: `agent-framework-ag-ui` (a `core[all]` plugin) declares
  `fastapi<0.142` (at MAF 1.26: `<0.163`). With it gone, FastAPI resolves to
  0.165.0.
- `openai`'s old `<3` cap was stale: the dev venv already ran 3.19.2 and the deep
  type path protected by that comment (`openai.types.chat.
  chat_completion_message_tool_call`) exists in 3.x. MAF declares `openai<4`.

### pytest 9 / pytest-asyncio 1.5

pytest 9 + pytest-asyncio 0.26 is **broken** (57 collection errors) and
pytest-asyncio 1.x removed the `event_loop` fixture, defaulting to
function-scoped loops — which breaks the import-time engine ("Future attached to
a different loop", 28 tests). Fixed in-tree by:

```ini
asyncio_default_test_loop_scope = session
asyncio_default_fixture_loop_scope = session
```

plus deleting the deprecated session `event_loop` fixture from
[`tests/conftest.py`](../backend/tests/conftest.py).

### Declared-but-missing / duplicate requirements

- `pytz` — imported by `scheduled_task_service.py` and `propose_schedule.py`
  inside `except ImportError: return None`; only ever present transitively via
  yfinance/mem0ai. Now declared (if it disappeared, scheduled tasks would
  silently get `next_run = None`).
- `json-repair` — imported by `agents/stabilizer.py` with a regex fallback, but
  not installed anywhere, so the fallback was always used. Now declared.
- Removed duplicate `httpx` (×2) and `pytest` (×2) entries, the contradictory
  `pytest-asyncio>=0.21,<1.0` cap and the duplicate `pytest-cov` floor.
- `pytest-xdist` was declared but missing from the dev venv (CI installs it).

## Known gaps / follow-ups

1. **Live-model e2e not run here.** Unit tests mock every model call, so the MAF
   bump needs a live smoke test (chat + tool call + workflow) before/after merge.
   The MAF bump is its own commit, so it can be reverted independently.
2. weasyprint, edgartools, yfinance and the docs tooling are **mocked** in tests;
   they were checked with real smoke probes (PDF render, live SEC filing read) —
   still worth one manual PDF + one SEC read after deploy.
3. Python version drift: Docker/CI run 3.11 while the dev venv is 3.12, and most
   requirements are unpinned floors, so numpy/scipy (and pandas) resolve to
   different versions. A constraints file would make the image reproducible.
4. Out of scope (deliberate): migrating `python-jose` → PyJWT (used in 15+ test
   files and `src/core/dependencies.py`), and `pytz` → stdlib `zoneinfo`.

## Rollout

1. This PR: version bumps, MAF 1.26 + core/plugins, pytest config, requirements
   clean-up. Rebuild the image (playwright browser binaries are version-locked:
   the Dockerfile's `playwright install chromium` handles it).
2. Merge after your live e2e pass; if MAF 1.26 misbehaves, revert only the MAF
   commit and keep the rest.
3. Follow-ups: PyJWT migration, `zoneinfo`, constraints file.

# Contributing to PH Agent Hub

First off, thanks for taking the time to contribute! 🎉

This document outlines the workflow for contributing to PH Agent Hub. It's a living guide — if something is missing, feel free to open an issue or PR to improve it.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [What to Work On](#what-to-work-on)
- [Project Overview](#project-overview)
- [Project Structure](#project-structure)
- [Development Environment](#development-environment)
- [Making Changes](#making-changes)
- [Coding Conventions](#coding-conventions)
- [Testing](#testing)
- [Documentation](#documentation)
- [Pull Request Process](#pull-request-process)
- [License](#license)

---

## Code of Conduct

This project is governed by the [Contributor Covenant](https://www.contributor-covenant.org/). By participating, you are expected to uphold this code. Report unacceptable behavior to the maintainers.

---

## What to Work On

- **Open issues** labelled [`good first issue`](https://github.com/kainotomo/ph-agent-hub/labels/good%20first%20issue) are great starting points.
- **Feature requests** and **bug reports** are always welcome — check the [issues page](https://github.com/kainotomo/ph-agent-hub/issues) first to avoid duplication.
- If you're unsure, open a [discussion](https://github.com/kainotomo/ph-agent-hub/discussions) or a draft issue to get feedback before investing a lot of time.

---

## Project Overview

PH Agent Hub is a multi-tenant AI application platform built on **FastAPI + React** with the **Microsoft Agent Framework** for agent runtime orchestration.

Key features:
- Dual UI: Chat Area for end users + Admin Area for operators
- Multi-tenant isolation at the auth, data, and governance layers
- Model & tool governance per tenant
- Embeddable chat widget for external websites
- Docker Compose-based deployment (dev and prod)

Read the [README](../README.md) for a quick intro and the [architecture overview](../docs/architecture-overview.md) for deeper context.

---

## Project Structure

```
ph-agent-hub/
├── backend/           # FastAPI Python application
│   ├── src/
│   │   ├── agents/    # Agent runtime, registry, stabilizer
│   │   ├── api/       # API route handlers (auth, chat, admin, etc.)
│   │   ├── core/      # Config, security, dependencies
│   │   ├── db/        # Database models and migrations
│   │   ├── models/    # AI provider interfaces
│   │   ├── services/  # Business logic layer
│   │   └── tools/     # Tool implementations
│   ├── scripts/       # Utility scripts (seed, license generation)
│   └── tests/         # Backend test suite (pytest)
├── frontend/          # React + TypeScript application
│   ├── src/
│   │   ├── app/       # App-level components and routing
│   │   ├── features/  # Feature modules (chat, admin, etc.)
│   │   ├── providers/ # React context providers
│   │   ├── services/  # API client services
│   │   └── shared/    # Shared UI components and utilities
│   └── public/        # Static assets and embed widget
├── docs/              # Documentation
│   └── planning/      # Architecture decision records
├── infrastructure/    # Docker Compose, nginx config
└── .github/           # Issue templates, PR template, contributing guide
```

See the [detailed backend architecture](../docs/backend-architecture.md) and [frontend architecture](../docs/frontend-architecture.md) for more depth.

---

## Development Environment

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)
- [Git](https://git-scm.com/)

### Quick Start (3 Commands)

```bash
git clone https://github.com/kainotomo/ph-agent-hub.git
cd ph-agent-hub/infrastructure
cp env.example env
```

Then edit the `env` file to set the required values (see [README](../README.md#run-in-3-commands) for the full list). Start the stack:

```bash
docker compose up --build
```

The backend has **hot reload** enabled — changes to `backend/src/` are picked up automatically without a Docker rebuild.

### Backend Virtual Environment (for IDE support)

If you want IDE features (autocomplete, type checking, linting) for the backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend Development

The frontend runs on port 3000 inside the Docker stack. To run it standalone:

```bash
cd frontend
npm install
npm run dev
```

---

## Making Changes

### Branching

1. Fork the repository (if you're not a maintainer).
2. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```
   Use a descriptive branch name: `feat/`, `fix/`, `docs/`, `refactor/`, `chore/` prefixes are helpful.
3. Make your changes and commit them with clear commit messages.
4. Push your branch and open a pull request.

### Commit Messages

Write clear, conventional commit messages:

```
feat: add tenant-scoped rate limiting
fix: resolve session timeout on long-running requests
docs: update deployment guide for production SSL
refactor: extract embed widget into standalone module
```

---

## Coding Conventions

There are no strict linter configurations checked into the repo yet, so please be consistent with the existing code.

### Backend (Python)

- **Type hints**: Use type annotations for all function signatures (the codebase is fully typed).
- **Docstrings**: Use triple-quoted docstrings for modules, classes, and public functions.
- **Imports**: Standard library → third-party → local, grouped alphabetically.
- **Async**: The backend uses `async/await` throughout — keep it consistent.
- **Naming**: `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_CASE` for constants.

### Frontend (TypeScript/React)

- **TypeScript strict mode** is enabled — avoid `any` types where possible.
- **Functional components** with hooks (no class components).
- **Naming**: `camelCase` for functions and variables, `PascalCase` for components and types, `kebab-case` for file names.
- **Imports**: Organize by external → internal, with a blank line between groups.
- **CSS**: Inline styles and Ant Design tokens are preferred (no separate CSS files).

When in doubt, look at how existing code in the same module is written and match that style.

---

## Testing

### Backend

Tests are in the `backend/tests/` directory and use **pytest**. Run them locally with Docker Compose running (MariaDB + Redis available on localhost):

```bash
cd backend
source .venv/bin/activate

# Required env vars (use 127.0.0.1, not Docker hostnames, when running from host)
DATABASE_URL="mysql+aiomysql://phagent:${MYSQL_PASSWORD}@127.0.0.1:3306/phagent_hub?charset=utf8mb4" \
  REDIS_URL="redis://127.0.0.1:6379/0" \
  JWT_SECRET="test" \
  ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  python -m pytest tests/ -m "not e2e"
```

Or, if you run inside the Docker backend container (no env var hassle):

```bash
docker compose exec backend pytest /app/tests/ -m "not e2e"
```

Run a subset by marker:

```bash
pytest tests/ -m unit                          # pure logic only
pytest tests/ -m "not e2e and not slow"        # CI-equivalent (default)
pytest tests/ -v --co                         # verbose with coverage report
```

When adding or modifying backend code:
- Add or update tests for the changed functionality.
- If your code calls an external service (API, embedding provider, MinIO, etc.), **mock it** at the boundary — look at existing `@patch` patterns in `tests/test_rag_service.py`.
- No real API keys are needed for tests to pass.
- Ensure existing tests still pass before opening a PR — CI enforces this.

### Frontend

The frontend currently has no formal test framework configured. When contributing frontend changes:
- Manually verify the UI works in the dev environment.
- Ensure the project builds without errors: `npm run build`.

### CI Pipeline

A GitHub Actions workflow runs on every pull request to `main`:

| Job | What it validates |
|-----|------------------|
| **Backend Tests** | `pytest tests/ -m "not e2e and not slow"` with coverage |
| **Frontend Build** | `tsc` type-check + `vite build` |

CI uses MariaDB and Redis as service containers — no Docker Compose or API keys needed. See `.github/workflows/ci.yml` for details.

---

## Documentation

The `docs/` directory contains role-based documentation (user, admin, developer). When you make changes:

- Update relevant documentation if your change affects behaviour, configuration, or the API.
- Add or update architecture decision records in `docs/planning/` for significant decisions.
- Keep the README up to date for user-facing changes.

---

## Pull Request Process

1. Ensure your branch is up to date with `main`.
2. Fill out the [pull request template](./PULL_REQUEST_TEMPLATE.md) when opening the PR.
3. Link the relevant issue in the PR description (e.g., "Closes #123").
4. Make sure CI passes (once configured) and there are no merge conflicts.
5. Request a review from a maintainer.
6. Address review feedback with additional commits — they will be squashed on merge.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](../LICENSE) that covers this project.

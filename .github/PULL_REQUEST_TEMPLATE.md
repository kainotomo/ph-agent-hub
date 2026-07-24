## Description

<!-- Provide a clear and concise summary of the changes. What problem does this solve? -->

## Related Issue

<!-- Link the issue this PR addresses using "Closes #N" or "Fixes #N". -->

Closes #

## Type of Change

<!-- Mark the relevant option(s) with an x. -->

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring / Code style cleanup
- [ ] Infrastructure / CI / Build
- [ ] Other (please describe):

## Testing

<!-- Describe the testing you performed to verify your changes. -->

- [ ] Tested locally with Docker Compose (dev)
- [ ] Backend tests pass (`pytest backend/tests/ -m "not e2e and not slow"`)
- [ ] Frontend builds without errors (`npm run build`)
- [ ] Manual testing completed (describe what you tested)

## Checklist

<!-- Confirm your changes meet these standards. -->

- [ ] My code follows the coding conventions of this project
- [ ] I have self-reviewed my own code
- [ ] I have commented complex code sections, particularly in hard-to-understand areas
- [ ] I have updated the documentation (`docs/`) if my change affects user-facing behaviour or configuration
- [ ] My changes generate no new warnings or errors
- [ ] New and existing tests pass locally
- [ ] **Migration safety** (if adding/modifying a migration):
  - [ ] Migration has a valid downgrade path (not just `pass`)
  - [ ] Migration is idempotent (safe to run multiple times)
  - [ ] If ENUM change: checked table size; planned maintenance window if large
  - [ ] If DROP COLUMN/TABLE: confirmed no production data loss
  - [ ] If data backfill: tested on staging copy of production data
  - [ ] `alembic upgrade heads` tested locally (DAG has a single head)
  - [ ] `alembic downgrade -1` tested locally (rollback works)

## Screenshots (if applicable)

<!-- Add screenshots to help explain your changes, especially for UI changes. -->

## Additional Notes

<!-- Any other information reviewers should know? -->

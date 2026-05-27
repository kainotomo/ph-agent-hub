---
name: Bug Report
description: Report a reproducible bug or unexpected behaviour
title: "[Bug]: "
labels: ["bug"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to fill out this bug report! Please provide as much detail as possible to help us reproduce and fix the issue.

  - type: textarea
    id: description
    attributes:
      label: Description
      description: A clear and concise description of the bug.
      placeholder: What happened? What did you expect to happen?
    validations:
      required: true

  - type: textarea
    id: reproduction
    attributes:
      label: Steps to Reproduce
      description: Steps to reproduce the behaviour.
      placeholder: |
        1. Go to '...'
        2. Click on '...'
        3. Scroll down to '...'
        4. See error
    validations:
      required: true

  - type: textarea
    id: expected
    attributes:
      label: Expected Behaviour
      description: What did you expect to happen instead?
    validations:
      required: true

  - type: textarea
    id: actual
    attributes:
      label: Actual Behaviour
      description: What actually happened? Include screenshots or screen recordings if applicable.
    validations:
      required: true

  - type: textarea
    id: logs
    attributes:
      label: Relevant Logs & Screenshots
      description: Paste any relevant logs, error messages, or screenshots. Logs are often available via `docker compose logs backend` in the infrastructure directory.
      placeholder: Paste logs, stack traces, or drag and drop screenshots here.
    validations:
      required: false

  - type: dropdown
    id: deployment
    attributes:
      label: Deployment Method
      description: How are you running PH Agent Hub?
      options:
        - Docker Compose (dev)
        - Docker Compose (prod)
        - Other
    validations:
      required: true

  - type: input
    id: version
    attributes:
      label: Version / Commit
      description: What version or commit hash are you running? You can find the version in the Admin panel or by running `git log --oneline -1`.
      placeholder: e.g., v1.12.0 or abc1234
    validations:
      required: false

  - type: input
    id: browser
    attributes:
      label: Browser (if frontend issue)
      description: Which browser and version were you using?
      placeholder: e.g., Chrome 125, Firefox 127
    validations:
      required: false

  - type: textarea
    id: context
    attributes:
      label: Additional Context
      description: Any other relevant information (e.g., environment variables, custom configuration, network setup).
    validations:
      required: false

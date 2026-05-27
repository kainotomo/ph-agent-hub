---
name: Feature Request
description: Suggest an idea or improvement for PH Agent Hub
title: "[Feature]: "
labels: ["enhancement"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        Thanks for suggesting a feature! Please check the [existing issues](https://github.com/kainotomo/ph-agent-hub/issues) and [discussions](https://github.com/kainotomo/ph-agent-hub/discussions) first to see if your idea has already been proposed.

  - type: textarea
    id: problem
    attributes:
      label: Problem Statement
      description: A clear and concise description of what problem this feature would solve.
      placeholder: I'm always frustrated when...
    validations:
      required: true

  - type: textarea
    id: solution
    attributes:
      label: Proposed Solution
      description: A clear and concise description of what you want to happen. Be as specific as possible.
      placeholder: A new page/API endpoint/configuration option that...
    validations:
      required: true

  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives Considered
      description: What alternative solutions or workarounds have you considered?
    validations:
      required: false

  - type: dropdown
    id: area
    attributes:
      label: Affected Area
      description: Which part of the project would this affect?
      options:
        - Backend (API / Agents / Services)
        - Frontend (Chat UI / Admin UI)
        - Documentation
        - Infrastructure / Deployment
        - Embed Widget
        - Other
    validations:
      required: true

  - type: textarea
    id: context
    attributes:
      label: Additional Context
      description: Add any other context, mockups, or references that help explain the feature.
    validations:
      required: false

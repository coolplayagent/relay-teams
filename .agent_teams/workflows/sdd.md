---
workflow_id: sdd
name: Standard Delivery Workflow
version: 1.0.0
description: Standard software delivery workflow for specification, design, implementation, and verification.
is_default: true
selection_hints:
  - build
  - code
  - implement
  - feature
  - api
  - service
  - app
tasks:
  - task_name: spec
    role_id: spec_spec
    objective_template: 'Input: user requirement "{objective}". Output: a structured requirement specification with clear goals, scope, and acceptance criteria.'
    depends_on: []
  - task_name: design
    role_id: spec_design
    objective_template: 'Input: spec.md from previous stage for "{objective}". Output: an implementation-ready technical design describing architecture, interfaces, and testing.'
    depends_on:
      - spec
  - task_name: code
    role_id: spec_coder
    objective_template: 'Input: design.md from previous stage for "{objective}". Output: code changes and tests that implement the approved design.'
    depends_on:
      - design
  - task_name: verify
    role_id: spec_verify
    objective_template: 'Input: implementation output and design artifacts for "{objective}". Output: a verification verdict (PASS/FAIL) with concrete findings and coverage gaps.'
    depends_on:
      - code
---
Use this workflow for standard software delivery tasks that require staged decomposition.

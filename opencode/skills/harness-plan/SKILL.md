---
name: harness-plan
description: >
  Investigate the project and define an implementation plan with measurable
  acceptance criteria before writing any code. Triggered at the start of any
  implement/build/create/develop task. Read-only — does not edit files.
---

# Plan and Acceptance Criteria

This skill covers two phases: Investigation (Fase 1) and Planning (Fase 2).
Both are read-only. No code is written yet.

---

## Fase 1: Investigation

Understand the project before proposing anything.

1. List the project root directory.
2. Read configuration files: `package.json`, `pyproject.toml`, `requirements.txt`,
   `Cargo.toml`, `AGENTS.md`, `README.md` — whatever exists.
3. Read the files relevant to the task (existing modules, components, routes).
4. Identify:
   - Language and framework.
   - Project conventions: naming, folder structure, code style.
    - Existing dependencies.
    - Test framework and commands (from `package.json` scripts, `Makefile`, etc.).
    - Whether a configured LSP is available for the project's languages. If it
      is unavailable, record `UNAVAILABLE` and continue without installing or
      starting a server.

Do not present this investigation to the user — use it internally.

---

## Fase 2: Plan and Acceptance Criteria

Define WHAT will be built and HOW success will be measured.

1. Create an implementation plan:
   - Files to create or modify.
    - Technical approach and rationale.
    - New dependencies if any.
    - LSP status and whether diagnostics are advisory or blocking.

2. Define **measurable acceptance criteria**. Every criterion must be
   answerable with YES or NO. Good examples:
   - "Server starts without errors: `npm run dev`"
   - "Function `get_user()` returns an object with `name` and `email` fields"
   - "Existing tests still pass: `npm test`"
   - "No linting errors: `npm run lint`"
   - "The page renders without exceptions in the browser"
   - "The endpoint `/api/users` returns 200 with a JSON array"
   - "If LSP is available, modified files have zero severity-error diagnostics;
     otherwise the result is recorded as UNAVAILABLE and does not block"

3. **PRESENT the plan and criteria to the user and WAIT for explicit approval**
   before any code is written.

Output of this phase: an approved plan with clear, verifiable acceptance criteria.

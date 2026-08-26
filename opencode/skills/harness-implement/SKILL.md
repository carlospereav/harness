---
name: harness-implement
description: >
  Write the code that fulfills an approved plan. Follow project conventions,
  preserve existing functionality, and flag any plan deviation to the user.
  Triggered after the user approves a plan from `harness-plan`.
---

# Implementation

Write the code that fulfills the approved plan.

**Prerequisite:** The plan and acceptance criteria must have been approved by
the user in the previous phase (`harness-plan`). If no plan is approved, go
back and load `harness-plan` first.

---

## Rules

1. **Follow project conventions** identified during investigation.
2. **Maintain documentation:** docstrings, type annotations where the project
   uses them.
3. **Do not break existing functionality.** Read affected files before modifying
   them.
4. **If the plan needs significant changes** discovered during implementation,
   STOP and inform the user before continuing.
5. **Write tests** if the project has a test framework and the plan requires them.
6. **Use existing dependencies** before adding new ones. New dependencies
   should already be in the approved plan.
7. After editing, use the optional `harness-lsp` skill to refresh diagnostics for
   modified files. Severity-error diagnostics are blocking only when LSP is
   available and the approved plan enabled the criterion. Never install or
   manually start an LSP server. Use only a trusted user-level server or one
   explicitly approved by the user for this workspace.

---

## After Implementation

Document these for the evaluation phase:
- Files created or modified.
- Verification commands that apply (test, lint, build).
- LSP status (`AVAILABLE` or `UNAVAILABLE`) and any blocking diagnostics.

Do NOT present the code to the user yet. The evaluation phase (`harness-evaluate`)
comes next.

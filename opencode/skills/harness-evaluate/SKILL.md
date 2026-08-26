---
name: harness-evaluate
description: >
  Verify the implementation against the acceptance criteria defined in the plan.
  Spawns the read-only `evaluator` subagent to run verifications. On failure,
  returns to `harness-implement` for the failing items (max 3 iterations).
  Does not edit files.
---

# Evaluation

Verify that every acceptance criterion from the approved plan is met, then
decide whether to proceed or retry.

---

## Step 1: Delegate to the `evaluator` subagent

Use the Task tool to invoke the `evaluator` subagent. Pass these in the task
description:

1. The **exact acceptance criteria** from the approved plan.
2. The **files modified or created** during implementation.
3. The **relevant verification commands** (test, lint, build — from
   `package.json`, `AGENTS.md`, or detected during investigation).
4. The LSP status and exact LSP criterion from the approved plan, if one was
   enabled.

The `evaluator` subagent is **read-only** (`edit: deny`). It can run
verification commands and read files, but cannot modify anything. This
guarantees an impartial evaluation.

---

## Step 2: Interpret the result

The evaluator returns a structured rubric: each criterion marked

- ✅ PASS (criterion met, with evidence)
- ❌ FAIL (criterion not met, with specific reason)
- ⚠️ UNVERIFIABLE (no way to check, with explanation)

For an enabled LSP criterion, mark it PASS only when the server is available
and modified files have no severity-error diagnostics. Mark it UNVERIFIABLE
when LSP is unavailable unless the approved plan explicitly made LSP required.
Warnings do not fail the criterion unless the plan says otherwise.

Decision:

- **All ✅:** pass to `harness-security-review`.
- **Any ❌:** return to `harness-implement` to fix **only** what failed.
  Then re-run this evaluation from Step 1.
- **Max 3 iterations total.** On the 3rd failure, STOP and present the
  criteria that could not be met along with specific reasons to the user.

---

## About the evaluator subagent

Configured as `mode: subagent`, `hidden: true`, `edit: deny`. It cannot
commit, push, delete, or edit files. Bash is limited to read-only commands.
This is a structural improvement over having the same agent evaluate itself:
the evaluator cannot fix code or tests to make them pass.

# Harness Workflow (Global)

When the user asks to implement, build, develop or create a feature, functionality,
component, module, endpoint or page, follow the harness protocol:

1. Load `harness-plan` skill: investigate project, define a plan with explicit
   measurable acceptance criteria. Present the plan to the user and WAIT for
   explicit approval.
2. Load `harness-implement` skill: implement the approved plan.
3. Load `harness-evaluate` skill: spawn the `evaluator` subagent to verify every
   criterion. If any criterion fails, loop back to step 2 for only the failing
   items (max 3 iterations total).
4. Load `harness-security-review` skill: spawn the `security-auditor` subagent
   to audit `git diff HEAD` for vulnerabilities. If HIGH findings, fix and re-run
   both evaluation and security review.
5. Present certification summary and proceed to `git commit` + `git push`.

For trivial changes (typos, color tweaks, single-line comments, formatting fixes),
skip steps 1-2 but still run `harness-evaluate` and `harness-security-review`
before committing.

NEVER present code to the user without passing evaluation and security review.
If the user explicitly asks to skip a phase, confirm with them before doing so.

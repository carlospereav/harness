---
description: Run full harness loop - plan, implement, evaluate, security review, commit, push
agent: build
---

Run the harness workflow for this task: $ARGUMENTS

## Protocol (execute every phase in order)

### Phase 1: Plan
Load the `harness-plan` skill and follow it:
1. Investigate the project structure and conventions.
2. Define a concrete implementation plan with measurable acceptance criteria.
3. Present the plan to the user and WAIT for explicit approval.

### Phase 2: Implement
After approval, load the `harness-implement` skill and implement exactly what
the plan specifies. Follow project conventions. Do not break existing code.

### Phase 3: Evaluate (with retry loop)
Load the `harness-evaluate` skill which spawns the `evaluator` subagent.
- All criteria pass -> proceed to Phase 4.
- Any criterion fails -> return to Phase 2 to fix ONLY what failed.
  Re-run evaluation. Max 3 iterations total. On the 3rd failure, stop
  and report the remaining issues to the user.

### Phase 4: Security Review
Load the `harness-security-review` skill which spawns the `security-auditor`
subagent.
- HIGH findings -> return to Phase 2 to fix. Re-run Phase 3 + Phase 4.
- MEDIUM findings -> warn user, proceed if they accept.
- CLEAN -> proceed to Phase 5.

### Phase 5: Certify and Deliver
Present the certification summary, then:
1. git add the modified files
2. git commit with a descriptive message
3. git push

The system will prompt you to approve git commit and git push.

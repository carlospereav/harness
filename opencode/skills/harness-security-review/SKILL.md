---
name: harness-security-review
description: >
  Audit the git diff for security vulnerabilities before commit and push.
  Spawns the read-only `security-auditor` subagent. If HIGH findings are
  detected, return to `harness-implement` to fix them. If clean, proceed
  to `git commit` and `git push`. Does not edit files.
---

# Security Review

Audit all pending changes for vulnerabilities before committing.

---

## Step 1: Delegate to the `security-auditor` subagent

Use the Task tool to invoke the `security-auditor` subagent. This subagent:

1. Runs `git diff HEAD` (or `git diff --cached` for repos with no commits).
2. Analyzes every added line (`+`) for vulnerabilities.
3. Returns a risk-classified report.

The `security-auditor` subagent is **read-only** (`edit: deny`) — it cannot
modify files, only analyze. This guarantees an independent audit.

---

## Step 2: Interpret the report

- **HIGH findings:** do NOT commit. Return to `harness-implement` to fix the
  vulnerabilities. Then re-run evaluation (`harness-evaluate`) and this
  security review.
- **MEDIUM findings:** warn the user clearly. Proceed only if they accept.
- **CLEAN:** proceed to Step 3.

---

## Step 3: Commit and Push

If security review is clean (or MEDIUM findings accepted by the user):

1. Present a certification summary:
   ```
   ✅ IMPLEMENTATION CERTIFIED
   ============================
   Summary: <what was done>
   Acceptance Criteria: all passed
   Security: reviewed — clean
   Files: <list with descriptions>
   ```

2. Stage the changes: `git add <modified/created files>`
3. Commit: `git commit -m "<descriptive message>"`
4. Push: `git push`

Your global opencode config has `git commit *: ask` and `git push *: ask`,
so the system will prompt you to approve each. Review the commit message
and files before approving.

---

## Categories audited

- **Credentials & Secrets:** hardcoded keys, tokens, passwords, patterns
  (`sk-`, `ghp_`, `AKIA`, `Bearer`, etc.).
- **Injection:** unparameterized SQL, `eval`/`exec` with user input, XSS.
- **Weak Cryptography:** MD5/SHA1 for passwords, deprecated algorithms,
  hardcoded keys.
- **Data Exposure:** sensitive data in logs, CORS `*`, unauthenticated endpoints.

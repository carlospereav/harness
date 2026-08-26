---
name: harness-lsp
description: >
  Use the project's available Language Server Protocol integration for code
  navigation and diagnostics. LSP is optional, read-only, and never installs
  or starts a server on its own.
---

# LSP Integration

Use LSP as an optional source of precise project context and post-edit
validation. Prefer the native LSP capability exposed by OpenCode. Do not
implement an LSP client in the harness and do not substitute an MCP server
unless the project explicitly requires it.

## Operating rules

1. Detect whether an LSP server is already configured and available for the
   relevant language. Use it only when it is on an explicitly trusted
   user-level list or the user has approved that server for this workspace.
   Do not install packages or launch a server manually.
2. If LSP is unavailable, report `UNAVAILABLE` and continue with the normal
   workflow. Never infer that the code is correct from missing diagnostics.
3. Keep queries scoped to the current workspace and affected files.
4. During investigation, use document symbols, definitions, references, and
   hover information to resolve unfamiliar code before making assumptions.
5. After edits, request diagnostics for modified files when the capability is
   available.

## Diagnostic policy

Classify results by the LSP severity field:

- `error`: blocking when it affects a modified file.
- `warning`: report but do not block by default.
- `information` and `hint`: retain only as context.
- no server or no response: `UNAVAILABLE`, not a pass.

Only diagnostics produced for the current workspace and current file version
count. Do not treat stale diagnostics as implementation failures.

## Trust boundary

LSP servers are local programs with workspace access. A project configuration
alone never grants permission to use one. If the server is not trusted, report
`UNAVAILABLE` and ask the user to approve the exact server and command before
using it. Do not forward workspace contents or diagnostics to remote services.

## Workflow hooks

- `harness-plan`: use navigation to improve repository understanding and record
  whether LSP is available.
- `harness-implement`: refresh diagnostics after edits and fix blocking errors
  before handing off.
- `harness-evaluate`: verify the LSP criterion when it was enabled by the plan;
  otherwise mark it optional and rely on the project's normal tests.
- `harness-security-review`: remains independent. LSP output does not replace
  review of the git diff.

## Output format

When reporting LSP status, use this compact form:

```text
LSP: AVAILABLE | UNAVAILABLE
Diagnostics: X errors, Y warnings, Z informational
Blocking files: <paths or none>
```

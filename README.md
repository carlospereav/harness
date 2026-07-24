# Harness — Global Development Harness for OpenCode

Collection of skills and agents that enforce a rigorous development workflow:
plan, implement, evaluate (with retry-loop), security review, and certified
commit+push. Works across **all projects** on your machine.

## How it works

The harness is stored in this repo (`opencode/`) and synced to your global
opencode config (`~/.config/opencode/`) via a PowerShell script.

Once synced, opencode detects the skills, agents, and commands globally.
When you type `/harness <task>` in any project, opencode runs the full
workflow automatically.

## Installation

```powershell
cd harness
.\sync.ps1 -DryRun     # preview what will change
.\sync.ps1             # install to ~/.config/opencode/
```

Restart opencode after syncing.

## What gets installed

| Path | Purpose |
|---|---|
| `AGENTS.md` | Always-on global rule: route implement tasks through harness |
| `agents/harness.md` | Primary agent with baked-in permissions (task allowlist, destructive deny, commit/push ask). Structural enforcement of the protocol. |
| `agents/evaluator.md` | Hidden subagent, `edit: deny`, `task: deny`: impartial verification of criteria |
| `agents/security-auditor.md` | Hidden subagent, `edit: deny`, `task: deny`: vulnerability audit before commit |
| `skills/harness-plan/` | Phase 1+2: investigate project + define plan with acceptance criteria |
| `skills/harness-implement/` | Phase 3: implement the approved plan |
| `skills/harness-evaluate/` | Phase 4: evaluate via read-only `evaluator` subagent; retry up to 3x |
| `skills/harness-security-review/` | Phase 5: audit git diff via read-only `security-auditor` subagent; commit+push |
| `commands/harness.md` | `/harness <task>` command that kicks off the full loop (`agent: harness`) |

## Workflow

```
/harness "add pagination to /users"
  → harness-plan (investigate + define criteria, WAIT for approval)
  → harness-implement (write code)
  → harness-evaluate → @evaluator (verify rubric)
       ❌ fail → back to implement (max 3 iterations)
  → harness-security-review → @security-auditor (audit diff)
       HIGH finding → back to implement
  → git commit (user approves via ask gate)
  → git push   (user approves via ask gate)
```

## Prerequisites

The harness agent bakes in all required permissions (task allowlist, git gates,
destructive deny). No manual `opencode.jsonc` configuration is needed for
commit/push approval — it's enforced structurally by the agent.

The sync script **never** touches your `opencode.jsonc`.

## Optional: different models per role

The evaluator and security-auditor subagents inherit your primary model by
default. To use a faster/cheaper model for reviews, uncomment and set the
`model` field in:

- `opencode/agents/evaluator.md`
- `opencode/agents/security-auditor.md`

Example: `model: anthropic/claude-haiku-4-20250514`

Run `sync.ps1` again after editing.

## Structure

```
harness/
├── opencode/                         # source of truth (mirrors ~/.config/opencode/)
│   ├── AGENTS.md
│   ├── agents/
│   │   ├── harness.md
│   │   ├── evaluator.md
│   │   └── security-auditor.md
│   ├── commands/
│   │   └── harness.md
│   └── skills/
│       ├── harness-plan/SKILL.md
│       ├── harness-implement/SKILL.md
│       ├── harness-evaluate/SKILL.md
│       └── harness-security-review/SKILL.md
├── sync.ps1
└── README.md
```

## Adding new skills or agents

1. Create the file under `opencode/skills/<name>/SKILL.md` or
   `opencode/agents/<name>.md`.
2. Run `.\sync.ps1` to install globally.
3. Commit to version them.

## Removing a skill or agent

Delete the file from this repo and commit. Then manually delete the
corresponding file from `~/.config/opencode/skills/<name>/` or
`~/.config/opencode/agents/<name>.md`. The sync script is additive only
(it overwrites but does not delete stale files from the target).

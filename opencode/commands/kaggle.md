---
description: Create, edit and push a PRIVATE Kaggle notebook from opencode (no GitHub leak)
agent: harness
---

Load the `kaggle-notebook` skill and perform this Kaggle notebook task:

$ARGUMENTS

Follow the skill's recommended workflow: scaffold a new private notebook (or
edit an existing one), write the requested Python code into the notebook's
`code.py` under the workspace (~/kaggle-workspace), then push it to Kaggle as
a PRIVATE kernel. Never commit notebook code or credentials to git. Use
`--dry-run` to validate push commands without hitting the Kaggle API.
---
description: Run the universal Kaggle competition harness (5-node pipeline, ML or GenAI, file or notebook submission)
agent: harness
---

Load the `kaggle-competition` skill and perform this Kaggle competition task:

$ARGUMENTS

Follow the skill's recommended workflow: `init` the competition (scaffolds a
PRIVATE notebook with `competition_sources` + the global `competition_state.json`),
choose `--mode ml|genai` and `--submission auto|file|notebook`, then `run` the
5-node pipeline (DataIngestion -> DataProcessing -> Experimentation ->
Evaluation -> DeploymentSync). Generate Python that prints a
`#METRIC:<name>=<float>` marker so the harness can parse `best_local_score`.
Never commit notebook code or credentials to git. Use `--dry-run` to validate
push/submit commands without hitting the Kaggle API.

Before implementation, follow the plan phase: investigate the competition,
run `plan <comp>`, fill `plan.md`, present the plan to the user, and **WAIT for
explicit approval**. The user may switch models while waiting. After approval,
record it with `plan <comp> --approve` and run the pipeline with
`run <comp> --require-plan`; do not edit `code.py` or run implementation before
that approval. Once `plan.md` exists, an unapproved or changed plan blocks even
plain `run`; use `--allow-unplanned` only for an explicit legacy-recovery
bypass.

Competition workspace (notebook code, state, data) lives OUTSIDE any git repo
under `$KAGGLE_WORKSPACE/competitions/<comp>/` (default
`~/kaggle-workspace/competitions/`).

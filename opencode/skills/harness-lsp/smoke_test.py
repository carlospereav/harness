"""Offline smoke checks for the declarative LSP integration."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def check_skill_contract() -> None:
    text = (ROOT / "skills" / "harness-lsp" / "SKILL.md").read_text(encoding="utf-8")
    required = (
        "UNAVAILABLE",
        "Do not install packages",
        "explicitly trusted",
        "severity field",
        "harness-security-review",
    )
    missing = [needle for needle in required if needle not in text]
    assert not missing, f"missing LSP contract terms: {missing}"


def check_workflow_hooks() -> None:
    plan = (ROOT / "skills" / "harness-plan" / "SKILL.md").read_text(encoding="utf-8")
    implement = (ROOT / "skills" / "harness-implement" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    evaluate = (ROOT / "skills" / "harness-evaluate" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "configured LSP" in plan
    assert "harness-lsp" in implement
    assert "LSP status" in evaluate


if __name__ == "__main__":
    check_skill_contract()
    check_workflow_hooks()
    print("LSP smoke tests passed")

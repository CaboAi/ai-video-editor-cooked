---
type: llm
focus: trace
---
PASS if Claude writes three scenarios with expected behaviour, checks the draft (skill_maker.py check), tests it in a fresh agent against each scenario and reports pass or fail per scenario.
FAIL if it declares the skill done without testing it, or names it with "claude" in it.

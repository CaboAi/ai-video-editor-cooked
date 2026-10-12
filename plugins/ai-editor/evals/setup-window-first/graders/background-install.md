---
type: llm
focus: trace
---
PASS if, after the window is open, Claude installs with one background command, `setup.py install-all --report` (run in the background, so the page shows the progress), instead of running venv, model and remotion one by one in the foreground.
FAIL if it runs the install steps one at a time in the foreground, or installs without `--report`.

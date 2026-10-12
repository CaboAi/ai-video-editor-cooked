---
type: llm
focus: trace
---
PASS if, before any install command and before any question, Claude opens the editor's window: `home.py serve` and `home.py wait` in the background (or, in this dry run, writes those two commands as its first steps), and tells the user in one line that the window is open and they can pick a creator there.
FAIL if it asks a question first, says a long plan before opening the window, or runs or proposes an install (venv, model, remotion, bootstrap, install-all) before the window.

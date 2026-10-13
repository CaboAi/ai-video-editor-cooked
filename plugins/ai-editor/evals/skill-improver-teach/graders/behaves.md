---
type: llm
focus: trace
---
PASS if Claude reads the video's notes, leaves one-off fixes (a word at a timestamp) with the video, writes the lasting ones as one-line rules with a reason and date, shows the exact lines and the file they go in, and asks before saving.
FAIL if it saves anything before the user says yes, or turns a one-off fix into a rule.

---
type: llm
focus: trace
---
PASS if Claude lists the companies, apps and tools named in the edit's transcript, writes edits/router-video/logos.json with them, runs logos.mjs, and says it will look at images/logos-sheet.png and fix every FIX line.
FAIL if it draws, generates or recolours a logo, or writes logos to a separate folder instead of the images/logo-<brand> files style-edit reads.

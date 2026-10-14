# Cloud renders: Modal and the GitHub CLI

Moved out of SKILL.md step 6. AWS Lambda is in `lambda.md`.

## Modal

Say these three things first:

- **What it is:** Modal (modal.com) rents computers by the second. The video is split into pieces,
  each piece renders on its own machine at the same time, and the finished video comes back here.
  The laptop stays free while it runs.
- **What it costs:** Modal's pricing page says the free Starter plan includes **$30 of free credit
  a month**. The whole 3.4-minute sample take, rendered uncut, cost **$0.18** on the bill; its
  44.9-second styled cut about **$0.03**. A 60-second video comes to about **$0.06** and a 10-minute
  video about **$0.54** (estimates), so the credit covers about fifty 10-minute videos a month. Every render measures the speed and the next quote uses it.
- **The card:** sign-up is free with GitHub, Google or SSO. Third-party listings say Modal may ask for a
  card before giving the whole $30; the sign-up page shows the current terms.

Then one step. The best place is the editor's window: **Settings, Modal, Connect Modal** (also on the
first-run keys page). It needs no Claude. From the chat, run the same thing in the background:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" modal-connect
```

It prints `STEP` lines as it goes:

1. Installs Modal into the editor's Python (skipped when it is there).
2. Runs `modal setup`, which opens the browser on Modal's "Authorize Modal" page: log in or sign up with
   GitHub, Google or SSO. Modal's CLI saves the token in `~/.modal.toml` itself; nothing here reads or
   prints it. No browser? It prints `LOGIN <url>`: give the user that link.
3. Builds the render machine in their Modal account once (Node, the renderer, Chrome; about 3 to 5 minutes)
   and caches it, so the first real render starts in seconds.
4. Prints `CREDIT` (what this month has cost so far, from `modal billing summary`), then `Connected.`

On failure the last line is `FAIL <one plain sentence>`: not logged in, out of credit, the render machine did
not build, or no connection. Say that sentence. `setup.py modal-test` renders a 5-second test video on Modal and
prints `TIME <seconds> <usd>` (the window's **Test render** button).

Doctor shows `ok modal` once it is logged in. The style-edit skill quotes the time and cost before
every Modal render.

## The GitHub CLI (free cloud renders on GitHub Actions)

Only when the user picks **Other: GitHub Actions** at render time. Doctor shows `gh` as optional.

1. Install it: Mac `brew install gh`, Windows `winget install --id GitHub.CLI` (then a new
   terminal), Linux: follow github.com/cli/cli/blob/trunk/docs/install_linux.md.
2. A free GitHub account (github.com/signup).
3. In their own terminal, not this chat: `gh auth login --web`. Pick GitHub.com and HTTPS, and say
   yes to logging in git with it. It opens the browser. Check: `gh auth status` says logged in.

The footage goes in a private repo in their account. Only they can see it.

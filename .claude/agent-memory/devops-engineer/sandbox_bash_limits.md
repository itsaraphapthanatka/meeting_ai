---
name: sandbox-bash-limits
description: worktree-isolation Bash sandbox refuses some inline PYTHONPATH forms; cmd.exe DOES work via `cmd //c` in Git Bash — don't conclude a check is unprovable too early
metadata:
  type: feedback
---

In this agent's worktree-isolated Bash tool, some otherwise-ordinary shell constructs are
refused outright with an error like "this command runs ... inside a construct too complex to
verify, so what it runs cannot be shown not to be git. Refusing to run it". Observed triggers:

- `VAR=value command ...` prefix form when `command` is `python`/`cmd` and `VAR` is
  `PYTHONPATH` (e.g. `PYTHONPATH="$PWD" python -m meeting_ai --help`).
- `export PYTHONPATH="$PWD"` followed by a command in the same or later call, when the value
  involves a variable like `$PWD` rather than a literal.
- ~~Direct `cmd /c "some.cmd --help"` invocation~~ — **wrong, corrected 2026-09-17.** This works:
  `cmd //c ".\mai.cmd --help"` ran fine and printed the Thai usage text. In Git Bash a single
  `/c` is mangled by MSYS path conversion into a Windows path, so the failure looked like a
  sandbox refusal when it was an argument-quoting problem. Use `//c`.

**Why:** the harness cannot statically prove these constructs don't smuggle in a git
write/destructive command, so it refuses rather than risk it — this is a blanket heuristic, not
specific to this repo.

**How to apply:** try the plain unprefixed form first (`./mai --help` works unprefixed), and for
Windows launchers use `cmd //c "..."` with the doubled slash. Reporting "unprovable here" is the
right move only after the invocation itself has been ruled out as the problem — declaring it too
early costs real coverage: the one run that *was* attempted here immediately exposed that bare
`mai.cmd --help` fails whenever `NoDefaultCurrentDirectoryInExePath=1`, which code review alone
had not caught. See [[meeting-ai-ci-workflow-facts]].

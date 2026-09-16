---
name: windows-python-tmp-paths
description: native Windows python (not Git Bash) does not understand /tmp or /c/... paths — use C:/... or the scratchpad dir
metadata:
  type: feedback
---

On this machine, `python` invoked from Git Bash is the native Windows interpreter. It silently fails to write to Git-Bash-style paths: `open('/tmp/x')` writes somewhere under the process's actual CWD/drive root (not Git Bash's `/tmp`), and `open('/c/Users/.../x')` raises `FileNotFoundError` outright (no drive-letter-less absolute path support).

**Why:** cost me two failed attempts (a curl `--data-binary @file` test) before I switched to a `C:/...` path for a file that both `python -c` and Git Bash `curl`/`ls` needed to agree on.

**How to apply:** When a python one-liner and a bash command both need to touch the same file, use a `C:/...`-style absolute path (e.g. the harness's provided scratchpad dir, converted: `/c/Users/x/y` → `C:/Users/x/y`). Bash tools (`ls`, `curl`, `cat`) accept both `/c/...` and `C:/...` forms; native `python.exe` only reliably accepts `C:/...` (or backslash) forms.

---
name: git-bash-curl-utf8-body
description: curl -d '<thai text>' from Git Bash mangles UTF-8 bytes before they reach the server (decode error), giving a false "server rejects Thai body" reading — write the JSON to a file with printf/heredoc and use --data-binary @file instead.
metadata:
  type: feedback
---

Sending `curl -X POST ... -d '{"title":"ทดสอบไม่มีช่องว่าง"}'` from this Git Bash produced a server-side `'utf-8' codec can't decode byte 0xb7 ... invalid start byte` 400 — not a real bug, just the shell mangling the multibyte literal on the way into curl's argv.

**Why:** confirmed by rewriting the same JSON as literal UTF-8 bytes via `printf '\xNN...'` into a temp file and posting with `--data-binary @file`, which round-tripped correctly (`meeting_ai/web/server.py`'s `_create_draft` happily stores Thai titles with no spaces).

**How to apply:** whenever testing Thai (or other multibyte) request bodies from this shell, always build the payload as a file (`printf` with explicit `\xNN` escapes, or a heredoc written with the Write tool) and send it with `curl --data-binary @file`, never `-d '<literal Thai text>'`. If you see a UTF-8 decode error from the server on a Thai-text test, suspect the shell first, reproduce with a file-based body before reporting it as a product bug.

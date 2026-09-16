---
description: Run the security-engineer for a full or scoped application security audit
argument-hint: [server|clients|secrets|money|all]  (default: all)
---
Run the **security-engineer** subagent (or `general-purpose` adopting `.claude/agents/security-engineer.md`) with scope: $ARGUMENTS (empty means all).

Before relaying, verify its top 3 findings yourself at the cited file:line. Relay the report verbatim in Thai, mark which findings you confirmed, and list the tickets that should be opened (`/fix <finding>`).

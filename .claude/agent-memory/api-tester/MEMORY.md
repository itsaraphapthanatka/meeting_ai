# Memory index

- [S3/R2 env isolation](env_isolation_s3.md) — .env has live Cloudflare R2 creds; clear S3_* env vars before starting ANY local test server or upload-url leaks real presigned URLs.
- [whisper-cli now installed](whisper_now_installed.md) — local STT actually works on this machine now; older "not installed" notes are stale, use real speech audio for happy-path process tests.
- [Windows python /tmp paths](windows_tmp_paths.md) — native python.exe from Git Bash needs C:/... paths, not /tmp or /c/... — use the scratchpad dir in C:/ form.

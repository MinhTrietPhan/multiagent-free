---
name: code-review
description: Review code for correctness, security, maintainability, and test coverage.
---

# Code review workflow

1. Identify the changed files and understand their callers before judging an implementation.
2. Prioritize correctness bugs, security issues, data loss risks, and broken compatibility.
3. Check input validation, path handling, error handling, and resource cleanup.
4. Check whether tests cover the changed behavior and important edge cases.
5. Report findings by severity and cite exact file paths and line numbers when available.
6. Do not invent findings. If no issue is found, state what was inspected and what remains unverified.

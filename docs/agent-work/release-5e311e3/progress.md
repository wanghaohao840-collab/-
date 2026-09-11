# Progress

## 2026-09-11 — Recovery fix checkpoint

- `deploy/learning_containers.py` was untracked along with the broader release tooling. Commit its standalone observation implementation and tests so the DNS helper is usable without pulling in unfinished release orchestration.
- Compatibility proves the original digest by varying only null/empty representations of Dns, DnsOptions and DnsSearch. Saved receipts and normal hashes remain unchanged; nonempty DNS and unrelated configuration changes are rejected.
- `venv/Scripts/python.exe -m pytest -q tests/deploy/test_learning_containers.py --basetemp=.pytest-tmp-dns-commit --tb=short`: 38 passed, exit 0.
- Recovery finish was independently verified in the preceding session; its operation-specific driver and receipts stay outside Git. Candidate build will use precisely 5e311e3, while current HEAD also contains later UI/startup fixes.

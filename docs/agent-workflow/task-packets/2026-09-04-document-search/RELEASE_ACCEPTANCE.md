# Search release acceptance — 2026-09-05

## Release and network recovery

- Safe updater report: `D:/python_self_agent/deploy-state/reports/update-20260905T004248Z.json`, succeeded; paired cold backup: `D:/python_self_agent_backups/daily/assistant-20260905T004522Z.tar.gz`.
- Clash configuration now persists `use_default_bypass: false` and the existing custom list plus `api.siliconflow.cn`. Windows effective ProxyOverride retains localhost/private-network defaults and all previous custom entries. The user saved the entry through the application UI after automation could not reliably focus its editor.
- Container no-auth request to `https://api.siliconflow.cn/v1/models` returned 401 with valid TLS. No TLS verification bypass, key change, model switch, or Docker restart was required for this final recovery.

## Fresh post-recovery verification

- `venv/Scripts/python.exe deploy/smoke_test.py --env-file deploy/.env --deep`: exit 0; App/Qdrant health, FastAPI/legacy config, Qdrant write/import, temporary document retrieval and real LLM answer all PASS.
- `Get-Content .worktrees/bge-m3-runtime-identity/.runtime/search-release-acceptance.py -Raw | docker exec -i -e PYTHONPATH=/app python_self_agent-app-1 python -u -`: exit 0. Real `BAAI/bge-m3`, 1024 dimensions; explicit document scope, cross-user denial, no-store, server-resolved source note, edited body, idempotency, QA scope API, deletion redaction, retained note body and deleted-scope denial all PASS.
- This fixture used its own temporary database, not the production account database. Successful fixture cleanup completed and production index registry hash remained unchanged.
- Earlier failed fixture `/tmp/zhiyan-search-acceptance-5ka_y1ag`: both imports terminal failed, zero notes, both exact temporary-user Qdrant namespaces counted zero. Removed only this resolved temporary directory after these assertions; no production SQL writes or vector deletes.
- Published `http://127.0.0.1:7860/search`: HTTP 200; assets `index-Cei1Qh7G.js` and `index-CNfQq2zz.css`. Unauthenticated POST `/api/v1/search`: HTTP 401.
- `git diff --check`: exit 0, existing LF/CRLF warnings only. App and Qdrant healthy; Neo4j not started.

## Remaining gate

These results close the network and real-service acceptance blockers. The subsequent mandatory FINAL_INTEGRATION_REVIEW.md is accepted and Packet 04 is done. No commit or push performed.

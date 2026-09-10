# BGE-M3 Journaled Cutover Plan

1. Persist a self-digesting per-migration cutover journal containing the exact
   previous and candidate registry records before changing either registry or
   provider configuration.
2. Apply the registry conditionally, support idempotent recovery across the
   replace/journal crash window, and refuse application RAG startup while any
   journal is incomplete.
3. Complete only after external configuration is verified. Permit rollback only
   before completion; post-activation rollback requires the separate no-new-data
   proof from the governing specification.
4. Add a deployment CLI and Windows controller in the next task to coordinate
   maintenance lock, backup, app stop, environment update, health and deep smoke.

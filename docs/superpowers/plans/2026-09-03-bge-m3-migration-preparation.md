# BGE-M3 Migration Preparation Plan

1. Run only under the external maintenance controller after writers stop and a
   cold backup succeeds.
2. Inventory the complete legacy Qdrant collection against application account
   and document ownership, repeatedly rescan it around candidate work, and use
   the existing resumable SQLite checkpoint to embed and publish a new physical
   collection.
3. Rescan the candidate, validate exact scope/content parity, bind the real
   bilingual quality report, and persist self-digesting migration evidence.
4. Do not write the registry or change provider configuration in this phase;
   activation and crash recovery are a separate journaled step.

The empty-deployment case is explicit and fully validated rather than treated
as a missing source. Non-empty sources use the same bounded path.

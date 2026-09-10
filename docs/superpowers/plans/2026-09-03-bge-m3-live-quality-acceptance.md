# BGE-M3 Live Quality Acceptance Plan

1. Make each language share a document so every query ranks ten competing
   chunks instead of trivially searching a one-chunk document.
2. Build isolated in-memory indexes with the same scoped-search contract for
   the old SimpleEmbedding baseline and candidate BGE-M3 runtime.
3. Enforce absolute Recall@5/MRR@5, zero isolation leaks and no regression from
   baseline, then write an exclusive, fsync-backed self-digesting audit report.
4. Unit-test competition, regression rejection, report integrity and refusal to
   overwrite evidence; then run the real provider only with the configured local
   env file and public/synthetic dataset.

No production index, registry, user content or provider setting is changed by
this plan.

# BGE-M3 Retrieval Quality Gate Plan

Goal: fixed 10 Chinese/10 English synthetic corpus and questions, scoped search evaluator for Recall@5, MRR@5 and isolation leaks. Require >=0.90, >=0.75, zero leaks and no regression versus explicit old-model baseline. Search implementation is injected so offline unit tests are deterministic; later live controller builds isolated temporary old/new indexes and supplies real search. No business data, model call, activation or cleanup in this unit.

Files: eval dataset/evaluator/tests and records/spec. Verify tests/evals, candidate tests, all Memory/RAG, compile/pip/diff. Serial inline already authorized.

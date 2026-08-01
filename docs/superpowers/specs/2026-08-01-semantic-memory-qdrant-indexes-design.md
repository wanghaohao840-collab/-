# SemanticMemory Qdrant Payload Index Design

## Goal

Ensure every `SemanticMemory` vector collection declares Qdrant payload
indexes for the fields used by its normal retrieval and cleanup filters:
`memory_type` and `user_id`.

## Design

`SemanticMemory` owns the business meaning of these payload fields, so it will
declare both indexes immediately after ensuring its vector collection:

```python
self.vector_store.ensure_payload_indexes(
    self.vector_collection,
    {"memory_type": "keyword", "user_id": "keyword"},
)
```

The existing `VectorStore` protocol remains unchanged. Its in-memory
implementation validates the declaration without persistence, while
`QdrantVectorStore` creates the actual Qdrant indexes idempotently.

## Data and isolation rules

- `memory_type` remains a keyword discriminator for semantic-memory cleanup
  and search.
- `user_id` remains a keyword tenant filter.
- No payload shape, collection name, vector dimension, or public API changes.
- Index ownership does not move into `QdrantConnectionManager`, because that
  infrastructure layer does not own SemanticMemory business fields.
- Episodic memory and RAG collection indexes are outside this change.

## Failure behavior

Index creation is part of SemanticMemory initialization. Backend failures
continue through the existing `VectorStore` error mapping instead of being
silently ignored, preventing startup with a partially prepared collection.

## Verification

- A recording in-memory store proves that initialization requests exactly the
  two keyword indexes on the configured collection.
- A live Qdrant test proves that constructing `SemanticMemory` creates
  `memory_type` and `user_id` keyword entries in the collection payload
  schema.
- Existing semantic-memory and Qdrant regression tests remain green.

## Non-goals

- Adding new vector-store interfaces.
- Retrofitting unrelated memory implementations.
- Adding numeric indexes for fields not used by current Qdrant filters.
- Changing Qdrant deployment, credentials, or collection migration policy.

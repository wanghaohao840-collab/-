"""Complete bounded Qdrant scans; counts alone are not a content snapshot."""
from hello_agents.memory.rag.source_records import SourceInventoryError, digest, qdrant_chunk, require_name
from hello_agents.memory.storage.vector_scan import native_point_id


class QdrantChunkSource:
    def __init__(self, store, *, collection, dimension, identity=None, page_size=128, max_pages=10_000):
        self.store = store
        self.collection = require_name(collection)
        if type(dimension) is not int or not 1 <= dimension <= 65536:
            raise SourceInventoryError("dimension")
        if identity and (identity.backend != "qdrant" or identity.physical_collection != collection
                         or identity.profile.dimension != dimension):
            raise SourceInventoryError("identity")
        self.dimension, self.identity = dimension, identity
        self.page_size, self.max_pages = page_size, max_pages
        self.completed_token = None
        self._running = False

    @property
    def contract(self):
        return {"backend": "qdrant", "collection": self.collection, "namespace": None,
                "dimension": self.dimension,
                "source_identity": self.identity.to_dict() if self.identity else None}

    def records(self):
        if self._running:
            raise SourceInventoryError("scan_busy")
        self._running = True
        self.completed_token = None
        try:
            self.store.require_collection(self.collection, self.dimension)
            before = self.store.count(self.collection)
            if type(before) is not int or before < 0:
                raise SourceInventoryError("count")
            count = 0
            pages = self.store.iter_scroll_pages(self.collection, page_size=self.page_size,
                                                max_pages=self.max_pages)
            try:
                for page in pages:
                    for record in page.records:
                        native_point_id(record.storage_id)
                        count += 1
                        yield qdrant_chunk(record, self.identity)
            finally:
                pages.close()
            after = self.store.count(self.collection)
            if type(after) is not int or before != count or after != count:
                raise SourceInventoryError("source_changed")
            self.completed_token = digest(["qdrant-count-v1", count])
        finally:
            self._running = False

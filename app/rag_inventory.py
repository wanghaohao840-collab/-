"""Application-authorized source inventory; no CLI or production activation."""
from pathlib import Path

from app.rag_authority import AppOwnershipSource, checked_path, namespace_user
from hello_agents.memory.rag.json_index_cache import versioned_cache_path
from hello_agents.memory.rag.source_inventory import InventorySummary, build_inventory
from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError


def build_app_inventory(data_root: Path | str, path: Path | str, *, source) -> InventorySummary:
    """Bind a complete source inventory to two matching authority scans.

    Caller must hold the deployment maintenance lock, stop writers and prepare
    a private inventories directory/ACL. This library confines paths but does
    not authorize a live scan or provision deployment permissions.
    JSON covers one explicit user cache; Qdrant covers the whole source
    collection. The later controller must enumerate ALL JSON user caches.
    """
    root = checked_path(data_root, directory=True)
    path = Path(path)
    parent = checked_path(root / "vector_indexes" / "rag" / "inventories", directory=True)
    if not path.is_absolute() or path.parent != parent or path.suffix != ".sqlite":
        raise SourceInventoryError("inventory_destination")
    checked_path(path, missing=True)
    if path.exists():
        raise SourceInventoryError("destination")
    if not isinstance(source, (JsonChunkSource, QdrantChunkSource)):
        raise SourceInventoryError("source")
    namespace = source.namespace if isinstance(source, JsonChunkSource) else None

    def check_source_path():
        if isinstance(source, JsonChunkSource):
            user_id = namespace_user(namespace)
            legacy = root / "users" / user_id / "rag" / "rag_cache.json"
            checked_path(legacy.parent, directory=True)
            expected = versioned_cache_path(legacy, source.identity, namespace) if source.identity else legacy
            if checked_path(source.path) != expected:
                raise SourceInventoryError("authority_source_path")

    check_source_path()
    owners = AppOwnershipSource(root, namespace=namespace)

    def verify_owners():
        if owners.completed_token is None:
            raise SourceInventoryError("incomplete_authority")
        check_source_path()
        checked_path(parent, directory=True)
        probe = AppOwnershipSource(root, namespace=namespace)
        for _ in probe.records():
            pass
        if probe.completed_token != owners.completed_token:
            raise SourceInventoryError("authority_changed")
        return owners.completed_token

    return build_inventory(path, source=source, owners=owners.records(), verify_owners=verify_owners)

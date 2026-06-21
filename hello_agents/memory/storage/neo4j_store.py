from typing import Dict, Any, List


class Neo4jGraphStore:
    def __init__(self, **kwargs):
        self.config = kwargs
        self.entities = {}
        self.relations = []

    def add_entity(self, entity_id: str, properties: Dict[str, Any]):
        self.entities[entity_id] = properties

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        properties: Dict[str, Any] = None
    ):
        self.relations.append({
            "source_id": source_id,
            "target_id": target_id,
            "relation_type": relation_type,
            "properties": properties or {}
        })

    def search_entities(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        results = []
        for entity_id, props in self.entities.items():
            text = str(props)
            if query in text:
                results.append({
                    "entity_id": entity_id,
                    "properties": props,
                    "similarity": 1.0
                })
        return results[:limit]
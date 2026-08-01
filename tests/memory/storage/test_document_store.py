import tempfile
import unittest
from pathlib import Path

from hello_agents.memory.storage.document_store import SQLiteDocumentStore


class SQLiteDocumentStoreTests(unittest.TestCase):
    def test_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.db"
            store = SQLiteDocumentStore(str(db_path))

            store.close()
            store.close()


if __name__ == "__main__":
    unittest.main()

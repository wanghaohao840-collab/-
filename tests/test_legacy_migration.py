from app.database import initialize_database
from app.auth import AuthService
from app.migration import LegacyMigrationService
from app.storage import UserStorage, write_json_atomic


def test_legacy_migration_scans_and_claims_user123_history_once(tmp_path):
    legacy_root = tmp_path / "legacy"
    memory_data = legacy_root / "memory_data"
    memory_data.mkdir(parents=True)
    write_json_atomic(
        memory_data / "learning_history_user123.json",
        {"documents": [{"document_id": "old"}], "questions": [], "notes": [{"note": "n"}]},
    )
    db_path = tmp_path / "app.db"
    initialize_database(db_path)
    user_id = AuthService(db_path).register("Alice", "correct horse battery").id
    storage = UserStorage(tmp_path / "data")
    service = LegacyMigrationService(db_path=db_path, storage=storage, legacy_root=legacy_root)

    manifest = service.scan()
    result = service.claim(user_id)

    assert manifest["history"]["exists"] is True
    assert result.status == "completed"
    assert (storage.user_paths(user_id).history).exists()
    assert service.claim(user_id).status == "completed"

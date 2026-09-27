import io
import json
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.object_store import ObjectIntegrityError, S3ObjectStore
from deploy.migrate_files_isolated import migrate_files


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    user = root / "users" / str(uuid4())
    (user / "documents").mkdir(parents=True)
    (user / "reports").mkdir()
    doc_id, report_id = str(uuid4()), str(uuid4())
    (user / "documents" / f"{doc_id}.txt").write_bytes(b"document bytes")
    (user / "reports" / f"{report_id}.md").write_bytes(b"report bytes")
    return root


@pytest.fixture
def case():
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    return client, S3ObjectStore(client, "test-bucket")


def _write_response(stub, item, content, version):
    stub.add_response("put_object", {"VersionId": version}, {
        "Bucket": "test-bucket", "Key": item["key"], "Body": content,
        "ContentLength": len(content), "Metadata": {"sha256": item["sha256"]}, "IfNoneMatch": "*",
    })
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(content), len(content))}, {
        "Bucket": "test-bucket", "Key": item["key"], "VersionId": version,
    })


def _target(store):
    return {"bucket": store.bucket, "endpoint": store.client.meta.endpoint_url}


def test_dry_run_scans_but_does_not_write_manifest_or_objects(source, case, tmp_path):
    client, store = case
    manifest = tmp_path / "evidence.json"
    with Stubber(client) as stub:
        result = migrate_files(source, store, "dry-run", manifest_path=manifest)
        assert result["count"] == 2 and result["uploaded"] == 0
        assert not manifest.exists()
        stub.assert_no_pending_responses()


def test_apply_saves_refs_and_replay_reads_explicit_versions_without_reupload(source, case, tmp_path):
    client, store = case
    manifest = tmp_path / "evidence.json"
    first_items = migrate_files(source, store, "dry-run", manifest_path=manifest)["files"]
    contents = {item["key"]: (source / "users" / item["user_id"] / item["kind"] / (item["artifact_id"] + item["suffix"])).read_bytes() for item in first_items}
    with Stubber(client) as stub:
        for i, item in enumerate(first_items):
            _write_response(stub, item, contents[item["key"]], f"version-{i}")
        result = migrate_files(source, store, "apply", manifest_path=manifest)
        assert result["uploaded"] == 2 and result["complete"]
        stub.assert_no_pending_responses()
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert all("version_id" in ref for ref in saved["completed"].values())
    with Stubber(client) as stub:
        for item in first_items:
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(contents[item["key"]]), len(contents[item["key"]]))}, {
                "Bucket": "test-bucket", "Key": item["key"], "VersionId": saved["completed"][item["key"]]["version_id"],
            })
        repeated = migrate_files(source, store, "apply", manifest_path=manifest)
        assert repeated["uploaded"] == 0
        stub.assert_no_pending_responses()


def test_verify_rejects_corrupt_saved_version(source, case, tmp_path):
    client, store = case
    manifest = tmp_path / "evidence.json"
    items = migrate_files(source, store, "dry-run", manifest_path=manifest)["files"]
    payload = {"schema_version": 1, "target": _target(store), "files": items, "completed": {
        item["key"]: {"key": item["key"], "sha256": item["sha256"], "size_bytes": item["size_bytes"], "version_id": "v1"} for item in items
    }}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with Stubber(client) as stub:
        for item in items:
            corrupt = b"x" * item["size_bytes"]
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(corrupt), len(corrupt))}, {
                "Bucket": "test-bucket", "Key": item["key"], "VersionId": "v1",
            })
            with pytest.raises(ObjectIntegrityError):
                migrate_files(source, store, "verify", manifest_path=manifest)
            break


@pytest.mark.parametrize("bad_name", ["unexpected.bin", f"{uuid4()}.exe", "not-a-uuid.txt"])
def test_scanner_rejects_unexpected_durable_file(source, case, tmp_path, bad_name):
    folder = next((source / "users").iterdir()) / "documents"
    (folder / bad_name).write_bytes(b"x")
    with pytest.raises(ValueError, match="unexpected durable file"):
        migrate_files(source, case[1], "dry-run", manifest_path=tmp_path / "m.json")


def test_scanner_rejects_symlinked_document_and_missing_users(source, case, tmp_path):
    folder = next((source / "users").iterdir()) / "documents"
    original = next(folder.iterdir())
    linked = folder / (str(uuid4()) + ".txt")
    try:
        linked.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink/reparse"):
        migrate_files(source, case[1], "dry-run", manifest_path=tmp_path / "m.json")
    linked.unlink()
    (source / "users").rename(source / "users-old")
    with pytest.raises(FileNotFoundError):
        migrate_files(source, case[1], "dry-run", manifest_path=tmp_path / "m.json")


def test_scanner_rejects_dangling_symlinked_durable_folder(source, case, tmp_path):
    user_dir = next((source / "users").iterdir())
    reports = user_dir / "reports"
    next(reports.iterdir()).unlink()
    reports.rmdir()
    try:
        (user_dir / "reports").symlink_to(user_dir / "missing-reports", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink/reparse"):
        migrate_files(source, case[1], "dry-run", manifest_path=tmp_path / "m.json")


def test_source_change_and_manifest_inside_source_are_rejected(source, case, tmp_path):
    with pytest.raises(ValueError, match="outside source_root"):
        migrate_files(source, case[1], "dry-run", manifest_path=source / "manifest.json")
    manifest = tmp_path / "m.json"
    initial = migrate_files(source, case[1], "dry-run", manifest_path=manifest)
    manifest.write_text(json.dumps({"schema_version": 1, "target": _target(case[1]), "files": initial["files"], "completed": {}}), encoding="utf-8")
    document = next((source / "users").glob("*/documents/*"))
    document.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source file list or hashes differ"):
        migrate_files(source, case[1], "apply", manifest_path=manifest)


def test_cross_user_manifest_reference_is_rejected_before_s3(source, case, tmp_path):
    client, store = case
    manifest = tmp_path / "m.json"
    items = migrate_files(source, store, "dry-run", manifest_path=manifest)["files"]
    item = items[0]
    payload = {"schema_version": 1, "target": _target(store), "files": items, "completed": {
        item["key"]: {"key": item["key"], "sha256": item["sha256"], "size_bytes": item["size_bytes"], "version_id": "v1"}
    }}
    payload["completed"][item["key"]]["key"] = item["key"].replace(item["user_id"], str(uuid4()))
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with Stubber(client):
        with pytest.raises(ValueError, match="invalid object reference"):
            migrate_files(source, store, "apply", manifest_path=manifest)


def test_manifest_cannot_resume_against_a_different_bucket(source, case, tmp_path):
    _, store = case
    manifest = tmp_path / "m.json"
    items = migrate_files(source, store, "dry-run", manifest_path=manifest)["files"]
    manifest.write_text(json.dumps({"schema_version": 1, "target": _target(store), "files": items, "completed": {}}), encoding="utf-8")
    other = S3ObjectStore(store.client, "other-bucket")
    with pytest.raises(ValueError, match="target bucket or endpoint differs"):
        migrate_files(source, other, "apply", manifest_path=manifest)


def test_apply_rejects_an_existing_manifest_lock_without_s3_calls(source, case, tmp_path):
    client, store = case
    manifest = tmp_path / "m.json"
    Path(str(manifest) + ".lock").write_text("controller=active", encoding="utf-8")
    with Stubber(client):
        with pytest.raises(RuntimeError, match="holds the manifest lock"):
            migrate_files(source, store, "apply", manifest_path=manifest)

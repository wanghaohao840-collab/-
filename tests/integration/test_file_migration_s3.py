"""Opt-in end-to-end migration against a disposable versioned S3 service."""

import os
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from app.object_store import S3ObjectStore
from deploy.migrate_files_isolated import migrate_files


@pytest.fixture
def store():
    endpoint = os.getenv("S3_TEST_ENDPOINT")
    if not endpoint:
        pytest.skip("S3_TEST_ENDPOINT must name a disposable test service")
    client = boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id=os.environ["S3_TEST_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_TEST_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}),
    )
    bucket = "cutover-test-" + uuid4().hex
    client.create_bucket(Bucket=bucket)
    try:
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        result = S3ObjectStore(client, bucket)
        result.check_ready()
        yield result
    finally:
        for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket):
            versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
            if versions:
                client.delete_objects(Bucket=bucket, Delete={"Objects": [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in versions
                ]})
        client.delete_bucket(Bucket=bucket)


def test_live_apply_resume_and_verify(store, tmp_path):
    source = tmp_path / "source"
    user_id, document_id, report_id = str(uuid4()), str(uuid4()), str(uuid4())
    (source / "users" / user_id / "documents").mkdir(parents=True)
    (source / "users" / user_id / "reports").mkdir()
    (source / "users" / user_id / "documents" / f"{document_id}.md").write_bytes(b"real s3 document")
    (source / "users" / user_id / "reports" / f"{report_id}.docx").write_bytes(b"real s3 report bytes")
    manifest = tmp_path / "evidence.json"
    first = migrate_files(source, store, "apply", manifest_path=manifest)
    repeated = migrate_files(source, store, "apply", manifest_path=manifest)
    verified = migrate_files(source, store, "verify", manifest_path=manifest)
    assert first["uploaded"] == 2
    assert repeated["uploaded"] == 0
    assert verified["verified"] == 2

"""Opt-in real S3 versioning and immutable artifact contract."""

import hashlib
import os
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config

from app.object_store import S3ObjectStore, artifact_key


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
        pages = client.get_paginator("list_object_versions").paginate(Bucket=bucket)
        for page in pages:
            versions = page.get("Versions", []) + page.get("DeleteMarkers", [])
            if versions:
                client.delete_objects(Bucket=bucket, Delete={"Objects": [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in versions
                ]})
        client.delete_bucket(Bucket=bucket)


def test_real_store_replay_isolation_and_prior_version_retention(store):
    user_id = str(uuid4())
    content = b"isolated artifact before application rollback"
    key = artifact_key(user_id, "reports", str(uuid4()), ".md", hashlib.sha256(content).hexdigest())
    first = store.put_immutable(user_id, key, content)
    repeated = store.put_immutable(user_id, key, content)
    assert first.created and not repeated.created
    assert first.ref == repeated.ref
    assert store.read_verified(user_id, first.ref) == content
    with pytest.raises(ValueError):
        store.read_verified(str(uuid4()), first.ref)

    # The actual service must retain the referenced bytes across later versions and deletion.
    later = store.client.put_object(Bucket=store.bucket, Key=key, Body=b"unpublished later bytes")
    assert later["VersionId"] != first.ref.version_id
    assert store.read_verified(user_id, first.ref) == content
    store.client.delete_object(Bucket=store.bucket, Key=key)
    assert store.read_verified(user_id, first.ref) == content

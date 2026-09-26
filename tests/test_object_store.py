import hashlib
import io
from uuid import uuid4

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.object_store import ObjectIntegrityError, ObjectRef, S3ObjectStore, artifact_key


@pytest.fixture
def case():
    user_id = str(uuid4())
    content = b"scoped document"
    digest = hashlib.sha256(content).hexdigest()
    key = artifact_key(user_id, "documents", str(uuid4()), ".txt", digest)
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    return user_id, content, key, digest, client, S3ObjectStore(client, "test-bucket")


def test_upload_is_conditional_and_reference_retains_version(case):
    user_id, content, key, digest, client, store = case
    with Stubber(client) as stub:
        stub.add_response("put_object", {"VersionId": "version-1"}, {
            "Bucket": "test-bucket", "Key": key, "Body": content,
            "ContentLength": len(content), "Metadata": {"sha256": digest}, "IfNoneMatch": "*",
        })
        result = store.put_immutable(user_id, key, content)
        assert result.created
        assert result.ref.version_id == "version-1"
        stub.assert_no_pending_responses()


def test_replay_reads_existing_bytes_before_reporting_success(case):
    user_id, content, key, digest, client, store = case
    with Stubber(client) as stub:
        stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
        stub.add_response("head_object", {"VersionId": "version-1"})
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(content), len(content))})
        result = store.put_immutable(user_id, key, content)
        assert not result.created
        assert result.ref.sha256 == digest
        stub.assert_no_pending_responses()


def test_corrupt_stored_bytes_are_rejected(case):
    user_id, content, key, digest, client, store = case
    ref = ObjectRef(key, digest, len(content), "version-1")
    with Stubber(client) as stub:
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"x" * len(content)), len(content))})
        with pytest.raises(ObjectIntegrityError, match="bytes do not match"):
            store.read_verified(user_id, ref)


def test_cross_user_and_wrong_content_are_rejected_before_s3(case):
    user_id, content, key, digest, client, store = case
    with Stubber(client):
        with pytest.raises(ValueError, match="outside the user scope"):
            store.put_immutable(str(uuid4()), key, content)
        with pytest.raises(ObjectIntegrityError, match="SHA-256"):
            store.put_immutable(user_id, key, b"different")
        with pytest.raises(ValueError):
            store.read_verified(str(uuid4()), ObjectRef(key, digest, len(content), "version-1"))


def test_versioning_is_required(case):
    *_, client, store = case
    with Stubber(client) as stub:
        stub.add_response("head_bucket", {})
        stub.add_response("get_bucket_versioning", {"Status": "Suspended"})
        with pytest.raises(RuntimeError, match="versioning must be enabled"):
            store.check_ready()


@pytest.mark.parametrize("suffix", [".exe", ".txt/../../secret", ".TXT"])
def test_keys_reject_unsupported_or_unnormalized_suffix(suffix):
    with pytest.raises(ValueError):
        artifact_key(str(uuid4()), "documents", str(uuid4()), suffix, "a" * 64)

"""Copy a stopped, immutable user-file snapshot into versioned object storage.

The local manifest is private migration evidence. It does not publish runtime
references. Run one controller against a stopped source tree at a time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from app.object_store import ObjectIntegrityError, ObjectRef, artifact_key


_SCHEMA = 1
_SUFFIXES = {"documents": {".pdf", ".txt", ".md", ".markdown", ".docx"}, "reports": {".md", ".docx"}}


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _check_directory_chain(path: Path) -> None:
    # lstat lexical components before resolve(), so a symlink cannot disappear
    # from the path being checked after resolution.
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError(f"symlink/reparse path is not allowed: {component}")
        if component == component.anchor:
            break


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _scan(source_root: Path) -> list[dict[str, Any]]:
    source_root = source_root.absolute()
    _check_directory_chain(source_root)
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source_root must be a directory")
    users = source_root / "users"
    _check_directory_chain(users)
    resolved_users = users.resolve(strict=True)
    if not resolved_users.is_dir() or not resolved_users.is_relative_to(root):
        raise ValueError("users directory must exist under source_root")
    entries: list[dict[str, Any]] = []
    for user_dir in sorted(users.iterdir(), key=lambda p: p.name):
        _check_directory_chain(user_dir)
        if not user_dir.is_dir() or not _canonical_uuid(user_dir.name):
            raise ValueError(f"unexpected user directory: {user_dir.name}")
        resolved_user = user_dir.resolve(strict=True)
        if not resolved_user.is_relative_to(root):
            raise ValueError("user path escapes source_root")
        for kind, suffixes in _SUFFIXES.items():
            folder = user_dir / kind
            if not os.path.lexists(folder):
                continue
            _check_directory_chain(folder)
            if not folder.is_dir() or not folder.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"invalid {kind} directory")
            for path in sorted(folder.iterdir(), key=lambda p: p.name):
                _check_directory_chain(path)
                if not path.is_file() or path.is_symlink():
                    raise ValueError(f"unexpected durable path: {path}")
                if path.resolve(strict=True).parent != folder.resolve(strict=True):
                    raise ValueError(f"file path escapes source tree: {path}")
                suffix = path.suffix
                artifact_id = path.name[:-len(suffix)] if suffix else ""
                if suffix not in suffixes or not _canonical_uuid(artifact_id):
                    raise ValueError(f"unexpected durable file: {path.name}")
                size, digest = _hash_file(path)
                entries.append({
                    "user_id": user_dir.name,
                    "kind": kind,
                    "artifact_id": artifact_id,
                    "key": artifact_key(user_dir.name, kind, artifact_id, suffix, digest),
                    "size_bytes": size,
                    "sha256": digest,
                    "suffix": suffix,
                })
    entries.sort(key=lambda item: (item["user_id"], item["kind"], item["artifact_id"]))
    return entries


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _target_identity(store: Any) -> dict[str, str | None]:
    client = getattr(store, "client", None)
    meta = getattr(client, "meta", None)
    endpoint = getattr(meta, "endpoint_url", None)
    if endpoint:
        parsed = urlsplit(endpoint)
        # Endpoint identity is useful for resume safety; userinfo, query strings,
        # and fragments are never part of the private evidence file.
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        endpoint = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return {
        "bucket": getattr(store, "bucket", None),
        "endpoint": endpoint,
    }


def _load_manifest(path: Path, files: list[dict[str, Any]], target: dict[str, str | None]) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("existing manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _SCHEMA:
        raise ValueError("unsupported manifest schema")
    if manifest.get("target") != target:
        raise ValueError("manifest target bucket or endpoint differs from configured store")
    if manifest.get("files") != files:
        raise ValueError("source file list or hashes differ from saved manifest")
    completed = manifest.get("completed")
    if not isinstance(completed, dict):
        raise ValueError("manifest completed references are invalid")
    by_key = {item["key"]: item for item in files}
    for key, ref_data in completed.items():
        item = by_key.get(key)
        if item is None or not isinstance(ref_data, dict):
            raise ValueError("manifest contains an unknown object reference")
        try:
            ref = ObjectRef(key=ref_data["key"], sha256=ref_data["sha256"], size_bytes=ref_data["size_bytes"], version_id=ref_data["version_id"])
            expected_user = item["user_id"]
            # Revalidate the tenant scope and ensure the saved immutable reference
            # exactly describes the scanned bytes.
            from app.object_store import _validate_key
            _validate_key(expected_user, ref.key)
            if ref.key != key or ref.sha256 != item["sha256"] or ref.size_bytes != item["size_bytes"] or not ref.version_id or ref.version_id == "null":
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("manifest contains an invalid object reference") from exc
    return manifest


def _ref_json(ref: ObjectRef) -> dict[str, Any]:
    return {"key": ref.key, "sha256": ref.sha256, "size_bytes": ref.size_bytes, "version_id": ref.version_id}


def _ensure_manifest_outside_source(source: Path, manifest: Path) -> None:
    source = source.absolute()
    manifest = manifest.absolute()
    source_resolved = source.resolve(strict=True)
    candidates = (
        manifest,
        manifest.with_name(manifest.name + ".lock"),
        manifest.with_name(manifest.name + f".{os.getpid()}.tmp"),
    )
    for candidate in candidates:
        # Check lexical containment first: a manifest symlink inside the frozen
        # source can resolve outside, while its lock and atomic-write temp remain
        # inside the source directory.
        if candidate == source or source in candidate.parents:
            raise ValueError("manifest_path must be outside source_root")
        resolved = candidate.resolve(strict=False)
        if resolved == source_resolved or source_resolved in resolved.parents:
            raise ValueError("manifest_path must be outside source_root")


def migrate_files(source_root: str | Path, store: Any, mode: str, *, manifest_path: str | Path) -> dict[str, Any]:
    if mode not in {"dry-run", "apply", "verify"}:
        raise ValueError("mode must be dry-run, apply, or verify")
    source = Path(source_root).absolute()
    manifest_file = Path(manifest_path).absolute()
    if ".." in source.parts or ".." in manifest_file.parts:
        raise ValueError("parent traversal is not allowed in migration paths")
    _ensure_manifest_outside_source(source, manifest_file)
    files = _scan(source)
    if mode == "dry-run":
        return {"mode": mode, "files": files, "count": len(files), "uploaded": 0}

    if mode == "verify":
        if not manifest_file.is_file():
            raise ValueError("verify requires an existing complete manifest")
        manifest = _load_manifest(manifest_file, files, _target_identity(store))
        if set(manifest["completed"]) != {item["key"] for item in files}:
            raise ValueError("verify requires a complete manifest")
        for item in files:
            saved = manifest["completed"][item["key"]]
            ref = ObjectRef(saved["key"], saved["sha256"], saved["size_bytes"], saved["version_id"])
            content = store.read_verified(item["user_id"], ref)
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ObjectIntegrityError("source and stored artifact digests differ")
        return {"mode": mode, "files": files, "count": len(files), "verified": len(files)}

    lock = manifest_file.with_name(manifest_file.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise RuntimeError("another migration controller holds the manifest lock") from exc
    try:
        if manifest_file.exists():
            target = _target_identity(store)
            manifest = _load_manifest(manifest_file, files, target)
            # Completed references are checked against their exact retained versions
            # before any new writes, preventing a resume over corrupted target data.
            for item in files:
                saved = manifest["completed"].get(item["key"])
                if saved:
                    ref = ObjectRef(saved["key"], saved["sha256"], saved["size_bytes"], saved["version_id"])
                    store.read_verified(item["user_id"], ref)
        else:
            manifest = {"schema_version": _SCHEMA, "target": _target_identity(store), "files": files, "completed": {}}
            _atomic_json(manifest_file, manifest)
        uploaded = 0
        for item in files:
            if item["key"] in manifest["completed"]:
                continue
            path = source / "users" / item["user_id"] / item["kind"] / (item["artifact_id"] + item["suffix"])
            size, digest = _hash_file(path)
            if size != item["size_bytes"] or digest != item["sha256"]:
                raise ValueError("source changed after manifest creation")
            content = path.read_bytes()
            if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("source changed while reading for upload")
            result = store.put_immutable(item["user_id"], item["key"], content)
            store.read_verified(item["user_id"], result.ref)
            manifest["completed"][item["key"]] = _ref_json(result.ref)
            _atomic_json(manifest_file, manifest)
            uploaded += 1
        return {"mode": mode, "files": files, "count": len(files), "uploaded": uploaded, "complete": True}
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root")
    parser.add_argument("mode", choices=("dry-run", "apply", "verify"))
    parser.add_argument("manifest_path")
    args = parser.parse_args(argv)
    from botocore.config import Config
    import boto3

    store = None
    if args.mode != "dry-run":
        endpoint = os.environ.get("CUTOVER_TEST_S3_ENDPOINT")
        bucket = os.environ.get("CUTOVER_TEST_S3_BUCKET")
        access = os.environ.get("CUTOVER_TEST_S3_ACCESS_KEY")
        secret = os.environ.get("CUTOVER_TEST_S3_SECRET_KEY")
        if not all((endpoint, bucket, access, secret)):
            parser.error("CUTOVER_TEST_S3_ENDPOINT, _BUCKET, _ACCESS_KEY, and _SECRET_KEY are required")
        client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", aws_access_key_id=access, aws_secret_access_key=secret, config=Config(s3={"addressing_style": "path"}))
        from app.object_store import S3ObjectStore
        store = S3ObjectStore(client, bucket)
        store.check_ready()
    result = migrate_files(args.source_root, store, args.mode, manifest_path=args.manifest_path)
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

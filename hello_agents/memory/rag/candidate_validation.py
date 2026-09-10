"""Persist exact source-to-candidate validation without activating it."""
from dataclasses import asdict, dataclass
import hashlib, json, os
from itertools import zip_longest
from pathlib import Path

from hello_agents.memory.rag.candidate_rebuild import CandidateSummary, inspect_candidate
from hello_agents.memory.rag.source_inventory import (
    InventorySummary, build_inventory, iter_partitions,
)
from hello_agents.memory.rag.source_records import SourceInventoryError, canonical, digest


@dataclass(frozen=True)
class CandidateValidation:
    candidate_fingerprint: str
    source_fingerprint: str
    target_fingerprint: str
    chunk_count: int
    partition_count: int
    content_digest: str
    scope_leaks: int
    fingerprint: str


def validate_candidate(path, *, candidate_path, candidate, source_inventory_path,
                       source_inventory, target_inventory_path, target_source,
                       verify_live_source):
    path=Path(path)
    if (not path.is_absolute() or path.exists() or not isinstance(candidate,CandidateSummary)
            or candidate.state!="published" or inspect_candidate(candidate_path,expected=candidate)!=candidate
            or verify_live_source()!=source_inventory):
        raise SourceInventoryError("candidate_validation")
    owners=((p.namespace,p.document_id,p.user_id) for p in
            iter_partitions(source_inventory_path,expected=source_inventory))
    target=build_inventory(target_inventory_path,source=target_source,owners=owners)
    content=hashlib.sha256(b"candidate-content-v1")
    sentinel=object()
    pairs=zip_longest(iter_partitions(source_inventory_path,expected=source_inventory),
                      iter_partitions(target_inventory_path,expected=target),fillvalue=sentinel)
    for left,right in pairs:
        if left is sentinel or right is sentinel: raise SourceInventoryError("candidate_scope")
        if ((left.namespace,left.document_id,left.user_id,left.count,left.content_bytes,left.content_digest)
                !=(right.namespace,right.document_id,right.user_id,right.count,right.content_bytes,right.content_digest)):
            raise SourceInventoryError("candidate_mismatch")
        content.update(digest([left.namespace,left.document_id,left.content_digest]).encode())
    if verify_live_source()!=source_inventory: raise SourceInventoryError("source_changed")
    body={"candidate_fingerprint":candidate.fingerprint,"source_fingerprint":source_inventory.fingerprint,
          "target_fingerprint":target.fingerprint,"chunk_count":target.count,
          "partition_count":target.partitions,"content_digest":content.hexdigest(),"scope_leaks":0}
    body["fingerprint"]=digest(["candidate-validation-v1",body])
    result=CandidateValidation(**body)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as stream:
        stream.write(canonical(asdict(result)));stream.flush();os.fsync(stream.fileno())
    return result


def inspect_validation(path, *, expected=None):
    try:
        raw=json.loads(Path(path).read_text(encoding="utf-8"));result=CandidateValidation(**raw)
        check=dict(raw);fingerprint=check.pop("fingerprint")
        if fingerprint!=digest(["candidate-validation-v1",check]) or result.scope_leaks!=0:
            raise ValueError()
        if expected is not None and result!=expected: raise ValueError()
        return result
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError):
        raise SourceInventoryError("candidate_validation") from None

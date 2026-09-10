import json
import pytest

from hello_agents.memory.rag.candidate_rebuild import (
    CandidateError, build_candidate, inspect_candidate, publish_json_candidate,
    publish_qdrant_candidate,
)
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.source_inventory import build_inventory, iter_chunks
from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.storage.vector_store import InMemoryVectorStore


def fixture(tmp_path, count=3):
    rows=[]
    for i in range(count):
        content=f"public chunk {i}"
        meta={"memory_id":f"doc_{i}","document_id":"doc","content":content,
              "rag_namespace":"pdf_user","chunk_index":i,"document_version":1,"page_number":i+1}
        rows.append({"id":f"doc_{i}","document_id":"doc","content":content,
                     "vector":[1.0,0.0],"metadata":meta})
    source_path=tmp_path/"source.json"
    source_path.write_text(json.dumps({"collection_name":"old","rag_namespace":"pdf_user",
      "dimension":2,"updated_at":"2026-09-03T00:00:00","chunk_count":count,"chunks":rows}),encoding="utf-8")
    inventory_path=tmp_path/"inventory.sqlite"
    summary=build_inventory(inventory_path,source=JsonChunkSource(source_path,collection="old",namespace="pdf_user",dimension=2),
      owners=[("pdf_user","doc","user")],verify_owners=lambda:"a"*64)
    runtime=build_rag_embedding({},backend="qdrant")
    identity=IndexIdentity("qdrant","candidate",runtime.profile)
    return inventory_path,summary,runtime,identity


def test_ordered_authority_chunks_and_resumable_candidate(tmp_path):
    inventory,summary,runtime,identity=fixture(tmp_path)
    assert [x.metadata["chunk_index"] for x in iter_chunks(inventory,expected=summary,require_authority=True)]==[0,1,2]
    checkpoint=tmp_path/"candidate.sqlite"
    result=build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
      identity=identity,embedding=runtime,verify_live_source=lambda:summary,batch_size=2)
    assert result.state=="embedded" and result.embedded_count==3
    assert inspect_candidate(checkpoint,expected=result)==result
    store=InMemoryVectorStore()
    published=publish_qdrant_candidate(checkpoint,expected=result,identity=identity,store=store,batch_size=2)
    assert published.state=="published" and published.published_count==3
    assert store.count(identity.physical_collection)==3
    points=store.scroll(identity.physical_collection,with_vectors=True)
    assert {p.payload["metadata"]["page_number"] for p in points}=={1,2,3}
    assert all(p.payload["metadata"]["embedding_fingerprint"]==identity.profile.fingerprint for p in points)
    assert all(p.payload["rag_namespace"]=="pdf_user" and p.payload["document_id"]=="doc" for p in points)


def test_build_resume_does_not_reembed_completed_batches(tmp_path):
    inventory,summary,runtime,identity=fixture(tmp_path)
    checkpoint=tmp_path/"candidate.sqlite"; calls=[]
    original=runtime.embed_documents
    object.__setattr__(runtime,"embed_documents",lambda texts:(calls.append(tuple(texts)) or original(texts)))
    with pytest.raises(RuntimeError):
        build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
          identity=identity,embedding=runtime,verify_live_source=lambda:(_ for _ in ()).throw(RuntimeError()) if len(calls)>=1 else summary,batch_size=2)
    result=build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
      identity=identity,embedding=runtime,verify_live_source=lambda:summary,batch_size=2)
    assert result.embedded_count==3 and [len(x) for x in calls]==[2,1]


def test_mismatch_and_nonempty_target_fail_closed(tmp_path):
    inventory,summary,runtime,identity=fixture(tmp_path)
    checkpoint=tmp_path/"candidate.sqlite"
    with pytest.raises(CandidateError):
        build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
          identity=identity,embedding=runtime,verify_live_source=lambda:None)
    result=build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
      identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    store=InMemoryVectorStore();store.ensure_collection(identity.physical_collection,identity.profile.dimension)
    from hello_agents.memory.storage.vector_store import VectorPoint
    store.upsert(identity.physical_collection,[VectorPoint("foreign",[1.0]+[0.0]*(identity.profile.dimension-1),{})])
    with pytest.raises(CandidateError,match="target_not_empty"):
        publish_qdrant_candidate(checkpoint,expected=result,identity=identity,store=store)


def test_unbound_inventory_is_refused(tmp_path):
    inventory,summary,runtime,identity=fixture(tmp_path)
    import sqlite3
    with sqlite3.connect(inventory) as db:
        db.execute("UPDATE state SET authority_receipt=NULL");db.execute("UPDATE state SET fingerprint='0'");db.commit()
    with pytest.raises(Exception):
        build_candidate(tmp_path/"candidate.sqlite",migration_id="m1",inventory_path=inventory,inventory=summary,
          identity=identity,embedding=runtime,verify_live_source=lambda:summary)


def test_publish_rejects_identity_not_bound_to_checkpoint(tmp_path):
    inventory,summary,runtime,identity=fixture(tmp_path)
    checkpoint=tmp_path/"candidate.sqlite"
    result=build_candidate(checkpoint,migration_id="m1",inventory_path=inventory,inventory=summary,
      identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    other=IndexIdentity("qdrant","another",runtime.profile)
    with pytest.raises(CandidateError,match="identity"):
        publish_qdrant_candidate(checkpoint,expected=result,identity=other,store=InMemoryVectorStore())


def test_json_publish_is_atomic_and_recovers_after_replace_before_commit(tmp_path):
    inventory,summary,_,_=fixture(tmp_path)
    runtime=build_rag_embedding({},backend="json")
    identity=IndexIdentity("json","candidate",runtime.profile)
    checkpoint=tmp_path/"candidate.sqlite"
    embedded=build_candidate(checkpoint,migration_id="m-json",inventory_path=inventory,
      inventory=summary,identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    legacy=tmp_path/"rag"/"cache.json";legacy.parent.mkdir()
    published=publish_json_candidate(checkpoint,expected=embedded,identity=identity,
      legacy_path=legacy,rag_namespace="pdf_user")
    assert published.state=="published" and published.published_count==3
    from hello_agents.memory.rag.json_index_cache import JsonIndexCache
    target=JsonIndexCache(legacy,identity,"pdf_user").path
    before=target.read_bytes()
    import sqlite3
    with sqlite3.connect(checkpoint) as db:
        db.execute("UPDATE state SET status='embedded',published=0");db.commit()
    recovered=inspect_candidate(checkpoint)
    again=publish_json_candidate(checkpoint,expected=recovered,identity=identity,
      legacy_path=legacy,rag_namespace="pdf_user")
    assert again.state=="published" and target.read_bytes()==before and not legacy.exists()


def test_json_publish_never_overwrites_unknown_target(tmp_path):
    inventory,summary,_,_=fixture(tmp_path)
    runtime=build_rag_embedding({},backend="json");identity=IndexIdentity("json","candidate",runtime.profile)
    checkpoint=tmp_path/"candidate.sqlite"
    embedded=build_candidate(checkpoint,migration_id="m-json",inventory_path=inventory,
      inventory=summary,identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    legacy=tmp_path/"rag"/"cache.json";legacy.parent.mkdir()
    from hello_agents.memory.rag.json_index_cache import JsonIndexCache
    target=JsonIndexCache(legacy,identity,"pdf_user").path;target.write_bytes(b"unknown")
    with pytest.raises(CandidateError,match="target_exists"):
        publish_json_candidate(checkpoint,expected=embedded,identity=identity,
          legacy_path=legacy,rag_namespace="pdf_user")
    assert target.read_bytes()==b"unknown"


def test_published_json_is_revalidated_and_repaired_from_checkpoint(tmp_path):
    inventory,summary,_,_=fixture(tmp_path)
    runtime=build_rag_embedding({},backend="json");identity=IndexIdentity("json","candidate",runtime.profile)
    checkpoint=tmp_path/"candidate.sqlite";legacy=tmp_path/"rag"/"cache.json";legacy.parent.mkdir()
    embedded=build_candidate(checkpoint,migration_id="m-json",inventory_path=inventory,
      inventory=summary,identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    published=publish_json_candidate(checkpoint,expected=embedded,identity=identity,legacy_path=legacy,rag_namespace="pdf_user")
    from hello_agents.memory.rag.json_index_cache import JsonIndexCache
    target=JsonIndexCache(legacy,identity,"pdf_user").path;target.unlink()
    repaired=publish_json_candidate(checkpoint,expected=published,identity=identity,legacy_path=legacy,rag_namespace="pdf_user")
    assert repaired==published and target.is_file()

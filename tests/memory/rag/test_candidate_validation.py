from hello_agents.memory.rag.candidate_rebuild import build_candidate,publish_json_candidate
from hello_agents.memory.rag.candidate_validation import validate_candidate,inspect_validation
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.json_index_cache import JsonIndexCache
from hello_agents.memory.rag.source_json import JsonChunkSource
from tests.memory.rag.test_candidate_rebuild import fixture


def test_exact_json_candidate_validation_is_persisted(tmp_path):
    inv,summary,_,_=fixture(tmp_path);runtime=build_rag_embedding({},backend="json")
    identity=IndexIdentity("json","candidate",runtime.profile);cp=tmp_path/"cp.sqlite"
    embedded=build_candidate(cp,migration_id="m",inventory_path=inv,inventory=summary,
      identity=identity,embedding=runtime,verify_live_source=lambda:summary)
    legacy=tmp_path/"rag"/"cache.json";legacy.parent.mkdir()
    published=publish_json_candidate(cp,expected=embedded,identity=identity,legacy_path=legacy,rag_namespace="pdf_user")
    target=JsonIndexCache(legacy,identity,"pdf_user").path
    source=JsonChunkSource(target,collection=identity.physical_collection,namespace="pdf_user",
      dimension=identity.profile.dimension,identity=identity)
    result=validate_candidate(tmp_path/"validation.json",candidate_path=cp,candidate=published,
      source_inventory_path=inv,source_inventory=summary,target_inventory_path=tmp_path/"target.sqlite",
      target_source=source,verify_live_source=lambda:summary)
    assert result.chunk_count==3 and result.partition_count==1 and result.scope_leaks==0
    assert inspect_validation(tmp_path/"validation.json",expected=result)==result

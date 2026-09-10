"""Recoverable offline candidate vectors and bounded Qdrant publication."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, math, os, re, sqlite3, stat, uuid
from pathlib import Path

from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.json_index_cache import versioned_cache_path
from hello_agents.memory.rag.source_inventory import InventorySummary, iter_chunks
from hello_agents.memory.rag.source_json import JsonChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError, canonical, digest
from hello_agents.memory.storage.vector_store import VectorPoint


class CandidateError(SourceInventoryError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CandidateSummary:
    migration_id: str; state: str; embedded_count: int
    published_count: int; total_count: int; fingerprint: str


_SCHEMA = """
CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),migration_id TEXT NOT NULL,
 inventory TEXT NOT NULL,identity TEXT NOT NULL,status TEXT NOT NULL,
 embedded INTEGER NOT NULL,published INTEGER NOT NULL,total INTEGER NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE rows(ordinal INTEGER PRIMARY KEY,record TEXT NOT NULL,
 source_digest TEXT NOT NULL,candidate_digest TEXT NOT NULL,vector TEXT NOT NULL);
"""


def _summary(db):
    row=db.execute("SELECT migration_id,status,embedded,published,total,inventory,identity,created_at FROM state").fetchone()
    if not row or row[1] not in {"building","embedded","published"} or not 0<=row[3]<=row[2]<=row[4]:
        raise CandidateError("checkpoint")
    try:
        stored_identity = IndexIdentity.from_dict(json.loads(row[6]))
    except Exception:
        raise CandidateError("checkpoint") from None
    hasher=hashlib.sha256(canonical(["candidate-v1",*row]).encode())
    count=0
    for ordinal,record,source_digest,candidate_digest,vector in db.execute(
        "SELECT ordinal,record,source_digest,candidate_digest,vector FROM rows ORDER BY ordinal"
    ):
        if (ordinal!=count or digest(json.loads(record))!=candidate_digest
                or not re.fullmatch(r"[0-9a-f]{64}",source_digest)):
            raise CandidateError("checkpoint")
        values=json.loads(vector)
        if (not isinstance(values,list) or len(values)!=stored_identity.profile.dimension
                or not any(values)
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in values)):
            raise CandidateError("checkpoint")
        hasher.update(digest([ordinal,record,source_digest,candidate_digest,vector]).encode())
        count+=1
    if count!=row[2]: raise CandidateError("checkpoint")
    fp=hasher.hexdigest()
    return CandidateSummary(row[0],row[1],row[2],row[3],row[4],fp)


def _checkpoint_path(path, *, missing=False):
    path = Path(path)
    if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
        raise CandidateError("checkpoint_path")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise CandidateError("checkpoint_path") from None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 \
            or not stat.S_ISREG(info.st_mode):
        raise CandidateError("checkpoint_path")
    return path


def _open(path):
    path = _checkpoint_path(path)
    db=sqlite3.connect(path); db.execute("PRAGMA synchronous=FULL"); return db


def _file_digest(path):
    hasher=hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block:=stream.read(65536): hasher.update(block)
    return hasher.digest()


def _target_exists(path):
    try: info=Path(path).lstat()
    except FileNotFoundError: return False
    if Path(path).is_symlink() or getattr(info,"st_file_attributes",0)&0x400:
        raise CandidateError("target_path")
    if not Path(path).is_file(): raise CandidateError("target_path")
    return True


def build_candidate(path, *, migration_id, inventory_path, inventory, identity,
                    embedding, verify_live_source, batch_size=8):
    path, inventory_path=_checkpoint_path(path, missing=True),Path(inventory_path)
    if (not path.is_absolute() or not inventory_path.is_absolute()
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}",migration_id or "")
        or not isinstance(inventory,InventorySummary) or not isinstance(identity,IndexIdentity)
        or identity.backend not in {"json","qdrant"} or embedding.profile!=identity.profile
        or type(batch_size) is not int or not 1<=batch_size<=8
        or verify_live_source()!=inventory): raise CandidateError("configuration")
    created=not path.exists()
    if created:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
    with closing(_open(path)) as db:
        if created:
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
            db.executescript(_SCHEMA); db.execute("INSERT INTO state VALUES(1,?,?,?,?,0,0,?,?)",
              (migration_id,canonical(inventory.__dict__),canonical(identity.to_dict()),"building",inventory.count,created_at)); db.commit()
        row=db.execute("SELECT migration_id,inventory,identity,status,embedded,total FROM state").fetchone()
        if not row or row[:3]!=(migration_id,canonical(inventory.__dict__),canonical(identity.to_dict())) or row[5]!=inventory.count:
            raise CandidateError("checkpoint")
        if row[3] in {"embedded","published"}: return _summary(db)
        pending=[]; ordinal=0
        for chunk in iter_chunks(inventory_path,expected=inventory,require_authority=True):
            record=chunk.to_dict(); rd=digest(record)
            prior=db.execute("SELECT source_digest FROM rows WHERE ordinal=?",(ordinal,)).fetchone()
            if prior:
                if prior[0]!=rd: raise CandidateError("source_changed")
            else:
                pending.append((ordinal,record,rd,chunk.content))
                if len(pending)==batch_size:
                    _embed(db,pending,embedding,identity); pending=[]
            ordinal+=1
        if pending:_embed(db,pending,embedding,identity)
        if ordinal!=inventory.count or verify_live_source()!=inventory: raise CandidateError("source_changed")
        db.execute("UPDATE state SET status='embedded',embedded=?",(ordinal,)); db.commit(); return _summary(db)


def _embed(db,batch,embedding,identity):
    vectors=embedding.embed_documents([x[3] for x in batch])
    for (ordinal,record,rd,_),vector in zip(batch,vectors):
        if len(vector)!=identity.profile.dimension or not any(vector) or any(not math.isfinite(x) for x in vector): raise CandidateError("vector")
        record["metadata"]["embedding_fingerprint"]=identity.profile.fingerprint
        encoded=canonical(record)
        db.execute("INSERT INTO rows VALUES(?,?,?,?,?)",(ordinal,encoded,rd,digest(record),canonical(vector)))
    db.execute("UPDATE state SET embedded=?",(batch[-1][0]+1,));db.commit()


def inspect_candidate(path, *, expected=None):
    try:
        path = _checkpoint_path(path)
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as db:
            result=_summary(db)
            if expected is not None and result!=expected: raise CandidateError("checkpoint_changed")
            return result
    except CandidateError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise CandidateError("checkpoint") from None


def publish_qdrant_candidate(path, *, expected, identity, store, batch_size=100):
    if (type(batch_size) is not int or not 1<=batch_size<=100
            or not isinstance(identity,IndexIdentity) or identity.backend!="qdrant"):
        raise CandidateError("configuration")
    with closing(_open(path)) as db:
        if _summary(db)!=expected or expected.state not in {"embedded","published"}: raise CandidateError("checkpoint")
        stored_identity=db.execute("SELECT identity FROM state").fetchone()[0]
        if stored_identity!=canonical(identity.to_dict()): raise CandidateError("identity")
        store.ensure_collection(identity.physical_collection,identity.profile.dimension,identity.profile.distance)
        store.require_collection(identity.physical_collection,identity.profile.dimension,identity.profile.distance)
        published=db.execute("SELECT published FROM state").fetchone()[0]
        if published==0 and store.count(identity.physical_collection)!=0: raise CandidateError("target_not_empty")
        while published<expected.total_count:
            rows=db.execute("SELECT ordinal,record,vector FROM rows WHERE ordinal>=? ORDER BY ordinal LIMIT ?",(published,batch_size)).fetchall()
            points=[]
            for _,raw,vector in rows:
                data=json.loads(raw); metadata=dict(data["metadata"])
                payload={key:metadata.pop(key) for key in (
                    "content","document_id","rag_namespace","chunk_index",
                    "created_at","updated_at","document_version") if key in metadata}
                for duplicate in ("content","document_id","rag_namespace"):
                    metadata.pop(duplicate,None)
                payload["metadata"]=metadata
                points.append(VectorPoint(data["chunk_id"],json.loads(vector),payload))
            store.upsert(identity.physical_collection,points); published+=len(points)
            if store.count(identity.physical_collection)!=published: raise CandidateError("target_count")
            db.execute("UPDATE state SET published=?",(published,));db.commit()
        db.execute("UPDATE state SET status='published'");db.commit();return _summary(db)


def publish_json_candidate(path, *, expected, identity, legacy_path, rag_namespace):
    """Atomically materialize a deterministic managed JSON cache.

    Rebuilding the same bytes permits recovery when replace succeeded but the
    checkpoint commit did not. An unrelated existing target is never replaced.
    """
    if not isinstance(identity,IndexIdentity) or identity.backend!="json":
        raise CandidateError("configuration")
    target=versioned_cache_path(legacy_path,identity,rag_namespace)
    if not target.parent.is_dir(): raise CandidateError("target_path")
    with closing(_open(path)) as db:
        current=_summary(db)
        if current!=expected or expected.state not in {"embedded","published"}: raise CandidateError("checkpoint")
        stored,created_at=db.execute("SELECT identity,created_at FROM state").fetchone()
        if stored!=canonical(identity.to_dict()): raise CandidateError("identity")
        temporary=target.parent/(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                prefix={"schema_version":2,"identity":identity.to_dict(),"rag_namespace":rag_namespace,
                        "updated_at":created_at,"chunk_count":expected.total_count}
                start=canonical(prefix)[:-1]+',"chunks":['
                stream.write(start.encode("utf-8"))
                first=True
                for record,vector in db.execute("SELECT record,vector FROM rows ORDER BY ordinal"):
                    data=json.loads(record)
                    item={"id":data["chunk_id"],"document_id":data["document_id"],
                          "content":data["content"],"vector":json.loads(vector),"metadata":data["metadata"]}
                    if not first: stream.write(b",")
                    stream.write(canonical(item).encode("utf-8")); first=False
                stream.write(b"]}");stream.flush();os.fsync(stream.fileno())
            candidate_hash=_file_digest(temporary)
            if _target_exists(target):
                if _file_digest(target)!=candidate_hash: raise CandidateError("target_exists")
                temporary.unlink()
            else:
                os.replace(temporary,target)
            source=JsonChunkSource(target,collection=identity.physical_collection,
                namespace=rag_namespace,dimension=identity.profile.dimension,identity=identity)
            if sum(1 for _ in source.records())!=expected.total_count: raise CandidateError("target_count")
            db.execute("UPDATE state SET status='published',published=total");db.commit();return _summary(db)
        finally:
            temporary.unlink(missing_ok=True)

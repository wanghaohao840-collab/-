"""Copy frozen paired legacy RAG vectors into an isolated immutable generation."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import tarfile
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import tuple_row

from app.postgres import PostgresDatabase
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import IndexRecord, registry_key
from hello_agents.memory.storage.generation_vector_store import (
    GenerationVectorStoreError, _check_caller_fields, _same_cosine_vector,
    _validate_logical_id,
)
from hello_agents.memory.storage.vector_store import QdrantVectorStore, VectorPoint
from deploy.inventory_paired_backup import _safe_name, _sha256
from deploy.migrate_document_refs_isolated import (
    TABLES_012, _desired as _document_references, _read as _read_documents,
)
from deploy.migrate_relational_isolated import TABLES, _fingerprint
from deploy.migrate_report_refs_isolated import (
    _references as _report_references, _read as _read_reports,
)
from deploy.migrate_structured_isolated import (
    _comparison, _read_auxiliary, _source,
)

_MIGRATION_OWNER = 'paired-vector-migration'
_REVISION = '20260929_12'


def _endpoint_key(url: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username \
                or parsed.password or parsed.path not in {'', '/'}:
            raise ValueError
        if host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError
        return ('loopback', parsed.port or (443 if parsed.scheme == 'https' else 80))
    except (TypeError, ValueError):
        raise ValueError('Isolated Qdrant endpoints must be loopback URLs') from None


@contextmanager
def _controller_lock(database_url: str, target_schema: str):
    """One PG session lock covers source scan through final target readback."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        held = connection.execute(
            'select pg_try_advisory_lock(1447379796, hashtext(%s))',
            (target_schema,)).fetchone()[0]
        if not held:
            raise ValueError('Another vector migration controller owns this target schema')
        yield


def _digest(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class FrozenVectorSource:
    identity: IndexIdentity
    owner: str
    namespace: str
    document_ids: frozenset[str]
    points: tuple[VectorPoint, ...]
    source_archive_sha256: str
    qdrant_archive_name: str
    qdrant_archive_sha256: str
    inventory_sha256: str
    source_points_sha256: str


def _active_identity(archive_path: Path) -> IndexIdentity:
    registry = None
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive:
            if _safe_name(member) == 'app/vector_indexes/rag/registry.json':
                if registry is not None or not member.isfile():
                    raise ValueError('Duplicated or invalid archived RAG registry')
                registry = json.loads(archive.extractfile(member).read())
    if not isinstance(registry, dict) or registry.get('schema_version') != 1:
        raise ValueError('Archived RAG registry is missing or invalid')
    entries = registry.get('entries')
    if not isinstance(entries, dict) or len(entries) != 1:
        raise ValueError('Expected one registered RAG index')
    key, value = next(iter(entries.items()))
    record = IndexRecord.from_dict(value)
    if key != registry_key(record.identity) or record.state != 'active' \
            or record.identity.backend != 'qdrant':
        raise ValueError('Archived RAG index is not active')
    return record.identity


def _visible_documents(columns, baseline, structured) -> tuple[str, set[str]]:
    active = {row[0] for row in baseline['users']
              if dict(zip(columns['users'], row))['status'] == 'active'}
    snapshots = [(owner, json.loads(payload)) for owner, kind, _, payload
                 in structured['user_snapshots'] if kind == 'history']
    if len(snapshots) != 1 or snapshots[0][0] not in active:
        raise ValueError('Expected one active source History owner')
    owner, history = snapshots[0]
    records = history.get('documents')
    if not isinstance(records, list):
        raise ValueError('Source History documents are invalid')
    latest = {}
    for record in records:
        if not isinstance(record, dict) or record.get('user_id', owner) != owner:
            raise ValueError('Source History document owner differs')
        document_id = record.get('document_id')
        if not isinstance(document_id, str) or not document_id:
            raise ValueError('Source History document ID is invalid')
        latest[document_id] = record
    fences = set()
    for row in baseline['qa_deletion_fences']:
        record = dict(zip(columns['qa_deletion_fences'], row))
        if (record['user_id'] == owner and record['target_type'] == 'document'
                and (record['status'] in {'queued', 'running'}
                     or record['status'] == 'failed' and record['attempt_count'] < 3)):
            fences.add(record['target_id'])
    return owner, set(latest) - fences


def read_frozen_source(archive_path: Path, inventory_path: Path,
                       expected_inventory_sha256: str, source_url: str) -> FrozenVectorSource:
    """Revalidate both archives and every read-only source Qdrant point."""
    archive_path, inventory_path = Path(archive_path).resolve(), Path(inventory_path).resolve()
    if (not isinstance(expected_inventory_sha256, str)
            or len(expected_inventory_sha256) != 64
            or _sha256(inventory_path) != expected_inventory_sha256):
        raise ValueError('Frozen vector inventory digest differs')
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    source, columns, baseline, structured = _source(archive_path)
    identity = _active_identity(archive_path)
    owner, document_ids = _visible_documents(columns, baseline, structured)
    namespace = f'pdf_{owner}'
    saved = inventory.get('collections')
    if (inventory.get('status') != 'passed'
            or inventory.get('source_qdrant_archive_sha256') != source['qdrant_sha256']
            or not isinstance(saved, list)
            or {item['collection'] for item in saved} != set(source['qdrant_collections'])
            or inventory.get('source_documents') != len(document_ids)
            or inventory.get('matched_documents') != len(document_ids)):
        raise ValueError('Saved vector inventory differs from frozen pair')
    actual, converted, seen_documents = [], [], set()
    seen_logical = set()
    with httpx.Client(base_url=source_url, timeout=20) as client:
        response = client.get('/collections')
        response.raise_for_status()
        names = {item['name'] for item in response.json()['result']['collections']}
        if names != set(source['qdrant_collections']):
            raise ValueError('Restored source collection set differs')
        for name in sorted(names):
            response = client.get(f'/collections/{name}')
            response.raise_for_status()
            config = response.json()['result']['config']['params']['vectors']
            response = client.post(f'/collections/{name}/points/count', json={'exact': True})
            response.raise_for_status()
            count = response.json()['result']['count']
            if type(count) is not int or not 0 <= count <= 100_000:
                raise ValueError('Source vector count is invalid')
            points, groups, offset, offsets = [], {}, None, set()
            for _ in range(10_000):
                request = {'limit': 128, 'with_payload': True, 'with_vector': True}
                if offset is not None:
                    request['offset'] = offset
                response = client.post(f'/collections/{name}/points/scroll', json=request)
                response.raise_for_status()
                page = response.json()['result']
                batch = page['points']
                if not isinstance(batch, list) or len(batch) > 128:
                    raise ValueError('Source vector scan page is invalid')
                for point in batch:
                    payload = point.get('payload')
                    if not isinstance(payload, dict):
                        raise ValueError('Source vector point is missing payload')
                    document_id = payload.get('document_id')
                    group = (payload.get('rag_namespace'), document_id)
                    if name == identity.physical_collection:
                        item = _source_point(point, owner, document_ids, identity)
                        if item.id in seen_logical:
                            raise ValueError('Duplicate source logical vector ID')
                        seen_logical.add(item.id)
                        converted.append(item)
                        seen_documents.add(document_id)
                    else:
                        raise ValueError('Unexpected points in inactive source collection')
                    groups.setdefault(group, []).append({'id': str(point['id']),
                                                           'sha256': _digest(point)})
                    points.append(point)
                    if len(points) > count:
                        raise ValueError('Source vector scan exceeds exact count')
                offset = page.get('next_page_offset')
                if offset is None:
                    break
                cursor = json.dumps(offset, sort_keys=True)
                if not batch or cursor in offsets:
                    raise ValueError('Source vector scan cursor repeats')
                offsets.add(cursor)
            else:
                raise ValueError('Source vector scan exceeds page bound')
            if len(points) != count:
                raise ValueError('Source vector scan is incomplete')
            actual.append({'collection': name, 'point_count': count, 'config': config,
                           'points_sha256': _digest(sorted(points, key=lambda p: str(p['id']))),
                           'documents': [{'namespace': key[0], 'document_id': key[1],
                                          'points': sorted(values, key=lambda p: p['id'])}
                                         for key, values in sorted(groups.items())]})
    if actual != sorted(saved, key=lambda item: item['collection']) \
            or seen_documents != document_ids:
        raise ValueError('Restored source vector content differs from frozen inventory')
    active = next(item for item in actual
                  if item['collection'] == identity.physical_collection)
    if active['config'] != {'size': identity.profile.dimension,
                           'distance': identity.profile.distance}:
        raise ValueError('Restored source collection profile differs')
    if (_sha256(inventory_path) != expected_inventory_sha256
            or _sha256(archive_path) != source['source_sha256']
            or _sha256(archive_path.parent / source['qdrant_archive'])
            != source['qdrant_sha256']):
        raise ValueError('Frozen source archives or inventory changed during scan')
    return FrozenVectorSource(identity, owner, namespace, frozenset(document_ids),
                              tuple(sorted(converted, key=lambda point: point.id)),
                              source['source_sha256'], source['qdrant_archive'],
                              source['qdrant_sha256'],
                              expected_inventory_sha256, active['points_sha256'])


def _source_point(point: dict, owner: str, document_ids: set[str],
                  identity: IndexIdentity) -> VectorPoint:
    if not isinstance(point, dict) or not isinstance(point.get('payload'), dict):
        raise ValueError('Source vector point has no payload')
    payload = point['payload']
    logical_id = payload.get(QdrantVectorStore.LOGICAL_ID_PAYLOAD_KEY)
    metadata = payload.get('metadata')
    vector = point.get('vector')
    if (not isinstance(logical_id, str) or not logical_id
            or str(QdrantVectorStore._qdrant_id(logical_id)) != str(point.get('id'))
            or payload.get('rag_namespace') != f'pdf_{owner}'
            or payload.get('document_id') not in document_ids
            or type(payload.get('chunk_index')) is not int
            or payload['chunk_index'] < 0
            or not isinstance(payload.get('content'), str)
            or not isinstance(metadata, dict)
            or metadata.get('user_id') != owner
            or metadata.get('embedding_fingerprint') != identity.profile.fingerprint
            or any(key.startswith('_gv_') for key in payload)
            or not isinstance(vector, list)
            or len(vector) != identity.profile.dimension
            or any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in vector)
            or not math.isfinite(math.hypot(*vector))):
        raise ValueError('Source vector identity, scope or content differs')
    public = dict(payload)
    del public[QdrantVectorStore.LOGICAL_ID_PAYLOAD_KEY]
    try:
        _validate_logical_id(logical_id)
        _check_caller_fields(VectorScope(owner, 'rag', f'pdf_{owner}', identity), public)
        _digest(public)
    except (GenerationVectorStoreError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('Source vector payload cannot be staged') from error
    return VectorPoint(logical_id, list(vector), public)


def _corpus_digest(points: tuple[VectorPoint, ...]) -> str:
    records = []
    for point in sorted(points, key=lambda item: item.id):
        records.append([point.id, point.payload, _digest(point.vector)])
    return _digest(records)


def _expected_baseline(archive_path, manifest_path, store):
    inventory, columns, baseline, structured = _source(archive_path)
    documents, extra = _document_references(
        inventory, columns, baseline, structured, manifest_path, store)
    reports = _report_references(inventory, columns, baseline, manifest_path, store)
    if extra:
        raise ValueError('Archived documents lack visible History references')
    owner, visible = _visible_documents(columns, baseline, structured)
    if {(row[0], row[1]) for row in documents} != {(owner, document)
                                                     for document in visible}:
        raise ValueError('Document references and History visibility differ')
    history_versions = [version for user, kind, version, _ in structured['user_snapshots']
                        if user == owner and kind == 'history']
    if len(history_versions) != 1:
        raise ValueError('Source History version is ambiguous')
    return columns, baseline, structured, documents, reports, history_versions[0]


def _check_baseline(cursor, target_schema, expected, *, lock=False):
    columns, baseline, structured, documents, reports, _ = expected
    if not isinstance(target_schema, str) or not target_schema.startswith('cutover_') \
            or not target_schema.replace('_', '').isalnum():
        raise ValueError('Target must be an explicit isolated cutover_ schema')
    names = {row[0] for row in cursor.execute(
        'select tablename from pg_catalog.pg_tables where schemaname=%s',
        (target_schema,)).fetchall()}
    if names != TABLES_012:
        raise ValueError('Target schema baseline differs')
    cursor.execute(sql.SQL('set local search_path to {}').format(sql.Identifier(target_schema)))
    if lock:
        protected = sorted(set(TABLES) | {'user_snapshots', 'memory_documents',
                                          'note_search_tokens', 'report_objects',
                                          'document_objects', 'auth_sessions',
                                          'import_objects', 'import_task_attempts',
                                          'import_user_schedule', 'alembic_version'})
        cursor.execute(sql.SQL('lock table {} in share mode').format(
            sql.SQL(',').join(sql.Identifier(name) for name in protected)))
    if cursor.execute('select version_num from alembic_version').fetchall() != [(_REVISION,)]:
        raise ValueError('Target schema revision differs')
    if cursor.execute('select 1 from auth_sessions limit 1').fetchone():
        raise ValueError('Target has active session authority')
    for name in ('import_objects', 'import_task_attempts', 'import_user_schedule'):
        if cursor.execute(sql.SQL('select 1 from {} limit 1').format(sql.Identifier(name))).fetchone():
            raise ValueError('Target has post-baseline import authority')
    if cursor.execute('''select 1 from import_tasks where lease_version <> 0
        or claimed_by is not null or lease_token is not null
        or heartbeat_at is not null or lease_expires_at is not null
        or user_lease_token is not null or user_lease_version is not null
        limit 1''').fetchone():
        raise ValueError('Target has post-baseline import task lease authority')
    for name in TABLES:
        rows = cursor.execute(sql.SQL('select {} from {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns[name])),
            sql.Identifier(name))).fetchall()
        if _fingerprint(rows) != _fingerprint(baseline[name]):
            raise ValueError('Target relational baseline differs: ' + name)
    _, differences = _comparison(structured, _read_auxiliary(cursor))
    if differences:
        raise ValueError('Target structured baseline differs: ' + ','.join(differences))
    if (_fingerprint(_read_documents(cursor)) != _fingerprint(documents)
            or _fingerprint(_read_reports(cursor)) != _fingerprint(reports)):
        raise ValueError('Target document/report reference baseline differs')


def _check_vector_state(cursor, scope, frozen, snapshot_version, *, published):
    indexes = cursor.execute('select tenant_id,vector_kind,namespace,index_key,identity,index_revision '
                             'from vector_indexes').fetchall()
    generations = cursor.execute('select generation_id,tenant_id,vector_kind,namespace,'
                                 'index_key,owner,state,expected_count,content_digest,'
                                 'publication_revision,publication_snapshot_version,'
                                 'user_lease_token,user_lease_version from vector_generations').fetchall()
    heads = cursor.execute('select tenant_id,vector_kind,namespace,index_key,revision,'
                           'generation_id,index_revision,snapshot_version from vector_heads').fetchall()
    leases = cursor.execute('select user_id,owner,lease_token,lease_version,'
                            'lease_expires_at <= clock_timestamp() from user_mutation_leases').fetchall()
    if not published:
        if indexes or generations or heads or leases:
            raise ValueError('Target has existing vector or lease authority')
        return None
    if (len(indexes) != 1 or len(generations) != 1 or len(heads) != 1
            or len(leases) != 1):
        raise ValueError('Target vector authority is not one completed migration')
    index, generation, head, lease = indexes[0], generations[0], heads[0], leases[0]
    if (index != (*scope.key, scope.identity.to_dict(), 1)
            or generation[1:5] != scope.key
            or generation[5:10] != (_MIGRATION_OWNER, 'published', len(frozen.points),
                                     _corpus_digest(frozen.points), 1)
            or generation[10] != snapshot_version
            or head != (*scope.key, 1,
                        generation[0] if frozen.points else None, 1, snapshot_version)
            or lease != (scope.tenant_id, _MIGRATION_OWNER,
                         generation[11], generation[12], True)):
        raise ValueError('Target vector generation, head or released lease differs')
    return generation[0]


def _verify_published(service, scope, frozen, snapshot_version):
    head = service.authority.read_head(scope)
    expected_state = 'published' if frozen.points else 'empty'
    if head.state != expected_state or head.revision != 1 \
            or head.index_revision != 1 or head.snapshot_version != snapshot_version:
        raise ValueError('Target vector head differs')
    view = service.read_view(scope)
    actual = view.scroll(scope.identity.physical_collection, with_vectors=True)
    desired = {point.id: point for point in frozen.points}
    if (len(actual) != len(desired)
            or {point.id for point in actual} != set(desired)
            or any(point.payload != desired[point.id].payload
                   or not _same_cosine_vector(point.vector, desired[point.id].vector)
                   for point in actual)):
        raise ValueError('Target published vector content differs')
    if service.raw.count(scope.identity.physical_collection) != len(desired):
        raise ValueError('Target contains unexpected physical vector points')
    return head


def migrate_frozen_vectors(archive_path, inventory_path, expected_inventory_sha256,
                           manifest_path, store, source_url, target_raw, *,
                           database_url, target_schema, mode):
    """Migrate one stopped-pair RAG scope into a separate isolated target."""
    if mode not in {'dry-run', 'apply', 'verify'}:
        raise ValueError('Invalid vector migration mode')
    if not isinstance(target_schema, str) or not target_schema.startswith('cutover_') \
            or not target_schema.replace('_', '').isalnum():
        raise ValueError('Target must be an explicit isolated cutover_ schema')
    if (not isinstance(target_raw, QdrantVectorStore) or not target_raw.url
            or not source_url or _endpoint_key(target_raw.url) == _endpoint_key(source_url)):
        raise ValueError('Source and target Qdrant endpoints must be distinct')
    with _controller_lock(database_url, target_schema):
        return _migrate_locked(archive_path, inventory_path,
                               expected_inventory_sha256, manifest_path, store,
                               source_url, target_raw, database_url=database_url,
                               target_schema=target_schema, mode=mode)


def _migrate_locked(archive_path, inventory_path, expected_inventory_sha256,
                    manifest_path, store, source_url, target_raw, *,
                    database_url, target_schema, mode):
    archive_path, manifest_path = Path(archive_path).resolve(), Path(manifest_path).resolve()
    inventory_path = Path(inventory_path).resolve()
    manifest_sha256 = _sha256(manifest_path)
    frozen = read_frozen_source(archive_path, inventory_path,
                                expected_inventory_sha256, source_url)
    expected = _expected_baseline(archive_path, manifest_path, store)
    snapshot_version = expected[-1]
    scope = VectorScope(frozen.owner, 'rag', frozen.namespace, frozen.identity)
    target_raw.require_collection(scope.identity.physical_collection,
                                  scope.identity.profile.dimension,
                                  scope.identity.profile.distance)
    connection_url = make_conninfo(database_url, options=f'-csearch_path={target_schema}')
    database = PostgresDatabase(connection_url)
    database.open()
    try:
        service = VectorGenerationService(PostgresVectorGenerationAuthority(database), target_raw)
        with psycopg.connect(database_url) as connection:
            with connection.cursor(row_factory=tuple_row) as cursor:
                _check_baseline(cursor, target_schema, expected)
                existing = bool(cursor.execute('select 1 from vector_heads limit 1').fetchone())
                if existing:
                    _check_vector_state(cursor, scope, frozen, snapshot_version, published=True)
                else:
                    _check_vector_state(cursor, scope, frozen, snapshot_version, published=False)
        expected_head = service.authority.read_head(scope)
        if existing:
            head = _verify_published(service, scope, frozen, snapshot_version)
            status = 'equal' if mode == 'verify' else 'unchanged'
        else:
            if (expected_head.state != 'missing' or expected_head.revision is not None
                    or expected_head.generation_id is not None
                    or expected_head.index_revision is not None
                    or expected_head.snapshot_version is not None):
                raise ValueError('Target vector authority changed after preflight')
            if mode == 'verify':
                raise ValueError('Target vector head is missing')
            if target_raw.count(scope.identity.physical_collection) != 0:
                raise ValueError('New target collection contains pre-existing vectors')
            if mode == 'dry-run':
                status, head = 'ready', None
            else:
                # The source is externally frozen and read-only. A second full
                # scan closes the interval before candidate staging.
                if read_frozen_source(archive_path, inventory_path,
                                      expected_inventory_sha256,
                                      source_url) != frozen:
                    raise ValueError('Frozen source changed before staging')
                if _sha256(manifest_path) != manifest_sha256:
                    raise ValueError('File manifest changed before staging')
                coordinator = PostgresUserMutationCoordinator(database)
                lease = coordinator.acquire(frozen.owner, _MIGRATION_OWNER,
                                            lease_seconds=3600)
                if lease is None:
                    raise ValueError('Target user mutation lease is occupied')
                publication_error = None
                try:
                    def publish_baseline(cursor):
                        with cursor.connection.cursor(row_factory=tuple_row) as check:
                            _check_baseline(check, target_schema, expected, lock=True)
                        if (_sha256(archive_path) != frozen.source_archive_sha256
                                or _sha256(archive_path.parent / frozen.qdrant_archive_name)
                                != frozen.qdrant_archive_sha256
                                or _sha256(inventory_path) != frozen.inventory_sha256
                                or _sha256(manifest_path) != manifest_sha256):
                            raise ValueError('Frozen migration inputs changed before publication')
                    result = service.publish_complete(
                        scope, lease, expected_head,
                        frozen.points, snapshot_version=snapshot_version,
                        domain_publish=publish_baseline)
                    head = result.head
                except BaseException as error:
                    publication_error = error
                    raise
                finally:
                    try:
                        if not coordinator.release(lease) and publication_error is None:
                            raise ValueError('Vector migration lease release was not confirmed')
                    except Exception:
                        if publication_error is None:
                            raise
                _verify_published(service, scope, frozen, snapshot_version)
                with psycopg.connect(database_url) as connection:
                    with connection.cursor(row_factory=tuple_row) as cursor:
                        _check_baseline(cursor, target_schema, expected)
                        _check_vector_state(cursor, scope, frozen, snapshot_version,
                                            published=True)
                status = 'applied'
        return {'status': status, 'mode': mode, 'source_archive_sha256':
                frozen.source_archive_sha256,
                'qdrant_archive_sha256': frozen.qdrant_archive_sha256,
                'inventory_sha256': frozen.inventory_sha256,
                'source_points_sha256': frozen.source_points_sha256,
                'document_count': len(frozen.document_ids),
                'point_count': len(frozen.points),
                'target_schema': target_schema,
                'head_revision': None if head is None else head.revision,
                'generation_id': None if head is None else str(head.generation_id),
                'content_digest': _corpus_digest(frozen.points)}
    finally:
        database.close()

"""Restore paired backups to fresh isolated containers; never mount live data.

Run using the project venv. Requires existing local images and Docker. The drill
has no published ports, no production env file and an internal-only network.
Evidence records contain counts and hashes, not document bodies or credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import time
from uuid import uuid4


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f'Docker {args[0]} failed: {result.stderr[-1000:]}')
    return result.stdout.strip()


def checked_archive(path):
    path = Path(path).resolve(strict=True)
    expected = Path(str(path)+'.sha256').read_text().split()[0]
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError('Backup checksum mismatch')
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive.getmembers():
            name = member.name.replace('\\', '/')
            if name.startswith('/') or ':' in name or '..' in name.split('/') or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe archive member')
    return path, actual


def inventory(root):
    values = {}
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                values[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return values


def drill(app_archive, qdrant_archive, app_image, qdrant_image, rollback_image, evidence_root, embedding_profile):
    profile = json.loads(Path(embedding_profile).read_text(encoding='utf-8-sig'))
    allowed = {'RAG_EMBEDDING_PROVIDER', 'RAG_EMBEDDING_BASE_URL', 'RAG_EMBEDDING_MODEL',
               'RAG_EMBEDDING_DIMENSION', 'RAG_EMBEDDING_REVISION', 'QDRANT_COLLECTION'}
    if set(profile) != allowed or not all(isinstance(v, str) for v in profile.values()):
        raise ValueError('Embedding profile must contain only the six non-secret identity fields')
    profile_args = [part for key, value in profile.items() for part in ('-e', key+'='+value)]
    profile_args += ['-e', 'RAG_EMBEDDING_API_KEY=isolated-drill-no-network']
    app_archive, app_hash = checked_archive(app_archive)
    qdrant_archive, qdrant_hash = checked_archive(qdrant_archive)
    for value in (app_image, qdrant_image, rollback_image):
        if not value.startswith('sha256:') or len(value) != 71:
            raise ValueError('Immutable image ID required')
        docker('image', 'inspect', value)
    evidence_root = Path(evidence_root).resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    token = 'zhiyan-drill-' + uuid4().hex[:16]
    folder = evidence_root/token
    folder.mkdir()
    data = folder/'data'
    data.mkdir()
    with tarfile.open(app_archive, 'r:gz') as archive:
        archive.extractall(data, filter='data')
    db_path = data/'app/app.db'
    db = sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)
    assert db.execute('pragma integrity_check').fetchall() == [('ok',)]
    assert db.execute('pragma foreign_key_check').fetchall() == []
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    counts = {name: db.execute('select count(*) from "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
    db.close()
    before = inventory(data)
    volume, network = token+'-volume', token+'-network'
    qcontainer, acontainer = token+'-qdrant', token+'-app'
    created = []
    results = []
    try:
        docker('volume', 'create', '--label', 'com.zhiyan.role=restore-drill', volume); created.append('volume')
        docker('network', 'create', '--internal', network); created.append('network')
        docker('run', '--rm', '--network', 'none', '--user', '0:0', '--mount', f'type=volume,source={volume},target=/target',
               '--mount', f'type=bind,source={qdrant_archive.parent},target=/backup,readonly', '--entrypoint', 'tar', app_image,
               '-C', '/target', '-xzf', '/backup/'+qdrant_archive.name)
        docker('run', '-d', '--name', qcontainer, '--network', network, '--network-alias', 'qdrant',
               '--mount', f'type=volume,source={volume},target=/qdrant/storage', qdrant_image); created.append('qdrant')
        # Both images run on the restored copy; only /app/data is writable there.
        for image in (rollback_image, app_image):
            docker('run', '-d', '--name', acontainer, '--network', network,
                   '--mount', f'type=bind,source={data / "app"},target=/app/data',
                   '-e', 'PYTHON_DOTENV_DISABLED=1', '-e', 'PDF_ASSISTANT_DATA_DIR=/app/data',
                   '-e', 'RAG_BACKEND=qdrant', '-e', 'QDRANT_URL=http://qdrant:6333',
                   '-e', 'APP_HOST=0.0.0.0', '-e', 'APP_PORT=7860', *profile_args, image)
            created.append('app')
            deadline = time.monotonic()+120
            while time.monotonic() < deadline:
                if docker('inspect', acontainer, '--format', '{{.State.Running}}') != 'true':
                    logs = subprocess.run(['docker', 'logs', acontainer, '--tail', '80'], capture_output=True, text=True)
                    (folder/'startup.log').write_text(logs.stdout+logs.stderr, encoding='utf-8')
                    raise RuntimeError('Isolated application exited; inspect its local startup configuration')
                state = docker('inspect', acontainer, '--format', '{{.State.Health.Status}}')
                if state == 'healthy': break
                time.sleep(2)
            else: raise RuntimeError('Isolated application did not become healthy')
            probe = "import urllib.request,json; b='http://127.0.0.1:7860'; assert urllib.request.urlopen(b+'/healthz').status==200; assert urllib.request.urlopen(b+'/notes').status==200; q=json.load(urllib.request.urlopen('http://qdrant:6333/collections')); print(len(q['result']['collections']))"
            collections = int(docker('exec', acontainer, 'python', '-c', probe))
            results.append(dict(image=image, healthy=True, qdrant_collections=collections))
            docker('rm', '-f', acontainer); created.remove('app')
        restored = sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)
        assert restored.execute('pragma integrity_check').fetchall() == [('ok',)]
        assert counts == {name: restored.execute('select count(*) from "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        restored.close()
        after = inventory(data)
        # SQLite may normalize journal/WAL on startup; user document files must not change.
        documents = {k:v for k,v in before.items() if not k.startswith('app/app.db')}
        assert all(after.get(k) == v for k,v in documents.items())
        report = dict(status='passed', app_sha256=app_hash, qdrant_sha256=qdrant_hash,
                      table_counts_unchanged=True, document_hashes_unchanged=True, images=results,
                      network='internal-only', production_mounts=False)
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(dict(report=str(folder/'result.json'), **report)))
    finally:
        if 'app' in created: docker('rm', '-f', acontainer)
        if 'qdrant' in created: docker('rm', '-f', qcontainer)
        if 'network' in created: docker('network', 'rm', network)
        if 'volume' in created: docker('volume', 'rm', volume)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app-archive', 'qdrant-archive', 'app-image', 'qdrant-image', 'rollback-image', 'evidence-root', 'embedding-profile'):
        parser.add_argument('--'+name, required=True)
    drill(**vars(parser.parse_args()))

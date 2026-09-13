"""Explicit fixed-image release CLI: prepare, run, recover.

Only the no-legacy-learning-source upgrade is supported. Preparation records
approved observations without stopping services. Run/recover retain maintenance
on failure. No automatic data rollback after normal release is allowed.
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import httpx
import yaml

from deploy.learning_containers import read_containers
from deploy.learning_coordinator import execute_maintenance
from deploy.learning_maintenance import begin_journal, read_journal
from deploy.learning_release_backend import ReleaseBackend
from deploy.learning_recovery_verification import verify_recovered_data
from deploy.learning_windows_backup import WindowsBackupActions
from deploy.learning_windows_bridge import WindowsContextBridge

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def safe(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.exists() and (item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('REPARSE_PATH_REJECTED')
    return path


def write_new(path, payload):
    with Path(path).open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode()


def docker(context, *args):
    result = subprocess.run(['docker', '--context', context, *args], capture_output=True,
                            timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('DOCKER_COMMAND_FAILED')
    return result.stdout


def prepare(repository, env_file, state, backups, context, project, candidate):
    repository, env_file, state, backups = map(safe, (repository, env_file, state, backups))
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', candidate):
        raise ValueError('IMMUTABLE_IMAGE_REQUIRED')
    # A stable deployment must actually have the maintenance-aware operations module.
    module = repository/'deploy/windows/Operations.Common.psm1'
    if digest(module) != digest(ROOT/'deploy/windows/Operations.Common.psm1'):
        raise ValueError('STABLE_OPERATIONS_MODULE_NOT_INTEGRATED')
    if (state/'maintenance.json').exists():
        raise ValueError('EXISTING_MAINTENANCE_REQUIRES_RECOVERY')
    initial = read_containers(context, project)
    if not all(r.running and r.health == 'healthy' for r in initial):
        raise ValueError('BOTH_SERVICES_MUST_BE_HEALTHY')
    app = next(r for r in initial if r.service == 'app')
    qdrant = next(r for r in initial if r.service == 'qdrant')
    if app.mode != '0' or app.operation_id or not app.standard_entrypoint or not app.loopback_port:
        raise ValueError('NORMAL_LOOPBACK_APP_REQUIRED')
    detail = json.loads(docker(context, 'inspect', app.container_id))[0]
    mounts = detail['Mounts']
    if len(mounts) != 1 or mounts[0]['Destination'] != '/app/data' or mounts[0]['Type'] != 'bind':
        raise ValueError('DATA_ONLY_APP_MOUNT_REQUIRED')
    data = safe(mounts[0]['Source'])
    user = detail['Config']['User']
    if not re.fullmatch(r'\d+:\d+', user):
        raise ValueError('EXPLICIT_UID_GID_REQUIRED')
    qdetail = json.loads(docker(context, 'inspect', qdrant.container_id))[0]
    qmounts = qdetail['Mounts']
    if len(qmounts) != 1 or qmounts[0]['Type'] != 'volume' or qmounts[0]['Destination'] != '/qdrant/storage':
        raise ValueError('QDRANT_NAMED_VOLUME_REQUIRED')
    volume = qmounts[0]['Name']
    # Ask the same operations module used by cold backup to resolve its targets.
    # Only emit paths/volume; never expose the deployment environment payload.
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    ps = (f'Import-Module {quote(module)} -WarningAction SilentlyContinue; '
          f'$c=Get-OperationsConfig -RepositoryRoot {quote(repository)} -EnvFile {quote(env_file)} '
          f'-StateRoot {quote(state)} -BackupRoot {quote(backups)}; '
          '$c | Select-Object DataRoot,QdrantVolumeName | ConvertTo-Json -Compress')
    resolved = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],
        capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    if resolved.returncode:
        raise ValueError('BACKUP_TARGET_RESOLUTION_FAILED')
    targets = json.loads(resolved.stdout)
    if (safe(Path(targets['DataRoot'])/'app') != data or targets['QdrantVolumeName'] != volume):
        raise ValueError('BACKUP_AND_RUNTIME_TARGETS_DIFFER')
    descriptor = json.loads(docker(context, 'volume', 'inspect', volume))[0]
    docker(context, 'image', 'inspect', candidate)
    source = repository/'compose.yaml'
    release = repository/'compose.release.yaml'
    selected = release if release.exists() else source
    original = yaml.safe_load(selected.read_text(encoding='utf-8'))
    if set(original['services']) - {'app', 'qdrant', 'neo4j'}:
        raise ValueError('UNEXPECTED_COMPOSE_SERVICES')
    operation = str(uuid4())
    folder = state/'learning-release'/operation
    folder.mkdir(parents=True)
    payloads = {}
    for name, image in [('candidate', candidate), ('rollback', app.image)]:
        config = json.loads(json.dumps(original))
        config['name'] = project
        for service, identity in [('app', image), ('qdrant', qdrant.image)]:
            config['services'][service]['image'] = identity
            config['services'][service].pop('build', None)
        payloads[name] = encoded(config)
        write_new(folder/(name+'.yaml'), payloads[name])
    write_new(folder/'maintenance.yaml', encoded(dict(services=dict(app=dict(environment=dict(
        ZHIYAN_MAINTENANCE_MODE='1', ZHIYAN_MAINTENANCE_OPERATION_ID=operation))))))
    write_new(folder/'initial.json', encoded([asdict(r) for r in initial]))
    paths = [source, env_file, module, folder/'initial.json', folder/'candidate.yaml',
             folder/'rollback.yaml', folder/'maintenance.yaml',
             ROOT/'deploy/learning_offline_upgrade.py', Path(__file__),
             ROOT/'deploy/learning_release_backend.py']
    if release.exists():
        write_new(folder/'previous-release.yaml', release.read_bytes())
    manifest = dict(version=1, repository=str(repository), env_file=str(env_file), state=str(state),
        backups=str(backups), context=context, project=project, operation=operation,
        candidate=candidate, data=str(data), uid=user, port=app.loopback_port, volume=volume,
        volume_sha256=sha256(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        endpoint=docker(context, 'context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}').decode().strip(),
        daemon=docker(context, 'info', '--format', '{{.ID}}').decode().strip(),
        previous_release=digest(release) if release.exists() else None,
        approved_inputs={str(p):digest(p) for p in paths},
        baseline=dict(configuration_sha256=digest(selected), app_image=app.image,
                      qdrant_image=qdrant.image, running_services=['app','qdrant']))
    write_new(folder/'manifest.json', encoded(manifest))
    return folder/'manifest.json'


def run(manifest_path, *, recover=False):
    manifest_path = safe(manifest_path)
    m = json.loads(manifest_path.read_text())
    folder, state = manifest_path.parent, safe(m['state'])
    repository, operation = safe(m['repository']), m['operation']
    context, project, baseline = m['context'], m['project'], m['baseline']
    # Credentials are supplied only to this process, never written into manifests.
    username, password = os.environ['ZHIYAN_RELEASE_USERNAME'], os.environ['ZHIYAN_RELEASE_PASSWORD']
    for path, expected in m['approved_inputs'].items():
        if digest(safe(path)) != expected:
            raise ValueError('APPROVED_INPUT_CHANGED')
    release = repository/'compose.release.yaml'
    accepted = {m['previous_release']}
    if recover:
        accepted |= {digest(folder/'candidate.yaml'), digest(folder/'rollback.yaml')}
    if (digest(release) if release.exists() else None) not in accepted:
        raise ValueError('PERSISTENT_CONFIG_CHANGED')
    initial = json.loads((folder/'initial.json').read_text())
    if not recover and [asdict(r) for r in read_containers(context, project)] != initial:
        raise ValueError('INITIAL_CONTAINERS_CHANGED')
    bridge_config = dict(state_root=str(state), operation_id=operation, docker_context=context,
        expected_endpoint=m['endpoint'], expected_daemon_id=m['daemon'], begin=not recover,
        approved_inputs=m['approved_inputs'] | {str(manifest_path):digest(manifest_path)},
        backup=dict(repository_root=str(repository), env_file=m['env_file'], backup_root=m['backups']),
        restore=dict(repository_root=str(repository), env_file=m['env_file'], backup_root=m['backups']))
    with WindowsContextBridge(bridge_config) as bridge:
        original_guard = bridge.verify_context
        release_digest = digest(release) if release.exists() else None
        def pinned_guard():
            return ((digest(release) if release.exists() else None) == release_digest
                    and original_guard())
        bridge.verify_context = pinned_guard
        if not bridge.verify_context():
            raise RuntimeError('CONTEXT_REJECTED')
        if not recover:
            begin_journal(state, operation, baseline)
        backups = WindowsBackupActions(bridge=bridge, state_root=state, operation_id=operation,
            baseline=baseline, backup_root=m['backups'], qdrant_volume=m['volume'])

        def offline(action):
            if not backend.verify_stopped() or not bridge.verify_context():
                raise RuntimeError('OFFLINE_GUARD_REJECTED')
            out = docker(context, 'run', '--rm', '--pull', 'never', '--network', 'none', '--user', m['uid'],
                '-e', 'PYTHON_DOTENV_DISABLED=1', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '--mount', f'type=bind,source={m["data"]},target=/app/data',
                '--mount', f'type=bind,source={folder},target=/release',
                '--mount', f'type=bind,source={ROOT / "deploy/learning_offline_upgrade.py"},target=/upgrade.py,readonly',
                '--entrypoint', 'python', m['candidate'], '-c',
                'import sys,runpy; sys.path.insert(0,"/app"); sys.argv=["/upgrade.py",'+repr(action)+
                ',"--root","/app/data","--evidence","/release/old-tables.json"]; runpy.run_path("/upgrade.py",run_name="__main__")')
            if b'OFFLINE_UPGRADE_VERIFIED' not in out:
                raise RuntimeError('OFFLINE_UPGRADE_NOT_VERIFIED')

        def prepare_container(mode, image):
            name = 'candidate' if image == m['candidate'] else 'rollback'
            files = ['-f', str(folder/(name+'.yaml'))]
            if mode:
                files += ['-f', str(folder/'maintenance.yaml')]
            docker(context, 'compose', '--project-directory', str(repository), *files,
                   '--env-file', m['env_file'], 'up', '--no-start', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'app')

        def verify_data(restored):
            if not restored:
                offline('verify')
                return True
            return verify_recovered_data(Path(m['data']).parent, state, operation,
                qdrant=dict(context=context, volume=m['volume'], volume_sha256=m['volume_sha256'], helper_image=m['candidate']),
                verify_context=bridge.verify_context, verify_stopped=backend.verify_stopped,
                verify_backup=backups.verify_backup)

        def business(port):
            return port == m['port'] and httpx.get(f'http://127.0.0.1:{port}/healthz', trust_env=False, timeout=5).status_code == 200

        def finish():
            nonlocal release_digest
            restored = read_journal(state, operation).phase == 'recovered'
            with httpx.Client(base_url=f'http://127.0.0.1:{m["port"]}', trust_env=False, timeout=20) as client:
                response = client.post('/api/v1/auth/login', json=dict(username=username, password=password))
                if response.status_code != 200:
                    raise RuntimeError('AUTHENTICATED_VERIFICATION_FAILED')
                if not restored and client.get('/api/v1/learning/plans').status_code != 200:
                    raise RuntimeError('LEARNING_VERIFICATION_FAILED')
            target = folder/('rollback.yaml' if restored else 'candidate.yaml')
            current = digest(release) if release.exists() else None
            if current not in {m['previous_release'], digest(target)} or not bridge.verify_context():
                raise RuntimeError('CONFIG_PERSISTENCE_REJECTED')
            if current != digest(target):
                temporary = repository/('compose.release.'+operation+'.tmp')
                write_new(temporary, target.read_bytes())
                os.replace(temporary, release)
            release_digest = digest(target)
            if digest(release) != digest(target):
                raise RuntimeError('PERSISTED_CONFIG_MISMATCH')
            print('FIXED_IMAGE_CONFIG_PERSISTED', flush=True)

        backend = ReleaseBackend(context=context, project=project, state_root=state, operation_id=operation,
            baseline=baseline, images=dict(app=baseline['app_image'],qdrant=baseline['qdrant_image']),
            configurations={r['service']:r['configuration_sha256'] for r in initial}, bridge=bridge, backups=backups,
            prepare_command=prepare_container, apply_action=lambda:offline('upgrade'), verify_data=verify_data,
            verify_business=business, finish_business=finish, candidate_image=m['candidate'])
        if recover:
            receipts = state/'learning-preparation'/operation
            completed = []
            for path in receipts.glob('*.json'):
                done = Path(str(path)+'.complete')
                if not done.exists():
                    raise RuntimeError('INTERRUPTED_PREPARATION_REQUIRES_INSPECTION')
                completed.append(done)
            if completed:
                last = max(completed, key=lambda p:p.stat().st_mtime_ns)
                rows = json.loads(last.read_text())
                actual = read_containers(context, project)
                for row in actual:
                    saved = next(r for r in rows if r['service'] == row.service)
                    if any(getattr(row,k) != saved[k] for k in ('container_id','image','configuration_sha256')):
                        raise RuntimeError('RECOVERY_CONTAINER_IDENTITY_CHANGED')
                backend.images = {r.service:r.image for r in actual}
                backend.configurations = {r.service:r.configuration_sha256 for r in actual}
                backend.mode = next(r.mode for r in actual if r.service == 'app') == '1'
        result = execute_maintenance(state, operation, backend, recover=recover)
        print('RELEASE_OK '+result.phase, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    plan = sub.add_parser('prepare')
    for name in ('repository','env-file','state','backups','context','project','candidate'):
        plan.add_argument('--'+name, required=True)
    for name in ('run','recover'):
        sub.add_parser(name).add_argument('manifest')
    args = vars(parser.parse_args())
    action = args.pop('action')
    if action == 'prepare':
        print(prepare(**args))
    else:
        run(args['manifest'], recover=action == 'recover')


if __name__ == '__main__':
    main()

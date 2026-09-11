"""Read-only observations, not authorization to mutate a deployment."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import subprocess
from itertools import product
from uuid import UUID


class ContainerError(RuntimeError):
    pass


def matches_legacy_dns_defaults(item, expected):
    """Prove a saved digest differs only in null/empty DNS-list representation.

    Does not change normal observations or saved receipts. Nonempty DNS values
    and every other configuration field remain part of the exact hash check.
    """
    host = dict(item['HostConfig'])
    if 'OomKillDisable' in host and host['OomKillDisable'] is None:
        host['OomKillDisable'] = False
    keys = [key for key in ('Dns', 'DnsOptions', 'DnsSearch')
            if key in host and (host[key] is None or host[key] == [])]
    mounts = sorted(item['Mounts'], key=lambda mount: json.dumps(
        mount, sort_keys=True, separators=(',', ':'), allow_nan=False))
    for values in product((None, []), repeat=len(keys)):
        candidate = dict(Config=item['Config'], HostConfig=host | dict(zip(keys, values)), Mounts=mounts)
        digest = sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':'),
                                   allow_nan=False).encode()).hexdigest()
        if digest == expected:
            return True
    return False


@dataclass(frozen=True)
class ContainerReceipt:
    container_id: str
    service: str
    image: str
    running: bool
    health: str
    mode: str
    operation_id: str
    standard_entrypoint: bool
    configuration_sha256: str
    loopback_port: int | None = None


def _loopback_port(item, env):
    """No fallback: missing/ambiguous runtime port cannot authorize a probe."""
    try:
        port = env['APP_PORT']
        if not re.fullmatch('[1-9][0-9]{0,4}', port) or int(port) > 65535:
            return None
        if env['APP_HOST'] != '0.0.0.0':
            return None
        host = item['HostConfig']
        network = host['NetworkMode']
        if not isinstance(network, str) or not network or network in ('host', 'none') or network.startswith('container:'):
            return None
        mapping = item['NetworkSettings']['Ports']
        if mapping != host['PortBindings'] or set(mapping) != {port + '/tcp'}:
            return None
        bindings = mapping[port + '/tcp']
        if type(bindings) is not list or len(bindings) != 1:
            return None
        binding = bindings[0]
        if set(binding) != {'HostIp', 'HostPort'} or binding['HostIp'] != '127.0.0.1':
            return None
        external = binding['HostPort']
        if type(external) is not str or not re.fullmatch('[1-9][0-9]{0,4}', external):
            return None
        return int(external) if int(external) <= 65535 else None
    except (KeyError, TypeError, ValueError):
        return None


def _require(condition):
    if not condition:
        raise ValueError('Invalid observation')


def _run(argv):
    result = subprocess.run(argv, capture_output=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    _require(result.returncode == 0)
    return result.stdout


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def read_containers(context, project, *, runner=None):
    """Return redacted receipts. Caller supplies a trusted context and lock.

    Accepted output is bounded; subprocess capture itself is not a streaming
    memory bound. Docker CLI/daemon must be trusted. No raw failures escape.
    """
    try:
        for name in (context, project):
            _require(type(name) is str and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', name))
        run = runner or _run
        prefix = ['docker', '--context', context]

        def command(args):
            value = run(prefix + args)
            _require(type(value) is bytes and len(value) <= 2 * 1024 * 1024)
            return value.decode('utf-8')

        def listing():
            ids = command(['ps', '--all', '--no-trunc', '--filter',
                           'label=com.docker.compose.project=' + project,
                           '--format', '{{.ID}}']).splitlines()
            _require(len(ids) == 2 and len(set(ids)) == 2)
            _require(all(re.fullmatch('[0-9a-f]{64}', item) for item in ids))
            return sorted(ids)

        ids = listing()
        records = json.loads(command(['container', 'inspect', *ids]), object_pairs_hook=_pairs)
        _require(type(records) is list and len(records) == 2)
        receipts = []
        for item in records:
            identity, image = item['Id'], item['Image']
            _require(identity in ids and re.fullmatch('sha256:[0-9a-f]{64}', image))
            config = item['Config']
            labels = config['Labels']
            service = labels['com.docker.compose.service']
            _require(labels['com.docker.compose.project'] == project)
            _require(labels['com.docker.compose.oneoff'] == 'False')
            _require(service in ('app', 'qdrant'))
            env = {}
            for entry in config['Env']:
                key, separator, value = entry.partition('=')
                _require(separator and key and key not in env)
                env[key] = value
            state = item['State']
            running = state['Running']
            _require(type(running) is bool)
            _require(all(state[key] is False for key in ('Paused', 'Restarting', 'Dead')))
            _require(state['Status'] == 'running' if running else state['Status'] in ('exited', 'created'))
            health = state.get('Health', {}).get('Status', 'none')
            _require(health in ('healthy', 'unhealthy', 'starting', 'none'))
            # Docker's inspect Mounts array is unordered across observations.
            # Canonicalize order only; retain every mount field in the digest.
            _require(type(item['Mounts']) is list)
            mounts = sorted(item['Mounts'], key=lambda mount: json.dumps(
                mount, sort_keys=True, separators=(',', ':'), allow_nan=False))
            host_config = dict(item['HostConfig'])
            # Linux daemon resolves an unset OOM-killer disable flag to false
            # on start. True remains distinct and therefore still rejects drift.
            if 'OomKillDisable' in host_config and host_config['OomKillDisable'] is None:
                host_config['OomKillDisable'] = False
            fingerprint = sha256(json.dumps(
                dict(Config=config, HostConfig=host_config, Mounts=mounts),
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            receipts.append(ContainerReceipt(identity, service, image, running, health,
                env.get('ZHIYAN_MAINTENANCE_MODE', '0'),
                env.get('ZHIYAN_MAINTENANCE_OPERATION_ID', ''),
                config.get('Entrypoint') == ['/app/deploy/entrypoint.sh'] and not config.get('Cmd'),
                fingerprint, _loopback_port(item, env) if service == 'app' and running else None))
        _require({r.container_id for r in receipts} == set(ids))
        _require({r.service for r in receipts} == {'app', 'qdrant'})
        _require(listing() == ids)
        return tuple(sorted(receipts, key=lambda r: r.service))
    except Exception:
        raise ContainerError('CONTAINER_OBSERVATION_FAILED') from None


def matches_expected(receipts, *, images, configurations, running_services, operation_id=None):
    """Compare to independently approved inputs, never trust-on-first-use.

    Maintenance still requires an operation-bound HTTP probe and exclusive
    writer control. This function alone is not the backend verification gate.
    """
    try:
        if operation_id is not None and str(UUID(operation_id)) != operation_id:
            return False
        if set(images) != {'app', 'qdrant'} or set(configurations) != set(images):
            return False
        if len(receipts) != 2 or {r.service for r in receipts} != set(images):
            return False
        if {r.service for r in receipts if r.running} != set(running_services):
            return False
        for receipt in receipts:
            if receipt.image != images[receipt.service] or receipt.configuration_sha256 != configurations[receipt.service]:
                return False
            if receipt.service == 'app':
                if not receipt.standard_entrypoint:
                    return False
                if operation_id is not None:
                    if receipt.mode != '1' or receipt.operation_id != operation_id:
                        return False
                elif receipt.mode != '0' or receipt.operation_id or (receipt.running and receipt.health != 'healthy'):
                    return False
            elif receipt.running and receipt.health != 'healthy':
                return False
        return True
    except (ValueError, TypeError, AttributeError, KeyError):
        return False

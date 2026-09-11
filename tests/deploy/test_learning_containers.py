from dataclasses import FrozenInstanceError
import json
import subprocess

import pytest

from deploy.learning_containers import ContainerError, matches_expected, read_containers

OP = '26bd55ed-6dd4-4d39-95be-4275b585b345'


def container(service, identity):
    return dict(Id=identity * 64, Image='sha256:' + identity * 64,
                Config=dict(Labels={'com.docker.compose.project': 'zhiyan',
                                    'com.docker.compose.service': service,
                                    'com.docker.compose.oneoff': 'False'},
                            Env=['SECRET_KEY=never-echo-me'],
                            Entrypoint=['/app/deploy/entrypoint.sh'], Cmd=None),
                HostConfig={'ReadonlyRootfs': False}, Mounts=[],
                State=dict(Running=True, Paused=False, Restarting=False, Dead=False,
                           Status='running', Health={'Status': 'healthy'}))


class Runner:
    def __init__(self):
        self.records = [container('app', 'a'), container('qdrant', 'b')]
        self.calls = []
        self.change = False

    def __call__(self, argv):
        self.calls.append(argv)
        assert argv[:3] == ['docker', '--context', 'desktop-linux']
        if argv[3] == 'ps':
            if self.change and len(self.calls) > 1:
                return b'c' * 64 + b'\n' + b'b' * 64
            return ('a' * 64 + '\n' + 'b' * 64).encode()
        assert argv[3:5] == ['container', 'inspect']
        return json.dumps(self.records).encode()


def read(runner):
    return read_containers('desktop-linux', 'zhiyan', runner=runner)


def expected(receipts):
    return dict(images={r.service: r.image for r in receipts},
                configurations={r.service: r.configuration_sha256 for r in receipts},
                running_services=frozenset({'app', 'qdrant'}))


def test_redacted_immutable_receipts_and_read_only_commands():
    runner = Runner()
    receipts = read(runner)
    assert 'never-echo-me' not in repr(receipts)
    with pytest.raises(FrozenInstanceError):
        receipts[0].service = 'other'
    assert runner.calls[0][3:] == ['ps', '--all', '--no-trunc', '--filter',
                                  'label=com.docker.compose.project=zhiyan', '--format', '{{.ID}}']
    assert runner.calls[1][5:] == ['a' * 64, 'b' * 64]
    assert runner.calls[0] == runner.calls[2]
    assert matches_expected(receipts, **expected(receipts))


def test_mount_observation_order_is_not_configuration_drift():
    runner = Runner()
    runner.records[0]['Mounts'] = [
        dict(Type='bind', Source='/one', Destination='/app/data', RW=True),
        dict(Type='bind', Source='/two', Destination='/app/code', RW=False)]
    before = read(runner)
    runner.records[0]['Mounts'].reverse()
    assert read(runner) == before
    runner.records[0]['Mounts'][0]['RW'] = True
    assert not matches_expected(read(runner), **expected(before))


def test_unset_oom_kill_disable_matches_docker_default_but_not_true():
    runner=Runner()
    runner.records[0]['HostConfig']['OomKillDisable']=None
    before=read(runner)
    runner.records[0]['HostConfig']['OomKillDisable']=False
    assert read(runner)==before
    runner.records[0]['HostConfig']['OomKillDisable']=True
    assert not matches_expected(read(runner), **expected(before))


def test_legacy_dns_default_fingerprint_requires_exact_other_configuration():
    from deploy.learning_containers import matches_legacy_dns_defaults
    runner = Runner()
    host = runner.records[0]['HostConfig']
    for key in ('Dns', 'DnsOptions', 'DnsSearch'):
        host[key] = None
    original = read(runner)[0].configuration_sha256
    for key in ('Dns', 'DnsOptions', 'DnsSearch'):
        host[key] = []
    assert matches_legacy_dns_defaults(runner.records[0], original)
    host['Dns'] = ['192.0.2.1']
    assert not matches_legacy_dns_defaults(runner.records[0], original)
    host['Dns'] = []
    host['ReadonlyRootfs'] = True
    assert not matches_legacy_dns_defaults(runner.records[0], original)
    assert not matches_legacy_dns_defaults(runner.records[0], '0' * 64)


@pytest.mark.parametrize('change', ['project', 'service', 'oneoff', 'id', 'image',
                                    'duplicate-env', 'paused', 'restarting', 'dead', 'status'])
def test_foreign_or_ambiguous_inspect_fails(change):
    runner = Runner()
    item = runner.records[0]
    if change in ('project', 'service', 'oneoff'):
        item['Config']['Labels']['com.docker.compose.' + change] = 'other'
    elif change == 'id':
        item['Id'] = 'c' * 64
    elif change == 'image':
        item['Image'] = 'mutable:latest'
    elif change == 'duplicate-env':
        item['Config']['Env'].append('SECRET_KEY=duplicate')
    elif change == 'status':
        item['State']['Status'] = 'exited'
    else:
        item['State'][change.capitalize()] = True
    with pytest.raises(ContainerError):
        read(runner)


def test_list_change_and_duplicate_service_rejected():
    runner = Runner()
    runner.change = True
    with pytest.raises(ContainerError):
        read(runner)
    runner.change = False
    runner.records[1]['Config']['Labels']['com.docker.compose.service'] = 'app'
    with pytest.raises(ContainerError):
        read(runner)


@pytest.mark.parametrize('value', [b'not-json SECRET', b'', b'a' * (2 * 1024 * 1024 + 1)],
                         ids=['invalid', 'empty', 'oversize'])
def test_bad_cli_output_is_static(value):
    with pytest.raises(ContainerError) as caught:
        read_containers('desktop-linux', 'zhiyan', runner=lambda argv: value)
    assert str(caught.value) == 'CONTAINER_OBSERVATION_FAILED'


def test_timeout_is_sanitized():
    def runner(argv):
        raise subprocess.TimeoutExpired('SECRET', 20, output=b'SECRET')
    with pytest.raises(ContainerError) as caught:
        read_containers('desktop-linux', 'zhiyan', runner=runner)
    assert 'SECRET' not in str(caught.value)


def test_maintenance_and_release_are_distinct():
    runner = Runner()
    normal = read(runner)
    runner.records[0]['Config']['Env'] += [
        'ZHIYAN_MAINTENANCE_MODE=1', 'ZHIYAN_MAINTENANCE_OPERATION_ID=' + OP]
    runner.records[0]['State']['Health']['Status'] = 'unhealthy'
    maintenance = read(runner)
    assert matches_expected(maintenance, **expected(maintenance), operation_id=OP)
    assert not matches_expected(maintenance, **expected(normal), operation_id=OP)
    assert not matches_expected(maintenance, **expected(maintenance))
    assert not matches_expected(normal, **expected(normal), operation_id=OP)
    assert not matches_expected(maintenance, **expected(maintenance), operation_id='bad')


@pytest.mark.parametrize('field', ['image', 'configuration', 'running', 'health', 'entrypoint'])
def test_expected_state_mismatch(field):
    runner = Runner()
    prior = read(runner)
    if field == 'image':
        runner.records[0]['Image'] = 'sha256:' + 'c' * 64
    elif field == 'configuration':
        runner.records[0]['HostConfig']['ReadonlyRootfs'] = True
    elif field == 'running':
        runner.records[0]['State'].update(Running=False, Status='exited')
    elif field == 'health':
        runner.records[0]['State']['Health']['Status'] = 'starting'
    else:
        runner.records[0]['Config']['Entrypoint'] = ['/bin/sh']
    assert not matches_expected(read(runner), **expected(prior))


def test_originally_stopped_services_remain_expected_stopped():
    runner = Runner()
    for item in runner.records:
        item['State'].update(Running=False, Status='exited')
        item['State'].pop('Health')
    receipts = read(runner)
    options = expected(receipts)
    options['running_services'] = frozenset()
    assert matches_expected(receipts, **options)


@pytest.mark.parametrize('context,project', [('', 'zhiyan'), ('--host', 'zhiyan'),
                                          ('desktop-linux', '-bad')])
def test_invalid_target_never_calls_cli(context, project):
    def runner(argv):
        pytest.fail('must not execute')
    with pytest.raises(ContainerError):
        read_containers(context, project, runner=runner)


@pytest.mark.parametrize('fault', [None, 'missing', 'wildcard', 'duplicate', 'mismatch',
                                 'env-port', 'host', 'network', 'extra'])
def test_observed_loopback_port(fault):
    runner = Runner()
    item = runner.records[0]
    item['Config']['Env'] += ['APP_PORT=7860', 'APP_HOST=0.0.0.0']
    mapping = {'7860/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '7861'}]}
    item['NetworkSettings'] = {'Ports': json.loads(json.dumps(mapping))}
    item['HostConfig'].update(PortBindings=mapping, NetworkMode='zhiyan_app_net')
    if fault == 'missing':
        item.pop('NetworkSettings')
    elif fault == 'wildcard':
        mapping['7860/tcp'][0]['HostIp'] = '0.0.0.0'
        item['NetworkSettings']['Ports'] = mapping
    elif fault == 'duplicate':
        mapping['7860/tcp'].append(dict(mapping['7860/tcp'][0]))
    elif fault == 'mismatch':
        mapping['7860/tcp'][0]['HostPort'] = '9999'
    elif fault == 'env-port':
        item['Config']['Env'][-2] = 'APP_PORT=bad'
    elif fault == 'host':
        item['Config']['Env'][-1] = 'APP_HOST=127.0.0.1'
    elif fault == 'network':
        item['HostConfig']['NetworkMode'] = 'host'
    elif fault == 'extra':
        mapping['1234/tcp'] = [{'HostIp': '127.0.0.1', 'HostPort': '1234'}]
    receipts = read(runner)
    assert receipts[0].loopback_port == (7861 if fault is None else None)
    assert receipts[1].loopback_port is None

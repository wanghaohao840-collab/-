"""Loopback maintenance liveness, not container identity or release authority."""
from http.client import HTTPConnection, HTTPException
import json
from uuid import UUID


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def verify_maintenance_http(port: int, operation_id: str) -> bool:
    try:
        if type(port) is not int or not 1 <= port <= 65535:
            return False
        if type(operation_id) is not str or str(UUID(operation_id)) != operation_id:
            return False
        for path in ('/maintenancez', '/healthz', '/maintenancez'):
            connection = HTTPConnection('127.0.0.1', port, timeout=3)
            try:
                connection.request('GET', path, headers={'Accept': 'application/json',
                                                         'Connection': 'close'})
                response = connection.getresponse()
                probe = path == '/maintenancez'
                if response.status != (200 if probe else 503):
                    return False
                if response.getheader('Content-Type') != 'application/json':
                    return False
                if response.getheader('Cache-Control') != 'no-store':
                    return False
                body = response.read(4097)
                if len(body) > 4096:
                    return False
                payload = json.loads(body.decode('utf-8'), object_pairs_hook=_unique)
                expected = {'status': 'maintenance'}
                if probe:
                    expected['operation_id'] = operation_id
                if payload != expected:
                    return False
            finally:
                connection.close()
        return True
    except (OSError, HTTPException, ValueError, TypeError, AttributeError):
        return False

"""Latched, dependency-free maintenance entrypoint; not a live-write drain.

The deployment coordinator must select this mode before starting the container.
Only replacing the process can release it. No marker polling or business imports
are allowed in this branch; /maintenancez proves liveness, not business readiness.
"""

from __future__ import annotations

import json
import os
from uuid import UUID


def create_server_application():
    mode = os.environ.get('ZHIYAN_MAINTENANCE_MODE', '0')
    operation = os.environ.get('ZHIYAN_MAINTENANCE_OPERATION_ID', '')
    if mode not in ('0', '1') or (mode == '0' and operation):
        raise RuntimeError('Invalid maintenance configuration')
    if mode == '0':
        from api.app import create_application

        return create_application()
    try:
        if str(UUID(operation)) != operation:
            raise ValueError
    except ValueError:
        raise RuntimeError('Invalid maintenance configuration') from None

    probe = json.dumps({'status': 'maintenance', 'operation_id': operation}).encode('utf-8')
    unavailable = b'{"status":"maintenance"}'

    async def maintenance_app(scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        elif scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1013})
        elif scope['type'] == 'http':
            is_probe = scope['path'] == '/maintenancez' and scope['method'] in ('GET', 'HEAD')
            body = probe if is_probe else unavailable
            headers = [(b'content-type', b'application/json'),
                       (b'cache-control', b'no-store'),
                       (b'content-length', str(len(body)).encode('ascii'))]
            if not is_probe:
                headers.append((b'retry-after', b'60'))
            await send({'type': 'http.response.start',
                        'status': 200 if is_probe else 503, 'headers': headers})
            await send({'type': 'http.response.body',
                        'body': b'' if scope['method'] == 'HEAD' else body})

    return maintenance_app

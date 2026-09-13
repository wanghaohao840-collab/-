"""Fixed-image lifecycle wiring. Caller owns approved inputs and Windows bridge.

No discovery or default production paths. Persistent preparation receipts detect
an interrupted container replacement; an incomplete receipt requires inspection.
"""
import json
from pathlib import Path
import os
import re

from deploy.learning_containers import read_containers
from deploy.learning_stop import ContainerStopActions
from deploy.learning_start import ContainerStartActions
from deploy.learning_readiness import ReadinessActions
from deploy.learning_maintenance import read_journal


class ReleaseBackend:
    def __init__(self, *, context, project, state_root, operation_id, baseline,
                 images, configurations, bridge, backups, prepare_command,
                 apply_action, verify_data, verify_business, finish_business,
                 candidate_image=None):
        self.context, self.project = context, project
        self.root, self.operation = Path(state_root), operation_id
        self.baseline = json.loads(json.dumps(baseline))
        self.images = dict(images)
        self.candidate_image = candidate_image or images['app']
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', self.candidate_image):
            raise ValueError('IMMUTABLE_CANDIDATE_REQUIRED')
        if images != dict(app=baseline['app_image'], qdrant=baseline['qdrant_image']):
            raise ValueError('INITIAL_IMAGES_MUST_MATCH_BASELINE')
        self.configurations = dict(configurations)
        self.bridge, self.backups = bridge, backups
        self.prepare_command = prepare_command
        self.apply_action, self.data_check = apply_action, verify_data
        self.business, self.finish_business = verify_business, finish_business
        self.mode = False
        self.preparation_uncertain = False

    def verify_context(self, operation, baseline):
        return (operation == self.operation and baseline == self.baseline
                and self.bridge.verify_context() is True)

    def stopper(self):
        if self.preparation_uncertain:
            raise RuntimeError('PREPARATION_REQUIRES_INSPECTION')
        return ContainerStopActions(self.context, self.project, images=self.images,
            configurations=self.configurations, operation_id=self.operation if self.mode else None,
            verify_context=self.bridge.verify_context)

    def stop(self):
        print('STAGE stop', flush=True)
        self.stopper().stop()

    def verify_stopped(self):
        return self.stopper().verify_stopped()

    def backup(self):
        print('STAGE paired-cold-backup', flush=True)
        return self.backups.backup()

    def verify_backup(self, record):
        return self.backups.verify_backup(record)

    def apply(self):
        if not self.verify_stopped() or not self.bridge.verify_context():
            raise RuntimeError('OFFLINE_GUARD_REJECTED')
        self.apply_action()

    def restore(self, record, baseline):
        print('STAGE paired-restore', flush=True)
        self.backups.restore(record, baseline)

    def verify_data(self, recovered):
        return self.data_check(recovered)

    def prepare(self, mode):
        self.stop()
        if not self.verify_stopped() or not self.bridge.verify_context():
            raise RuntimeError('PREPARE_GUARD_REJECTED')
        before = read_containers(self.context, self.project)
        # Journal checkpoints do not track container replacement. Keep an
        # exclusive intent so an uncertain compose command cannot be repeated.
        phase = read_journal(self.root, self.operation).phase
        target_image = (self.baseline['app_image'] if not mode and phase == 'recovered'
                        else self.candidate_image)
        desired_images = dict(app=target_image, qdrant=self.baseline['qdrant_image'])
        receipts = self.root/'learning-preparation'/self.operation
        receipts.mkdir(parents=True, exist_ok=True)
        receipt = receipts/f'prepare-{phase}-{int(mode)}.json'
        payload = dict(mode=mode, phase=phase, target_image=target_image,
                       before=[r.container_id for r in before])
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
        self.preparation_uncertain = True
        self.prepare_command(mode, target_image)
        current = read_containers(self.context, self.project)
        if any(r.running for r in current) or any(r.image != desired_images[r.service] for r in current):
            raise RuntimeError('PREPARED_CONTAINER_REJECTED')
        if next(r for r in current if r.service == 'qdrant') != next(r for r in before if r.service == 'qdrant'):
            raise RuntimeError('QDRANT_REPLACED')
        app = next(r for r in current if r.service == 'app')
        if (not app.standard_entrypoint or app.mode != ('1' if mode else '0')
                or app.operation_id != (self.operation if mode else '')):
            raise RuntimeError('PREPARED_MODE_REJECTED')
        if not self.bridge.verify_context():
            raise RuntimeError('PREPARE_CONTEXT_LOST')
        with Path(str(receipt)+'.complete').open('x', encoding='utf-8') as stream:
            json.dump([r.__dict__ for r in current], stream, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        self.mode = mode
        self.images = desired_images
        self.configurations = {r.service:r.configuration_sha256 for r in current}
        self.preparation_uncertain = False

    def readiness(self):
        return ReadinessActions(self.context, self.project, self.root, self.operation,
            baseline=self.baseline, images=self.images, normal_configurations=self.configurations,
            maintenance_configurations=self.configurations, verify_context=self.bridge.verify_context,
            verify_business=self.business)

    def starts(self):
        ready = self.readiness()
        return ContainerStartActions(self.context,self.project,self.root,self.operation,
            baseline=self.baseline,images=self.images,normal_configurations=self.configurations,
            maintenance_configurations=self.configurations,verify_context=self.bridge.verify_context,
            verify_maintenance=ready.verify_maintenance,verify_released=ready.verify_released)

    def start_maintenance(self, baseline):
        print('STAGE maintenance-start', flush=True)
        self.prepare(True)
        self.starts().start_maintenance(baseline)

    def verify_maintenance(self, baseline, recovered):
        return self.readiness().verify_maintenance(baseline, recovered)

    def release(self, baseline, recovered):
        print('STAGE normal-release', flush=True)
        self.prepare(False)
        self.starts().release(baseline, recovered)

    def verify_released(self, baseline, recovered):
        target_image = self.baseline['app_image'] if recovered else self.candidate_image
        if self.images['app'] != target_image:
            return False
        return self.readiness().verify_released(baseline, recovered)

    def clear_marker(self):
        phase = read_journal(self.root, self.operation).phase
        if not self.verify_released(self.baseline, phase == 'recovered'):
            raise RuntimeError('RELEASE_NOT_VERIFIED')
        self.finish_business()
        if not self.bridge.verify_context():
            raise RuntimeError('RELEASE_CONTEXT_LOST')
        (self.root/'maintenance.json').unlink()
        print('STAGE business-verified-marker-cleared', flush=True)

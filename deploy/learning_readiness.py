"""Read-only coordinator readiness callbacks; never start services or release locks.

Caller independently approves both mode configurations and supplies local-context
and business readiness checks. An originally stopped App needs no HTTP probe.
"""
import json
from deploy.learning_maintenance import read_journal
from deploy.learning_containers import read_containers,matches_expected
from deploy.learning_probe import verify_maintenance_http


class ReadinessActions:
    def __init__(self,context,project,state_root,operation_id,*,baseline,images,
                 normal_configurations,maintenance_configurations,verify_context,verify_business,runner=None):
        self._context,self._project=context,project
        self._root,self._operation=state_root,operation_id
        self._approved=json.loads(json.dumps(dict(baseline=baseline,images=images,
            normal=normal_configurations,maintenance=maintenance_configurations),allow_nan=False))
        self._context_guard,self._business=verify_context,verify_business
        self._runner=runner

    def _verify(self,baseline,recovered,*,released):
        try:
            if type(recovered) is not bool: return False
            baseline=json.loads(json.dumps(baseline,allow_nan=False))
            state=read_journal(self._root,self._operation)
            phase=('recovered' if recovered else 'complete') if released else ('recovering' if recovered else 'verified')
            if (state.phase!=phase or baseline!=self._approved['baseline'] or
                    json.loads(state.baseline_json)!=baseline): return False
            def guard():
                return (read_journal(self._root,self._operation).record_sha256==state.record_sha256 and
                        self._context_guard() is True and
                        read_journal(self._root,self._operation).record_sha256==state.record_sha256)
            expected=dict(images=self._approved['images'],
                configurations=self._approved['normal' if released else 'maintenance'],
                running_services=baseline['running_services'],operation_id=None if released else self._operation)
            if not guard(): return False
            before=read_containers(self._context,self._project,runner=self._runner)
            if not matches_expected(before,**expected) or not guard(): return False
            app=next(r for r in before if r.service=='app')
            if app.running:
                if type(app.loopback_port) is not int or not 1<=app.loopback_port<=65535: return False
                result=self._business(app.loopback_port) if released else verify_maintenance_http(app.loopback_port,self._operation)
                if result is not True or not guard(): return False
            after=read_containers(self._context,self._project,runner=self._runner)
            return after==before and matches_expected(after,**expected) and guard()
        except Exception:
            return False

    def verify_maintenance(self,baseline,recovered):
        return self._verify(baseline,recovered,released=False)

    def verify_released(self,baseline,recovered):
        return self._verify(baseline,recovered,released=True)

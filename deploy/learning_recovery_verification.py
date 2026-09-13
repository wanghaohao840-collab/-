"""Joint byte-level recovery proof; no service release or journal writes."""
import json
from deploy.learning_maintenance import read_journal
from deploy.learning_restored_tree import verify_restored_app
from deploy.learning_qdrant_recovery import verify_restored_qdrant


def verify_recovered_data(data_root,state_root,operation_id,*,qdrant,verify_context,verify_stopped,verify_backup):
    try:
        approved=json.loads(json.dumps(qdrant,allow_nan=False))
        if set(approved)!={'context','volume','volume_sha256','helper_image'}: return False
        state=read_journal(state_root,operation_id)
        if state.phase!='recovering' or state.backup_json is None: return False
        def guard():
            return (read_journal(state_root,operation_id).record_sha256==state.record_sha256 and
                    verify_context() is True and verify_stopped() is True and
                    read_journal(state_root,operation_id).record_sha256==state.record_sha256)
        options=dict(verify_context=guard,verify_stopped=verify_stopped,verify_backup=verify_backup)
        if not guard() or verify_restored_app(data_root,state_root,operation_id,**options) is not True: return False
        if not guard() or verify_restored_qdrant(state_root,operation_id,**approved,**options) is not True: return False
        return guard()
    except Exception:
        return False

from typing import Annotated, Literal
import sqlite3

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_current_session, get_csrf_validated_session, get_learning_service
from api.errors import error_response
from api.schemas.learning import PlanCreateRequest, TaskStateRequest
from app.learning_service import LearningService
from app.learning_models import (
    CreateLearningPlan, SetLearningTaskState, LearningPlan, LearningTask,
    LearningPlanMutation, LearningTaskMutation, Page, LearningValidationError,
    LearningNotFound, LearningVersionConflict, LearningIdempotencyConflict,
    LearningDocumentDeleting, LearningMigrationRequired,
)
from app.session import UserSession

router = APIRouter(prefix='/api/v1/learning', tags=['learning'])
Session = Annotated[UserSession, Depends(get_current_session)]
WriteSession = Annotated[UserSession, Depends(get_csrf_validated_session)]
Service = Annotated[LearningService, Depends(get_learning_service)]
Limit = Annotated[int, Query(ge=1, le=50)]
Cursor = Annotated[str | None, Query(max_length=2048)]


class LearningNoStoreMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        private = scope['type'] == 'http' and (path == '/api/v1/learning' or path.startswith('/api/v1/learning/'))
        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control']
                message = {**message, 'headers': [*headers, (b'cache-control', b'no-store')]}
            await send(message)
        await self.app(scope, receive, private_send if private else send)


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except LearningNotFound:
        return error_response(404, 'LEARNING_NOT_FOUND', '学习资源不存在或文档不可用')
    except LearningValidationError:
        return error_response(422, 'LEARNING_VALIDATION_ERROR', '学习请求参数无效，请检查后重试')
    except LearningVersionConflict:
        return error_response(409, 'LEARNING_VERSION_CONFLICT', '任务已更新，请刷新后重试')
    except LearningIdempotencyConflict:
        return error_response(409, 'LEARNING_IDEMPOTENCY_CONFLICT', '请求标识已用于其他内容')
    except LearningDocumentDeleting:
        return error_response(409, 'LEARNING_DOCUMENT_DELETING', '文档正在删除或已删除')
    except LearningMigrationRequired:
        return error_response(409, 'LEARNING_MIGRATION_REQUIRED', '旧学习数据需要完成迁移后才能使用')
    except (sqlite3.Error, OSError):
        return error_response(503, 'LEARNING_UNAVAILABLE', '学习服务暂不可用，请稍后重试', retryable=True)


@router.post('/plans', response_model=LearningPlanMutation, status_code=201)
def create_plan(body: PlanCreateRequest, session: WriteSession, service: Service):
    return _call(service.create_plan, session, CreateLearningPlan(**body.model_dump()))


@router.get('/plans', response_model=Page[LearningPlan])
def list_plans(session: Session, service: Service, cursor: Cursor = None, limit: Limit = 20):
    return _call(service.list_plans, session, cursor=cursor, limit=limit)


@router.get('/plans/{plan_id}', response_model=LearningPlan)
def get_plan(plan_id: str, session: Session, service: Service):
    return _call(service.get_plan, session, plan_id)


@router.get('/plans/{plan_id}/tasks', response_model=Page[LearningTask])
def list_tasks(plan_id: str, session: Session, service: Service, cursor: Cursor = None, limit: Limit = 20):
    return _call(service.list_tasks, session, plan_id, cursor=cursor, limit=limit)


@router.get('/today', response_model=Page[LearningTask])
def today(session: Session, service: Service, bucket: Literal['today', 'overdue', 'completed'] = 'today', cursor: Cursor = None, limit: Limit = 20):
    return _call(service.today, session, bucket=bucket, cursor=cursor, limit=limit)


@router.patch('/tasks/{task_id}', response_model=LearningTaskMutation)
def set_task_state(task_id: str, body: TaskStateRequest, session: WriteSession, service: Service):
    return _call(service.set_task_state, session, task_id, SetLearningTaskState(**body.model_dump()))

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse

from api.dependencies import get_csrf_validated_session, get_current_session, get_session_token
from api.errors import error_response
from api.schemas.insights import (
    CreateReportRequest, LearningReportItem, LearningReportResponse,
    LearningStatsResponse, OverviewResponse,
)
from app.insights import InsightsService
from app.session import UserSession


router = APIRouter(prefix="/api/v1", tags=["insights"])


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def service(request: Request) -> InsightsService:
    services = request.app.state.services
    return InsightsService(
        services.session_registry, services.document_library,
        services.qa_service, services.note_service,
    )


@router.get("/overview", response_model=OverviewResponse)
def overview(request: Request, response: Response, _session: Annotated[UserSession, Depends(get_current_session)]):
    private(response)
    return service(request).overview(get_session_token(request))


@router.get("/insights/stats", response_model=LearningStatsResponse)
def stats(
    request: Request,
    response: Response,
    _session: Annotated[UserSession, Depends(get_current_session)],
    days: Annotated[int, Query(ge=7, le=90)] = 30,
):
    private(response)
    return service(request).stats(get_session_token(request), days=days)


@router.get("/insights/reports", response_model=list[LearningReportItem])
def reports(request: Request, response: Response, _session: Annotated[UserSession, Depends(get_current_session)]):
    private(response)
    return service(request).list_reports(get_session_token(request))


@router.post("/insights/reports", response_model=LearningReportResponse, status_code=201)
def create_report(
    body: CreateReportRequest,
    request: Request,
    response: Response,
    _session: Annotated[UserSession, Depends(get_csrf_validated_session)],
):
    private(response)
    return service(request).create_report(get_session_token(request))


@router.get("/insights/reports/{report_id}", response_model=LearningReportResponse)
def read_report(
    report_id: UUID, request: Request, response: Response,
    _session: Annotated[UserSession, Depends(get_current_session)],
):
    private(response)
    try:
        return service(request).read_report(get_session_token(request), str(report_id))
    except FileNotFoundError:
        return error_response(404, "REPORT_NOT_FOUND", "学习报告不存在")


@router.get("/insights/reports/{report_id}/download")
def download_report(
    report_id: UUID, request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    format: Literal["md", "docx"] = "md",
):
    try:
        path = service(request).download_path(get_session_token(request), str(report_id), format)
        if not path.is_file():
            raise FileNotFoundError(str(report_id))
    except FileNotFoundError:
        return error_response(404, "REPORT_NOT_FOUND", "学习报告不存在")
    media = "text/markdown; charset=utf-8" if format == "md" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(
        path, filename=f"zhiyan-learning-report-{report_id}.{format}",
        media_type=media, headers={"Cache-Control": "no-store"},
    )

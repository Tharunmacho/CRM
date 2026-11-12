"""Authenticated candidate inspection downloads; no business records are changed."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services import candidate_exports as service

router = APIRouter(prefix="/api/candidates/{candidate_id}", tags=["candidate exports"])
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/inspection.xlsx")
def inspection(candidate_id: str, applicationId: str | None = Query(default=None, max_length=36),
               orderId: str | None = Query(default=None, max_length=36),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = service.load_inspection(db, candidate_id, user, application_id=applicationId, order_id=orderId)
    name = service.safe_name(data.candidate.ref) + "-inspection.xlsx"
    return Response(service.workbook(data), media_type=service.XLSX_TYPE,
                    headers={**HEADERS, "Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/documents.zip")
def documents(candidate_id: str, applicationId: str | None = Query(default=None, max_length=36),
              orderId: str | None = Query(default=None, max_length=36),
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = service.load_inspection(db, candidate_id, user, application_id=applicationId, order_id=orderId)
    spool = service.document_package(db, data, user)
    name = service.safe_name(data.candidate.ref) + "-documents.zip"

    def chunks():
        try:
            while block := spool.read(service.CHUNK_BYTES):
                yield block
        finally:
            spool.close()

    return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(spool.close),
                             headers={**HEADERS, "Content-Disposition": f'attachment; filename="{name}"'})

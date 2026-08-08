from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas import ActionResponse
from ..services.auth import get_current_user
from .schemas import RagDocumentListResponse, RagSearchResponse, RagUploadResponse
from .service import (
    delete_document_service,
    list_documents_service,
    search_documents_service,
    upload_document_service,
)

router = APIRouter(prefix="/rag", tags=["RAG"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


@router.get("/documents", response_model=RagDocumentListResponse)
def list_documents(user: CurrentUser, db: Database):
    return list_documents_service(db, user)


@router.post("/upload", response_model=RagUploadResponse)
def upload_document(user: CurrentUser, db: Database, file: UploadFile = File()):
    return upload_document_service(db, user, file)


@router.delete("/documents/{document_id}", response_model=ActionResponse)
def delete_document(document_id: int, user: CurrentUser, db: Database):
    return delete_document_service(db, user, document_id)


@router.get("/search", response_model=RagSearchResponse)
def search_documents(
    user: CurrentUser,
    db: Database,
    q: str = Query(min_length=1, max_length=1000),
):
    return search_documents_service(db, user, q)

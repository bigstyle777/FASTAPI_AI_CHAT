from pydantic import BaseModel


class RagDocumentResponse(BaseModel):
    document_id: int
    filename: str
    mime_type: str | None = None
    file_size: int
    status: str
    chunk_count: int = 0
    embedding_model: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RagDocumentListResponse(BaseModel):
    success: bool
    documents: list[RagDocumentResponse]


class RagUploadResponse(BaseModel):
    success: bool
    document: RagDocumentResponse


class RagSearchHit(BaseModel):
    document_id: int
    chunk_id: int
    filename: str
    content: str
    score: float


class RagSearchResponse(BaseModel):
    success: bool
    hits: list[RagSearchHit]

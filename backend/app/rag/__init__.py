from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/rag", tags=["RAG"])


@router.post("/upload")
def upload_document(file: UploadFile = File(default=...)):
    return {"filename": file.filename, "content_type": file.content_type}

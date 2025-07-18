"""Shared upload router exposing chunked upload endpoints for all modules.
Client sends multipart/form-data with file and a JSON `chunk_data` field that
MUST include:
    file_id, chunk_number, total_chunks, filename, module
Optionally any extra metadata (tags, folder_uuid, etc.) can be included and
will be passed through unchanged – the respective module can query the final
assembled file later.
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
import json

from app.services.chunk_service import chunk_manager
from app.database import get_db

router = APIRouter()

@router.post("/upload/chunk")
async def upload_chunk(
    file: UploadFile = File(...),
    chunk_data: str = Form(...),
    db: AsyncSession = Depends(get_db)  # placeholder – modules may need DB later
):
    """Receive a single chunk and store it via ChunkUploadManager."""
    try:
        meta: Dict[str, Any] = json.loads(chunk_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid chunk_data JSON")

    required = {"file_id", "chunk_number", "total_chunks", "filename"}
    if not required.issubset(meta):
        raise HTTPException(status_code=400, detail="Missing fields in chunk_data")

    # Save chunk
    status = await chunk_manager.save_chunk(
        file_id=meta["file_id"],
        chunk_number=int(meta["chunk_number"]),
        chunk_data=file.file,
        filename=meta["filename"],
        total_chunks=int(meta["total_chunks"]),
        total_size=int(meta.get("total_size", 0)),
    )

    # If all chunks received, start assembly in background
    if status["status"] == "assembling":
        # Fire and forget – assembly runs concurrently
        _ = chunk_manager.assemble_file(meta["file_id"])

    return JSONResponse(status)

@router.get("/upload/{file_id}/status")
async def get_upload_status(file_id: str):
    status = await chunk_manager.get_upload_status(file_id)
    if not status:
        raise HTTPException(status_code=404, detail="Upload not found")
    return status

@router.delete("/upload/{file_id}")
async def cancel_upload(file_id: str):
    # Currently just drop tracking & temp files via internal cleanup
    # TODO: immediate cleanup logic if required
    if file_id in chunk_manager.uploads:
        del chunk_manager.uploads[file_id]
    return {"message": "Upload cancelled"} 
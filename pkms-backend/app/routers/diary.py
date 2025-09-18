"""
Diary Router for Personal Journal and Diary Entries
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, Form, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, extract, or_, text
from sqlalchemy.orm import selectinload, aliased
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from pathlib import Path
import json
import logging
import base64
import uuid as uuid_lib
import hashlib
from typing import Dict
import time
import asyncio

from app.database import get_db
from app.config import NEPAL_TZ, get_data_dir, get_file_storage_dir
from app.models.diary import DiaryEntry, DiaryMedia
from app.models.user import User, RecoveryKey
from app.auth.dependencies import get_current_user
from app.auth.security import verify_password, hash_password
from app.utils.diary_encryption import write_encrypted_file, read_encrypted_header, InvalidPKMSFile
from app.models.tag import Tag
from app.models.tag_associations import diary_tags
from app.services.chunk_service import chunk_manager
from app.services.fts_service_enhanced import enhanced_fts_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diary"])

# Secure in-memory session store for diary encryption material
# Format: {user_id: {"key": bytes, "timestamp": float, "expires_at": float}}
_diary_sessions: Dict[int, Dict[str, any]] = {}
DIARY_SESSION_TIMEOUT = 1800  # 30 minutes in seconds (aligned with app session)

def _get_diary_password_from_session(user_id: int) -> Optional[bytes]:
    """Get diary derived key from session if valid and not expired."""
    if user_id not in _diary_sessions:
        return None
    
    session = _diary_sessions[user_id]
    current_time = time.time()
    
    # Check if session has expired
    if current_time > session["expires_at"]:
        logger.info(f"🕐 Diary session expired for user {user_id}")
        _clear_diary_session(user_id)
        return None
    
    return session["key"]

def _store_diary_password_in_session(user_id: int, password: str):
    """Store derived diary key in secure session with expiry."""
    current_time = time.time()
    _diary_sessions[user_id] = {
        "key": _derive_diary_encryption_key(password),
        "timestamp": current_time,
        "expires_at": current_time + DIARY_SESSION_TIMEOUT
    }
    logger.info(f"🔐 Diary session created for user {user_id}, expires in {DIARY_SESSION_TIMEOUT}s")

def _clear_diary_session(user_id: int):
    """Clear diary session and password from memory."""
    if user_id in _diary_sessions:
        # Securely overwrite key in memory before deletion
        if "key" in _diary_sessions[user_id]:
            try:
                key_len = len(_diary_sessions[user_id]["key"]) if _diary_sessions[user_id]["key"] else 0
                _diary_sessions[user_id]["key"] = b"\x00" * key_len
            except Exception:
                _diary_sessions[user_id]["key"] = None
        del _diary_sessions[user_id]
        logger.info(f"🗑️ Diary session cleared for user {user_id}")

def _is_diary_unlocked(user_id: int) -> bool:
    """Check if diary is currently unlocked for user."""
    return _get_diary_password_from_session(user_id) is not None

async def _cleanup_expired_sessions():
    """Periodically clean up expired diary sessions."""
    while True:
        try:
            current_time = time.time()
            expired_users = [
                user_id for user_id, session in _diary_sessions.items()
                if current_time > session["expires_at"]
            ]
            
            for user_id in expired_users:
                logger.info(f"🕐 Auto-expiring diary session for user {user_id}")
                _clear_diary_session(user_id)
            
            if expired_users:
                logger.info(f"🧹 Cleaned up {len(expired_users)} expired diary sessions")
            
        except Exception as e:
            logger.error(f"❌ Error during session cleanup: {e}")
        
        await asyncio.sleep(300)  # Run every 5 minutes

# Start cleanup task when module is imported (application startup)
try:
    # Only start if not already running (avoid multiple tasks in development)
    if not hasattr(_cleanup_expired_sessions, '_task_started'):
        asyncio.create_task(_cleanup_expired_sessions())
        _cleanup_expired_sessions._task_started = True
        logger.info("🔄 Started diary session cleanup task (runs every 5 minutes)")
except RuntimeError:
    # Handle case where no event loop is running (e.g., during testing)
    logger.info("⏸️ Diary session cleanup task will start when event loop is available")

# --- Helper Functions ---

def _derive_diary_encryption_key(password: str) -> bytes:
    """Derive encryption key from diary password using SHA-256.
    
    This matches the key derivation used in the standalone decrypt script
    for consistency and security.
    
    Args:
        password: User's diary password
        
    Returns:
        32-byte encryption key for AES-256
    """
    return hashlib.sha256(password.encode("utf-8")).digest()


def _generate_diary_file_path(entry_uuid: str) -> Path:
    """Generate stable file path for diary entry using UUID.
    
    Format: diary_{UUID}.dat
    Example: diary_550e8400-e29b-41d4-a716-446655440000.dat
    """
    data_dir = get_file_storage_dir()
    diary_dir = data_dir / "secure" / "entries" / "text"
    diary_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"diary_{entry_uuid}.dat"
    return diary_dir / filename


async def _handle_diary_tags(db: AsyncSession, entry: DiaryEntry, tag_names: List[str], user_id: int):
    """Handle diary entry tag associations and update usage counts."""
    from app.models.tag import Tag
    from app.models.tag_associations import diary_tags
    from sqlalchemy import delete
    
    # Clear existing tag associations
    await db.execute(
        delete(diary_tags).where(diary_tags.c.diary_entry_uuid == entry.uuid)
    )
    
    if not tag_names:
        return
    
    for tag_name in tag_names:
        # Get or create tag with proper module_type
        result = await db.execute(
            select(Tag).where(
                and_(
                    Tag.name == tag_name,
                    Tag.user_id == user_id,
                    Tag.module_type == "diary"
                )
            )
        )
        tag = result.scalar_one_or_none()
        
        if not tag:
            # Create new tag with diary module_type
            tag = Tag(
                name=tag_name,
                user_id=user_id,
                module_type="diary",
                usage_count=1,
                color="#10b981"  # Green color for diary tags
            )
            db.add(tag)
            await db.flush()
        else:
            # Increment usage count
            tag.usage_count += 1
        
        # Create association
        await db.execute(
            diary_tags.insert().values(
                diary_entry_uuid=entry.uuid,
                tag_uuid=tag.uuid
            )
        )


async def _get_entry_tags(db: AsyncSession, entry_uuid: str) -> List[str]:
    """Get tag names for a diary entry."""
    from app.models.tag import Tag
    from app.models.tag_associations import diary_tags
    
    result = await db.execute(
        select(Tag.name)
        .select_from(diary_tags.join(Tag))
        .where(diary_tags.c.diary_entry_uuid == entry_uuid)
    )
    return [row[0] for row in result.fetchall()]


def _calculate_day_of_week(entry_date: date) -> int:
    """Calculate day of week for database storage.
    
    Returns 0=Monday, 1=Tuesday, ..., 6=Sunday to match Python's weekday()
    """
    return entry_date.weekday()

# --- Pydantic Models ---
# Models are now in app/schemas/diary.py

# --- Encryption Endpoints ---

@router.get("/encryption/status")
async def get_encryption_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if diary encryption is set up for the current user.
    
    Now checks for the presence of diary_password_hash in the User model.
    """
    try:
        # Simple check: encryption is setup if user has diary_password_hash
        is_setup = current_user.diary_password_hash is not None
        is_unlocked = _is_diary_unlocked(current_user.id) if is_setup else False
        
        # Additional security info for monitoring
        session_info = {}
        if is_unlocked and current_user.id in _diary_sessions:
            session = _diary_sessions[current_user.id]
            time_remaining = max(0, int(session["expires_at"] - time.time()))
            session_info = {
                "session_expires_in": time_remaining,
                "session_created_at": session["timestamp"]
            }
        
        logger.info(f"🔍 Diary encryption status for user {current_user.id}: {'setup' if is_setup else 'not setup'}, {'unlocked' if is_unlocked else 'locked'}")
        return {
            "is_setup": is_setup, 
            "is_unlocked": is_unlocked,
            **session_info
        }
        
    except Exception as e:
        logger.error(f"❌ Error checking diary encryption status for user {current_user.id}: {str(e)}")
        # Return safe default
        return {"is_setup": False}

@router.post("/encryption/setup")
async def setup_encryption(
    request: EncryptionSetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Set up diary encryption for the current user.
    
    Stores diary_password_hash and diary_password_hint in the User model.
    """
    try:
        logger.info(f"🔒 Setting up diary encryption for user {current_user.id}")
        
        # Hash the diary password using bcrypt
        from app.auth.security import hash_password
        pwd_hash = hash_password(request.password)
        
        # Store in User model
        current_user.diary_password_hash = pwd_hash
        current_user.diary_password_hint = request.hint
        
        # Mark user as no longer first-time login since they've set up diary
        if current_user.is_first_login:
            current_user.is_first_login = False
        
        await db.commit()
        
        logger.info(f"✅ Diary encryption setup completed for user {current_user.id}")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"❌ Error setting up diary encryption for user {current_user.id}: {str(e)}")
        await db.rollback()
        return {"success": False, "error": str(e)}

@router.post("/encryption/unlock")
async def unlock_encryption(
    request: EncryptionUnlockRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Unlock diary encryption session by validating the password.
    
    Validates against diary_password_hash in the User model.
    """
    try:
        logger.info(f"🔓 Diary unlock attempt for user {current_user.id}")
        
        # Check if diary encryption is set up
        if not current_user.diary_password_hash:
            logger.warning(f"⚠️  No diary password hash found for user {current_user.id}")
            return {"success": False, "error": "Diary encryption not set up"}
        
        # Validate password against stored hash
        from app.auth.security import verify_password
        
        if verify_password(request.password, current_user.diary_password_hash):
            # Store password in secure session for subsequent operations
            _store_diary_password_in_session(current_user.id, request.password)
            logger.info(f"✅ Diary unlock successful for user {current_user.id}")
            return {"success": True, "session_expires_in": DIARY_SESSION_TIMEOUT}
        else:
            logger.warning(f"❌ Diary unlock failed due to incorrect password for user {current_user.id}")
            return {"success": False, "error": "Incorrect diary password"}
        
    except Exception as e:
        logger.error(f"❌ Error unlocking diary encryption for user {current_user.id}: {str(e)}")
        return {"success": False, "error": "Failed to unlock diary encryption"}

@router.post("/encryption/lock")
async def lock_encryption(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lock diary encryption session by clearing the stored password.
    
    This securely clears the diary password from memory, requiring
    the user to unlock again for subsequent operations.
    """
    try:
        logger.info(f"🔒 Diary lock requested for user {current_user.id}")
        
        # Clear the session (password) from memory
        _clear_diary_session(current_user.id)
        
        logger.info(f"✅ Diary locked successfully for user {current_user.id}")
        return {"success": True, "message": "Diary locked successfully"}
        
    except Exception as e:
        logger.error(f"❌ Error locking diary encryption for user {current_user.id}: {str(e)}")
        return {"success": False, "error": "Failed to lock diary encryption"}

@router.get("/encryption/hint")
async def get_password_hint(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get the password hint for diary encryption.
    
    Returns hint from User.diary_password_hint field.
    """
    try:
        # Return hint from User model
        hint = current_user.diary_password_hint or ""
        logger.info(f"🔍 Password hint requested for user {current_user.id}")
        return {"hint": hint}
        
    except Exception as e:
        logger.error(f"❌ Error getting password hint for user {current_user.id}: {str(e)}")
        return {"hint": ""}

# --- API Endpoints ---

@router.post("/entries", response_model=DiaryEntryResponse)
async def create_diary_entry(
    entry_data: DiaryEntryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new diary entry with file-based encrypted storage.
    
    Uses file-based storage but maintains API compatibility by also storing
    encryption metadata in database columns for redundancy.
    """
    try:
        logger.info(f"📝 Creating diary entry for user {current_user.id} on {entry_data.date}")
        
        # Calculate day of week for efficient filtering
        day_of_week = _calculate_day_of_week(entry_data.date)
        
        # Create initial entry with temporary path (we need the ID first)
        entry = DiaryEntry(
            uuid=str(uuid_lib.uuid4()),
            date=datetime.combine(entry_data.date, datetime.min.time()),
            nepali_date=entry_data.nepali_date,
            title=entry_data.title,
            day_of_week=day_of_week,
            media_count=0,  # New entry has no media yet
            content_file_path="",  # Will be updated after we get the ID
            file_hash="",  # Will be updated after file creation
            mood=entry_data.mood,
            metadata_json=json.dumps(entry_data.metadata),
            is_template=entry_data.is_template,
            user_id=current_user.id,
            # Store encryption metadata redundantly for API compatibility
            encryption_iv=entry_data.encryption_iv,
            encryption_tag=entry_data.encryption_tag
        )
        
        # Add and flush to obtain the primary key without committing the transaction yet.
        # This prevents partially-created rows (e.g., mood saved but file write failed).
        db.add(entry)
        await db.flush()
        # Ensure we have the generated fields available
        await db.refresh(entry)
        
        # Now generate a stable file path using the entry UUID
        file_path = _generate_diary_file_path(entry.uuid)
        
        # Write encrypted file using diary_encryption utility
        # The encrypted_blob contains ciphertext+tag, iv is separate
        file_result = write_encrypted_file(
            dest_path=file_path,
            iv_b64=entry_data.encryption_iv,
            encrypted_blob_b64=entry_data.encrypted_blob,
            original_extension=""  # Empty for diary text
        )
        
        # Update entry with file information
        entry.content_file_path = str(file_path)
        entry.file_hash = file_result["file_hash"]
        
        # Commit once after file has been successfully written and entry is fully populated
        await db.commit()
        await db.refresh(entry)
        
        # Handle tags
        if entry_data.tags:
            await _handle_diary_tags(db, entry, entry_data.tags, current_user.id)
        
        # Get tags for response
        tags = await _get_entry_tags(db, entry.uuid)
        
        logger.info(f"✅ Diary entry {entry.id} created successfully with file {file_path.name}")
        
        # Create response maintaining API compatibility
        response = DiaryEntryResponse(
            uuid=entry.uuid,
            id=entry.id,
            date=entry.date.date(),
            nepali_date=entry.nepali_date,
            title=entry.title,
            encrypted_blob=entry_data.encrypted_blob,  # Return original for frontend compatibility
            encryption_iv=entry.encryption_iv,
            encryption_tag=entry.encryption_tag,
            mood=entry.mood,
            metadata=json.loads(entry.metadata_json) if entry.metadata_json else {},
            is_template=entry.is_template,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            media_count=entry.media_count,
            tags=tags
        )
        return response
        
    except Exception as e:
        logger.error(f"❌ Error creating diary entry for user {current_user.id}: {str(e)}")
        # Roll back the unit of work to avoid leaving a partially-created row
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create diary entry"
        )

@router.get("/entries", response_model=List[DiaryEntrySummary])
async def list_diary_entries(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    mood: Optional[int] = Query(None),
    templates: bool = Query(False),
    search_title: Optional[str] = Query(None, description="Search by entry title, tag, or metadata"),
    day_of_week: Optional[int] = Query(None, description="Filter by day of week (0=Sun, 1=Mon..)", ge=0, le=6),
    has_media: Optional[bool] = Query(None, description="Filter entries that have media attached"),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List diary entries with filtering. Uses FTS5 for text search if search_title is provided."""
    
    # Check if diary is unlocked
    diary_password = _get_diary_password_from_session(current_user.id)
    if not diary_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diary is locked. Please unlock diary first."
        )
    
    from sqlalchemy import text
    import json
    
    # Subquery to count media per entry
    media_count_subquery = (
        select(DiaryMedia.diary_entry_uuid, func.count(DiaryMedia.id).label("media_count"))
        .group_by(DiaryMedia.diary_entry_uuid)
        .subquery()
    )

    summaries = []
    if search_title:
        # Use centralized FTS5 for full-text search (title, tags, metadata)
        id_list = await enhanced_fts_service.search_diary_entries(
            db, search_title, current_user.id, limit=limit, offset=offset
        )
        if not id_list:
            return []
        # Fetch full rows, preserving FTS5 order
        entry_query = (
            select(
                DiaryEntry.uuid,
                DiaryEntry.id,
                DiaryEntry.title,
                DiaryEntry.date,
                DiaryEntry.nepali_date,
                DiaryEntry.mood,
                DiaryEntry.is_template,
                DiaryEntry.created_at,
                DiaryEntry.updated_at,
                DiaryEntry.user_id,
                DiaryEntry.metadata_json,
                DiaryEntry.encryption_iv,
                DiaryEntry.encryption_tag,
                func.coalesce(media_count_subquery.c.media_count, 0).label("media_count")
            )
            .outerjoin(media_count_subquery, DiaryEntry.uuid == media_count_subquery.c.diary_entry_uuid)
            .where(DiaryEntry.id.in_(id_list))
        )
        # Apply other filters
        if year:
            entry_query = entry_query.where(extract('year', DiaryEntry.date) == year)
        if month:
            entry_query = entry_query.where(extract('month', DiaryEntry.date) == month)
        if mood:
            entry_query = entry_query.where(DiaryEntry.mood == mood)
        if day_of_week is not None:
            entry_query = entry_query.where(func.strftime('%w', DiaryEntry.date) == str(day_of_week))
        if has_media is not None:
            if has_media:
                entry_query = entry_query.where(func.coalesce(media_count_subquery.c.media_count, 0) > 0)
            else:
                entry_query = entry_query.where(func.coalesce(media_count_subquery.c.media_count, 0) == 0)
        if templates:
            entry_query = entry_query.where(DiaryEntry.is_template == True)
        else:
            entry_query = entry_query.where(DiaryEntry.is_template == False)
        entry_result = await db.execute(entry_query)
        entry_rows = entry_result.fetchall()
        # Map id to row for FTS5 order
        row_map = {row.id: row for row in entry_rows}
        for row in id_list:
            if row in row_map:
                r = row_map[row]
                summary = DiaryEntrySummary(
                    uuid=r.uuid,
                    id=r.id,
                    date=r.date.date() if isinstance(r.date, datetime) else r.date,
                    nepali_date=r.nepali_date,
                    title=r.title,
                    mood=r.mood,
                    is_template=r.is_template,
                    created_at=r.created_at,
                    media_count=r.media_count,
                    encrypted_blob="",  # File-based storage, content not needed for summary
                    encryption_iv=r.encryption_iv or "",
                    encryption_tag=r.encryption_tag or "",
                    metadata=json.loads(r.metadata_json) if r.metadata_json else {},
                    tags=[]
                )
                summaries.append(summary)
        return summaries
    else:
        # Default: no search, use existing logic
        query = (
            select(
                DiaryEntry.uuid,
                DiaryEntry.id,
                DiaryEntry.title,
                DiaryEntry.date,
                DiaryEntry.nepali_date,
                DiaryEntry.mood,
                DiaryEntry.is_template,
                DiaryEntry.created_at,
                DiaryEntry.updated_at,
                DiaryEntry.user_id,
                DiaryEntry.metadata_json,
                DiaryEntry.encryption_iv,
                DiaryEntry.encryption_tag,
                func.coalesce(media_count_subquery.c.media_count, 0).label("media_count")
            )
            .outerjoin(media_count_subquery, DiaryEntry.uuid == media_count_subquery.c.diary_entry_uuid)
            .where(DiaryEntry.user_id == current_user.id)
        )
        if year:
            query = query.where(extract('year', DiaryEntry.date) == year)
        if month:
            query = query.where(extract('month', DiaryEntry.date) == month)
        if mood:
            query = query.where(DiaryEntry.mood == mood)
        if day_of_week is not None:
            query = query.where(func.strftime('%w', DiaryEntry.date) == str(day_of_week))
        if has_media is not None:
            if has_media:
                query = query.where(func.coalesce(media_count_subquery.c.media_count, 0) > 0)
            else:
                query = query.where(func.coalesce(media_count_subquery.c.media_count, 0) == 0)
        if templates:
            query = query.where(DiaryEntry.is_template == True)
        else:
            query = query.where(DiaryEntry.is_template == False)
        query = query.order_by(DiaryEntry.date.desc()).offset(offset).limit(limit)
        result = await db.execute(query)
        for row in result.all():
            summary = DiaryEntrySummary(
                uuid=row.uuid,
                id=row.id,
                date=row.date.date() if isinstance(row.date, datetime) else row.date,
                nepali_date=row.nepali_date,
                title=row.title,
                mood=row.mood,
                is_template=row.is_template,
                created_at=row.created_at,
                media_count=row.media_count,
                encrypted_blob="",  # File-based storage, content not needed for summary
                encryption_iv=row.encryption_iv or "",
                encryption_tag=row.encryption_tag or "",
                metadata=json.loads(row.metadata_json) if row.metadata_json else {},
                tags=[]
            )
            summaries.append(summary)
        return summaries

@router.get("/entries/date/{entry_date}", response_model=List[DiaryEntryResponse])
async def get_diary_entries_by_date(
    entry_date: date,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all diary entries for a specific date."""
    result = await db.execute(
        select(DiaryEntry)
        .options(selectinload(DiaryEntry.media))
        .where(
            and_(
                func.date(DiaryEntry.date) == entry_date,
                DiaryEntry.user_id == current_user.id
            )
        ).order_by(DiaryEntry.date.asc())
    )
    
    entries = result.scalars().all()
    
    response = []
    for entry in entries:
        # Read encrypted blob from file if available
        encrypted_blob = ""
        try:
            if entry.content_file_path and Path(entry.content_file_path).exists():
                extension, iv, tag, header_size = read_encrypted_header(Path(entry.content_file_path))
                with open(entry.content_file_path, "rb") as f:
                    f.seek(header_size)
                    ciphertext = f.read()
                encrypted_blob = base64.b64encode(ciphertext + tag).decode()
        except Exception:
            encrypted_blob = ""

        res = DiaryEntryResponse(
            uuid=entry.uuid,
            id=entry.id,
            date=entry.date.date() if isinstance(entry.date, datetime) else entry.date,
            nepali_date=entry.nepali_date,
            title=entry.title,
            encrypted_blob=encrypted_blob,
            encryption_iv=entry.encryption_iv,
            encryption_tag=entry.encryption_tag,
            mood=entry.mood,
            metadata=json.loads(entry.metadata_json) if entry.metadata_json else {},
            is_template=entry.is_template,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            media_count=len(entry.media),
            tags=[t.name for t in entry.tag_objs]
        )
        response.append(res)
        
    return response

@router.get("/entries/{entry_ref}", response_model=DiaryEntryResponse)
async def get_diary_entry_by_id(
    entry_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a single diary entry by its unique ID.
    
    Reads encrypted content from file but returns it in API-compatible format.
    """
    try:
        # Allow lookup by numeric id or uuid
        try:
            numeric_id = int(entry_ref)
            condition = DiaryEntry.id == numeric_id
        except ValueError:
            condition = DiaryEntry.uuid == entry_ref

        result = await db.execute(
            select(DiaryEntry)
            .options(selectinload(DiaryEntry.media))
            .where(
                and_(condition, DiaryEntry.user_id == current_user.id)
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Diary entry not found")
        
        # Read encrypted_blob from file for API compatibility
        encrypted_blob = ""
        if entry.content_file_path and Path(entry.content_file_path).exists():
            try:
                # Read the file and extract ciphertext + tag
                file_path = Path(entry.content_file_path)
                extension, iv, tag, header_size = read_encrypted_header(file_path)
                
                # Read ciphertext from after header
                with open(file_path, "rb") as f:
                    f.seek(header_size)
                    ciphertext = f.read()
                
                # Combine ciphertext + tag and encode as base64 (matching frontend expectation)
                combined = ciphertext + tag
                encrypted_blob = base64.b64encode(combined).decode()
                
                logger.debug(f"📖 Read encrypted content from {file_path.name} for entry {entry_ref}")
                
            except InvalidPKMSFile as e:
                logger.error(f"❌ Corrupt diary file for entry {entry_ref}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Diary file is corrupted or has invalid format: {str(e)}"
                )
            except FileNotFoundError:
                logger.error(f"❌ Diary file missing for entry {entry_ref}: {entry.content_file_path}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Diary content file not found on disk"
                )
            except PermissionError:
                logger.error(f"❌ Permission denied reading diary file for entry {entry_ref}: {entry.content_file_path}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Permission denied accessing diary file"
                )
            except OSError as e:
                logger.error(f"❌ OS error reading diary file for entry {entry_ref}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"File system error: {str(e)}"
                )
            except (UnicodeDecodeError, ValueError) as e:
                logger.error(f"❌ Encoding error reading diary file for entry {entry_ref}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Diary file contains invalid data encoding"
                )
        else:
            if not entry.content_file_path:
                logger.warning(f"⚠️ No file path configured for entry {entry_ref}")
                # This is okay - entry might be from older version without file storage
                encrypted_blob = ""
            else:
                logger.error(f"❌ Diary file not found for entry {entry_ref} at {entry.content_file_path}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Diary content file not found on disk"
                )
        
        # Get tags
        tags = await _get_entry_tags(db, entry.uuid)
        
        response = DiaryEntryResponse(
            uuid=entry.uuid,
            id=entry.id,
            date=entry.date.date() if isinstance(entry.date, datetime) else entry.date,
            nepali_date=entry.nepali_date,
            title=entry.title,
            encrypted_blob=encrypted_blob,  # Read from file for API compatibility
            encryption_iv=entry.encryption_iv or "",
            encryption_tag=entry.encryption_tag or "",
            mood=entry.mood,
            metadata=json.loads(entry.metadata_json) if entry.metadata_json else {},
            is_template=entry.is_template,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            media_count=len(entry.media) if hasattr(entry, 'media') else entry.media_count,
            tags=tags
        )
        return response
        
    except HTTPException:
        # Re-raise specific HTTP exceptions (from file operations above)
        raise
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON in metadata for entry {entry_ref}: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Diary entry contains invalid metadata format"
        )
    except Exception as e:
        # Catch remaining unexpected errors (like database connection issues)
        logger.error(f"❌ Unexpected error retrieving diary entry {entry_ref}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving the diary entry"
        )

@router.put("/entries/{entry_ref}", response_model=DiaryEntryResponse)
async def update_diary_entry(
    entry_ref: str,
    entry_data: DiaryEntryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing diary entry."""
    # Allow lookup by numeric id or uuid
    try:
        numeric_id = int(entry_ref)
        condition = DiaryEntry.id == numeric_id
    except ValueError:
        condition = DiaryEntry.uuid == entry_ref

    result = await db.execute(
        select(DiaryEntry).options(selectinload(DiaryEntry.media)).where(
            and_(condition, DiaryEntry.user_id == current_user.id)
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Diary entry not found")

    entry.date = datetime.combine(entry_data.date, datetime.min.time())
    entry.nepali_date = entry_data.nepali_date
    entry.title = entry_data.title
    entry.encryption_iv = entry_data.encryption_iv
    entry.encryption_tag = entry_data.encryption_tag
    entry.mood = entry_data.mood
    entry.metadata_json = json.dumps(entry_data.metadata)
    entry.is_template = entry_data.is_template
    entry.updated_at = datetime.now(NEPAL_TZ)
    
    # Write updated encrypted content to file and update hash/path (UUID-based stable path)
    try:
        file_path = _generate_diary_file_path(entry.uuid)
        file_result = write_encrypted_file(
            dest_path=file_path,
            iv_b64=entry_data.encryption_iv,
            encrypted_blob_b64=entry_data.encrypted_blob,
            original_extension=""
        )
        entry.content_file_path = str(file_path)
        entry.file_hash = file_result["file_hash"]
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ Failed to write diary file during update for entry {entry_ref}: {e}")
        raise HTTPException(status_code=500, detail="Failed to write diary file")

    # Update tags if provided
    if entry_data.tags is not None:
        await _handle_diary_tags(db, entry, entry_data.tags, current_user.id)

    await db.commit()
    await db.refresh(entry)

    response = DiaryEntryResponse(
        uuid=entry.uuid,
        id=entry.id,
        date=entry.date.date() if isinstance(entry.date, datetime) else entry.date,
        nepali_date=entry.nepali_date,
        title=entry.title,
        encrypted_blob=entry_data.encrypted_blob,
        encryption_iv=entry.encryption_iv,
        encryption_tag=entry.encryption_tag,
        mood=entry.mood,
        metadata=json.loads(entry.metadata_json) if entry.metadata_json else {},
        is_template=entry.is_template,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        media_count=len(entry.media) if hasattr(entry, 'media') else 0,
        tags=[t.name for t in entry.tag_objs]
    )
    return response

@router.delete("/entries/{entry_ref}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_diary_entry(
    entry_ref: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a diary entry by its ID.
    
    Also deletes the associated encrypted file from storage.
    """
    try:
        # Check if diary is unlocked first
        diary_password = _get_diary_password_from_session(current_user.id)
        if not diary_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Diary is locked. Please unlock diary first."
            )

        # Allow lookup by numeric id or uuid
        try:
            numeric_id = int(entry_ref)
            condition = DiaryEntry.id == numeric_id
        except ValueError:
            condition = DiaryEntry.uuid == entry_ref

        result = await db.execute(
            select(DiaryEntry).where(
                and_(condition, DiaryEntry.user_id == current_user.id)
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Diary entry not found"
            )
        
        # Delete associated encrypted file
        if entry.content_file_path:
            try:
                file_path = Path(entry.content_file_path)
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"🗑️ Deleted diary file: {file_path.name}")
            except Exception as e:
                logger.error(f"❌ Failed to delete diary file {entry.content_file_path}: {str(e)}")
                # Continue with DB deletion even if file deletion fails
        
        await db.delete(entry)
        await db.commit()
        
        logger.info(f"✅ Diary entry {entry_ref} deleted successfully")
        return None
        
    except HTTPException:
        # Re-raise specific HTTP exceptions (like entry not found)
        raise
    except Exception as e:
        # Catch remaining unexpected errors (like database connection issues)
        logger.error(f"❌ Unexpected error deleting diary entry {entry_ref}: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete diary entry"
        )

@router.get("/entries/{entry_id}/media", response_model=List[DiaryMediaResponse])
async def get_entry_media(
    entry_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all media associated with a diary entry."""
    # Check if diary is unlocked first
    diary_password = _get_diary_password_from_session(current_user.id)
    if not diary_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diary is locked. Please unlock diary first."
        )

    # First, ensure the user has access to the diary entry
    entry_res = await db.execute(
        select(DiaryEntry.id).where(
            and_(DiaryEntry.id == entry_id, DiaryEntry.user_id == current_user.id)
        )
    )
    if not entry_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diary entry not found"
        )

    media_res = await db.execute(
        select(DiaryMedia).where(DiaryMedia.diary_entry_uuid == entry_id)
    )
    media_items = media_res.scalars().all()
    return media_items

@router.post("/media/upload/commit", response_model=DiaryMediaResponse)
async def commit_diary_media_upload(
    payload: CommitDiaryMediaRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Finalize a previously chunk-uploaded diary media file: encrypt it and create DB record.
    
    Uses the core chunk upload service for efficient uploading, then applies diary-specific
    encryption with the naming scheme: {date}_{diary_id}_{media_id}.dat
    """
    try:
        # Check if diary is unlocked first
        diary_password = _get_diary_password_from_session(current_user.id)
        if not diary_password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Diary is locked. Please unlock diary first."
            )
            
        # Verify diary entry exists and belongs to user
        entry_result = await db.execute(
            select(DiaryEntry).where(
                and_(
                    DiaryEntry.id == payload.entry_id,
                    DiaryEntry.user_id == current_user.id
                )
            )
        )
        entry = entry_result.scalar_one_or_none()
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Diary entry not found"
            )

        # Create DiaryMedia record first to get the ID for filename
        diary_media = DiaryMedia(
            uuid=str(uuid_lib.uuid4()),
            diary_entry_uuid=payload.entry_id,
            user_id=current_user.id,
            filename="temp",  # Will be updated with proper name
            original_name="",
            file_path="temp",  # Will be updated
            file_size=0,  # Placeholder, will be updated after encryption
            mime_type="application/octet-stream",  # Default, will be updated
            media_type=payload.media_type,
            caption=payload.caption,
            is_encrypted=True
        )
        
        db.add(diary_media)
        await db.flush()  # Get the ID without committing

        # Generate proper filename using the naming scheme: {date}_{diary_id}_{media_id}.dat
        entry_date = entry.date.strftime("%Y-%m-%d")
        encrypted_filename = f"{entry_date}_{payload.entry_id}_{diary_media.id}.dat"

        # Check assembled file status
        status_obj = await chunk_manager.get_upload_status(payload.file_id)
        if not status_obj or status_obj.get("status") != "completed":
            raise HTTPException(status_code=400, detail="File not yet assembled")

        # Locate assembled file path
        temp_dir = Path(get_data_dir()) / "temp_uploads"
        assembled = next(temp_dir.glob(f"complete_{payload.file_id}_*"), None)
        if not assembled:
            raise HTTPException(status_code=404, detail="Assembled file not found")

        # Prepare destination directory
        media_dir = get_file_storage_dir() / "secure" / "entries" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        
        encrypted_file_path = media_dir / encrypted_filename

        # Read the assembled file content for encryption
        with open(assembled, "rb") as f:
            file_content = f.read()

        # SECURITY: Use proper diary password-based encryption
        import os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        
        # Generate random IV for this media file
        iv = os.urandom(12)
        
        # Use already-derived encryption key from session
        encryption_key = diary_password
        aesgcm = AESGCM(encryption_key)
        
        # Encrypt the file content using user's unique key
        encrypted_content = aesgcm.encrypt(iv, file_content, None)
        
        logger.info(f"🔒 Media encrypted using user-specific diary password for user {current_user.id}")
        
        # Split into ciphertext and tag (last 16 bytes)
        ciphertext = encrypted_content[:-16]
        auth_tag = encrypted_content[-16:]
        
        # Extract file extension
        file_extension = assembled.suffix.lower() if assembled.suffix else ""
        
        # Use diary_encryption utility to write the encrypted file
        write_result = write_encrypted_file(
            dest_path=encrypted_file_path,
            iv_b64=base64.b64encode(iv).decode(),
            encrypted_blob_b64=base64.b64encode(ciphertext).decode(),
            original_extension=file_extension
        )

        # Get file size before database operations
        assembled_file_size = assembled.stat().st_size
        
        # Update the DiaryMedia record with proper values
        diary_media.filename = encrypted_filename
        diary_media.file_path = str(encrypted_file_path)
        diary_media.file_size = assembled_file_size
        diary_media.mime_type = status_obj.get("mime_type", "application/octet-stream")

        # Calculate and update entry media count
        media_count_result = await db.execute(
            select(func.count(DiaryMedia.id)).where(DiaryMedia.diary_entry_uuid == payload.entry_id)
        )
        new_media_count = media_count_result.scalar() or 0
        entry.media_count = new_media_count

        await db.commit()
        await db.refresh(diary_media)

        # Clean up temporary assembled file
        try:
            assembled.unlink()
            logger.debug(f"🗑️ Cleaned up temporary file: {assembled}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to cleanup temporary file: {e}")

        # Remove tracking from chunk manager
        if payload.file_id in chunk_manager.uploads:
            del chunk_manager.uploads[payload.file_id]

        logger.info(f"✅ Diary media committed successfully: {encrypted_filename}")
        
        return DiaryMediaResponse(
            uuid=str(diary_media.id),
            entry_id=diary_media.diary_entry_uuid,
            filename_encrypted=diary_media.filename,
            mime_type=diary_media.mime_type,
            size_bytes=diary_media.file_size,
            media_type=diary_media.media_type,
            duration_seconds=None,  # Could be extracted for audio/video files
            created_at=diary_media.created_at
        )
        
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ Error committing diary media upload: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to commit media upload"
        )

@router.get("/media/{media_id}/download")
async def download_diary_media(
    media_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Download and decrypt diary media file.
    
    Returns the original decrypted file with proper content headers.
    Requires diary to be unlocked.
    """
    try:
        # SECURITY: Check diary is unlocked and get password from session  
        diary_password = _get_diary_password_from_session(current_user.id)
        if not diary_password:
            logger.warning(f"❌ Diary not unlocked for download attempt by user {current_user.id}")
            raise HTTPException(status_code=401, detail="Diary is locked. Please unlock first.")
        
        # Get media record
        result = await db.execute(
            select(DiaryMedia)
            .join(DiaryEntry)
            .where(
                and_(
                    DiaryMedia.id == media_id,
                    DiaryEntry.user_id == current_user.id
                )
            )
        )
        media = result.scalar_one_or_none()
        if not media:
            raise HTTPException(status_code=404, detail="Media file not found")
        
        # Check if encrypted file exists
        file_path = Path(media.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Media file not found on disk")
        
        # Decrypt the file and return decrypted content
        try:
            from app.utils.diary_encryption import read_encrypted_header
            
            # Read encrypted file header
            extension, iv, tag, header_size = read_encrypted_header(file_path)
            
            # Read ciphertext after header
            with open(file_path, "rb") as f:
                f.seek(header_size)
                ciphertext = f.read()
            
            # Decrypt using user's diary password
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            
            encryption_key = diary_password
            aesgcm = AESGCM(encryption_key)
            
            # Decrypt the content
            decrypted_content = aesgcm.decrypt(iv, ciphertext + tag, None)
            
            # Create temporary file for decrypted content
            import tempfile
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}" if extension else "")
            temp_file.write(decrypted_content)
            temp_file.close()
            
            logger.info(f"🔓 Successfully decrypted media {media_id} for user {current_user.id}")
            
            # Return decrypted file
            return FileResponse(
                path=temp_file.name,
                filename=f"{media.original_name}",
                media_type=media.mime_type,
                headers={
                    "X-Media-Type": media.media_type,
                    "X-File-Size": str(len(decrypted_content)),
                    "X-Is-Encrypted": "false"
                }
            )
            
        except InvalidPKMSFile as e:
            logger.error(f"❌ Corrupt media file for media {media_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Media file is corrupted or has invalid format: {str(e)}"
            )
        except FileNotFoundError:
            logger.error(f"❌ Media file missing for media {media_id}: {file_path}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media file not found on disk"
            )
        except PermissionError:
            logger.error(f"❌ Permission denied accessing media file {media_id}: {file_path}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied accessing media file"
            )
        except ValueError as e:
            # Decryption failed (wrong password, corrupted data, etc.)
            logger.error(f"❌ Decryption failed for media {media_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Failed to decrypt media file - invalid password or corrupted data"
            )
        except OSError as e:
            logger.error(f"❌ File system error for media {media_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"File system error: {str(e)}"
            )
        except Exception as e:
            # Catch any other unexpected errors during decryption/file handling
            logger.error(f"❌ Unexpected error during media decryption {media_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during media decryption"
            )
        
    except HTTPException:
        # Re-raise specific HTTP exceptions (from above)
        raise
    except Exception as e:
        # Catch remaining unexpected errors (like database connection issues)
        logger.error(f"❌ Unexpected error downloading diary media {media_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while downloading media"
        )

@router.get("/calendar/{year}/{month}", response_model=Dict[str, List[DiaryCalendarData]])
async def get_calendar_data(
    year: int,
    month: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"🗓️ Fetching calendar data for user {current_user.id}, year {year}, month {month}")
    """Get calendar data for a specific month, showing which days have entries."""
    
    # Subquery to count media per day
    media_count_subquery = (
        select(
            func.date(DiaryMedia.created_at).label("media_date"),
            func.count(DiaryMedia.id).label("media_count")
        )
        .where(
            and_(
                DiaryMedia.user_id == current_user.id,
                extract('year', DiaryMedia.created_at) == year,
                extract('month', DiaryMedia.created_at) == month
            )
        )
        .group_by(func.date(DiaryMedia.created_at))
        .subquery()
    )

    query = (
        select(
            func.date(DiaryEntry.date).label("entry_date"),
            func.avg(DiaryEntry.mood).label("avg_mood"),
            func.count(DiaryEntry.id).label("entry_count")
        )
        .where(
            and_(
                DiaryEntry.user_id == current_user.id,
                extract('year', DiaryEntry.date) == year,
                extract('month', DiaryEntry.date) == month,
                DiaryEntry.is_template == False
            )
        )
        .group_by(func.date(DiaryEntry.date))
    )
    
    result = await db.execute(query)
    db_data = {}
    for row in result.all():
        # Handle case where entry_date might be string or date
        if isinstance(row.entry_date, str):
            date_key = row.entry_date
        else:
            date_key = row.entry_date.strftime('%Y-%m-%d')
        db_data[date_key] = row

    media_result = await db.execute(select(media_count_subquery))
    media_data = {}
    for row in media_result.all():
        # Handle case where media_date might be string or date
        if isinstance(row.media_date, str):
            date_key = row.media_date
        else:
            date_key = row.media_date.strftime('%Y-%m-%d')
        media_data[date_key] = row.media_count

    calendar_data = []
    import calendar
    num_days = calendar.monthrange(year, month)[1]
    for day in range(1, num_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        day_data = db_data.get(date_str)
        media_count = media_data.get(date_str, 0)
        
        calendar_data.append(
            DiaryCalendarData(
                date=date_str,
                has_entry=day_data is not None and day_data.entry_count > 0,
                mood=round(day_data.avg_mood) if (day_data and day_data.avg_mood) else None,
                media_count=media_count
            )
        )
        
    return {"calendar_data": calendar_data}

@router.get("/stats/mood", response_model=MoodStats)
async def get_mood_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get mood statistics."""
    # Mood Distribution
    dist_query = (
        select(DiaryEntry.mood, func.count(DiaryEntry.id).label("count"))
        .where(
            and_(
                DiaryEntry.user_id == current_user.id,
                DiaryEntry.mood.isnot(None)
            )
        )
        .group_by(DiaryEntry.mood)
    )
    dist_result = await db.execute(dist_query)
    mood_distribution = {mood: count for mood, count in dist_result.all()}

    # Average Mood
    avg_query = select(func.avg(DiaryEntry.mood)).where(
        and_(
            DiaryEntry.user_id == current_user.id,
            DiaryEntry.mood.isnot(None)
        )
    )
    average_mood = (await db.execute(avg_query)).scalar_one_or_none()

    # Total entries with mood
    total_entries = sum(mood_distribution.values())

    return MoodStats(
        average_mood=average_mood,
        mood_distribution=mood_distribution,
        total_entries=total_entries
    )
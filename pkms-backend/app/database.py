"""
PKMS Backend Database Configuration
SQLAlchemy async setup with session management
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import event, text, inspect
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from app.config import get_database_url, settings, get_data_dir
from app.services.fts_service import fts_service

# Import Base and all models to register them with Base.metadata
# This ensures all tables are created by Base.metadata.create_all()
from app.models.base import Base
from app.models.user import User, Session, RecoveryKey
from app.models.note import Note, NoteFile
from app.models.document import Document
from app.models.todo import Todo, Project
from app.models.diary import DiaryEntry, DiaryMedia
from app.models.archive import ArchiveFolder, ArchiveItem
from app.models.tag import Tag
from app.models.link import Link
# Import all tag association tables
from app.models.tag_associations import (
    note_tags, document_tags, todo_tags, 
    diary_tags, archive_tags, link_tags
)

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create async engine
engine = create_async_engine(
    get_database_url(),
    echo=settings.debug,  # Log SQL queries in debug mode
    poolclass=StaticPool,  # Better for SQLite
    connect_args={"check_same_thread": False, "timeout": 20} if "sqlite" in get_database_url() else {}
)

# SQLite Foreign Key Event Listener
# Ensures PRAGMA foreign_keys = ON for every new connection
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign keys for every SQLite connection"""
    if "sqlite" in get_database_url():
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            logger.debug("✅ Foreign keys enabled for new connection")
        except Exception as e:
            logger.warning(f"⚠️ Failed to enable foreign keys on connection: {e}")

# Create async session factory (SQLAlchemy 2.0 syntax)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session() -> AsyncSession:
    """Context manager for database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


async def verify_table_schema(table_name: str) -> None:
    """Verify the schema of a specific table"""
    try:
        async with get_db_session() as session:
            result = await session.execute(
                text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
            )
            if result.fetchone():
                logger.info(f"✅ Table '{table_name}' exists")
            else:
                logger.warning(f"⚠️ Table '{table_name}' not found")
    except Exception as e:
        logger.error(f"❌ Error verifying table '{table_name}': {e}")


async def init_db():
    """Initialize database and create all tables, indexes, FTS tables, and triggers"""
    
    try:
        # Ensure data directory exists
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📁 Data directory: {data_dir}")
        
        # Phase 1: Configure SQLite with optimizations and fallbacks
        logger.info("🔧 Phase 1: Configuring SQLite optimizations...")
        async with get_db_session() as session:
            # Enable foreign keys first (this is critical and usually works)
            try:
                await session.execute(text("PRAGMA foreign_keys = ON;"))
                logger.info("✅ Foreign keys enabled")
            except Exception as e:
                logger.warning(f"⚠️ Could not enable foreign keys: {e}")

            # --- Journal mode with full fallback ---
            # Try each mode with graceful degradation to ensure startup success
            journal_mode = "default"
            for mode in ["WAL", "TRUNCATE", "DELETE"]:
                try:
                    await session.execute(text(f"PRAGMA journal_mode = {mode};"))
                    journal_mode = mode
                    logger.info(f"✅ Journal mode set to {mode}")
                    break
                except Exception as e:
                    logger.warning(f"⚠️ Journal mode {mode} failed: {e}")
                    continue
            
            if journal_mode == "default":
                logger.warning("⚠️ All journal modes failed, using SQLite default")

            # Other optimizations (with individual fallbacks)
            optimizations = [
                ("PRAGMA synchronous = NORMAL;", "synchronous mode"),
                ("PRAGMA cache_size = -64000;", "cache size"),
                ("PRAGMA temp_store = memory;", "temp store"),
                ("PRAGMA mmap_size = 268435456;", "memory mapping"),
                ("PRAGMA busy_timeout = 30000;", "busy timeout"),
                ("PRAGMA wal_autocheckpoint = 1000;", "WAL autocheckpoint")
            ]
            
            for pragma_sql, description in optimizations:
                try:
                    await session.execute(text(pragma_sql))
                    logger.debug(f"✅ {description} optimization applied")
                except Exception as e:
                    logger.warning(f"⚠️ {description} optimization failed: {e}")
            
            logger.info("✅ SQLite configuration completed")
        
        # Phase 2: Create all tables from SQLAlchemy models
        logger.info("🗄️ Phase 2: Creating database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ All tables created successfully")
        
        # Phase 3: Create performance indexes
        logger.info("📊 Phase 3: Creating performance indexes...")
        async with get_db_session() as session:
            indexes = [
                # User & Auth indexes
                "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);",
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
                "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);",
                "CREATE INDEX IF NOT EXISTS idx_recovery_keys_user_id ON recovery_keys(user_id);",
                
                # Notes indexes
                "CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_notes_user_created ON notes(user_id, created_at DESC);",
                "CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC);",
                "CREATE INDEX IF NOT EXISTS idx_notes_title ON notes(title);",
                "CREATE INDEX IF NOT EXISTS idx_notes_archived ON notes(is_archived);",
                "CREATE INDEX IF NOT EXISTS idx_note_files_note_id ON note_files(note_id);",
                "CREATE INDEX IF NOT EXISTS idx_note_files_user_id ON note_files(user_id);",
                
                # Documents indexes  
                "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_documents_user_mime ON documents(user_id, mime_type);",
                "CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);",
                "CREATE INDEX IF NOT EXISTS idx_documents_uuid ON documents(uuid);",
                "CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title);",
                "CREATE INDEX IF NOT EXISTS idx_documents_archived ON documents(is_archived);",
                
                # Todos indexes
                "CREATE INDEX IF NOT EXISTS idx_todos_user_id ON todos(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_todos_user_status ON todos(user_id, is_completed);",
                "CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);",
                "CREATE INDEX IF NOT EXISTS idx_todos_user_priority_date ON todos(user_id, priority DESC, created_at DESC);",
                "CREATE INDEX IF NOT EXISTS idx_todos_due_date ON todos(due_date);",
                "CREATE INDEX IF NOT EXISTS idx_todos_project_id ON todos(project_id);",
                "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_projects_archived ON projects(is_archived);",
                
                # Diary indexes
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_user_id ON diary_entries(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_user_date ON diary_entries(user_id, date);",
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_day_of_week ON diary_entries(day_of_week);",
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_date ON diary_entries(date);",
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_mood ON diary_entries(mood);",
                "CREATE INDEX IF NOT EXISTS idx_diary_entries_location ON diary_entries(location);",
                "CREATE INDEX IF NOT EXISTS idx_diary_media_entry_id ON diary_media(diary_entry_id);",
                "CREATE INDEX IF NOT EXISTS idx_diary_media_user_id ON diary_media(user_id);",
                
                # Archive indexes
                "CREATE INDEX IF NOT EXISTS idx_archive_folders_user_id ON archive_folders(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_archive_folders_parent ON archive_folders(parent_uuid);",
                "CREATE INDEX IF NOT EXISTS idx_archive_folders_path ON archive_folders(path);",
                "CREATE INDEX IF NOT EXISTS idx_archive_folders_name ON archive_folders(name);",
                "CREATE INDEX IF NOT EXISTS idx_archive_folders_archived ON archive_folders(is_archived);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_user_id ON archive_items(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_folder ON archive_items(folder_uuid);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_name ON archive_items(name);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_mime_type ON archive_items(mime_type);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_created ON archive_items(created_at);",
                "CREATE INDEX IF NOT EXISTS idx_archive_items_archived ON archive_items(is_archived);",
                
                # Tags indexes
                "CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name);",
                "CREATE INDEX IF NOT EXISTS idx_tags_name_user ON tags(name, user_id);",
                "CREATE INDEX IF NOT EXISTS idx_tags_module_type ON tags(module_type, name);",
                "CREATE INDEX IF NOT EXISTS idx_tags_usage_count ON tags(usage_count DESC);",
                
                # Tag association indexes
                "CREATE INDEX IF NOT EXISTS idx_note_tags_note_id ON note_tags(note_id);",
                "CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id);",
                "CREATE INDEX IF NOT EXISTS idx_document_tags_document_id ON document_tags(document_id);",
                "CREATE INDEX IF NOT EXISTS idx_document_tags_tag_id ON document_tags(tag_id);",
                "CREATE INDEX IF NOT EXISTS idx_todo_tags_todo_id ON todo_tags(todo_id);",
                "CREATE INDEX IF NOT EXISTS idx_todo_tags_tag_id ON todo_tags(tag_id);",
                "CREATE INDEX IF NOT EXISTS idx_diary_tags_diary_entry_id ON diary_tags(diary_entry_id);",
                "CREATE INDEX IF NOT EXISTS idx_diary_tags_tag_id ON diary_tags(tag_id);",
                "CREATE INDEX IF NOT EXISTS idx_archive_tags_archive_item_id ON archive_tags(archive_item_id);",
                "CREATE INDEX IF NOT EXISTS idx_archive_tags_tag_id ON archive_tags(tag_id);",
            ]
            
            created_count = 0
            for index_sql in indexes:
                try:
                    await session.execute(text(index_sql))
                    created_count += 1
                    index_name = index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'unknown'
                    logger.debug(f"✅ Index ensured: {index_name}")
                except Exception as e:
                    logger.warning(f"⚠️ Index creation failed: {e}")
            
            logger.info(f"✅ {created_count} performance indexes created/verified")
        
        # Phase 4: Initialize FTS5 full-text search
        logger.info("🔍 Phase 4: Initializing FTS5 full-text search...")
        async with get_db_session() as session:
            try:
                fts_success = await fts_service.initialize_fts_tables(session)
                if fts_success:
                    # Populate FTS tables with existing data
                    populate_success = await fts_service.populate_fts_tables(session)
                    if populate_success:
                        logger.info("✅ FTS5 initialization and population completed successfully")
                    else:
                        logger.warning("⚠️ FTS5 tables created but population failed")
                else:
                    logger.warning("⚠️ FTS5 initialization failed - search performance will be limited")
            except Exception as e:
                logger.error(f"❌ FTS5 initialization error: {e}")
        
        # Phase 5: Create essential data directories
        logger.info("📁 Phase 5: Creating essential data directories...")
        essential_dirs = [
            data_dir / "secure" / "entries" / "text",
            data_dir / "secure" / "entries" / "media", 
            data_dir / "assets" / "documents",
            data_dir / "assets" / "images",
            data_dir / "archive",
            data_dir / "backups",
            data_dir / "exports",
            data_dir / "recovery",
            data_dir / "temp_uploads"
        ]
        
        for dir_path in essential_dirs:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                logger.debug(f"✅ Directory ensured: {dir_path.relative_to(data_dir)}")
            except Exception as e:
                logger.warning(f"⚠️ Could not create directory {dir_path}: {e}")
        
        logger.info("✅ Essential directories created")
        
        # Phase 6: Database integrity and optimization
        logger.info("🔧 Phase 6: Running database integrity checks...")
        async with get_db_session() as session:
            try:
                # Run integrity check
                result = await session.execute(text("PRAGMA integrity_check;"))
                integrity_result = result.fetchone()
                if integrity_result and integrity_result[0] == "ok":
                    logger.info("✅ Database integrity check passed")
                else:
                    logger.warning(f"⚠️ Database integrity check result: {integrity_result}")
                
                # Optimize database
                await session.execute(text("PRAGMA optimize;"))
                logger.info("✅ Database optimization completed")
                
            except Exception as e:
                logger.warning(f"⚠️ Database integrity/optimization error: {e}")
        
        logger.info("🎉 Database initialization completed successfully!")
        logger.info("=" * 60)
        logger.info("✅ PKMS Database is ready for use")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {str(e)}")
        logger.error("This is a critical error - the application cannot start without a working database")
        raise


async def close_db():
    """Close database connections"""
    await engine.dispose()
    logger.info("🔌 Database connections closed") 
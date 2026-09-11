"""
Database setup — PostgreSQL via SQLAlchemy.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, Text, DateTime, Float, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Render gives postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CourseChunk(Base):
    __tablename__ = "course_chunks"

    id          = Column(Integer, primary_key=True, index=True)
    file_name   = Column(Text, nullable=False)
    file_id     = Column(Text, nullable=False, index=True)
    chunk_index = Column(Integer, default=0)
    total_chunks = Column(Integer, default=1)
    content     = Column(Text, nullable=False)
    source_type = Column(Text, default="text")
    processed_at = Column(DateTime, server_default=func.now())


class LibraryChunk(Base):
    __tablename__ = "library_chunks"

    id           = Column(Integer, primary_key=True, index=True)
    file_name    = Column(Text, nullable=False)
    file_id      = Column(Text, nullable=False, index=True)
    category     = Column(Text, nullable=False, default="General")
    subcategory  = Column(Text, nullable=True)
    chunk_index  = Column(Integer, default=0)
    total_chunks = Column(Integer, default=1)
    content      = Column(Text, nullable=False)
    source_type  = Column(Text, default="text")
    processed_at = Column(DateTime, server_default=func.now())


class LibraryVideo(Base):
    __tablename__ = "library_videos"

    id           = Column(Integer, primary_key=True, index=True)
    file_name    = Column(Text, nullable=False)
    file_id      = Column(Text, nullable=False, unique=True, index=True)
    category     = Column(Text, nullable=False, default="General")
    subcategory  = Column(Text, nullable=True)
    drive_link   = Column(Text, nullable=False)
    size_mb      = Column(Float, default=0)
    mime_type    = Column(Text, default="video/mp4")
    processed_at = Column(DateTime, server_default=func.now())


def init_db():
    """Create tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    # Auto-migrate: add subcategory column to library_chunks if absent
    with SessionLocal() as db:
        try:
            db.execute(text(
                "ALTER TABLE library_chunks ADD COLUMN IF NOT EXISTS subcategory TEXT"
            ))
            db.commit()
        except Exception:
            db.rollback()


def already_processed(file_id: str) -> bool:
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT 1 FROM course_chunks WHERE file_id = :fid LIMIT 1"),
            {"fid": file_id}
        ).fetchone()
        return row is not None


def store_chunks(file_name: str, file_id: str, content: str, source_type: str):
    """Split content into chunks and store in DB."""
    size, overlap = 3000, 300
    chunks, start = [], 0
    while start < len(content):
        chunks.append(content[start:start + size])
        start += size - overlap

    with SessionLocal() as db:
        for i, chunk in enumerate(chunks):
            db.add(CourseChunk(
                file_name=file_name,
                file_id=file_id,
                chunk_index=i,
                total_chunks=len(chunks),
                content=chunk,
                source_type=source_type,
            ))
        db.commit()
    return len(chunks)


def search_chunks(query: str, top_n: int = 6) -> str:
    """Full-text search using PostgreSQL ts_vector."""
    with SessionLocal() as db:
        # Use PostgreSQL full-text search
        rows = db.execute(
            text("""
                SELECT file_name, source_type, content,
                       ts_rank(to_tsvector('english', content),
                               plainto_tsquery('english', :q)) AS rank
                FROM course_chunks
                WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                ORDER BY rank DESC
                LIMIT :n
            """),
            {"q": query, "n": top_n}
        ).fetchall()

        if not rows:
            # Fallback: return most recent chunks as context
            rows = db.execute(
                text("SELECT file_name, source_type, content FROM course_chunks ORDER BY id DESC LIMIT :n"),
                {"n": top_n}
            ).fetchall()

    if not rows:
        return "No specific course content matched. Answer based on general Amy Porterfield principles."

    parts = [f"[Source: {r.file_name} ({r.source_type})]\n{r.content}" for r in rows]
    return "\n\n---\n\n".join(parts)


# ── Library helpers ────────────────────────────────────────────────────────────

def library_already_processed(file_id: str) -> bool:
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT 1 FROM library_chunks WHERE file_id = :fid LIMIT 1"),
            {"fid": file_id}
        ).fetchone()
        return row is not None


def library_video_exists(file_id: str) -> bool:
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT 1 FROM library_videos WHERE file_id = :fid LIMIT 1"),
            {"fid": file_id}
        ).fetchone()
        return row is not None


def store_library_chunks(file_name: str, file_id: str, content: str,
                         source_type: str, category: str, subcategory: str = None):
    size, overlap = 3000, 300
    chunks, start = [], 0
    while start < len(content):
        chunks.append(content[start:start + size])
        start += size - overlap

    with SessionLocal() as db:
        for i, chunk in enumerate(chunks):
            db.add(LibraryChunk(
                file_name=file_name,
                file_id=file_id,
                category=category,
                subcategory=subcategory,
                chunk_index=i,
                total_chunks=len(chunks),
                content=chunk,
                source_type=source_type,
            ))
        db.commit()
    return len(chunks)


def store_library_video(file_name: str, file_id: str, category: str,
                        subcategory: str, drive_link: str, size_mb: float,
                        mime_type: str = "video/mp4"):
    with SessionLocal() as db:
        try:
            db.add(LibraryVideo(
                file_name=file_name,
                file_id=file_id,
                category=category,
                subcategory=subcategory,
                drive_link=drive_link,
                size_mb=size_mb,
                mime_type=mime_type,
            ))
            db.commit()
        except Exception:
            db.rollback()  # Already exists — skip gracefully


def search_library_chunks(query: str, top_n: int = 6, category: str = None) -> str:
    with SessionLocal() as db:
        if category:
            rows = db.execute(
                text("""
                    SELECT file_name, category, source_type, content,
                           ts_rank(to_tsvector('english', content),
                                   plainto_tsquery('english', :q)) AS rank
                    FROM library_chunks
                    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                      AND category = :cat
                    ORDER BY rank DESC LIMIT :n
                """),
                {"q": query, "n": top_n, "cat": category}
            ).fetchall()
        else:
            rows = db.execute(
                text("""
                    SELECT file_name, category, source_type, content,
                           ts_rank(to_tsvector('english', content),
                                   plainto_tsquery('english', :q)) AS rank
                    FROM library_chunks
                    WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                    ORDER BY rank DESC LIMIT :n
                """),
                {"q": query, "n": top_n}
            ).fetchall()

    if not rows:
        return ""
    parts = [f"[{r.category} — {r.file_name}]\n{r.content}" for r in rows]
    return "\n\n---\n\n".join(parts)


def get_library_docs(category: str = None):
    """Return distinct documents in the library (for browsing)."""
    with SessionLocal() as db:
        if category:
            rows = db.execute(
                text("""
                    SELECT DISTINCT ON (file_id)
                        file_id, file_name, category, subcategory, source_type, processed_at
                    FROM library_chunks
                    WHERE category = :cat
                    ORDER BY file_id, processed_at DESC
                """),
                {"cat": category}
            ).fetchall()
        else:
            rows = db.execute(
                text("""
                    SELECT DISTINCT ON (file_id)
                        file_id, file_name, category, subcategory, source_type, processed_at
                    FROM library_chunks
                    ORDER BY file_id, processed_at DESC
                """)
            ).fetchall()
    return [{"file_id": r.file_id, "file_name": r.file_name,
             "category": r.category, "subcategory": r.subcategory,
             "source_type": r.source_type} for r in rows]


def get_document_content(file_id: str) -> str:
    """Return full content of a document by file_id."""
    with SessionLocal() as db:
        rows = db.execute(
            text("""
                SELECT content FROM library_chunks
                WHERE file_id = :fid
                ORDER BY chunk_index
            """),
            {"fid": file_id}
        ).fetchall()
    return "\n".join(r.content for r in rows)


def get_library_videos(category: str = None):
    with SessionLocal() as db:
        if category:
            rows = db.execute(
                text("SELECT * FROM library_videos WHERE category = :cat ORDER BY category, subcategory, file_name"),
                {"cat": category}
            ).fetchall()
        else:
            rows = db.execute(
                text("SELECT * FROM library_videos ORDER BY category, subcategory, file_name")
            ).fetchall()
    return [{"file_id": r.file_id, "file_name": r.file_name,
             "category": r.category, "subcategory": r.subcategory,
             "drive_link": r.drive_link, "size_mb": r.size_mb,
             "mime_type": r.mime_type} for r in rows]

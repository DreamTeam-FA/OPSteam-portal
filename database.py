"""
Database setup — PostgreSQL via SQLAlchemy.
"""

import os
from sqlalchemy import create_engine, Column, Integer, Text, DateTime, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

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


def init_db():
    """Create tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


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

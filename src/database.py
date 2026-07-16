from datetime import datetime
import pathlib
import sys
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session, text

class KnownWord(SQLModel, table=True):
    """Words the user already knows."""
    id: Optional[int] = Field(default=None, primary_key=True)
    word: str = Field(index=True, unique=True)

class SkippedWord(SQLModel, table=True):
    """Words the user skipped to be moved to the back of the queue."""
    id: Optional[int] = Field(default=None, primary_key=True)
    word: str = Field(index=True, unique=True)

class FrequencyWord(SQLModel, table=True):
    """Dictionary frequency ranks for Japanese words."""
    id: Optional[int] = Field(default=None, primary_key=True)
    word: str = Field(index=True, unique=True)
    rank: int

class MinedCard(SQLModel, table=True):
    """Saved flashcards ready for Anki export."""
    id: Optional[int] = Field(default=None, primary_key=True)
    sentence: str
    target_word: str
    reading: str = Field(default="")
    definition: str
    anki_note_id: Optional[int] = Field(default=None, index=True)
    audio_path: Optional[str] = Field(default=None)
    image_path: Optional[str] = Field(default=None)
    base_score: Optional[float] = Field(default=None)
    adjusted_score: Optional[float] = Field(default=None)
    known_words: Optional[str] = Field(default=None)
    unknown_words: Optional[str] = Field(default=None)
    tags: Optional[str] = Field(default=None)
    created_at: Optional[datetime] = Field(default_factory=datetime.now)

class MiningSession(SQLModel, table=True):
    """Saved history of mined subtitle and video files."""
    id: Optional[int] = Field(default=None, primary_key=True)
    subtitle_path: str = Field(index=True)
    video_path: str = Field(default="")
    
class PitchAccentCache(SQLModel, table=True):
    """Cached pitch accent data to prevent repeated API calls."""
    id: Optional[int] = Field(default=None, primary_key=True)
    word: str = Field(index=True)
    reading: str = Field(index=True)
    pitch: str

    
DB_FILE = (
    pathlib.Path.home() / "AppData" / "Local" / "nihongo-miner" / "data.db"
    if sys.platform == "win32"
    else (
        pathlib.Path.home() / "Library" / "Application Support" / "nihongo-miner" / "data.db"
        if sys.platform == "darwin"
        else pathlib.Path.home() / ".local" / "share" / "nihongo-miner" / "data.db"
    )
)
sqlite_url = f"sqlite:///{DB_FILE.absolute()}"

engine = create_engine(sqlite_url, echo=False)

def create_db_and_tables() -> None:
    """Initializes the database schema."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    
    # Safe migration: add 'tags' column to minedcard if it doesn't exist
    try:
        with engine.begin() as connection:
            result = connection.execute(text("PRAGMA table_info(minedcard)"))
            columns = [row[1] for row in result.fetchall()]
            if "tags" not in columns:
                connection.execute(text("ALTER TABLE minedcard ADD COLUMN tags TEXT"))
            if "created_at" not in columns:
                connection.execute(text("ALTER TABLE minedcard ADD COLUMN created_at DATETIME"))
                connection.execute(text("UPDATE minedcard SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
    except Exception as e:
        print(f"[bold yellow]Warning:[/bold yellow] Failed to run database migration: {e}")

def get_session() -> Session:
    """Returns a new database session."""
    return Session(engine)

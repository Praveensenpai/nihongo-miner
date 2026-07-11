import pathlib
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session

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

DB_FILE = pathlib.Path("data.db")
sqlite_url = f"sqlite:///{DB_FILE.absolute()}"

engine = create_engine(sqlite_url, echo=False)

def create_db_and_tables():
    """Initializes the database schema."""
    SQLModel.metadata.create_all(engine)

def get_session():
    """Returns a new database session."""
    return Session(engine)

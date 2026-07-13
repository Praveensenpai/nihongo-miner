import json
import urllib.request
from sqlmodel import select

from src.database import get_session, PitchAccentCache


def count_morae(text: str) -> int:
    """Helper to count the number of morae in a Japanese string, ignoring small kana."""
    small_kana = set("ゃゅょぁぃぅぇぉャュョァィゥェォ")
    return sum(1 for char in text if char not in small_kana)


def get_pitch_accent(word: str, reading: str) -> str:
    """Queries the Jotoba API for pitch accent data of a specific word and reading.
    
    Returns a string of 'H' and 'L' representing High and Low pitch accents,
    or an empty string if not found or on error.
    """
    if not word or not reading:
        return ""
        
    with get_session() as session:
        statement = select(PitchAccentCache).where(
            PitchAccentCache.word == word,
            PitchAccentCache.reading == reading
        )
        cached = session.exec(statement).first()
        if cached:
            return cached.pitch

        url = "https://jotoba.de/api/search/words"
        data = json.dumps({"query": word, "language": "English"}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as response:
                res = json.loads(response.read().decode())
                
                pitch_str = ""
                found = False
                if res.get("words"):
                    for w in res["words"]:
                        w_kana = w["reading"]["kana"]
                        w_kanji = w["reading"].get("kanji", w_kana)
                        if w_kanji == word or w_kana == reading:
                            pitch_data = w.get("pitch")
                            if pitch_data:
                                for part in pitch_data:
                                    mora_count = count_morae(part["part"])
                                    pitch_char = "H" if part["high"] else "L"
                                    pitch_str += pitch_char * mora_count
                                found = True
                                break
                            
                # Cache the result (even if empty string to avoid repeated API calls for words with no pitch)
                new_cache = PitchAccentCache(word=word, reading=reading, pitch=pitch_str)
                session.add(new_cache)
                session.commit()
                
                return pitch_str
        except Exception:
            return ""

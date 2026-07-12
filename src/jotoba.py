import json
import urllib.request


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
        
    url = "https://jotoba.de/api/search/words"
    data = json.dumps({"query": word, "language": "English"}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            res = json.loads(response.read().decode())
            if not res.get("words"):
                return ""
            for w in res["words"]:
                w_kana = w["reading"]["kana"]
                w_kanji = w["reading"].get("kanji", w_kana)
                if w_kanji == word or w_kana == reading:
                    pitch_data = w.get("pitch")
                    if pitch_data:
                        pitch_str = ""
                        for part in pitch_data:
                            mora_count = count_morae(part["part"])
                            pitch_char = "H" if part["high"] else "L"
                            pitch_str += pitch_char * mora_count
                        return pitch_str
            return ""
    except Exception:
        return ""

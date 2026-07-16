import pathlib
from typing import List, Union
from sudachipy import dictionary, tokenizer  # type: ignore[import-untyped]


def katakana_to_hiragana(text: str) -> str:
    """Converts a string of Katakana characters to Hiragana."""
    return "".join(
        chr(ord(char) - 96) if 0x30A1 <= ord(char) <= 0x30F6 else char for char in text
    )


def _contains_kanji(text: str) -> bool:
    """Returns True if the text contains at least one CJK kanji character."""
    return any(
        0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF for ch in text
    )


def furigana_sentence(text: str) -> str:
    """
    Returns an HTML string of the sentence with furigana <ruby> tags over kanji tokens.
    Uses SudachiPy's contextual reading_form() for accurate per-context readings.
    Tokens without kanji (kana-only, punctuation) are passed through unchanged.
    """
    _tokenizer = dictionary.Dictionary().create()
    _mode = tokenizer.Tokenizer.SplitMode.A
    raw_tokens = _tokenizer.tokenize(text, _mode)
    parts: List[str] = []
    for token in raw_tokens:
        surface = token.surface()
        if not _contains_kanji(surface):
            parts.append(surface)
            continue
        try:
            katakana_reading = token.reading_form()
            hiragana_reading = (
                katakana_to_hiragana(katakana_reading) if katakana_reading else ""
            )
        except Exception:
            hiragana_reading = ""
        if hiragana_reading and hiragana_reading != surface:
            parts.append(f"<ruby>{surface}<rt>{hiragana_reading}</rt></ruby>")
        else:
            parts.append(surface)
    return "".join(parts)


def clean_tag_from_path(path: Union[str, pathlib.Path]) -> str:
    """Generates a clean, valid Anki tag from a file path."""
    import re

    p = pathlib.Path(path)
    stem = p.stem.strip()
    if stem.endswith("_synced"):
        stem = stem[:-7]
    # Replace non-alphanumeric character sequences with a single underscore
    cleaned = re.sub(r"[^\w]+", "_", stem)
    # Strip leading/trailing underscores
    cleaned = cleaned.strip("_")
    return cleaned

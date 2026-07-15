import asyncio
import typer
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import IntPrompt
import dataclasses
import json
import pathlib
import re
import subprocess
import os
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Any

from jamdict import Jamdict
from sqlmodel import select
from sudachipy import dictionary, tokenizer

from src.database import KnownWord, FrequencyWord, MinedCard, MiningSession, get_session, create_db_and_tables
from src.anki import AnkiClient
from src.jpdb import scrape_jpdb, get_jpdb_global_rank, list_cached_jpdb
from src.jotoba import get_pitch_accent, prefetch_pitch_accents

app = typer.Typer(rich_markup_mode="rich")


from src.utils import katakana_to_hiragana, furigana_sentence

GRAMMAR_DICT: Dict[str, Dict[str, str]] = {
    "〜てしまう": {
        "definition": "to end up doing (something/accidentally/regretfully); to complete thoroughly",
        "reading": "てしまう"
    },
    "〜てみる": {
        "definition": "to try doing (something); to attempt",
        "reading": "てみる"
    },
    "〜てください": {
        "definition": "please do (polite request)",
        "reading": "てください"
    },
    "〜たことがある": {
        "definition": "to have the experience of doing (something) in the past",
        "reading": "たことがある"
    },
    "〜やすい": {
        "definition": "easy to do; simple to; likely to",
        "reading": "やすい"
    },
    "〜にくい": {
        "definition": "hard to do; difficult to; unlikely to",
        "reading": "にくい"
    },
    "〜てほしい": {
        "definition": "want someone to do; I would like you to do; please do for me",
        "reading": "てほしい"
    },
    "〜ざるを得ない": {
        "definition": "cannot help but; cannot avoid; have no choice but to",
        "reading": "ざるをえない"
    },
    "〜かもしれない": {
        "definition": "might; perhaps; may; possibly",
        "reading": "かもしれない"
    },
    "〜わけではない": {
        "definition": "it doesn't mean that; it is not the case that; not necessarily",
        "reading": "わけではない"
    },
    "〜わけにはいかない": {
        "definition": "cannot afford to; must not; no way one can do",
        "reading": "わけにはいかない"
    },
    "〜そうだ": {
        "definition": "looks like; seems like; appears; I hear that",
        "reading": "そうだ"
    },
    "〜すぎる": {
        "definition": "too much; excessively",
        "reading": "すぎる"
    },
    "〜ようになる": {
        "definition": "to reach the point where; to come to be; to start to",
        "reading": "ようになる"
    }
}


def detect_grammar_patterns(tokens: Sequence[Any]) -> List[Tuple[int, int, str]]:
    """
    Scans a list of raw SudachiPy tokens.
    Returns a list of tuples: (start_index, end_index, pattern_display_form)
    indices are inclusive of the matched tokens range in the raw list.
    """
    matches = []
    i = 0
    n = len(tokens)
    while i < n:
        # 1. 〜たことがある (past verb + ことが ある/あった)
        # Sequence: [Verb] + [た/だ] + [こと] + [が/も/は] + [ある/あった]
        if i + 4 < n:
            t0, t1, t2, t3, t4 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3], tokens[i+4]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t3_pos = t3.part_of_speech()[0]
            t4_lemma = t4.dictionary_form()
            if (t0_pos == "動詞" and 
                t1_lemma in ("た", "だ") and 
                t2_lemma == "こと" and 
                t3_pos == "助詞" and 
                t4_lemma == "ある"):
                matches.append((i, i + 4, "〜たことがある"))
                i += 5
                continue

        # 〜ざるを得ない
        # Sequence: [Verb] + [ざる] + [を] + [得/え (lemma: 得る/える)] + [ない]
        if i + 4 < n:
            t0, t1, t2, t3, t4 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3], tokens[i+4]
            t0_pos = t0.part_of_speech()[0]
            t1_surface = t1.surface()
            t2_surface = t2.surface()
            t3_lemma = t3.dictionary_form()
            t4_lemma = t4.dictionary_form()
            if (t0_pos == "動詞" and 
                t1_surface == "ざる" and 
                t2_surface == "を" and 
                t3_lemma in ("得る", "える") and 
                t4_lemma == "ない"):
                matches.append((i, i + 4, "〜ざるを得ない"))
                i += 5
                continue

        # 〜かもしれない
        # Sequence: [Word] + [か] + [も] + [しれ/知れ (lemma: しれる/知れる)] + [ない]
        if i + 4 < n:
            t0, t1, t2, t3, t4 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3], tokens[i+4]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t3_lemma = t3.dictionary_form()
            t4_lemma = t4.dictionary_form()
            if (t1_lemma == "か" and 
                t2_lemma == "も" and 
                t3_lemma in ("しれる", "知れる") and 
                t4_lemma == "ない"):
                matches.append((i, i + 4, "〜かもしれない"))
                i += 5
                continue

        # 〜わけにはいかない
        # Sequence: [Word] + [わけ] + [に] + [は] + [いかない/行く/いく]
        if i + 4 < n:
            t0, t1, t2, t3, t4 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3], tokens[i+4]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t3_lemma = t3.dictionary_form()
            t4_lemma = t4.dictionary_form()
            if (t1_lemma in ("わけ", "訳") and 
                t2_lemma == "に" and 
                t3_lemma == "は" and 
                t4_lemma in ("いかない", "行く", "いく")):
                matches.append((i, i + 4, "〜わけにはいかない"))
                i += 5
                continue

        # 〜わけではない
        # Sequence: [Word] + [わけ] + [で] + [は] + [ない]
        if i + 4 < n:
            t0, t1, t2, t3, t4 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3], tokens[i+4]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t3_lemma = t3.dictionary_form()
            t4_lemma = t4.dictionary_form()
            if (t1_lemma in ("わけ", "訳") and 
                t2_lemma == "で" and 
                t3_lemma == "は" and 
                t4_lemma in ("ない", "無し", "なし")):
                matches.append((i, i + 4, "〜わけではない"))
                i += 5
                continue

        # 〜ようになる
        # Sequence: [Verb] + [よう] + [に] + [なる]
        if i + 3 < n:
            t0, t1, t2, t3 = tokens[i], tokens[i+1], tokens[i+2], tokens[i+3]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t3_lemma = t3.dictionary_form()
            if (t0_pos == "動詞" and 
                t1_lemma in ("よう", "様") and 
                t2_lemma == "に" and 
                t3_lemma in ("なる", "成る")):
                matches.append((i, i + 3, "〜ようになる"))
                i += 4
                continue

        # 2. 〜てください
        # Sequence: [Verb] + [て/で] + [くださる/ください]
        if i + 2 < n:
            t0, t1, t2 = tokens[i], tokens[i+1], tokens[i+2]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            if (t0_pos == "動詞" and 
                t1_lemma in ("て", "で") and 
                t2_lemma in ("くださる", "ください")):
                matches.append((i, i + 2, "〜てください"))
                i += 3
                continue

        # 3. 〜てしまう
        # Sequence: [Verb] + [て/で] + [しまう]
        if i + 2 < n:
            t0, t1, t2 = tokens[i], tokens[i+1], tokens[i+2]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            if (t0_pos == "動詞" and 
                t1_lemma in ("て", "で") and 
                t2_lemma == "しまう"):
                matches.append((i, i + 2, "〜てしまう"))
                i += 3
                continue

        # 4. 〜てみる
        # Sequence: [Verb] + [て/で] + [みる]
        if i + 2 < n:
            t0, t1, t2 = tokens[i], tokens[i+1], tokens[i+2]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            t2_pos = t2.part_of_speech()[0]
            if (t0_pos == "動詞" and 
                t1_lemma in ("て", "で") and 
                t2_lemma == "みる" and 
                t2_pos == "動詞"):
                matches.append((i, i + 2, "〜てみる"))
                i += 3
                continue

        # 5. 〜てほしい
        # Sequence: [Verb] + [て/で (接続助詞)] + [ほしい]
        if i + 2 < n:
            t0, t1, t2 = tokens[i], tokens[i+1], tokens[i+2]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            t1_subpos = t1.part_of_speech()[1] if len(t1.part_of_speech()) > 1 else ""
            t2_lemma = t2.dictionary_form()
            if (t0_pos == "動詞" and
                t1_lemma in ("て", "で") and
                t1_subpos == "接続助詞" and
                t2_lemma == "ほしい"):
                matches.append((i, i + 2, "〜てほしい"))
                i += 3
                continue

        # 〜そうだ (looks like / seems like / heard that)
        # Sequence: [Word] + [そう] + [だ/です/な/に]
        if i + 2 < n:
            t0, t1, t2 = tokens[i], tokens[i+1], tokens[i+2]
            t1_lemma = t1.dictionary_form()
            t2_lemma = t2.dictionary_form()
            if (t1_lemma == "そう" and 
                t2_lemma in ("だ", "です", "な", "に", "である")):
                matches.append((i, i + 2, "〜そうだ"))
                i += 3
                continue

        # 6. 〜やすい / 〜にくい
        # Sequence: [Verb] + [やすい/にくい]
        if i + 1 < n:
            t0, t1 = tokens[i], tokens[i+1]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            if t0_pos == "動詞" and t1_lemma in ("やすい", "にくい"):
                matches.append((i, i + 1, f"〜{t1_lemma}"))
                i += 2
                continue

        # 〜すぎる
        # Sequence: [Word] + [すぎる/過ぎる]
        if i + 1 < n:
            t0, t1 = tokens[i], tokens[i+1]
            t0_pos = t0.part_of_speech()[0]
            t1_lemma = t1.dictionary_form()
            if (t0_pos in ("動詞", "形容詞", "名詞") and 
                t1_lemma in ("すぎる", "過ぎる")):
                matches.append((i, i + 1, "〜すぎる"))
                i += 2
                continue

        i += 1
    return matches


@dataclasses.dataclass(frozen=True)
class SubtitleLine:
    """Represents a single subtitle entry from an SRT file."""
    index: int
    timestamp: str
    text: str


@dataclasses.dataclass(frozen=True)
class AnalyzedToken:
    """A content token normalized for mining and scoring."""
    lemma: str
    surface: str
    pos: Tuple[str, ...]
    is_proper_noun: bool
    reading: str = ""





class SubtitleParser:
    """Parses subtitle files into structured SubtitleLine objects."""
    
    def parse(self, filepath: pathlib.Path) -> List[SubtitleLine]:
        if not filepath.exists():
            return []
        
        with open(filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()

        if filepath.suffix.lower() in {".ass", ".ssa"}:
            return self._parse_ass(content)
        return self._parse_srt(content)

    def _parse_srt(self, content: str) -> List[SubtitleLine]:
        # Split by empty lines to isolate each subtitle card
        blocks = re.split(r"\n\s*\n", content.strip())
        lines: List[SubtitleLine] = []
        
        for block in blocks:
            sub_lines = [line.strip() for line in block.split("\n") if line.strip()]
            if len(sub_lines) >= 3:
                try:
                    idx = int(sub_lines[0])
                    timestamp = sub_lines[1]
                    text = " ".join(sub_lines[2:])
                    lines.append(SubtitleLine(index=idx, timestamp=timestamp, text=text))
                except ValueError:
                    continue
        return lines

    def _parse_ass(self, content: str) -> List[SubtitleLine]:
        lines: List[SubtitleLine] = []
        in_events = False
        format_fields: List[str] = []

        for raw_line in content.splitlines():
            line = raw_line.strip()
            lower_line = line.lower()
            if not line:
                continue
            if lower_line == "[events]":
                in_events = True
                continue
            if line.startswith("["):
                in_events = False
                continue
            if not in_events:
                continue

            if lower_line.startswith("format:"):
                format_fields = [
                    field.strip().lower()
                    for field in line.split(":", 1)[1].split(",")
                ]
                continue
            if not lower_line.startswith("dialogue:"):
                continue

            fields = self._split_ass_dialogue(line, format_fields)
            text = self._clean_ass_text(fields.get("text", ""))
            if not text:
                continue

            start = fields.get("start", "").strip()
            end = fields.get("end", "").strip()
            timestamp = f"{start} --> {end}" if start or end else ""
            lines.append(
                SubtitleLine(
                    index=len(lines) + 1,
                    timestamp=timestamp,
                    text=text,
                )
            )
        return lines

    def _split_ass_dialogue(
        self,
        line: str,
        format_fields: List[str],
    ) -> Dict[str, str]:
        payload = line.split(":", 1)[1].lstrip()
        if not format_fields:
            format_fields = [
                "layer",
                "start",
                "end",
                "style",
                "name",
                "marginl",
                "marginr",
                "marginv",
                "effect",
                "text",
            ]

        values = payload.split(",", len(format_fields) - 1)
        return {
            field: values[index].strip()
            for index, field in enumerate(format_fields)
            if index < len(values)
        }

    def _clean_ass_text(self, text: str) -> str:
        cleaned = re.sub(r"\{[^}]*\}", "", text)
        cleaned = re.sub(r"\\[Nn]", " ", cleaned)
        cleaned = cleaned.replace(r"\h", " ")
        return re.sub(r"\s+", " ", cleaned).strip()


class TextAnalyzer:
    """Tokenizes Japanese text and filters out grammatical elements to find vocabulary words."""
    
    def __init__(self) -> None:
        self._tokenizer = dictionary.Dictionary().create()
        self._mode = tokenizer.Tokenizer.SplitMode.A
        self._ignored_pos: Set[str] = {"助詞", "助動詞", "補助記号", "記号", "感動詞"}
        self._ignored_prefixes: Set[str] = {"お", "御", "ご"}

    def extract_content_tokens(self, text: str) -> List[AnalyzedToken]:
        cleaned = self._clean_text(text)
        raw_tokens = self._tokenizer.tokenize(cleaned, self._mode)
        
        matches = detect_grammar_patterns(raw_tokens)
        
        grammar_insertions = {}
        skip_indices = set()
        for start, end, pattern in matches:
            grammar_insertions[start] = (end, pattern)
            for idx in range(start + 1, end + 1):
                skip_indices.add(idx)
                
        words: List[AnalyzedToken] = []
        i = 0
        n = len(raw_tokens)
        while i < n:
            if i in skip_indices:
                i += 1
                continue
                
            token = raw_tokens[i]
            pos = tuple(token.part_of_speech())
            lemma = token.dictionary_form()
            surface = token.surface()
            
            if not self._should_ignore_token(pos, lemma, surface):
                raw_reading = ""
                try:
                    raw_reading = token.reading_form()
                except Exception:
                    pass
                reading = katakana_to_hiragana(raw_reading) if raw_reading else ""
                
                words.append(
                    AnalyzedToken(
                        lemma=lemma,
                        surface=surface,
                        pos=pos,
                        is_proper_noun="固有名詞" in pos,
                        reading=reading,
                    )
                )
                
            if i in grammar_insertions:
                end, pattern = grammar_insertions[i]
                grammar_surface = "".join(raw_tokens[k].surface() for k in range(i + 1, end + 1))
                grammar_reading = GRAMMAR_DICT.get(pattern, {}).get("reading", "")
                words.append(
                    AnalyzedToken(
                        lemma=pattern,
                        surface=grammar_surface,
                        pos=("助動詞", "文法パターン", "*", "*"),
                        is_proper_noun=False,
                        reading=grammar_reading,
                    )
                )
                i = end + 1
            else:
                i += 1
                
        return words

    def extract_content_words(self, text: str) -> List[str]:
        return [token.lemma for token in self.extract_content_tokens(text)]

    def _clean_text(self, text: str) -> str:
        cleaned = re.sub(r"<[^>]+>", "", text)
        cleaned = re.sub(r"\([^)]+\)", "", cleaned)
        cleaned = re.sub(r"（[^）]+）", "", cleaned)
        return cleaned

    def _should_ignore_token(
        self,
        pos: Tuple[str, ...],
        lemma: str,
        surface: str,
    ) -> bool:
        lemma = lemma.strip()
        if not lemma or lemma == "*":
            return True
        if pos and pos[0] in self._ignored_pos:
            return True
        if pos and len(pos) > 1 and pos[1] == "非自立可能" and pos[0] != "動詞":
            return True
        if pos and pos[0] == "接頭辞" and (
            lemma in self._ignored_prefixes or surface in self._ignored_prefixes
        ):
            return True
        if re.match(r"^[a-zA-Z0-9_]+$", lemma):
            return True
        # Ignore single hiragana or katakana (usually stutters or exclamations like ひ, ア)
        if len(lemma) == 1 and re.match(r"^[ぁ-んァ-ン]$", lemma):
            return True
        return False


class KnowledgeModel:
    """Tracks words the user already knows using SQLite."""
    
    def __init__(self, known_path: Any = None) -> None:
        self.known_path = known_path
        if known_path is not None:
            self.known_words = set()
            path = pathlib.Path(known_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self.known_words = {line.strip() for line in f if line.strip()}
        else:
            with get_session() as session:
                statement = select(KnownWord.word)
                self.known_words: Set[str] = set(session.exec(statement).all())

    def is_known(self, word: str) -> bool:
        return word in self.known_words

    def add_known(self, word: str) -> bool:
        return self.add_known_words([word]) == 1

    def add_known_words(self, words: Iterable[str]) -> int:
        new_words = [
            word
            for word in _ordered_unique(words)
            if word and word not in self.known_words
        ]
        if not new_words:
            return 0

        for word in new_words:
            self.known_words.add(word)
            
        if self.known_path is not None:
            with open(self.known_path, "a", encoding="utf-8") as f:
                for word in new_words:
                    f.write(f"{word}\n")
        else:
            with get_session() as session:
                for word in new_words:
                    session.add(KnownWord(word=word))
                session.commit()
        return len(new_words)

    def remove_known_words(self, words: Iterable[str]) -> int:
        words_list = [w for w in words if w in self.known_words]
        if not words_list:
            return 0
        for word in words_list:
            self.known_words.discard(word)
        with get_session() as session:
            for word in words_list:
                db_word = session.exec(select(KnownWord).where(KnownWord.word == word)).first()
                if db_word:
                    session.delete(db_word)
            session.commit()
        return len(words_list)


class WordFrequency:
    """Looks up frequency ranks for Japanese words using SQLite."""
    
    def __init__(self, freq_path: Any = None) -> None:
        if freq_path is not None:
            path = pathlib.Path(freq_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self.freq_map = json.load(f)
            else:
                self.freq_map = {}
        else:
            with get_session() as session:
                statement = select(FrequencyWord.word, FrequencyWord.rank)
                results = session.exec(statement).all()
                self.freq_map: Dict[str, int] = {word: rank for word, rank in results}

    def get_rank(self, word: str) -> int:
        # Default to a high rank (100000) for rare/unlisted words
        return self.freq_map.get(word, 100000)


@dataclasses.dataclass(frozen=True)
class CandidateSentence:
    """Represents a recommended sentence to mine."""
    sentence: SubtitleLine
    content_words: Tuple[str, ...]
    known_context_words: Tuple[str, ...]
    unknown_word: str
    freq_rank: int
    score: float
    unknown_word_reading: str = ""


class MiningEngine:
    """Finds i+1 sentences and ranks them by frequency and length."""
    _short_length_penalty = 0.5
    _long_length_penalty = 0.3
    _proper_noun_penalty = 3.0
    
    def __init__(
        self,
        analyzer: TextAnalyzer,
        knowledge: KnowledgeModel,
        frequency: WordFrequency,
        jpdb_vocab: Dict[str, Any] | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.knowledge = knowledge
        self.frequency = frequency
        self.jpdb_vocab = jpdb_vocab or {}

    def find_candidates(self, lines: List[SubtitleLine]) -> List[CandidateSentence]:
        # Pass 1: Build episode frequency map
        episode_freq = {}
        for line in lines:
            tokens = self.analyzer.extract_content_tokens(line.text)
            for token in tokens:
                episode_freq[token.lemma] = episode_freq.get(token.lemma, 0) + 1

        # Pass 2: Evaluate candidates
        candidates: List[CandidateSentence] = []
        seen_sentences = set()
        best_candidate_for_word = {}
        for line in lines:
            cleaned_text = line.text.strip()
            if not cleaned_text:
                continue
            if cleaned_text in seen_sentences:
                continue
            seen_sentences.add(cleaned_text)

            tokens = self.analyzer.extract_content_tokens(cleaned_text)
            if not tokens:
                continue

            content_words = tuple(token.lemma for token in tokens)
            all_unknown = _ordered_unique(
                word for word in content_words if not self.knowledge.is_known(word)
            )
            
            # If JPDB is active, target words must be in the JPDB list.
            # Otherwise, any unknown word in the sentence can be a target word.
            if self.jpdb_vocab:
                target_words = [w for w in all_unknown if w in self.jpdb_vocab]
            else:
                target_words = all_unknown

            for target_word in target_words:
                target_word = target_word.strip()
                if not target_word:
                    continue
                
                # Count other unknown words in the sentence (excluding target_word)
                extra_unknown_count = len([w for w in all_unknown if w != target_word])
                
                if self.jpdb_vocab and target_word in self.jpdb_vocab:
                    rank = self.jpdb_vocab[target_word]["rank"]
                else:
                    rank = self.frequency.get_rank(target_word)
                    
                ep_freq = episode_freq.get(target_word, 1)
                
                score = self._calculate_score(
                    tokens, 
                    rank, 
                    ep_freq=ep_freq, 
                    extra_unknown_count=extra_unknown_count
                )
                
                known_context_words = tuple(
                    _ordered_unique(
                        word
                        for word in content_words
                        if word != target_word and self.knowledge.is_known(word)
                    )
                )
                target_token = next((t for t in tokens if t.lemma == target_word), None)
                target_reading = target_token.reading if target_token else ""
                cand = CandidateSentence(
                    sentence=line,
                    content_words=content_words,
                    known_context_words=known_context_words,
                    unknown_word=target_word,
                    unknown_word_reading=target_reading,
                    freq_rank=rank,
                    score=score,
                )
                
                if target_word not in best_candidate_for_word or score > best_candidate_for_word[target_word].score:
                    best_candidate_for_word[target_word] = cand
                
        candidates = list(best_candidate_for_word.values())
        # Sort candidates descending by score (highest learning value first)
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _calculate_score(
        self,
        tokens: Sequence[AnalyzedToken],
        rank: int,
        ep_freq: int = 1,
        extra_unknown_count: int = 0,
    ) -> float:
        # Global frequency score (0 to 100)
        if rank >= 100000:
            freq_score = -50.0  # Penalize missing/rare words, but let ep_freq overcome it
        else:
            freq_score = max(0.0, (50000.0 - rank) / 500.0)
            
        # Episode frequency bonus (+10 points per occurrence)
        ep_score = ep_freq * 10.0
        
        # Length penalty: Goldilocks zone is 5 to 12 content tokens
        length = len(tokens)
        length_penalty = 0.0
        if length < 5:
            length_penalty = (5.0 - length) * 5.0
        elif length > 12:
            length_penalty = (length - 12.0) * 3.0

        proper_noun_penalty = (
            sum(1 for token in tokens if token.is_proper_noun)
            * 20.0
        )
            
        # Penalty for extra unknown words in the sentence (-250.0 per extra unknown word)
        extra_unknown_penalty = extra_unknown_count * 250.0
            
        return freq_score + ep_score - length_penalty - proper_noun_penalty - extra_unknown_penalty




class DictLookup:
    """Queries offline dictionary definition for words."""
    
    def __init__(self) -> None:
        self.jam = Jamdict()

    def _get_best_entry_and_kana(self, word: str, reading: str | None = None) -> Tuple[Any, str]:
        if word in GRAMMAR_DICT:
            return None, GRAMMAR_DICT[word]["reading"]
        try:
            result = self.jam.lookup(word)
            if not result.entries:
                return None, ""
            
            # If reading is provided, check if any entry has a matching kana form.
            # If so, we only consider entries where the reading matches.
            has_reading_match = False
            if reading:
                for entry in result.entries:
                    if any(kf.text == reading for kf in entry.kana_forms):
                        has_reading_match = True
                        break
            
            # Prioritize entries where the searched word is the primary spelling
            # and sort by JMdict priority tags (ichi1, news1, etc.)
            best_entry = None
            best_score = -1
            
            for entry in result.entries:
                is_match = False
                if entry.kana_forms and entry.kana_forms[0].text == word:
                    is_match = True
                if entry.kanji_forms and entry.kanji_forms[0].text == word:
                    is_match = True
                if not is_match:
                    if any(kf.text == word for kf in entry.kanji_forms):
                        is_match = True
                    if any(kf.text == word for kf in entry.kana_forms):
                        is_match = True
                    
                if not is_match:
                    continue
                
                # Filter by actual reading if there is a matching entry
                if has_reading_match and reading:
                    if not any(kf.text == reading for kf in entry.kana_forms):
                        continue
                    
                score = 0
                for form in list(entry.kanji_forms) + list(entry.kana_forms):
                    if hasattr(form, 'pri') and form.pri:
                        for p in form.pri:
                            if p in ('ichi1', 'news1', 'spec1', 'gai1') or p.endswith('1'):
                                score = max(score, 3)
                            elif p.endswith('2') or p.startswith('nf'):
                                score = max(score, 2)
                            else:
                                score = max(score, 1)
                                
                if score > best_score:
                    best_score = score
                    best_entry = entry
            
            if not best_entry:
                best_entry = result.entries[0]
                
            entry = best_entry
            # Prefer the matching reading if available
            kana = reading if (reading and any(kf.text == reading for kf in entry.kana_forms)) else (entry.kana_forms[0].text if entry.kana_forms else "")
            return entry, kana
        except Exception:
            return None, ""

    def get_definition(self, word: str, reading: str | None = None) -> Tuple[str, str]:
        if word in GRAMMAR_DICT:
            return GRAMMAR_DICT[word]["definition"], GRAMMAR_DICT[word]["reading"]
        try:
            entry, kana = self._get_best_entry_and_kana(word, reading)
            if not entry:
                return "No definition found.", kana
            
            if not entry.senses:
                return "No senses found.", kana
            
            glosses = [g.text for g in entry.senses[0].gloss]
            
            pitch = get_pitch_accent(word, kana)
            if pitch:
                kana = f"{kana} [Pitch: {pitch}]"
                
            return "; ".join(glosses), kana
        except Exception as e:
            return f"Error looking up definition: {e}", ""


def prompt_pre_add_known_words(
    candidates: List[CandidateSentence],
    knowledge: KnowledgeModel,
    engine: MiningEngine,
    lines: List[SubtitleLine]
) -> List[CandidateSentence]:
    # Collect all unique unknown words
    all_unknown_set = set()
    for cand in candidates:
        all_unknown_set.add(cand.unknown_word)
        for word in cand.content_words:
            if not knowledge.is_known(word):
                all_unknown_set.add(word)

    if not all_unknown_set:
        return candidates

    # Get their frequency ranks
    word_ranks = []
    for word in all_unknown_set:
        if engine.jpdb_vocab and word in engine.jpdb_vocab:
            rank = engine.jpdb_vocab[word]["rank"]
        else:
            rank = engine.frequency.get_rank(word)
        word_ranks.append((word, rank))

    # Sort by rank ascending (easiest words first)
    word_ranks.sort(key=lambda x: x[1])
    top_easy_words = word_ranks[:100]

    choices = [word for word, rank in top_easy_words]
    ranks = [rank for word, rank in top_easy_words]

    # Interactive selection
    cursor = 0
    scroll_offset = 0
    selected = set()

    import sys
    import tty
    import termios
    import select
    import os
    from rich.console import Console
    from rich.table import Table
    from rich.console import Group
    from rich.text import Text

    def get_key():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
            if rlist:
                seq = os.read(fd, 10)
                if seq in (b'\x1b[A', b'\x1bOA'):
                    return 'up'
                elif seq in (b'\x1b[B', b'\x1bOB'):
                    return 'down'
                elif seq in (b'\x1b[D', b'\x1bOD'):
                    return 'left'
                elif seq in (b'\x1b[C', b'\x1bOC'):
                    return 'right'
                elif seq == b' ':
                    return 'space'
                elif seq in (b'\r', b'\n'):
                    return 'enter'
                elif seq in (b'q', b'Q', b'\x03', b'\x1b'):
                    return 'quit'
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return None

    def draw_menu(console):
        console.clear()
        console.print("[bold yellow]Interactive Known Word Selection[/bold yellow]")
        console.print("Use [cyan]Arrow Keys[/cyan] to navigate, [cyan]Space[/cyan] to toggle selection, [cyan]Enter[/cyan] to submit, [cyan]Q/Esc[/cyan] to skip.")
        console.print()

        table = Table(
            title="[bold yellow]Pre-add Known Words (Interactive Selection)[/bold yellow]",
            show_header=False,
            show_edge=False,
            box=None,
            padding=(0, 2)
        )
        
        num_cols = 2
        for _ in range(num_cols):
            table.add_column(justify="left")
            
        total_rows = (len(choices) + num_cols - 1) // num_cols
        max_visible_rows = 10
        
        start_row = scroll_offset
        end_row = min(total_rows, scroll_offset + max_visible_rows)
        
        for r in range(start_row, end_row):
            row_data = []
            for c in range(num_cols):
                idx = r * num_cols + c
                if idx < len(choices):
                    word = choices[idx]
                    rank = ranks[idx]
                    rank_str = f"#{rank}" if rank < 100000 else "N/A"
                    
                    is_sel = idx in selected
                    is_cur = idx == cursor
                    
                    prefix = "[magenta]▶[/magenta]" if is_cur else " "
                    box = "[[bold green]x[/bold green]]" if is_sel else "[ ]"
                    
                    style = "bold green" if is_sel else "white"
                    if is_cur:
                        style = "bold cyan reverse"
                    
                    row_data.append(f"{prefix} {box} [{style}]{word}[/{style}] [dim]({rank_str})[/dim]")
                else:
                    row_data.append("")
            table.add_row(*row_data)
            
        top_indicator = Text("   ▲  More words above  ▲", style="bold yellow") if scroll_offset > 0 else Text("")
        bottom_indicator = Text("   ▼  More words below  ▼", style="bold yellow") if end_row < total_rows else Text("")
        status_text = Text(f"   Showing words {start_row * num_cols + 1} - {min(len(choices), end_row * num_cols)} of {len(choices)}", style="dim cyan")
        
        group_elements = []
        if scroll_offset > 0:
            group_elements.append(top_indicator)
        group_elements.append(table)
        if end_row < total_rows:
            group_elements.append(bottom_indicator)
        group_elements.append(status_text)
        
        console.print(Group(*group_elements))

    console = Console()
    
    # Hide cursor
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()

    try:
        draw_menu(console)
        while True:
            key = get_key()
            if key == 'quit':
                selected.clear()
                break
            elif key == 'enter':
                break
            elif key == 'space':
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                draw_menu(console)
            elif key in ('up', 'down', 'left', 'right'):
                if key == 'up':
                    cursor = max(0, cursor - 2)
                elif key == 'down':
                    cursor = min(len(choices) - 1, cursor + 2)
                elif key == 'left':
                    cursor = max(0, cursor - 1)
                elif key == 'right':
                    cursor = min(len(choices) - 1, cursor + 1)
                
                # Update scroll offset
                max_visible_rows = 10
                cursor_row = cursor // 2
                if cursor_row < scroll_offset:
                    scroll_offset = cursor_row
                elif cursor_row >= scroll_offset + max_visible_rows:
                    scroll_offset = cursor_row - max_visible_rows + 1
                    
                draw_menu(console)
    except (KeyboardInterrupt, SystemExit):
        selected.clear()
    finally:
        # Show cursor
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        console.clear()

    if selected:
        newly_known = [choices[i] for i in selected]
        added = knowledge.add_known_words(newly_known)
        console.print(f"\n[bold green]Successfully added {added} word(s) directly to database: {', '.join(newly_known)}[/bold green]\n")
        # Recalculate candidates
        return engine.find_candidates(lines)
    else:
        console.print("\n[bold yellow]No words pre-added. Proceeding...[/bold yellow]\n")
        
    return candidates


class CliApp:
    """Ties the logic together in a simple command-line interface."""
    
    def __init__(
        self,
        subtitle_path: str,
        video_path: str | None = None,
        jpdb_url: str | None = None,
    ) -> None:
        self.subtitle_path = pathlib.Path(subtitle_path)
        self.video_path = pathlib.Path(video_path) if video_path else None
        self.anki = AnkiClient()
        self.jpdb_url = jpdb_url
        self.jpdb_vocab: Dict[str, Any] = {}

    def _select_subtitle_file(self) -> pathlib.Path:
        # GUI picker handles this now, but keep fallback
        return self.subtitle_path
        parent_dir = self.subtitle_path.parent
        extensions = {".srt", ".ass", ".ssa"}
        
        if not parent_dir.exists():
            return self.subtitle_path
            
        sub_files = [
            f for f in parent_dir.iterdir()
            if f.is_file() and f.suffix.lower() in extensions
        ]
        sub_files.sort(key=lambda f: f.name.lower())
        
        if not sub_files:
            return self.subtitle_path
            
        choices = [f.name for f in sub_files]
        default_choice = (
            self.subtitle_path.name 
            if self.subtitle_path.name in choices 
            else choices[0]
        )
        
        try:
            default_num = choices.index(default_choice) + 1
        except ValueError:
            default_num = 1
            
        try:
            console = Console()
            console.print()
            table = Table(title="[bold yellow]Available Subtitle Files[/bold yellow]", show_header=True, header_style="bold magenta")
            table.add_column("No.", justify="right", style="cyan")
            table.add_column("File Name", style="green")
            
            for idx, name in enumerate(choices, 1):
                table.add_row(str(idx), name)
                
            console.print(table)
            console.print()
            
            choice_num = IntPrompt.ask(
                "Select subtitle file to mine", 
                choices=[str(x) for x in range(1, len(choices) + 1)],
                default=default_num
            )
            return parent_dir / choices[choice_num - 1]
        except (KeyboardInterrupt, SystemExit):
            pass
            
        return self.subtitle_path

    def sync_unsynced_cards(self) -> None:
        with get_session() as session:
            unsynced = session.exec(select(MinedCard).where(MinedCard.anki_note_id == None)).all()  # noqa: E711
            if unsynced:
                print(f"[bold cyan]Found {len(unsynced)} unsynced cards. Syncing to Anki...[/bold cyan]")
                synced_count = 0
                for card in unsynced:
                    note_id = self.anki.add_card(
                        card.sentence,
                        card.target_word,
                        card.reading,
                        card.definition,
                        audio_path=card.audio_path,
                        image_path=card.image_path,
                        base_score=card.base_score,
                        adjusted_score=card.adjusted_score,
                        known_words=card.known_words,
                        unknown_words=card.unknown_words,
                    )
                    if note_id:
                        card.anki_note_id = note_id
                        session.add(card)
                        synced_count += 1
                        if note_id == -2:
                            print(f"  [bold yellow]->[/bold yellow] '[bold green]{card.target_word}[/bold green]' already exists in Anki. Marked as synced.")
                        
                        # Delete local files if successfully synced
                        if card.audio_path and os.path.exists(card.audio_path):
                            try:
                                os.remove(card.audio_path)
                            except Exception as e:
                                print(f"[bold yellow]Warning:[/bold yellow] Failed to delete local audio: {e}")
                        if card.image_path and os.path.exists(card.image_path):
                            try:
                                os.remove(card.image_path)
                            except Exception as e:
                                print(f"[bold yellow]Warning:[/bold yellow] Failed to delete local image: {e}")
                                
                if synced_count > 0:
                    session.commit()
                    print(f"  [bold green]-> Successfully synced {synced_count} cards to Anki![/bold green]")
                else:
                    print("  [bold red]-> Failed to sync cards to Anki (check connection).[/bold red]")

    def run(self) -> None:
        Console().print(Panel(
            "[bold cyan]AI-Assisted Sentence Miner MVP[/bold cyan]",
            title="[bold yellow]Welcome[/bold yellow]",
            expand=False,
            padding=(1, 4)
        ))
        create_db_and_tables()
        if self.anki.is_running():
            print("[bold green]Connected to Anki (AnkiConnect detected).[/bold green]")
            self.anki.create_deck_if_missing()
            self.sync_unsynced_cards()
        else:
            print("[bold yellow]Warning:[/bold yellow] Anki not detected. Cards will only save to local SQLite.")
            
        if self.jpdb_url:
            try:
                print(f"[bold cyan]Fetching JPDB vocabulary list from {self.jpdb_url}...[/bold cyan]")
                jpdb_words = scrape_jpdb(self.jpdb_url)
                if jpdb_words:
                    for entry in jpdb_words:
                        word = entry["word"]
                        rank = get_jpdb_global_rank(entry["tags"])
                        self.jpdb_vocab[word] = {
                            "definition": entry["definition"],
                            "rank": rank
                        }
                    print(f"[bold green]Loaded {len(self.jpdb_vocab)} words from JPDB.[/bold green]")
                else:
                    print("[bold yellow]Warning:[/bold yellow] No words fetched from JPDB. Falling back to local dictionary.")
            except Exception as e:
                print(f"[bold yellow]Warning:[/bold yellow] Failed to fetch JPDB vocab list: {e}. Falling back to local dictionary.")

        if self.video_path and self.video_path.exists():
            synced_path = self.subtitle_path.with_name(f"{self.subtitle_path.stem}_synced{self.subtitle_path.suffix}")
            if not synced_path.exists():
                print("[bold cyan]Synchronizing subtitles using ffsubsync...[/bold cyan]")
                try:
                    subprocess.run([
                        "ffs", str(self.video_path), 
                        "-i", str(self.subtitle_path), 
                        "-o", str(synced_path)
                    ], check=True)
                    print(f"[bold green]Subtitles synchronized:[/bold green] {synced_path.name}")
                    self.subtitle_path = synced_path
                except Exception as e:
                    print(f"[bold yellow]Warning:[/bold yellow] Synchronization failed. {e}")
            else:
                print(f"[bold green]Using already synchronized subtitles:[/bold green] {synced_path.name}")
                self.subtitle_path = synced_path

        if not self.subtitle_path.exists():
            print(f"[bold red]Error: Could not find '{self.subtitle_path}'. Please make sure the file exists.[/bold red]")
            return

        parser = SubtitleParser()
        lines = parser.parse(self.subtitle_path)
        
        analyzer = TextAnalyzer()
        knowledge = KnowledgeModel()
        frequency = WordFrequency()
        engine = MiningEngine(analyzer, knowledge, frequency, jpdb_vocab=self.jpdb_vocab)
        
        candidates = engine.find_candidates(lines)
        if not candidates:
            print("[bold yellow]No i+1 sentences found.[/bold yellow]")
            return

        candidates = prompt_pre_add_known_words(candidates, knowledge, engine, lines)

        lookup = DictLookup()
        print(f"Found [bold cyan]{len(candidates)}[/bold cyan] candidate sentences.")
        print("-" * 50)

        try:
            target_mined = IntPrompt.ask(
                "How many cards would you like to mine?",
                choices=["10", "15", "20", "25"],
                default=10
            )
        except (KeyboardInterrupt, SystemExit):
            print("\n[bold red]Operation cancelled.[/bold red]")
            return

        asyncio.run(self._async_mine(candidates, target_mined, knowledge, lookup))

    async def _async_mine(
        self,
        candidates: List[CandidateSentence],
        target_mined: int,
        knowledge: KnowledgeModel,
        lookup: DictLookup
    ) -> None:
        mined_count = 0
        
        # Prefetch pitch accents for the first 3 unknown words
        first_targets = []
        for cand in candidates:
            if len(first_targets) >= 3:
                break
            if not knowledge.is_known(cand.unknown_word):
                _, kana = lookup._get_best_entry_and_kana(cand.unknown_word, getattr(cand, "unknown_word_reading", None))
                if kana:
                    first_targets.append((cand.unknown_word, kana))
        if first_targets:
            asyncio.create_task(prefetch_pitch_accents(first_targets))

        for idx, cand in enumerate(candidates, 1):
            if mined_count >= target_mined:
                print(f"\n🎉 [bold green]You've successfully mined {target_mined} cards! Great session.[/bold green]")
                break
                
            if knowledge.is_known(cand.unknown_word):
                continue

            # Prefetch pitch accents for the next 3 unknown words
            next_targets = []
            for next_cand in candidates[idx:]:
                if len(next_targets) >= 3:
                    break
                if not knowledge.is_known(next_cand.unknown_word):
                    _, kana = lookup._get_best_entry_and_kana(next_cand.unknown_word, getattr(next_cand, "unknown_word_reading", None))
                    if kana:
                        next_targets.append((next_cand.unknown_word, kana))
            if next_targets:
                asyncio.create_task(prefetch_pitch_accents(next_targets))
                
            if self.jpdb_vocab and cand.unknown_word in self.jpdb_vocab:
                definition = self.jpdb_vocab[cand.unknown_word]["definition"]
                _, kana = lookup.get_definition(cand.unknown_word, getattr(cand, "unknown_word_reading", None))
                if not definition:
                    definition, _ = lookup.get_definition(cand.unknown_word, getattr(cand, "unknown_word_reading", None))
            else:
                definition, kana = lookup.get_definition(cand.unknown_word, getattr(cand, "unknown_word_reading", None))
 
            display_word = f"{cand.unknown_word} ({kana})" if kana and kana != cand.unknown_word else cand.unknown_word
            
            # Print candidate card details beautifully in a panel
            extra_unknowns = _ordered_unique(
                w for w in cand.content_words
                if w != cand.unknown_word and w not in cand.known_context_words
            )
            penalty_applied = len(extra_unknowns) * 250.0
            base_score = cand.score + penalty_applied
            
            known_words_str = ", ".join(cand.known_context_words) if cand.known_context_words else "None"
            all_unknowns = [cand.unknown_word] + extra_unknowns
            unknown_words_str = ", ".join(all_unknowns)
            
            freq_rank_label = "JPDB Frequency Rank" if (self.jpdb_vocab and cand.unknown_word in self.jpdb_vocab) else "Frequency Rank"
            
            card_info = (
                f"[bold cyan]Sentence:[/bold cyan] {cand.sentence.text}\n"
                f"[bold cyan]Target Word:[/bold cyan] [bold green]{display_word}[/bold green]\n"
                f"[bold cyan]{freq_rank_label}:[/bold cyan] [bold yellow]#{cand.freq_rank}[/bold yellow]\n"
                f"[bold cyan]Definition:[/bold cyan] {definition}\n"
                f"[bold cyan]Known Words:[/bold cyan] {known_words_str}\n"
                f"[bold cyan]Unknown Words:[/bold cyan] [bold red]{unknown_words_str}[/bold red]"
            )
            if extra_unknowns:
                card_info += f"\n[bold cyan]Extra Unknowns:[/bold cyan] [bold yellow]{', '.join(extra_unknowns)}[/bold yellow] (Penalty: -{penalty_applied:.1f})"
            
            card_info += (
                f"\n[bold cyan]Base Score:[/bold cyan] {base_score:.2f} | "
                f"[bold cyan]Adjusted Score:[/bold cyan] [bold green]{cand.score:.2f}[/bold green]"
            )
            
            Console().print(Panel(
                card_info,
                title=f"[bold yellow]RANK #{idx}[/bold yellow]",
                expand=False,
                padding=(1, 2)
            ))
            print()
            
            choice = await asyncio.to_thread(input, "Mine this card? (y/n/q to quit): ")
            choice = choice.strip().lower()
            if choice == "y":
                added_count = self._mine_candidate(knowledge, cand, kana, definition)
                mined_count += 1
                print(
                    f"[bold green]Successfully mined and added {added_count} new known "
                    f"word(s), including '{cand.unknown_word}'.[/bold green]"
                )
            elif choice == "q":
                print("[bold yellow]Exiting app.[/bold yellow]")
                break

    def _mine_candidate(
        self,
        knowledge: KnowledgeModel,
        candidate: CandidateSentence,
        reading: str,
        definition: str,
    ) -> int:
        words_to_mark_known = [
            *candidate.known_context_words,
            candidate.unknown_word,
        ]
        added_count = knowledge.add_known_words(words_to_mark_known)
        
        audio_path = None
        image_path = None
        if self.video_path and self.video_path.exists():
            media_dir = self.subtitle_path.parent / "media"
            media_dir.mkdir(exist_ok=True)
            
            # Parse subtitle timestamp: "00:01:23,456 --> 00:01:25,789"
            ts = candidate.sentence.timestamp
            if ts and "-->" in ts:
                import ffmpeg
                start_ts, end_ts = [t.strip().replace(',', '.') for t in ts.split("-->")]
                
                # Extract audio
                audio_filename = f"{candidate.unknown_word}_{candidate.sentence.index}.mp3"
                audio_path = str(media_dir / audio_filename)
                if not os.path.exists(audio_path):
                    print(f"  [bold cyan]->[/bold cyan] Extracting audio to [bold green]{audio_filename}[/bold green] in media/ folder...")
                    try:
                        (
                            ffmpeg
                            .input(str(self.video_path), ss=start_ts, to=end_ts)
                            .output(audio_path, acodec='libmp3lame', q=4, map='0:a:0')
                            .overwrite_output()
                            .run(quiet=True)
                        )
                    except Exception as e:
                        print(f"[bold yellow]Warning:[/bold yellow] Failed to extract audio: {e}")
                        audio_path = None
                        
                # Extract screenshot at midpoint
                image_filename = f"{candidate.unknown_word}_{candidate.sentence.index}.jpg"
                image_path = str(media_dir / image_filename)
                if not os.path.exists(image_path):
                    # Parse timestamp to seconds
                    def ts_to_seconds(t_str: str) -> float:
                        parts = t_str.split(":")
                        if len(parts) == 3:
                            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                        elif len(parts) == 2:
                            return float(parts[0]) * 60 + float(parts[1])
                        return float(parts[0])
                    
                    try:
                        start_sec = ts_to_seconds(start_ts)
                        end_sec = ts_to_seconds(end_ts)
                        mid_sec = start_sec + (end_sec - start_sec) / 2.0
                    except Exception:
                        mid_sec = start_ts
                        
                    print(f"  [bold cyan]->[/bold cyan] Extracting screenshot to [bold green]{image_filename}[/bold green]...")
                    try:
                        (
                            ffmpeg
                            .input(str(self.video_path), ss=mid_sec)
                            .filter('scale', -1, 360)
                            .output(image_path, vframes=1)
                            .overwrite_output()
                            .run(quiet=True)
                        )
                    except Exception as e:
                        print(f"[bold yellow]Warning:[/bold yellow] Failed to extract screenshot: {e}")
                        image_path = None

        extra_unknowns = _ordered_unique(
            w for w in candidate.content_words
            if w != candidate.unknown_word and w not in candidate.known_context_words
        )
        penalty_applied = len(extra_unknowns) * 250.0
        base_score = candidate.score + penalty_applied
        
        known_words_str = ", ".join(candidate.known_context_words) if candidate.known_context_words else ""
        all_unknowns = [candidate.unknown_word] + extra_unknowns
        unknown_words_str = ", ".join(all_unknowns)

        self._export_card(
            candidate.sentence.text,
            candidate.unknown_word,
            reading,
            definition,
            audio_path=audio_path,
            image_path=image_path,
            base_score=base_score,
            adjusted_score=candidate.score,
            known_words=known_words_str,
            unknown_words=unknown_words_str,
        )
        return added_count

    def _export_card(
        self,
        sentence: str,
        word: str,
        reading: str,
        definition: str,
        audio_path: str | None = None,
        image_path: str | None = None,
        base_score: float | None = None,
        adjusted_score: float | None = None,
        known_words: str | None = None,
        unknown_words: str | None = None,
    ) -> None:
        anki_note_id = None
        if self.anki.is_running():
            anki_note_id = self.anki.add_card(
                sentence,
                word,
                reading,
                definition,
                audio_path=audio_path,
                image_path=image_path,
                base_score=base_score,
                adjusted_score=adjusted_score,
                known_words=known_words,
                unknown_words=unknown_words,
            )
            if anki_note_id:
                if anki_note_id == -2:
                    print("  [bold yellow]->[/bold yellow] Card already exists in Anki. Marked as synced.")
                else:
                    print("  [bold green]-> Successfully synced card to Anki.[/bold green]")
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception as e:
                        print(f"[bold yellow]Warning:[/bold yellow] Failed to delete local audio: {e}")
                if image_path and os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        print(f"[bold yellow]Warning:[/bold yellow] Failed to delete local image: {e}")
            else:
                print("  [bold red]-> Failed to sync to Anki. Saved locally for later sync.[/bold red]")
        else:
            print("  [bold yellow]-> Anki not running. Saved locally for later sync.[/bold yellow]")

        with get_session() as session:
            card = MinedCard(
                sentence=sentence,
                target_word=word,
                reading=reading,
                definition=definition,
                anki_note_id=anki_note_id,
                audio_path=audio_path,
                image_path=image_path,
                base_score=base_score,
                adjusted_score=adjusted_score,
                known_words=known_words,
                unknown_words=unknown_words,
            )
            session.add(card)
            session.commit()


def _ordered_unique(words: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        result.append(word)
    return result


@app.command()
def run_app(
    stats: bool = typer.Option(
        False, 
        "--stats", 
        help="Display the total number of known words and exit."
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Interactively test sentence parsing and preview Anki card generation."
    ),
    forget: bool = typer.Option(
        False,
        "--forget",
        help="Interactively search and remove words from your known words database."
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Sync pending local cards to Anki and exit."
    )
) -> None:
    create_db_and_tables()
    if sync:
        create_db_and_tables()
        cli_app = CliApp("")
        if cli_app.anki.is_running():
            print("[bold green]Connected to Anki (AnkiConnect detected).[/bold green]")
            cli_app.anki.create_deck_if_missing()
            with get_session() as session:
                unsynced = session.exec(select(MinedCard).where(MinedCard.anki_note_id == None)).all()  # noqa: E711
                if not unsynced:
                    print("[bold green]No pending local cards to sync.[/bold green]")
                    return
            cli_app.sync_unsynced_cards()
        else:
            print("[bold red]Error: Anki not detected. Please make sure Anki is open and AnkiConnect is installed.[/bold red]")
        return

    if stats:
        create_db_and_tables()
        knowledge = KnowledgeModel()
        count = len(knowledge.known_words)
        
        sorted_words = sorted(knowledge.known_words)
        
        console = Console()
        
        console.print()
        console.print(Panel(
            f"[bold cyan]You currently know [magenta]{count:,}[/magenta] words! Keep it up! 🚀[/bold cyan]",
            title="[bold yellow]Vocabulary Stats[/bold yellow]",
            expand=False,
            padding=(1, 4)
        ))
        
        if count > 0:
            table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
            num_cols = 4
            
            for _ in range(num_cols):
                table.add_column(justify="right", style="dim")
                table.add_column(justify="left", style="bold green")
                
            for i in range(0, count, num_cols):
                row_data = []
                for j in range(num_cols):
                    idx = i + j
                    if idx < count:
                        row_data.extend([f"{idx + 1}.", sorted_words[idx]])
                    else:
                        row_data.extend(["", ""])
                table.add_row(*row_data)
                
            console.print("\n[bold]Your known words:[/bold]")
            console.print(table)
            
        console.print()
        return

    if verify:
        create_db_and_tables()
        knowledge = KnowledgeModel()
        analyzer = TextAnalyzer()
        lookup = DictLookup()
        console = Console()
        
        console.print()
        console.print("[bold yellow]Interactive Sentence Parser & Verification[/bold yellow]")
        console.print("Enter a Japanese sentence to see how it is parsed and how cards would look:")
        console.print()
        
        try:
            sentence = input("Sentence: ").strip()
            if not sentence:
                console.print("[bold red]No sentence entered. Exiting.[/bold red]")
                return
                
            tokens = analyzer.extract_content_tokens(sentence)
            if not tokens:
                console.print("[bold red]No content words found in sentence.[/bold red]")
                return
                
            content_words = tuple(token.lemma for token in tokens)
            
            console.print()
            console.print(Panel(
                f"[bold cyan]Sentence:[/bold cyan] {sentence}\n"
                f"[bold cyan]Extracted Lemmas:[/bold cyan] {', '.join(content_words)}",
                title="[bold green]Analysis Results[/bold green]"
            ))
            
            # Print card preview for each content word
            for idx, token in enumerate(tokens, 1):
                definition, reading = lookup.get_definition(token.lemma, getattr(token, "reading", None))
                is_known = knowledge.is_known(token.lemma)
                
                status = "[bold green]KNOWN[/bold green]" if is_known else "[bold red]UNKNOWN[/bold red]"
                
                # Build context words (other content words)
                context_words = [w for w in content_words if w != token.lemma]
                
                card_table = Table(title=f"Card Preview #{idx} (Target: {token.lemma})", show_header=False, box=None, padding=(0, 2))
                card_table.add_column("Field", style="bold yellow")
                card_table.add_column("Value")
                
                card_table.add_row("Target Word", f"{token.lemma} ({status})")
                card_table.add_row("Reading", reading or "N/A")
                card_table.add_row("Definition", definition)
                card_table.add_row("Context Words", ", ".join(context_words) if context_words else "None")
                
                console.print(Panel(card_table, expand=False))
                console.print()
        except (KeyboardInterrupt, SystemExit):
            console.print("\n[bold red]Cancelled.[/bold red]")
        return

    if forget:
        create_db_and_tables()
        knowledge = KnowledgeModel()
        console = Console()

        if not knowledge.known_words:
            console.print("\n[bold yellow]Your known words database is empty.[/bold yellow]\n")
            return

        console.print()
        search = input("Search word to forget (press Enter to list all): ").strip().lower()
        console.print()

        all_words = sorted(knowledge.known_words)
        matches = [w for w in all_words if search in w.lower()] if search else all_words

        if not matches:
            console.print(f"[bold red]No known words matching '{search}'.[/bold red]\n")
            return

        import sys
        import tty
        import termios
        import select as _select
        import os

        choices = matches
        cursor = 0
        scroll_offset = 0
        selected: set = set()

        def get_key_forget():
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                rlist, _, _ = _select.select([sys.stdin], [], [], 0.1)
                if rlist:
                    seq = os.read(fd, 10)
                    if seq in (b'\x1b[A', b'\x1bOA'):
                        return 'up'
                    elif seq in (b'\x1b[B', b'\x1bOB'):
                        return 'down'
                    elif seq in (b'\x1b[D', b'\x1bOD'):
                        return 'left'
                    elif seq in (b'\x1b[C', b'\x1bOC'):
                        return 'right'
                    elif seq == b' ':
                        return 'space'
                    elif seq in (b'\r', b'\n'):
                        return 'enter'
                    elif seq in (b'q', b'Q', b'\x03', b'\x1b'):
                        return 'quit'
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            return None

        def draw_forget(console):
            console.clear()
            console.print("[bold red]Forget Words[/bold red]  [dim](Space = toggle, Enter = confirm, Q = cancel)[/dim]")
            console.print()

            table = Table(
                title=f"[bold red]Select words to REMOVE from known database[/bold red] [dim]({len(matches)} matching)[/dim]",
                show_header=False, show_edge=False, box=None, padding=(0, 2)
            )
            num_cols = 2
            for _ in range(num_cols):
                table.add_column(justify="left")

            max_visible = 15
            total_rows = (len(choices) + num_cols - 1) // num_cols
            start_row = scroll_offset
            end_row = min(total_rows, scroll_offset + max_visible)

            for r in range(start_row, end_row):
                row_data = []
                for c in range(num_cols):
                    idx = r * num_cols + c
                    if idx < len(choices):
                        word = choices[idx]
                        is_sel = idx in selected
                        is_cur = idx == cursor
                        prefix = "[magenta]▶[/magenta]" if is_cur else " "
                        box = "[[bold red]x[/bold red]]" if is_sel else "[ ]"
                        style = "bold red" if is_sel else "white"
                        if is_cur:
                            style = "bold cyan reverse"
                        row_data.append(f"{prefix} {box} [{style}]{word}[/{style}]")
                    else:
                        row_data.append("")
                table.add_row(*row_data)

            if scroll_offset > 0:
                console.print(Text("   ▲  More words above  ▲", style="bold yellow"))
            console.print(table)
            if end_row < total_rows:
                console.print(Text("   ▼  More words below  ▼", style="bold yellow"))
            console.print()
            console.print(Text(f"   {len(selected)} word(s) selected for removal", style="bold red" if selected else "dim"))

        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        try:
            draw_forget(console)
            while True:
                key = get_key_forget()
                if key == 'quit':
                    selected.clear()
                    break
                elif key == 'enter':
                    break
                elif key == 'space':
                    if cursor in selected:
                        selected.remove(cursor)
                    else:
                        selected.add(cursor)
                    draw_forget(console)
                elif key in ('up', 'down', 'left', 'right'):
                    if key == 'up':
                        cursor = max(0, cursor - 2)
                    elif key == 'down':
                        cursor = min(len(choices) - 1, cursor + 2)
                    elif key == 'left':
                        cursor = max(0, cursor - 1)
                    elif key == 'right':
                        cursor = min(len(choices) - 1, cursor + 1)
                    max_visible = 15
                    cursor_row = cursor // 2
                    if cursor_row < scroll_offset:
                        scroll_offset = cursor_row
                    elif cursor_row >= scroll_offset + max_visible:
                        scroll_offset = cursor_row - max_visible + 1
                    draw_forget(console)
        except (KeyboardInterrupt, SystemExit):
            selected.clear()
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
            console.clear()

        if selected:
            words_to_remove = [choices[i] for i in selected]
            removed = knowledge.remove_known_words(words_to_remove)
            console.print(f"\n[bold red]Removed {removed} word(s) from your database:[/bold red]")
            for w in words_to_remove:
                console.print(f"  [dim]- {w}[/dim]")
            console.print()
        else:
            console.print("\n[bold yellow]No words removed.[/bold yellow]\n")
        return


    subtitle_path = ""
    video_path = ""
    
    loaded_session = False
    
    with get_session() as session:
        db_history = session.exec(select(MiningSession).order_by(MiningSession.id.desc())).all()
        history = [{"id": h.id, "subtitle_path": h.subtitle_path, "video_path": h.video_path} for h in db_history]
            
    valid_sessions = []
    invalid_found = False
    for sess in history:
        sub = sess.get("subtitle_path")
        vid = sess.get("video_path")
        sub_exists = sub and pathlib.Path(sub).exists()
        vid_exists = not vid or pathlib.Path(vid).exists()
        if sub_exists and vid_exists:
            valid_sessions.append(sess)
        else:
            invalid_found = True
            
    if invalid_found and not valid_sessions:
        print("[bold yellow]Warning:[/bold yellow] Past sessions exist, but all paths are now invalid.")
        
    if valid_sessions:
        console = Console()
        console.print()
        
        table = Table(title="[bold yellow]Recent Sessions[/bold yellow]", show_header=True, header_style="bold magenta")
        table.add_column("No.", justify="right", style="cyan")
        table.add_column("Subtitle File", style="green")
        table.add_column("Video File", style="dim")
        
        for idx, sess in enumerate(valid_sessions, 1):
            sub_name = pathlib.Path(sess["subtitle_path"]).name
            vid_name = pathlib.Path(sess["video_path"]).name if sess.get("video_path") else "None"
            table.add_row(str(idx), sub_name, vid_name)
            
        new_sess_num = len(valid_sessions) + 1
        table.add_row(str(new_sess_num), "[bold yellow]Start a new session[/bold yellow]", "")
        
        console.print(table)
        console.print()
        
        try:
            choice_num = IntPrompt.ask(
                "Select an option", 
                choices=[str(x) for x in range(1, new_sess_num + 1)],
                default=1
            )
            
            if choice_num != new_sess_num:
                selected_sess = valid_sessions[choice_num - 1]
                subtitle_path = selected_sess["subtitle_path"]
                video_path = selected_sess.get("video_path") or ""
                loaded_session = True
                
                # Move this session to the top (MRU)
                with get_session() as session:
                    db_sess = session.get(MiningSession, selected_sess["id"])
                    if db_sess:
                        session.delete(db_sess)
                        new_db_sess = MiningSession(subtitle_path=subtitle_path, video_path=video_path)
                        session.add(new_db_sess)
                        session.commit()
        except (KeyboardInterrupt, SystemExit):
            print("\n[bold red]Operation cancelled.[/bold red]")
            return

    if not loaded_session:
        try:
            from PyQt5.QtWidgets import QApplication, QFileDialog
            
            app = QApplication.instance()
            if not app:
                app = QApplication([])
                
            print("Please select a subtitle file (.srt, .ass, .ssa)...")
            subtitle_path, _ = QFileDialog.getOpenFileName(
                None,
                "Select Subtitle File",
                "",
                "Subtitle Files (*.srt *.ass *.ssa);;All Files (*)"
            )
            
            if not subtitle_path:
                print("No subtitle file selected. Exiting.")
                return
                
            print("Please select a video file (optional) for sync & audio extraction...")
            video_path, _ = QFileDialog.getOpenFileName(
                None,
                "Select Video File (Cancel to skip)",
                "",
                "Video Files (*.mkv *.mp4 *.avi);;All Files (*)"
            )
            
        except Exception as e:
            print(f"PyQt5 dialog failed or not available, falling back to Tkinter. Error: {e}")
            import tkinter as tk
            from tkinter import filedialog
            
            root = tk.Tk()
            root.withdraw() # Hide the main window
            
            print("Please select a subtitle file (.srt, .ass, .ssa)...")
            subtitle_path = filedialog.askopenfilename(
                title="Select Subtitle File",
                filetypes=[("Subtitle Files", "*.srt *.ass *.ssa"), ("All Files", "*.*")]
            )
            
            if not subtitle_path:
                print("No subtitle file selected. Exiting.")
                return
                
            print("Please select a video file (optional) for sync & audio extraction...")
            video_path = filedialog.askopenfilename(
                title="Select Video File (Cancel to skip)",
                filetypes=[("Video Files", "*.mkv *.mp4 *.avi"), ("All Files", "*.*")]
            )
            
        # Save session history
        if subtitle_path:
            abs_sub = str(pathlib.Path(subtitle_path).absolute())
            abs_vid = str(pathlib.Path(video_path).absolute()) if video_path else ""
            with get_session() as session:
                # Remove any matching duplicate to re-insert at front
                old_sess = session.exec(select(MiningSession).where(MiningSession.subtitle_path == abs_sub)).first()
                if old_sess:
                    session.delete(old_sess)
                
                new_db_sess = MiningSession(subtitle_path=abs_sub, video_path=abs_vid)
                session.add(new_db_sess)
                session.commit()
                
                # Keep only last 10
                all_sessions = session.exec(select(MiningSession).order_by(MiningSession.id.desc())).all()
                if len(all_sessions) > 10:
                    for s in all_sessions[10:]:
                        session.delete(s)
                    session.commit()
        
    # JPDB prompt: offer cached lists first, then new URL or skip
    jpdb_url = None
    cached_entries = list_cached_jpdb()

    _NEW_URL = "[Enter a new URL]"
    _SKIP    = "[Skip — no JPDB]"

    if cached_entries:
        choices = []
        for e in cached_entries:
            label = e['title'] if e['title'] else e['url']
            choices.append(f"{label}  ({e['count']} words)")
        choices += [_NEW_URL, _SKIP]

        console = Console()
        console.print()
        table = Table(title="[bold yellow]JPDB Vocabulary Lists[/bold yellow]", show_header=True, header_style="bold magenta")
        table.add_column("No.", justify="right", style="cyan")
        table.add_column("List Options", style="green")
        
        for idx, choice in enumerate(choices, 1):
            if choice in (_NEW_URL, _SKIP):
                table.add_row(str(idx), f"[bold yellow]{choice}[/bold yellow]")
            else:
                table.add_row(str(idx), choice)
                
        console.print(table)
        console.print()
        
        try:
            choice_num = IntPrompt.ask(
                "Select a JPDB option",
                choices=[str(x) for x in range(1, len(choices) + 1)],
                default=len(choices)  # Default to skip
            )
            selection = choices[choice_num - 1]
        except (KeyboardInterrupt, SystemExit):
            print("\n[bold red]Operation cancelled.[/bold red]")
            return

        if selection and selection not in (_NEW_URL, _SKIP):
            idx = choices.index(selection)
            jpdb_url = cached_entries[idx]["url"]
        elif selection == _NEW_URL:
            jpdb_url = input("Enter JPDB vocabulary list URL: ").strip() or None
        # else _SKIP or cancelled → jpdb_url stays None
    else:
        print("\nDo you want to filter and define words using a JPDB vocabulary list?")
        jpdb_url = input("Enter JPDB vocabulary list URL (optional, press Enter to skip): ").strip() or None

    cli_app = CliApp(subtitle_path, video_path, jpdb_url=jpdb_url)
    cli_app.run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()

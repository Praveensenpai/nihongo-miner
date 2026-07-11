import argparse
import csv
import dataclasses
import json
import pathlib
import re
import subprocess
import os
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Any

import questionary
from jamdict import Jamdict
from sqlmodel import select
from sudachipy import dictionary, tokenizer

from src.database import KnownWord, FrequencyWord, MinedCard, get_session, create_db_and_tables
from src.anki import AnkiClient
from src.jpdb import scrape_jpdb, get_jpdb_global_rank, list_cached_jpdb


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
            sub_lines = [l.strip() for l in block.split("\n") if l.strip()]
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
        tokens = self._tokenizer.tokenize(cleaned, self._mode)
        words: List[AnalyzedToken] = []

        for token in tokens:
            pos = tuple(token.part_of_speech())
            lemma = token.dictionary_form()
            surface = token.surface()
            if self._should_ignore_token(pos, lemma, surface):
                continue

            words.append(
                AnalyzedToken(
                    lemma=lemma,
                    surface=surface,
                    pos=pos,
                    is_proper_noun="固有名詞" in pos,
                )
            )
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
        if pos and len(pos) > 1 and pos[1] == "非自立可能":
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
    
    def __init__(self) -> None:
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

        with get_session() as session:
            for word in new_words:
                self.known_words.add(word)
                session.add(KnownWord(word=word))
            session.commit()
        return len(new_words)


class WordFrequency:
    """Looks up frequency ranks for Japanese words using SQLite."""
    
    def __init__(self) -> None:
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
            unknown = _ordered_unique(
                word for word in content_words if not self.knowledge.is_known(word)
            )
            
            # If JPDB is active, only show unknown words that exist in the JPDB list
            if self.jpdb_vocab:
                unknown = [w for w in unknown if w in self.jpdb_vocab]
            
            # i+1 rule: exactly 1 unknown vocabulary word
            if len(unknown) == 1:
                target_word = unknown[0].strip()
                if not target_word:
                    continue
                
                if self.jpdb_vocab and target_word in self.jpdb_vocab:
                    rank = self.jpdb_vocab[target_word]["rank"]
                else:
                    rank = self.frequency.get_rank(target_word)
                    
                ep_freq = episode_freq.get(target_word, 1)
                
                # We no longer strictly reject rank >= 100000. 
                # Episode frequency can bump up unlisted/rare words.
                score = self._calculate_score(tokens, rank, ep_freq)
                
                known_context_words = tuple(
                    _ordered_unique(
                        word
                        for word in content_words
                        if word != target_word and self.knowledge.is_known(word)
                    )
                )
                cand = CandidateSentence(
                    sentence=line,
                    content_words=content_words,
                    known_context_words=known_context_words,
                    unknown_word=target_word,
                    freq_rank=rank,
                    score=score,
                )
                
                if target_word not in best_candidate_for_word or score > best_candidate_for_word[target_word].score:
                    best_candidate_for_word[target_word] = cand
                
        candidates = list(best_candidate_for_word.values())
        # Sort candidates descending by score (highest learning value first)
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _calculate_score(self, tokens: Sequence[AnalyzedToken], rank: int, ep_freq: int) -> float:
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
            
        return freq_score + ep_score - length_penalty - proper_noun_penalty


class DictLookup:
    """Queries offline dictionary definition for words."""
    
    def __init__(self) -> None:
        self.jam = Jamdict()

    def get_definition(self, word: str) -> Tuple[str, str]:
        try:
            result = self.jam.lookup(word)
            if not result.entries:
                return "No definition found.", ""
            
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
            kana = entry.kana_forms[0].text if entry.kana_forms else ""
            
            if not entry.senses:
                return "No senses found.", kana
            
            glosses = [g.text for g in entry.senses[0].gloss]
            return "; ".join(glosses), kana
        except Exception as e:
            return f"Error looking up definition: {e}", ""


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
            selected_name = questionary.select(
                "Select subtitle file to mine:",
                choices=choices,
                default=default_choice
            ).ask()
            if selected_name:
                return parent_dir / selected_name
        except Exception:
            pass
            
        return self.subtitle_path

    def sync_unsynced_cards(self) -> None:
        with get_session() as session:
            unsynced = session.exec(select(MinedCard).where(MinedCard.anki_note_id == None)).all()
            if unsynced:
                print(f"Found {len(unsynced)} unsynced cards. Syncing to Anki...")
                synced_count = 0
                for card in unsynced:
                    note_id = self.anki.add_card(
                        card.sentence,
                        card.target_word,
                        card.reading,
                        card.definition,
                        audio_path=card.audio_path,
                        image_path=card.image_path
                    )
                    if note_id:
                        card.anki_note_id = note_id
                        session.add(card)
                        synced_count += 1
                        if note_id == -2:
                            print(f" -> '{card.target_word}' already exists in Anki. Marked as synced.")
                        
                        # Delete local files if successfully synced
                        if card.audio_path and os.path.exists(card.audio_path):
                            try:
                                os.remove(card.audio_path)
                            except Exception as e:
                                print(f"Warning: Failed to delete local audio: {e}")
                        if card.image_path and os.path.exists(card.image_path):
                            try:
                                os.remove(card.image_path)
                            except Exception as e:
                                print(f"Warning: Failed to delete local image: {e}")
                                
                if synced_count > 0:
                    session.commit()
                    print(f" -> Successfully synced {synced_count} cards to Anki!")
                else:
                    print(" -> Failed to sync cards to Anki (check connection).")

    def run(self) -> None:
        print("=== AI-Assisted Sentence Miner MVP ===")
        create_db_and_tables()
        if self.anki.is_running():
            print("Connected to Anki (AnkiConnect detected).")
            self.anki.create_deck_if_missing()
            self.sync_unsynced_cards()
        else:
            print("Warning: Anki not detected. Cards will only save to local SQLite.")
            
        if self.jpdb_url:
            try:
                print(f"Fetching JPDB vocabulary list from {self.jpdb_url}...")
                jpdb_words = scrape_jpdb(self.jpdb_url)
                if jpdb_words:
                    for entry in jpdb_words:
                        word = entry["word"]
                        rank = get_jpdb_global_rank(entry["tags"])
                        self.jpdb_vocab[word] = {
                            "definition": entry["definition"],
                            "rank": rank
                        }
                    print(f"Loaded {len(self.jpdb_vocab)} words from JPDB.")
                else:
                    print("Warning: No words fetched from JPDB. Falling back to local dictionary.")
            except Exception as e:
                print(f"Warning: Failed to fetch JPDB vocab list: {e}. Falling back to local dictionary.")

        if self.video_path and self.video_path.exists():
            synced_path = self.subtitle_path.with_name(f"{self.subtitle_path.stem}_synced{self.subtitle_path.suffix}")
            if not synced_path.exists():
                print(f"Synchronizing subtitles using ffsubsync...")
                try:
                    subprocess.run([
                        "ffs", str(self.video_path), 
                        "-i", str(self.subtitle_path), 
                        "-o", str(synced_path)
                    ], check=True)
                    print(f"Subtitles synchronized: {synced_path.name}")
                    self.subtitle_path = synced_path
                except Exception as e:
                    print(f"Warning: Synchronization failed. {e}")
            else:
                print(f"Using already synchronized subtitles: {synced_path.name}")
                self.subtitle_path = synced_path

        if not self.subtitle_path.exists():
            print(f"Error: Could not find '{self.subtitle_path}'. Please make sure the file exists.")
            return

        parser = SubtitleParser()
        lines = parser.parse(self.subtitle_path)
        
        analyzer = TextAnalyzer()
        knowledge = KnowledgeModel()
        frequency = WordFrequency()
        engine = MiningEngine(analyzer, knowledge, frequency, jpdb_vocab=self.jpdb_vocab)
        
        candidates = engine.find_candidates(lines)
        if not candidates:
            print("No i+1 sentences found.")
            return

        lookup = DictLookup()
        print(f"Found {len(candidates)} candidate sentences.")
        print("-" * 50)

        mined_count = 0
        
        for idx, cand in enumerate(candidates[:50], 1):
            if mined_count >= 10:
                print("\n🎉 You've successfully mined 10 cards! Great session.")
                break
                
            if knowledge.is_known(cand.unknown_word):
                continue
                
            if self.jpdb_vocab and cand.unknown_word in self.jpdb_vocab:
                definition = self.jpdb_vocab[cand.unknown_word]["definition"]
                _, kana = lookup.get_definition(cand.unknown_word)
                if not definition:
                    definition, _ = lookup.get_definition(cand.unknown_word)
            else:
                definition, kana = lookup.get_definition(cand.unknown_word)

            display_word = f"{cand.unknown_word} ({kana})" if kana and kana != cand.unknown_word else cand.unknown_word
            
            print(f"\nRANK #{idx}: {cand.sentence.text}")
            print(f"-> Target Word: {display_word}")
            if self.jpdb_vocab and cand.unknown_word in self.jpdb_vocab:
                print(f"-> JPDB Frequency Rank: #{cand.freq_rank}")
            else:
                print(f"-> Frequency Rank: #{cand.freq_rank}")
            print(f"-> Definition: {definition}")
            print("-" * 50)
            
            choice = input("Mine this card? (y/n/q to quit): ").strip().lower()
            if choice == "y":
                added_count = self._mine_candidate(knowledge, cand, kana, definition)
                mined_count += 1
                print(
                    f"Successfully mined and added {added_count} new known "
                    f"word(s), including '{cand.unknown_word}'."
                )
            elif choice == "q":
                print("Exiting app.")
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
                    print(f"Extracting audio to {audio_filename} in media/ folder...")
                    try:
                        (
                            ffmpeg
                            .input(str(self.video_path), ss=start_ts, to=end_ts)
                            .output(audio_path, acodec='libmp3lame', q=4, map='0:a:0')
                            .overwrite_output()
                            .run(quiet=True)
                        )
                    except Exception as e:
                        print(f"Warning: Failed to extract audio. {e}")
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
                        
                    print(f"Extracting screenshot to {image_filename}...")
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
                        print(f"Warning: Failed to extract screenshot. {e}")
                        image_path = None

        self._export_card(
            candidate.sentence.text,
            candidate.unknown_word,
            reading,
            definition,
            audio_path=audio_path,
            image_path=image_path,
        )
        return added_count

    def _export_card(self, sentence: str, word: str, reading: str, definition: str, audio_path: str | None = None, image_path: str | None = None) -> None:
        anki_note_id = None
        if self.anki.is_running():
            anki_note_id = self.anki.add_card(sentence, word, reading, definition, audio_path=audio_path, image_path=image_path)
            if anki_note_id:
                if anki_note_id == -2:
                    print(" -> Card already exists in Anki. Marked as synced.")
                else:
                    print(" -> Successfully synced card to Anki.")
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception as e:
                        print(f"Warning: Failed to delete local audio: {e}")
                if image_path and os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except Exception as e:
                        print(f"Warning: Failed to delete local image: {e}")
            else:
                print(" -> Failed to sync to Anki. Saved locally for later sync.")
        else:
            print(" -> Anki not running. Saved locally for later sync.")

        with get_session() as session:
            card = MinedCard(
                sentence=sentence,
                target_word=word,
                reading=reading,
                definition=definition,
                anki_note_id=anki_note_id,
                audio_path=audio_path,
                image_path=image_path,
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find i+1 Japanese subtitle sentences for Anki mining.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    
    subtitle_path = ""
    video_path = ""
    
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

        print("\nDo you want to use a JPDB vocabulary list?")
        selection = questionary.select(
            "Select a cached list or enter a new URL:",
            choices=choices,
        ).ask()

        if selection and selection not in (_NEW_URL, _SKIP):
            idx = choices.index(selection)
            jpdb_url = cached_entries[idx]["url"]
        elif selection == _NEW_URL:
            jpdb_url = input("Enter JPDB vocabulary list URL: ").strip() or None
        # else _SKIP or cancelled → jpdb_url stays None
    else:
        print("\nDo you want to filter and define words using a JPDB vocabulary list?")
        jpdb_url = input("Enter JPDB vocabulary list URL (optional, press Enter to skip): ").strip() or None

    app = CliApp(subtitle_path, video_path, jpdb_url=jpdb_url)
    app.run()


if __name__ == "__main__":
    main()

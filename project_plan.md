# Master Project Plan: AI-Assisted Japanese Sentence Miner

## 1. Core Problem & Vision
**Problem:** AJATT/Immersion learners waste hours manually hunting for $i+1$ (one unknown word/grammar point) sentences in anime subtitles.
**Vision:** A standalone offline desktop application that analyzes subtitle files, compares them against the user's known vocabulary, and automatically recommends the absolute best sentences to mine as flashcards.

---

## 2. The Core Architecture & Workflow
1. **Input:** User provides a subtitle file (`.srt` or `.ass`) via GUI dialog picker or history selection.
2. **Parsing:** The app strips HTML/timing tags and extracts raw Japanese text.
3. **NLP Tokenization:** The app uses `SudachiPy` (Split Mode A) to break sentences into morphological tokens.
4. **Knowledge Comparison:** The app checks tokens against a local SQLite database of known words.
5. **Bootstrapping Selection:** On startup, the app displays the easiest 100 unknown words from the subtitle in a scrollable, terminal-based checkbox list so the user can bulk-mark already known words.
6. **Filtering ($i+1$ Rule):** The app filters out sentences that do not contain exactly **1 unknown content word**.
7. **Ranking:** Candidate sentences are scored based on Word Frequency, Sentence Length, and Proper Nouns.
8. **Export:** User reviews recommended sentences and exports cards directly to local Anki decks or caches them for sync.

---

## 3. Solving the Cold Start Problem
When a brand new user opens the app, they have zero data. To calibrate them quickly:
*   **Interactive Bootstrapping Grid:** Before candidates are generated, the app extracts the easiest 100 unknown words (sorted by frequency rank) present in the subtitle file. It renders them in a keyboard-driven, 2-column terminal checkbox grid (using raw terminal modes and `console.clear()` to prevent ghosting). The user navigates with Arrows, toggles with Space, and confirms with Enter to bulk-add them to the SQLite database.
*   **The Anki Import:** Allow users to import an Anki `collection.anki2` file to instantly populate their known words database.

---

## 4. Implicit Knowledge Tracking (Auto-Learning)
The app must get smarter without endless quizzes. 
*   **The Implicit Rule:** If a user chooses to MINE a recommended $i+1$ sentence, the app assumes the user **already knew** every other word in that sentence. 
*   It silently adds those context words to the "Known" database in the background.

---

## 5. NLP & Tokenization Strategy
Do not use slow LLMs or heavy dependency parsers. Use **SudachiPy**:
*   **For Vocabulary:** Ignore tokens tagged as `助詞` (Particles), `助動詞` (Auxiliary Verbs), or `補助記号` (Punctuation). Extract the `dictionary_form()` (lemma) of Nouns, Verbs, Adjectives, and Adverbs.
*   **For Grammar (Phase 2):** Grammar in Japanese is agglutinative. Model grammar using **Token Sequence Rules**. For example, the grammar point `~てしまう` is detected by looking for: `[Verb in te-form]` + `[Lemma: しまう]`.

---

## 6. The Ranking Algorithm (The Secret Sauce)
Once sentences are filtered down to $i+1$, they must be sorted so the most useful cards are at the top.
**The Score Formula:** `Score = Frequency Score - Length Penalty - Proper Noun Penalty`

*   **Frequency Score:** Look up the single unknown word in an Anime Frequency Dictionary. Lower rank number (e.g., Rank #100) = higher score.
*   **Length Penalty:** The "Goldilocks Zone" is **5 to 12 words**. Penalize sentences shorter than 5 words (lack context) and longer than 12 words (too much cognitive load).
*   **Proper Noun Penalty:** Tokenizers flag character names/places as `固有名詞`. Penalize these heavily so the user isn't mining sentences full of fictional jargon.

---

## 7. Tech Stack for the MVP
If rebuilding or expanding this, stick to this fast, offline-first stack:
*   **Package Manager:** `uv`
*   **Language:** Python (Strict Typing, OOP Design)
*   **CLI Framework:** `typer` + `rich` (Terminal formatting and raw input terminal redirection)
*   **Database:** `sqlmodel` + SQLite (Local relational storage)
*   **NLP Engine:** `sudachipy` + `sudachidict-core`
*   **Dictionary Lookup:** `jamdict` + `jamdict-data` (Offline JMdict SQLite database)

## 8. MVP Code Reference (`miner.py`)
The MVP requires 7 key components:
1.  `SubtitleParser`: Cleans and extracts SRT text.
2.  `TextAnalyzer`: Wraps SudachiPy to filter particles and extract content lemmas.
3.  `KnowledgeModel`: Performs direct reads/writes of known words to the SQLite database.
4.  `WordFrequency`: Looks up word frequency ranks.
5.  `prompt_pre_add_known_words`: Standardizes raw terminal inputs (`tty`, `termios`, `os.read`) and manages viewport rendering (`console.clear()`) for the scrollable known-words checkbox list.
6.  `MiningEngine`: Calculates the $i+1$ filter and applies the Scoring Algorithm.
7.  `DictLookup`: Wraps Jamdict to get English definitions.

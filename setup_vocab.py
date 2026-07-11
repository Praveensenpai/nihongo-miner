import sys
from sqlmodel import select
from src.database import get_session, create_db_and_tables, FrequencyWord, KnownWord, SkippedWord
from src.miner import DictLookup, TextAnalyzer

def run_assessment():
    create_db_and_tables()
    lookup = DictLookup()
    analyzer = TextAnalyzer()
    
    with get_session() as session:
        print("Loading official N5 vocabulary list...")
        try:
            with open("n5_vocab.txt", "r", encoding="utf-8") as f:
                n5_words_raw = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            print("Error: n5_vocab.txt not found.")
            return

        # Fetch ranks to keep the rank display working
        freq_map = {fw.word: fw.rank for fw in session.exec(select(FrequencyWord)).all()}
        
        class DummyWord:
            def __init__(self, word, rank):
                self.word = word
                self.rank = rank
                
        n5_words = [DummyWord(w, freq_map.get(w, 100000)) for w in n5_words_raw]
        n5_words.sort(key=lambda x: x.rank)
        
        # Ensure all existing known words in DB are converted to lemmas and duplicates merged
        known_words_db = session.exec(select(KnownWord)).all()
        lemma_to_kws = {}
        for kw in known_words_db:
            tokens = analyzer.extract_content_tokens(kw.word)
            lemma = tokens[0].lemma if tokens else kw.word
            lemma_to_kws.setdefault(lemma, []).append(kw)
            
        # First, delete all duplicate entries from the database
        for lemma, kws in lemma_to_kws.items():
            if len(kws) > 1:
                # Keep the one that already has the lemma as word if possible, else keep the first one
                kws.sort(key=lambda x: 0 if x.word == lemma else 1)
                for duplicate_kw in kws[1:]:
                    session.delete(duplicate_kw)
                lemma_to_kws[lemma] = [kws[0]]
                
        # Commit deletes first to avoid UNIQUE constraint violations during update
        session.commit()
        
        # Now, update the remaining entries to their lemma forms
        updated = False
        for lemma, kws in lemma_to_kws.items():
            kw = kws[0]
            if kw.word != lemma:
                kw.word = lemma
                session.add(kw)
                updated = True
                
        if updated:
            session.commit()
            
        known_set = set(lemma_to_kws.keys())
            
        skipped_words_db = session.exec(select(SkippedWord)).all()
        skipped_set = {kw.word for kw in skipped_words_db}
            
        valid_n5_pool = []
        seen_pool_lemmas = set()
        
        for w in n5_words:
            tokens = analyzer.extract_content_tokens(w.word)
            if not tokens:
                continue
            lemma = tokens[0].lemma
            
            if lemma in seen_pool_lemmas:
                continue
                
            seen_pool_lemmas.add(lemma)
            valid_n5_pool.append((w, lemma))
            
        words_to_test_normal = []
        words_to_test_skipped = []
        
        for w, lemma in valid_n5_pool:
            if lemma in known_set:
                continue
            if lemma in skipped_set:
                words_to_test_skipped.append((w, lemma))
            else:
                words_to_test_normal.append((w, lemma))
                
        words_to_test = words_to_test_normal + words_to_test_skipped
        
        if not words_to_test:
            print("You have already assessed or know all of the N5 words!")
            return
            
        print("=== N5 Vocabulary Baseline Assessment ===")
        print(f"You have {len(words_to_test)} words left to assess out of {len(valid_n5_pool)}.")
        print("Type 'y' if you know it, 'n' if you don't, or 'q' to save and quit.\n")
        
        for idx, (freq_word, lemma) in enumerate(words_to_test, 1):
            definition, kana = lookup.get_definition(lemma)
            display_word = f"{lemma} ({kana})" if kana and kana != lemma else lemma
            
            print(f"[{idx}/{len(words_to_test)}] Rank #{freq_word.rank}")
            print(f"Word: {display_word}")
            print(f"Definition: {definition}")
            
            while True:
                choice = input("Do you know this word? (y/n/q) [n]: ").strip().lower()
                if not choice:
                    choice = 'n'
                if choice in ('y', 'n', 'q'):
                    break
                print("Please enter 'y', 'n', or 'q'.")
                
            if choice == 'y':
                session.add(KnownWord(word=lemma))
                session.commit()
                known_set.add(lemma)
                print(" -> Marked as known!\n")
            elif choice == 'n':
                if lemma not in skipped_set:
                    session.add(SkippedWord(word=lemma))
                    session.commit()
                    skipped_set.add(lemma)
                print(" -> Skipped.\n")
            elif choice == 'q':
                print("\nProgress saved! You can resume anytime by running this script again.")
                sys.exit(0)
                
        print(f"\nAssessment complete! You have finished all {len(valid_n5_pool)} words.")

if __name__ == "__main__":
    run_assessment()

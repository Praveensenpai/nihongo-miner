import json
from rich import print
import urllib.request
import urllib.error
from typing import Any, Optional

from src.config import config
from src.utils import furigana_sentence

class AnkiClient:
    """Client for communicating with Anki Desktop via AnkiConnect."""
    
    def __init__(self, url: str = "http://127.0.0.1:8765") -> None:
        self.url = url
        self.deck_name = config.deck_name
        self.model_name = config.model_name

    def _invoke(self, action: str, **params: Any) -> Any:
        """Sends a JSON request to AnkiConnect."""
        request_dict = {"action": action, "version": 6}
        if params:
            request_dict["params"] = params
            
        request_json = json.dumps(request_dict).encode("utf-8")
        req = urllib.request.Request(self.url, request_json)
        
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                response_data = json.load(response)
        except (urllib.error.URLError, OSError):
            raise ConnectionError("Could not connect to Anki. Make sure Anki is open and AnkiConnect is installed.")
            
        if len(response_data) != 2:
            raise Exception("Response has an unexpected number of fields.")
        if "error" not in response_data:
            raise Exception("Response is missing required error field.")
        if "result" not in response_data:
            raise Exception("Response is missing required result field.")
        if response_data["error"] is not None:
            raise Exception(response_data["error"])
            
        return response_data["result"]

    def is_running(self) -> bool:
        """Checks if AnkiConnect is responsive."""
        try:
            self._invoke("version")
            return True
        except ConnectionError:
            return False

    def create_deck_if_missing(self) -> None:
        """Creates the default mining deck if it doesn't exist."""
        try:
            decks = self._invoke("deckNames")
            if self.deck_name not in decks:
                self._invoke("createDeck", deck=self.deck_name)
        except Exception as e:
            print(f"[bold yellow]Warning:[/bold yellow] Failed to ensure Anki deck exists: {e}")

    def add_card(self, sentence: str, target_word: str, reading: str, definition: str, audio_path: Optional[str] = None, image_path: Optional[str] = None, base_score: Optional[float] = None, adjusted_score: Optional[float] = None, known_words: Optional[str] = None, unknown_words: Optional[str] = None) -> Optional[int]:
        """Adds a mined flashcard to Anki. Returns the note ID on success, or None on failure."""
        audio_tag = ""
        if audio_path:
            import os
            filename = os.path.basename(audio_path)
            try:
                self._invoke("storeMediaFile", filename=filename, path=str(audio_path))
                audio_tag = f"<br>[sound:{filename}]"
            except Exception as e:
                print(f"[bold yellow]Warning:[/bold yellow] Failed to upload audio: {e}")
                
        image_tag = ""
        if image_path:
            import os
            filename = os.path.basename(image_path)
            try:
                self._invoke("storeMediaFile", filename=filename, path=str(image_path))
                image_tag = f"<br><img src=\"{filename}\" style=\"max-height: 270px; max-width: 100%; height: auto;\">"
            except Exception as e:
                print(f"[bold yellow]Warning:[/bold yellow] Failed to upload image: {e}")
                
        stats_html = "<br><hr><div style=\"text-align: left; font-size: 0.8em; color: #888;\">"
        if known_words:
            stats_html += f"<b>Known words:</b> {known_words}<br>"
        if unknown_words:
            stats_html += f"<b>Unknown words:</b> {unknown_words}<br>"
        if base_score is not None:
            stats_html += f"<b>Base Score:</b> {base_score:.2f}<br>"
        if adjusted_score is not None:
            stats_html += f"<b>Adjusted Score:</b> {adjusted_score:.2f}<br>"
        stats_html += "</div>"
        
        reading_suffix = f" ({reading})" if reading and reading != target_word else ""
        
        try:
            furigana = furigana_sentence(sentence)
        except Exception:
            furigana = sentence

        vars_dict = {
            "word": target_word,
            "reading": reading,
            "reading_suffix": reading_suffix,
            "sentence": sentence,
            "furigana_sentence": furigana,
            "definition": definition,
            "audio": audio_tag,
            "image": image_tag,
            "known_words": known_words or "",
            "unknown_words": unknown_words or "",
            "base_score": f"{base_score:.2f}" if base_score is not None else "",
            "adjusted_score": f"{adjusted_score:.2f}" if adjusted_score is not None else "",
            "stats": stats_html,
        }
        
        try:
            front_html = config.front_template.format(**vars_dict)
        except Exception as e:
            print(f"[bold yellow]Warning:[/bold yellow] Failed to format front template: {e}. Falling back to default layout.")
            front_html = f"<div><b>{target_word}</b>{reading_suffix}</div><br><div>{sentence}</div>{audio_tag}"
            
        try:
            back_html = config.back_template.format(**vars_dict)
        except Exception as e:
            print(f"[bold yellow]Warning:[/bold yellow] Failed to format back template: {e}. Falling back to default layout.")
            back_html = f"<div>{definition}</div>{image_tag}{stats_html}"
        
        note = {
            "deckName": self.deck_name,
            "modelName": self.model_name,
            "fields": {
                "Front": front_html,
                "Back": back_html
            },
            "options": {
                "allowDuplicate": False,
            },
            "tags": config.tags
        }
        
        try:
            return self._invoke("addNote", note=note)
        except ConnectionError:
            return None
        except Exception as e:
            if "duplicate" in str(e).lower():
                return -2
            print(f"[bold yellow]Warning:[/bold yellow] Failed to add card to Anki: {e}")
            return None

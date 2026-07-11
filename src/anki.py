import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

class AnkiClient:
    """Client for communicating with Anki Desktop via AnkiConnect."""
    
    def __init__(self, url: str = "http://127.0.0.1:8765") -> None:
        self.url = url
        self.deck_name = "Japanese Mining"
        self.model_name = "Basic"

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
        except urllib.error.URLError:
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
            print(f"Warning: Failed to ensure Anki deck exists: {e}")

    def add_card(self, sentence: str, target_word: str, reading: str, definition: str, audio_path: Optional[str] = None, image_path: Optional[str] = None) -> Optional[int]:
        """Adds a mined flashcard to Anki. Returns the note ID on success, or None on failure."""
        front_html = f"<div><b>{target_word}</b>"
        if reading and reading != target_word:
            front_html += f" ({reading})"
        front_html += f"</div><br><div>{sentence}</div>"
        
        if audio_path:
            import os
            filename = os.path.basename(audio_path)
            try:
                self._invoke("storeMediaFile", filename=filename, path=str(audio_path))
                front_html += f"<br>[sound:{filename}]"
            except Exception as e:
                print(f"Warning: Failed to upload audio: {e}")
                
        back_html = f"<div>{definition}</div>"
        if image_path:
            import os
            filename = os.path.basename(image_path)
            try:
                self._invoke("storeMediaFile", filename=filename, path=str(image_path))
                back_html += f"<br><img src=\"{filename}\" style=\"max-height: 270px; max-width: 100%; height: auto;\">"
            except Exception as e:
                print(f"Warning: Failed to upload image: {e}")
        
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
            "tags": ["ai_mined"]
        }
        
        try:
            return self._invoke("addNote", note=note)
        except ConnectionError:
            return None
        except Exception as e:
            if "duplicate" in str(e).lower():
                return -2
            print(f"Warning: Failed to add card to Anki: {e}")
            return None

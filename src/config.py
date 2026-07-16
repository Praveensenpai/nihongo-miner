import pathlib
import sys
from typing import List

try:
    import tomllib  # Available in Python 3.11+
except ImportError:
    # Fallback for Python < 3.11 if needed, though project requires >=3.14
    import pip._vendor.tomli as tomllib  # type: ignore

# Base folder for configuration files
CONFIG_DIR = (
    pathlib.Path.home() / "AppData" / "Local" / "nihongo-miner"
    if sys.platform == "win32"
    else (
        pathlib.Path.home() / "Library" / "Application Support" / "nihongo-miner"
        if sys.platform == "darwin"
        else pathlib.Path.home() / ".config" / "nihongo-miner"
    )
)
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULT_FRONT_TEMPLATE = (
    "<div><b>{word}</b>{reading_suffix}</div><br><div>{sentence}</div>{audio}"
)
DEFAULT_BACK_TEMPLATE = "<div>{definition}</div>{image}{stats}"


class CardConfig:
    """Configuration class for Anki card generation."""

    def __init__(self) -> None:
        self.deck_name: str = "Japanese Mining"
        self.model_name: str = "Basic"
        self.front_template: str = DEFAULT_FRONT_TEMPLATE
        self.back_template: str = DEFAULT_BACK_TEMPLATE
        self.tags: List[str] = ["ai_mined"]
        self.load_config()

    def load_config(self) -> None:
        """Loads configuration from config.toml, creating it with defaults if it doesn't exist."""
        if not CONFIG_FILE.exists():
            self.save_defaults()
            return

        try:
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)

            anki_cfg = data.get("anki", {})
            self.deck_name = anki_cfg.get("deck_name", self.deck_name)
            self.model_name = anki_cfg.get("model_name", self.model_name)
            self.front_template = anki_cfg.get("front_template", self.front_template)
            self.back_template = anki_cfg.get("back_template", self.back_template)
            self.tags = anki_cfg.get("tags", self.tags)
        except Exception as e:
            print(
                f"[bold yellow]Warning:[/bold yellow] Failed to load configuration from {CONFIG_FILE}: {e}. Using defaults."
            )

    def save_defaults(self) -> None:
        """Saves default configuration file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            default_toml = (
                "[anki]\n"
                f'deck_name = "{self.deck_name}"\n'
                f'model_name = "{self.model_name}"\n'
                f"tags = {self.tags}\n\n"
                "# Template variables available:\n"
                "# {word}            - Target Japanese word\n"
                "# {reading}         - Word reading/pronunciation\n"
                "# {reading_suffix}  - Helper that returns ' (reading)' if reading differs from word, otherwise empty\n"
                "# {sentence}        - Source Japanese sentence (plain text)\n"
                "# {furigana_sentence} - Source sentence with furigana <ruby> tags over kanji (HTML)\n"
                "# {definition}      - Dictionary definition\n"
                "# {audio}           - Sound play tag (e.g. [sound:abc.mp3]) if audio is present\n"
                "# {image}           - Image tag (e.g. <img src=...>) if image is present\n"
                "# {known_words}     - List of known words\n"
                "# {unknown_words}   - List of unknown words\n"
                "# {base_score}      - Raw sentence frequency/length score\n"
                "# {adjusted_score}  - Adjusted frequency/length score\n"
                "# {stats}           - The default formatted stats HTML block\n\n"
                f'front_template = """{self.front_template}"""\n'
                f'back_template = """{self.back_template}"""\n'
            )
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(default_toml)
        except Exception as e:
            print(
                f"[bold yellow]Warning:[/bold yellow] Failed to write default configuration: {e}"
            )


# Global instance of configuration
config = CardConfig()

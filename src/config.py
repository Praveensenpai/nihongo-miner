import pathlib
from typing import List

try:
    import tomllib  # Available in Python 3.11+
except ImportError:
    # Fallback for Python < 3.11 if needed, though project requires >=3.14
    import pip._vendor.tomli as tomllib  # type: ignore

# Base folder for configuration files
CONFIG_DIR = pathlib.Path.home() / ".nihongo-miner"


def migrate_legacy_data() -> None:
    """Migrates database and config from old locations to the new .nihongo-miner folder."""
    import shutil

    new_dir = pathlib.Path.home() / ".nihongo-miner"

    # 1. Migrate config.toml
    new_config = new_dir / "config.toml"
    if not new_config.exists():
        legacy_config_paths = [
            pathlib.Path.home() / ".anime-miner" / "config.toml",
            pathlib.Path.home() / "AppData" / "Local" / "nihongo-miner" / "config.toml",
            pathlib.Path.home() / "Library" / "Application Support" / "nihongo-miner" / "config.toml",
            pathlib.Path.home() / ".config" / "nihongo-miner" / "config.toml",
        ]
        for old_config in legacy_config_paths:
            if old_config.exists():
                try:
                    new_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old_config), str(new_config))
                    print(f"Migrated legacy configuration to {new_config}")
                    break
                except Exception:
                    pass

    # 2. Migrate database
    new_db = new_dir / "data.db"
    if not new_db.exists():
        legacy_db_paths = [
            pathlib.Path.home() / ".anime-miner" / "data.db",
            pathlib.Path("data.db"),
            pathlib.Path.home() / "AppData" / "Local" / "nihongo-miner" / "data.db",
            pathlib.Path.home() / "Library" / "Application Support" / "nihongo-miner" / "data.db",
            pathlib.Path.home() / ".local" / "share" / "nihongo-miner" / "data.db",
        ]
        for old_db in legacy_db_paths:
            if old_db.exists() and old_db.resolve() != new_db.resolve():
                try:
                    new_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(old_db), str(new_db))
                    print(f"Migrated legacy database to {new_db}")
                    break
                except Exception:
                    pass


migrate_legacy_data()

LOCAL_CONFIG = pathlib.Path("config.toml")
CONFIG_FILE = LOCAL_CONFIG if LOCAL_CONFIG.exists() else CONFIG_DIR / "config.toml"

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
        self.media_dir: str = str(CONFIG_DIR / "media")
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

            media_cfg = data.get("media", {})
            self.media_dir = media_cfg.get("media_dir", self.media_dir)
        except Exception as e:
            print(
                f"[bold yellow]Warning:[/bold yellow] Failed to load configuration from {CONFIG_FILE}: {e}. Using defaults."
            )

    def save_defaults(self) -> None:
        """Saves default configuration file."""
        try:
            if CONFIG_FILE != pathlib.Path("config.toml"):
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
                f'back_template = """{self.back_template}"""\n\n'
                "[media]\n"
                "# Optional: Directory where extracted media (audio/images) will be saved.\n"
                "# If empty, it defaults to a 'media' folder in the same directory as the subtitle file.\n"
                f'media_dir = "{self.media_dir}"\n'
            )
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(default_toml)
        except Exception as e:
            print(
                f"[bold yellow]Warning:[/bold yellow] Failed to write default configuration: {e}"
            )


# Global instance of configuration
config = CardConfig()

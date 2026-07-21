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


def extract_media_package(
    video_path: pathlib.Path,
    subtitle_path: pathlib.Path,
    output_dir: pathlib.Path,
    opus_bitrate: str = "96k",
    scale_height: int = 360,
) -> pathlib.Path:
    """
    Extracts full audio stream and 360p screenshots for all subtitle timestamps
    into output_dir / <episode_stem>/ folder.
    Returns the episode media package directory path.
    """
    import json
    import re
    import subprocess
    import time
    from rich import print

    episode_stem = clean_tag_from_path(subtitle_path)
    package_dir = output_dir / episode_stem
    package_dir.mkdir(parents=True, exist_ok=True)

    # Save copy of subtitle file into package_dir
    import shutil
    pkg_sub = package_dir / f"{episode_stem}{subtitle_path.suffix}"
    if subtitle_path.exists() and not pkg_sub.exists():
        try:
            shutil.copy(str(subtitle_path), str(pkg_sub))
        except Exception:
            pass

    # 1. Extract Audio if not already present
    audio_files = list(package_dir.glob("audio.*"))
    if not audio_files:
        print(f"  [bold cyan]->[/bold cyan] Extracting audio stream from {video_path.name}...")
        start_audio = time.time()
        # Inspect audio codec using ffprobe
        probe_cmd = [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            str(video_path),
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        codec = "opus"
        if probe_res.returncode == 0:
            try:
                data = json.loads(probe_res.stdout)
                for s in data.get("streams", []):
                    if s.get("codec_type") == "audio":
                        codec = s.get("codec_name", "").lower()
                        break
            except Exception:
                pass

        compressed_codecs = {"aac", "opus", "mp3", "vorbis"}
        codec_ext_map = {"aac": "m4a", "opus": "opus", "mp3": "mp3", "vorbis": "ogg"}
        out_ext = codec_ext_map.get(codec, "opus")
        audio_out = package_dir / f"audio.{out_ext}"

        ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_path), "-map", "0:a:0"]
        if codec in compressed_codecs and codec == out_ext:
            ffmpeg_cmd.extend(["-c:a", "copy"])
        elif codec == "aac":
            ffmpeg_cmd.extend(["-c:a", "copy"])
        else:
            ffmpeg_cmd.extend(["-c:a", "libopus", "-b:a", opus_bitrate])
        ffmpeg_cmd.append(str(audio_out))

        subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        audio_elapsed = time.time() - start_audio
        print(f"  [bold green]✓[/bold green] Audio extracted in [bold yellow]{audio_elapsed:.2f}s[/bold yellow]")

    # 2. Extract Subtitle Screenshots if subtitle file is present
    screenshots_dir = package_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    if subtitle_path.exists():
        text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
        pattern = re.compile(
            r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})"
        )

        def ts_to_sec(ts_str: str) -> float:
            ts_str = ts_str.strip().replace(",", ".")
            parts = ts_str.split(":")
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

        matches = list(pattern.finditer(text))
        if matches and len(list(screenshots_dir.glob("*.jpg"))) < len(matches):
            import concurrent.futures
            import os

            print(f"  [bold cyan]->[/bold cyan] Pre-extracting {len(matches)} subtitle screenshots (parallel)...")
            start_img = time.time()
            
            tasks = []
            for m in matches:
                idx = int(m.group(1))
                start_sec = ts_to_sec(m.group(2))
                end_sec = ts_to_sec(m.group(3))
                mid_sec = start_sec + (end_sec - start_sec) / 2.0
                tasks.append((idx, mid_sec))

            def _extract_one(item: tuple[int, float]) -> None:
                idx, mid_sec = item
                img_path = screenshots_dir / f"sub_{idx:04d}.jpg"
                if img_path.exists():
                    return
                img_cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss", f"{mid_sec:.3f}",
                    "-i", str(video_path),
                    "-vf", f"scale=-1:{scale_height}",
                    "-vframes", "1",
                    "-q:v", "4",
                    str(img_path),
                ]
                subprocess.run(img_cmd, capture_output=True, text=True)

            max_workers = min(16, (os.cpu_count() or 4) * 2)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                list(executor.map(_extract_one, tasks))

            img_elapsed = time.time() - start_img
            print(f"  [bold green]✓[/bold green] {len(matches)} screenshots extracted in [bold yellow]{img_elapsed:.2f}s[/bold yellow]")

    return package_dir


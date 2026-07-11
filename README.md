<div align="center">

# `nihongo-miner` 
**[ にほんご マイナー ]**

> *Stop hunting. Start immersing.*
> An offline, CLI-first sentence miner for AJATT & immersion learners.

</div>

---

## ✦ vision 

Anime immersion is the best way to learn Japanese, but finding the perfect $i+1$ sentences (just one unknown word) in a 24-minute episode takes hours. 

`nihongo-miner` analyzes subtitle files against your known vocabulary and recommends the absolute best sentences to mine for Anki, ranked by anime word frequency and sentence length.

---

## ✦ features

- **$i+1$ Filtering** ─ Automatically extracts sentences with exactly ONE unknown word.
- **Smart NLP** ─ Powered by `SudachiPy`. Ignores particles, conjugations, and grammar filler.
- **Anime Frequency Ranking** ─ Sorts candidate sentences based on anime word frequencies. 
- **Offline Dictionary** ─ Integrated with `jamdict` for instant English definitions.
- **Auto-Learning** ─ Automatically updates your "Known Words" database in the background.

---

## ✦ setup

Requires `uv`.

```bash
# clone & install
$ git clone git@github.com:Praveensenpai/nihongo-miner.git
$ cd nihongo-miner
$ uv sync
```

---

## ✦ usage

Just run the miner, and a beautiful GUI file picker will pop up to let you select your subtitle file.

```bash
# launch the app
$ uv run python main.py
```

---

<div align="center">
  <i>Read, listen, and mine.</i>
</div>

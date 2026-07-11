import csv
import pathlib
import tempfile
import unittest

from miner import (
    AnalyzedToken,
    CandidateSentence,
    CliApp,
    KnowledgeModel,
    MiningEngine,
    SubtitleLine,
    SubtitleParser,
    TextAnalyzer,
    WordFrequency,
    build_arg_parser,
)


def token(lemma: str, *, proper: bool = False) -> AnalyzedToken:
    pos = (
        ("名詞", "固有名詞", "一般", "*", "*", "*")
        if proper
        else ("名詞", "普通名詞", "一般", "*", "*", "*")
    )
    return AnalyzedToken(
        lemma=lemma,
        surface=lemma,
        pos=pos,
        is_proper_noun=proper,
    )


class FakeAnalyzer:
    def __init__(self, tokens_by_text: dict[str, list[AnalyzedToken]]) -> None:
        self.tokens_by_text = tokens_by_text

    def extract_content_tokens(self, text: str) -> list[AnalyzedToken]:
        return self.tokens_by_text[text]


class MinerTests(unittest.TestCase):
    def test_parse_multiline_srt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "sample.srt"
            path.write_text(
                "1\n"
                "00:00:01,000 --> 00:00:03,000\n"
                "<i>学校</i>\n"
                "に行く。\n\n",
                encoding="utf-8",
            )

            lines = SubtitleParser().parse(path)

        self.assertEqual(
            lines,
            [
                SubtitleLine(
                    index=1,
                    timestamp="00:00:01,000 --> 00:00:03,000",
                    text="<i>学校</i> に行く。",
                )
            ],
        )

    def test_parse_ass_dialogue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = pathlib.Path(tmpdir) / "sample.ass"
            path.write_text(
                "[Script Info]\n"
                "Title: Example\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
                "MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
                "{\\i1}学校\\Nに行く。\n",
                encoding="utf-8",
            )

            lines = SubtitleParser().parse(path)

        self.assertEqual(
            lines,
            [
                SubtitleLine(
                    index=1,
                    timestamp="0:00:01.00 --> 0:00:03.00",
                    text="学校 に行く。",
                )
            ],
        )

    def test_text_analyzer_cleans_markup_and_filters_prefix_tokens(self) -> None:
        words = TextAnalyzer().extract_content_words(
            "<b>美味しい</b>お茶を飲む。（noise）"
        )

        self.assertEqual(words, ["美味しい", "茶", "飲む"])

    def test_find_candidates_accepts_only_one_unique_unknown_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            known_path = pathlib.Path(tmpdir) / "known.txt"
            freq_path = pathlib.Path(tmpdir) / "freq.json"
            known_path.write_text("学校\n茶\n", encoding="utf-8")
            freq_path.write_text('{"飲む": 420, "見る": 10}', encoding="utf-8")
            analyzer = FakeAnalyzer(
                {
                    "zero": [token("学校"), token("茶")],
                    "one": [token("学校"), token("茶"), token("飲む")],
                    "repeat": [token("学校"), token("飲む"), token("飲む")],
                    "two": [token("学校"), token("飲む"), token("見る")],
                }
            )
            engine = MiningEngine(
                analyzer,  # type: ignore[arg-type]
                KnowledgeModel(known_path),
                WordFrequency(freq_path),
            )

            candidates = engine.find_candidates(
                [
                    SubtitleLine(1, "", "zero"),
                    SubtitleLine(2, "", "one"),
                    SubtitleLine(3, "", "repeat"),
                    SubtitleLine(4, "", "two"),
                ]
            )

        self.assertEqual([candidate.sentence.index for candidate in candidates], [2, 3])
        self.assertEqual(candidates[0].unknown_word, "飲む")
        self.assertEqual(candidates[0].content_words, ("学校", "茶", "飲む"))
        self.assertEqual(candidates[0].known_context_words, ("学校", "茶"))

    def test_score_uses_token_length_and_proper_noun_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MiningEngine(
                FakeAnalyzer({}),  # type: ignore[arg-type]
                KnowledgeModel(pathlib.Path(tmpdir) / "known.txt"),
                WordFrequency(pathlib.Path(tmpdir) / "freq.json"),
            )

            short_score = engine._calculate_score(
                [token("a"), token("b"), token("c")],
                1000,
            )
            proper_noun_score = engine._calculate_score(
                [token("a"), token("b"), token("東京", proper=True)],
                1000,
            )

        self.assertEqual(short_score, 8.0)
        self.assertEqual(proper_noun_score, 5.0)

    def test_csv_export_uses_csv_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = pathlib.Path(tmpdir) / "cards.csv"
            app = CliApp("unused.srt", "unused-known.txt", "unused-freq.json", str(export_path))

            app._export_card('彼は"茶,水"を飲む。', "飲む", "drink, imbibe")

            with open(export_path, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))

        self.assertEqual(
            rows,
            [
                ["Sentence", "Word", "Definition"],
                ['彼は"茶,水"を飲む。', "飲む", "drink, imbibe"],
            ],
        )

    def test_mining_candidate_marks_context_and_target_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            known_path = pathlib.Path(tmpdir) / "known.txt"
            export_path = pathlib.Path(tmpdir) / "cards.csv"
            knowledge = KnowledgeModel(known_path)
            app = CliApp("unused.srt", str(known_path), "unused-freq.json", str(export_path))
            candidate = CandidateSentence(
                sentence=SubtitleLine(1, "", "学校で茶を飲む。"),
                content_words=("学校", "茶", "飲む"),
                known_context_words=("学校", "茶"),
                unknown_word="飲む",
                freq_rank=420,
                score=8.58,
            )

            added_count = app._mine_candidate(knowledge, candidate, "drink")

        self.assertEqual(added_count, 3)
        self.assertEqual(knowledge.known_words, {"学校", "茶", "飲む"})

    def test_sample_file_produces_expected_candidate(self) -> None:
        parser = SubtitleParser()
        lines = parser.parse(pathlib.Path("sample.srt"))
        engine = MiningEngine(
            TextAnalyzer(),
            KnowledgeModel(pathlib.Path("known.txt")),
            WordFrequency(pathlib.Path("freq.json")),
        )

        candidates = engine.find_candidates(lines)

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0].sentence.index, 2)
        self.assertEqual(candidates[0].unknown_word, "飲む")

    def test_cli_parser_accepts_subtitle_and_data_paths(self) -> None:
        args = build_arg_parser().parse_args(
            [
                "subs/episode 01.ass",
                "--known",
                "profile-known.txt",
                "--freq",
                "anime-freq.json",
                "--export",
                "cards.csv",
            ]
        )

        self.assertEqual(args.subtitle_path, "subs/episode 01.ass")
        self.assertEqual(args.known, "profile-known.txt")
        self.assertEqual(args.freq, "anime-freq.json")
        self.assertEqual(args.export, "cards.csv")


if __name__ == "__main__":
    unittest.main()

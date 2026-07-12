import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sqlmodel import create_engine, select
import src.database

# Redirect database to in-memory SQLite for testing
src.database.engine = create_engine("sqlite://")
src.database.create_db_and_tables()

from miner import (  # noqa: E402
    AnalyzedToken,
    CandidateSentence,
    CliApp,
    KnowledgeModel,
    MiningEngine,
    SubtitleLine,
    SubtitleParser,
    TextAnalyzer,
    WordFrequency,
    app,
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

        # Candidates now include index 4 (sentence with two unknown words, targeting "見る")
        self.assertEqual([candidate.sentence.index for candidate in candidates], [2, 4])
        self.assertEqual(candidates[0].unknown_word, "飲む")
        self.assertEqual(candidates[0].content_words, ("学校", "茶", "飲む"))
        self.assertEqual(candidates[0].known_context_words, ("学校", "茶"))
        self.assertEqual(candidates[1].unknown_word, "見る")

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

        self.assertEqual(short_score, 98.0)
        self.assertEqual(proper_noun_score, 78.0)

    @patch("src.anki.AnkiClient.is_running", return_value=False)
    def test_db_export_saves_to_database(self, mock_is_running: MagicMock) -> None:
        app = CliApp("unused.srt")
        # Clear mined cards table for this test
        with src.database.get_session() as session:
            session.exec(src.database.SQLModel.metadata.tables["minedcard"].delete())  # type: ignore[name-defined]
            session.commit()

        app._export_card('彼は"茶,水"を飲む。', "飲む", "drink", "drink, imbibe")

        with src.database.get_session() as session:
            cards = session.exec(select(src.database.MinedCard)).all()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].sentence, '彼は"茶,水"を飲む。')
        self.assertEqual(cards[0].target_word, "飲む")
        self.assertEqual(cards[0].definition, "drink, imbibe")

    @patch("src.anki.AnkiClient.is_running", return_value=False)
    def test_mining_candidate_marks_context_and_target_known(self, mock_is_running: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            known_path = pathlib.Path(tmpdir) / "known.txt"
            knowledge = KnowledgeModel(known_path)
            app = CliApp("unused.srt")
            candidate = CandidateSentence(
                sentence=SubtitleLine(1, "", "学校で茶を飲む。"),
                content_words=("学校", "茶", "飲む"),
                known_context_words=("学校", "茶"),
                unknown_word="飲む",
                freq_rank=420,
                score=8.58,
            )

            added_count = app._mine_candidate(knowledge, candidate, "drink", "to drink")

        self.assertEqual(added_count, 3)
        self.assertEqual(knowledge.known_words, {"学校", "茶", "飲む"})

    def test_sample_file_produces_expected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = pathlib.Path(tmpdir) / "sample.srt"
            known_path = pathlib.Path(tmpdir) / "known.txt"
            freq_path = pathlib.Path(tmpdir) / "freq.json"

            sample_path.write_text(
                "1\n"
                "00:00:01,000 --> 00:00:03,000\n"
                "学校に行く。\n\n"
                "2\n"
                "00:00:04,000 --> 00:00:06,000\n"
                "お茶を飲む。\n\n",
                encoding="utf-8",
            )
            known_path.write_text("学校\n茶\n", encoding="utf-8")
            freq_path.write_text('{"飲む": 420, "行く": 1000}', encoding="utf-8")

            parser = SubtitleParser()
            lines = parser.parse(sample_path)
            engine = MiningEngine(
                TextAnalyzer(),
                KnowledgeModel(known_path),
                WordFrequency(freq_path),
            )

            candidates = engine.find_candidates(lines)

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0].sentence.index, 2)
        self.assertEqual(candidates[0].unknown_word, "飲む")

    def test_cli_parser_is_empty(self) -> None:
        from typer.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--stats", result.stdout)


if __name__ == "__main__":
    unittest.main()

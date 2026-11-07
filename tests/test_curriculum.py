import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lumina_orion.curriculum import CURRICULUM_ID, exercises, grade


class CurriculumTests(unittest.TestCase):
    def submission(self, split="practice", profile="orion"):
        # Explicit expectations reviewed against the invented scenarios, not imported from the grader key.
        answers = ({
            "p-classify": {"log": "evidence", "saved": "remembered_claim", "explanation": "inference"},
            "p-origins": {"origins": "one", "independent": "no"},
            "p-correction": {"count": "three", "old_status": "superseded"},
            "p-missing": {"status": "blocked", "citation": "report_missing_source"},
        } if split == "practice" else {
            "e-classify": {"hypothesis": "inference", "sound": "evidence", "memory": "remembered_claim"},
            "e-origins": {"origins": "two", "independent": "no"},
            "e-correction": {"count": "unknown", "old_status": "superseded"},
            "e-missing": {"status": "partial", "second_claim": "unsupported"},
        })
        return {"version": 1, "curriculum_id": CURRICULUM_ID, "profile": profile,
                "split": split, "answers": answers}

    def test_reference_decisions_score_correctly_in_both_splits(self):
        for split in ("practice", "evaluation"):
            response = grade(self.submission(split), profile="orion", split=split)
            self.assertEqual((response["correct"], response["total"], response["missing"]), (9, 9, 0))
            self.assertEqual(response["score"], 1)
            self.assertEqual(response["scope"], "synthetic_structured_decisions_only")

    def test_wrong_and_missing_answers_cannot_improve_score_by_skipping(self):
        submission = self.submission()
        submission["answers"]["p-classify"]["log"] = "inference"
        submission["answers"]["p-origins"]["origins"] = "three"
        del submission["answers"]["p-correction"]
        before = copy.deepcopy(submission)
        response = grade(submission, profile="orion", split="practice")
        self.assertEqual((response["correct"], response["total"], response["missing"]), (5, 9, 2))
        self.assertAlmostEqual(response["score"], 5 / 9)
        self.assertEqual(submission, before)
        submission["answers"] = {}
        response = grade(submission, profile="orion", split="practice")
        self.assertEqual((response["correct"], response["total"], response["missing"]), (0, 9, 9))

    def test_profile_split_and_version_mismatches_fail(self):
        original = self.submission()
        invalid = [None, {}, {**original, "version": True}, {**original, "curriculum_id": "different"},
                   {**original, "profile": "aurora"}, {**original, "split": "evaluation"},
                   {**original, "extra": "ignored?"}]
        for submission in invalid:
            with self.subTest(submission=submission), self.assertRaises(ValueError):
                grade(submission, profile="orion", split="practice")
        self.assertEqual(grade(self.submission(profile="aurora"), profile="aurora", split="practice")["profile"],
                         "aurora")

    def test_unknown_case_question_and_invalid_choices_fail(self):
        for mutate in (
            lambda s: s["answers"].update({"e-origins": {"origins": "two"}}),
            lambda s: s["answers"]["p-origins"].update({"unknown": "one"}),
            lambda s: s["answers"]["p-origins"].update({"origins": "invented choice"}),
            lambda s: s["answers"]["p-origins"].update({"origins": None}),
            lambda s: s["answers"].update({"p-origins": []}),
        ):
            submission = self.submission()
            mutate(submission)
            with self.assertRaises(ValueError):
                grade(submission, profile="orion", split="practice")

    def test_exercise_splits_are_disjoint_and_returns_do_not_mutate_catalog(self):
        practice, evaluation = exercises("practice"), exercises("evaluation")
        self.assertEqual(len(practice["cases"]), 4)
        self.assertEqual(len(evaluation["cases"]), 4)
        self.assertFalse({c["id"] for c in practice["cases"]} & {c["id"] for c in evaluation["cases"]})
        practice["cases"][0]["questions"][0]["choices"].clear()
        self.assertTrue(exercises("practice")["cases"][0]["questions"][0]["choices"])
        self.assertNotIn("expected", json.dumps(exercises("evaluation")))

    def test_cli_restart_preserves_input_and_rejects_bad_files_without_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "submission.json"
            path.write_text(json.dumps(self.submission()), encoding="utf-8")
            before = path.read_bytes()
            command = [sys.executable, "-m", "lumina_orion.curriculum", "grade", "orion",
                       "--split", "practice", "--answers", str(path)]
            first = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
            second = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(json.loads(first.stdout)["correct"], 9)
            self.assertEqual(path.read_bytes(), before)
            for content in ('{"version":1,"version":1}', '{broken', '[' * 1100, ' ' * 100001):
                path.write_text(content, encoding="utf-8")
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("Curriculum error:", result.stderr)
            self.assertEqual({p.name for p in Path(directory).iterdir()}, {"submission.json"})


if __name__ == "__main__":
    unittest.main()

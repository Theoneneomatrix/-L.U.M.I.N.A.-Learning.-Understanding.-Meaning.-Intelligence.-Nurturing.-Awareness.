"""Offline synthetic research exercises and deterministic structured-answer grading."""
from __future__ import annotations

import argparse
import copy
import json

from .memory import PROFILES

CURRICULUM_ID = "research-foundations-v1"
SPLITS = ("practice", "evaluation")
INPUT_LIMIT = 100000


def _q(item_id, prompt, *choices):
    return {"id": item_id, "prompt": prompt, "choices": list(choices)}


_KINDS = ("remembered_claim", "evidence", "inference")
_CASES = (
    {"id": "p-classify", "split": "practice", "skill": "claim_evidence_inference",
     "scenario": "Invented exercise: O1 is a direct observation log of three leaves at one visit. "
                 "M1 is an unverified saved claim that the plant always has five leaves. "
                 "I1 suggests that shade caused the difference. Treat O1 as evidence only for this visit.",
     "questions": [_q("log", "What role does O1 play here?", *_KINDS),
                   _q("saved", "What role does M1 play?", *_KINDS),
                   _q("explanation", "What role does I1 play?", *_KINDS)]},
    {"id": "p-origins", "split": "practice", "skill": "source_independence",
     "scenario": "Invented documents: A records one observation. B and C only copy A; neither adds a check.",
     "questions": [_q("origins", "How many observation origins are represented?", "one", "three"),
                   _q("independent", "Are these three independent observations?", "yes", "no")]},
    {"id": "p-correction", "split": "practice", "skill": "corrections",
     "scenario": "Invented log L1 says five leaves. Its explicit correction L2 says three leaves. "
                 "The exercise policy retains earlier versions and uses the latest correction.",
     "questions": [_q("count", "Which count does the current log support?", "three", "five", "unknown"),
                   _q("old_status", "What is L1's status under that policy?", "superseded", "verified", "deleted")]},
    {"id": "p-missing", "split": "practice", "skill": "missing_evidence",
     "scenario": "Invented task: verify a claim from source S. S could not be opened; no other evidence is available.",
     "questions": [_q("status", "What status fits this verification attempt?", "complete", "blocked"),
                   _q("citation", "How should the missing citation be handled?", "invent_one", "report_missing_source")]},
    {"id": "e-classify", "split": "evaluation", "skill": "claim_evidence_inference",
     "scenario": "Invented exercise: R1 is an unverified remembered claim that a pond has no frogs. "
                 "O2 directly records one unidentified croak during a visit. H1 proposes a frog made it. "
                 "Treat O2 as evidence for the sound, not confirmed frog presence.",
     "questions": [_q("hypothesis", "What role does H1 play?", *_KINDS),
                   _q("sound", "What role does O2 play here?", *_KINDS),
                   _q("memory", "What role does R1 play?", *_KINDS)]},
    {"id": "e-origins", "split": "evaluation", "skill": "source_independence",
     "scenario": "Invented records: D and E report separate observations. F only copies E.",
     "questions": [_q("origins", "How many observation origins are represented?", "two", "three"),
                   _q("independent", "Are all three records independent observations?", "yes", "no")]},
    {"id": "e-correction", "split": "evaluation", "skill": "corrections",
     "scenario": "Invented log T1 identifies four target calls. Correction T2 withdraws that identification "
                 "and says the target count is unknown. Retain history and use the latest correction.",
     "questions": [_q("count", "Which target count is currently supported?", "four", "zero", "unknown"),
                   _q("old_status", "What is T1's status?", "superseded", "verified", "deleted")]},
    {"id": "e-missing", "split": "evaluation", "skill": "missing_evidence",
     "scenario": "Invented task: verify two claims. Source U supports the first. Source V is unavailable, "
                 "and the second claim has no other evidence.",
     "questions": [_q("status", "What status fits the two-claim task?", "complete", "partial", "blocked"),
                   _q("second_claim", "What is the second claim's evidence status here?", "supported", "unsupported")]},
)

# Public reference answers, not secrets or a protected holdout benchmark.
_KEY = {
    "p-classify": {"log": "evidence", "saved": "remembered_claim", "explanation": "inference"},
    "p-origins": {"origins": "one", "independent": "no"},
    "p-correction": {"count": "three", "old_status": "superseded"},
    "p-missing": {"status": "blocked", "citation": "report_missing_source"},
    "e-classify": {"hypothesis": "inference", "sound": "evidence", "memory": "remembered_claim"},
    "e-origins": {"origins": "two", "independent": "no"},
    "e-correction": {"count": "unknown", "old_status": "superseded"},
    "e-missing": {"status": "partial", "second_claim": "unsupported"},
}


def exercises(split: str) -> dict:
    if split not in SPLITS:
        raise ValueError("Choose practice or evaluation")
    return {"version": 1, "curriculum_id": CURRICULUM_ID, "split": split,
            "rubric": "One point per correct structured decision; wrong or missing answers receive zero.",
            "scope": "synthetic_structured_decisions_only",
            "cases": copy.deepcopy([case for case in _CASES if case["split"] == split])}


def grade(submission: dict, *, profile: str, split: str) -> dict:
    """Score saved answers without executing them, calling models, or storing records."""
    cases = exercises(split)["cases"]
    if profile not in PROFILES:
        raise ValueError("Choose a configured profile")
    fields = {"version", "curriculum_id", "profile", "split", "answers"}
    if type(submission) is not dict or set(submission) != fields:
        raise ValueError("Submission requires version, curriculum_id, profile, split, and answers")
    if (type(submission["version"]) is not int or submission["version"] != 1
            or submission["curriculum_id"] != CURRICULUM_ID):
        raise ValueError("Submission curriculum version does not match")
    if submission["profile"] != profile or submission["split"] != split:
        raise ValueError("Submission profile or split does not match the requested evaluation")
    answers = submission["answers"]
    if type(answers) is not dict or set(answers) - {case["id"] for case in cases}:
        raise ValueError("Answers must use only case IDs from the requested split")
    decisions = []
    for case in cases:
        supplied = answers.get(case["id"], {})
        if type(supplied) is not dict or set(supplied) - {q["id"] for q in case["questions"]}:
            raise ValueError("Answers contain an unknown question or invalid case response")
        for question in case["questions"]:
            answer = supplied.get(question["id"])
            if question["id"] in supplied and (type(answer) is not str or answer not in question["choices"]):
                raise ValueError("Each supplied answer must be one of the question's choices")
            expected = _KEY[case["id"]][question["id"]]
            decisions.append({"case_id": case["id"], "question_id": question["id"],
                              "answer": answer, "expected": expected, "correct": answer == expected})
    correct = sum(item["correct"] for item in decisions)
    return {"curriculum_id": CURRICULUM_ID, "rubric_version": 1, "profile": profile, "split": split,
            "correct": correct, "total": len(decisions), "score": correct / len(decisions),
            "missing": sum(item["answer"] is None for item in decisions),
            "scope": "synthetic_structured_decisions_only", "decisions": decisions}


def _load_answers(path: str) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Submission contains duplicate JSON keys")
            result[key] = value
        return result

    with open(path, "rb") as stream:
        raw = stream.read(INPUT_LIMIT + 1)
    if len(raw) > INPUT_LIMIT:
        raise ValueError("Submission exceeds 100000 bytes")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="Show public synthetic exercises without reference answers")
    show.add_argument("--split", choices=SPLITS, required=True)
    check = commands.add_parser("grade", help="Grade a saved structured submission; no model calls")
    check.add_argument("profile", choices=PROFILES)
    check.add_argument("--split", choices=SPLITS, required=True)
    check.add_argument("--answers", required=True)
    args = parser.parse_args(argv)
    try:
        result = (exercises(args.split) if args.command == "show" else
                  grade(_load_answers(args.answers), profile=args.profile, split=args.split))
    except (ValueError, UnicodeError, OSError, RecursionError) as error:
        parser.exit(2, f"Curriculum error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

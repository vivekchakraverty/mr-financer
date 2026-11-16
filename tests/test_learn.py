from __future__ import annotations

from collections import Counter

from fastapi.testclient import TestClient

from mrfinancer import learn
from mrfinancer.api import app
from mrfinancer.config import Settings
from mrfinancer.learn_youtube import parse_vtt


def test_rolling_auto_captions_preserve_timestamps():
    result = parse_vtt(
        "WEBVTT\n\n"
        "00:00:00.500 --> 00:00:03.000\n"
        "Ask about something the customer did\n\n"
        "00:00:02.000 --> 00:00:04.000 align:start\n"
        "Ask about something the customer did\n"
        "<00:00:03.020><c> last time.</c>\n\n"
        "00:01:02.000 --> 00:01:05.500\n"
        "Write down their exact words."
    )
    assert result == [
        {"id": "S1", "start": 0.5, "end": 4.0, "text": "Ask about something the customer did last time."},
        {"id": "S2", "start": 62.0, "end": 65.5, "text": "Write down their exact words."},
    ]


SEGMENTS = [
    {"id": f"S{i}", "start": 20 * i, "end": 20 * (i + 1), "text": text}
    for i, text in enumerate((
        "Founder interviews reveal buying patterns through open conversation with relevant customers.",
        "Screening criteria define qualified participants around recent attempts to solve practical problems.",
        "Record customer actions and look for recurring obstacles in their previous purchase decisions.",
        "Set aside prescriptive language when discussing existing solutions during independent research.",
        "Independent organizations maintain support workflows that reveal actual operational constraints.",
        "Describe financial impact with an estimate of lost hours and direct spending.",
        "Interview prompts encourage specific memories and avoid leading questions about potential solutions.",
        "Customer discovery documents recurring pain and meaningful differences across comparable target buyers.",
        "A neutral interview captures observations in clear notes and distinguishes interpretations afterwards.",
        "Follow up with potential buyers to explore switching costs and competing offers before writing proposals.",
        "Compare primary customer evidence across cases to identify consistent behaviors and unusual decisions.",
        "A structured note allows researchers to separate observations from unsupported market hypotheses.",
        "Identify useful behavioral examples when interviewing stakeholders in several independent companies.",
        "Distinguish prospective decision makers from end users who handle day to day work.",
        "Ask about purchasing budgets during structured conversations without including confidential financial details.",
        "Use open questions to understand previous experiences and specific alternatives customers considered.",
    ), 1)
]


def selections(attempt):
    return [q["answer_indices"] if q.get("type") == "match" else q["answer_index"] for q in attempt["questions"]]


def stems(attempt):
    return {stem for q in attempt["questions"]
            for stem in ([p["prompt"] for p in q["pairs"]] if q.get("type") == "match" else [q["question"]])}


def test_fresh_quizzes_only_use_selection_answers_and_keep_original_keys(monkeypatch, tmp_path):
    settings = Settings(learn_database=tmp_path / "learn.sqlite")
    monkeypatch.setattr(learn, "get_settings", lambda: settings)
    db = learn.store(settings)
    video = {"video_id": "z1iF1c8w5Lg", "title": "How To Talk To Users",
             "caption_language": "en-orig", "caption_source": "automatic"}
    db.save_videos({"T02-V2": video})
    db.save_captions(video["video_id"], "en-orig", "automatic", SEGMENTS)
    model_calls = []

    async def generate(self, lesson, anchors, avoid):
        assert all(anchor["answer"] in anchor["excerpt"] for anchor in anchors)
        model_calls.append((anchors, avoid))
        return {"model": "fixture", "prompt_version": 3, "questions": [
            {"id": a["id"], "question": f"Which phrase is missing in this video excerpt: {a['clue'].split('“')[-1].rstrip('”')}?",
             "distractors": ["public earnings", "expense records", "government grants"]}
            for a in anchors
        ]}

    monkeypatch.setattr(learn.SpaceClient, "questions", generate)
    with TestClient(app) as client:
        assert len(client.get("/learn/course").json()["topics"]) == 24
        response = client.post("/learn/slots/T02-V2/quiz")
        assert response.status_code == 201, response.text
        first = response.json()
        first_id = first["id"]
        assert Counter(q["type"] for q in first["questions"]) == {"mcq": 6, "fill_blank": 3, "match": 1}
        assert all(set(q) <= {"type", "question", "choices", "pairs"} for q in first["questions"])
        assert all("answer_index" not in q for q in client.get(f"/learn/attempts/{first_id}").json()["questions"])
        match_index = next(i for i, q in enumerate(first["questions"]) if q["type"] == "match")
        assert all(set(p) == {"prompt"} for p in first["questions"][match_index]["pairs"])
        answers = selections(db.attempt(first_id))
        duplicate = answers[:]
        duplicate[match_index] = [0, 0, 1]
        assert client.post(f"/learn/attempts/{first_id}/submit", json={"answers": duplicate}).status_code == 422
        score = client.post(f"/learn/attempts/{first_id}/submit", json={"answers": answers}).json()
        assert score["score"] == 10
        assert score["questions"][match_index]["pairs"][0]["end"] > score["questions"][match_index]["pairs"][0]["start"]
        wrong = answers[:]
        wrong[match_index] = list(reversed(answers[match_index]))
        assert client.post(f"/learn/attempts/{first_id}/submit", json={"answers": wrong}).json()["score"] == 10
        second_response = client.post("/learn/slots/T02-V2/quiz")
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()
        assert second["id"] != first_id
        assert not stems(first) & stems(second)
        assert len(model_calls) == 6 and model_calls[3][1]
        second_answers = selections(db.attempt(second["id"]))
        new_match = next(i for i, q in enumerate(second["questions"]) if q["type"] == "match")
        second_answers[new_match] = list(reversed(second_answers[new_match]))
        next_score = client.post(f"/learn/attempts/{second['id']}/submit", json={"answers": second_answers}).json()
        assert next_score["score"] == 9
        assert client.get("/learn/course").json()["best_scores"] == {"T02-V2": 10}
        assert [row["id"] for row in client.get("/learn/attempts").json()["attempts"]] == [second["id"], first_id]


async def test_incomplete_framing_preserves_grounding_and_all_three_formats():
    class IncompleteModel:
        async def questions(self, _lesson, _anchors, _avoid):
            return {"model": "fixture", "prompt_version": 3, "questions": []}

    questions, _, _ = await learn._build_questions(IncompleteModel(), learn.get_slot("T02-V2"), SEGMENTS, [])
    by_id = {row["id"]: row["text"] for row in SEGMENTS}
    assert len(questions) == 10
    for item in questions:
        if item["type"] == "match":
            for pair, answer in zip(item["pairs"], item["answer_indices"]):
                assert item["choices"][answer] in by_id[pair["evidence_id"]]
        else:
            assert "____" in item["question"]
            assert item["choices"][item["answer_index"]] in by_id[item["evidence_id"]]

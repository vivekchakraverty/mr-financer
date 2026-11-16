"""Course discovery, live quiz generation, and scoring."""

from __future__ import annotations

import asyncio
import json
import math
import re
import secrets
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from rapidfuzz.fuzz import ratio

from . import learn_youtube as youtube
from .config import Settings, get_settings
from .learn_storage import LearnStore, now_iso

router = APIRouter(prefix="/learn")
QUESTION_COUNT = 10
COURSE_FILE = Path(__file__).parent / "web" / "learn-course.json"
RNG = secrets.SystemRandom()
quiz_run = asyncio.Semaphore(1)
discovery_run = asyncio.Semaphore(1)
WORDS = re.compile(r"[a-z0-9]{3,}")
COMMON = set("the and for that you are how from this your into with what why when which will should "
             "new beginner example startup business small tutorial founder use video learn explained "
             "role play walkthrough examples worked beginners tools method guide".split())


@lru_cache(maxsize=1)
def course() -> dict:
    return json.loads(COURSE_FILE.read_text(encoding="utf-8"))


def get_topic(topic_id: str) -> dict:
    for topic in course()["topics"]:
        if topic["topic_id"] == topic_id:
            return topic
    raise HTTPException(404, detail="Course topic not found.")


def get_slot(slot_id: str) -> dict:
    for topic in course()["topics"]:
        for slot in topic["video_slots"]:
            if slot["slot_id"] == slot_id:
                return slot
    raise HTTPException(404, detail="Course lesson not found.")


def store(settings: Settings) -> LearnStore:
    return LearnStore(settings.learn_database)


class SpaceError(Exception):
    pass


class SpaceClient:
    def __init__(self, settings: Settings, authorization: str | None):
        self.url = settings.learn_space_url.rstrip("/")
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in
                                              {"127.0.0.1", "localhost", "::1"}):
            raise SpaceError("Use an HTTPS quiz Space URL or a local CPU service.")
        provided = (authorization or "").split(None, 1)
        self.token = provided[1] if len(provided) == 2 and provided[0].lower() == "bearer" else settings.learn_space_token

    async def _post(self, path: str, payload: dict, *, retry_startup: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        failures = 4 if retry_startup else 1
        async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(110, connect=12)) as client:
            for attempt in range(failures):
                try:
                    response = await client.post(self.url + path, json=payload)
                except httpx.HTTPError:
                    raise SpaceError("Could not reach the CPU quiz service. Try again shortly.") from None
                if response.status_code in (401, 403):
                    raise SpaceError("Connect a valid Hugging Face read token.")
                if response.status_code == 404:
                    raise SpaceError("The CPU quiz service could not be found. Check the Space URL.")
                if response.status_code in (502, 503, 504) and attempt + 1 < failures:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if response.status_code in (502, 503, 504):
                    raise SpaceError("The CPU quiz service is starting or busy. Try again shortly.")
                if not response.is_success:
                    raise SpaceError("The CPU quiz service could not process the request.")
                try:
                    result = response.json()
                    if isinstance(result, dict):
                        return result
                except ValueError:
                    pass
                raise SpaceError("The CPU quiz service returned an invalid response.")
        raise SpaceError("The CPU quiz service did not respond.")

    async def comment_signals(self, videos: dict[str, list[str]]) -> dict:
        if not videos:
            return {}
        result = await self._post("/signals", {"videos": [
            {"video_id": video_id, "comments": comments} for video_id, comments in videos.items()
        ]})
        return {
            r["video_id"]: r for r in result.get("videos", [])
            if isinstance(r, dict) and isinstance(r.get("video_id"), str)
            and all(isinstance(r.get(k), int) for k in ("count", "helpful", "confused", "corrections"))
            and 0 < r["count"] <= 12 and all(0 <= r[k] <= r["count"] for k in ("helpful", "confused", "corrections"))
        }

    async def questions(self, lesson: str, anchors: list[dict], avoid: list[str]) -> dict:
        return await self._post("/questions", {
            "lesson": lesson, "anchors": anchors,
            "seed": RNG.randrange(2_147_483_647), "avoid": avoid[-9:],
        }, retry_startup=True)


class PickRequest(BaseModel):
    video: str = Field(min_length=1, max_length=500)


ChoiceIndex = Annotated[int, Field(strict=True, ge=0, le=3)]
MatchingAnswer = Annotated[list[ChoiceIndex], Field(min_length=3, max_length=3)]


class AnswersRequest(BaseModel):
    answers: list[ChoiceIndex | MatchingAnswer] = Field(min_length=QUESTION_COUNT, max_length=QUESTION_COUNT)


class QuestionFrame(BaseModel):
    id: str = Field(pattern=r"^A[0-9]+$")
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=12, max_length=240)]
    distractors: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=190)]] = Field(
        min_length=3, max_length=3,
    )


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _date(value) -> str | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except (ValueError, TypeError):
        return None


def _terms(text: str) -> set[str]:
    words = set(WORDS.findall(text.casefold()))
    return {w.rstrip("s") for w in words - COMMON if len(w.rstrip("s")) >= 3}


def transcript_fit(slot: dict, video: dict, segments: list[dict]) -> float:
    query = _terms(slot["seed_query"])
    lesson = _terms(slot["brief_title"])
    title = _terms(video["title"])
    evidence = _terms(" ".join(s["text"] for s in segments))
    targets = query | lesson
    coverage = len(targets & evidence) / max(1, len(targets))
    heading = len(targets & title) / max(1, min(4, len(targets)))
    return 0.78 * coverage + 0.22 * min(heading, 1.0)


def engagement(video: dict, candidates: list[dict]) -> float | None:
    views, likes = video["views"], video["likes"]
    if not views or likes is None or likes > views:
        return None

    def similar(peer: dict) -> bool:
        viewers, peers = video["subscribers"], peer["subscribers"]
        if viewers and peers and abs(math.log(viewers / peers)) > math.log(10):
            return False
        if video["published"] and peer["published"]:
            ages = [max(30, (date.today() - date.fromisoformat(p["published"])).days) for p in (video, peer)]
            if max(ages) > 4 * min(ages):
                return False
        return True

    rates = [p["likes"] / p["views"] for p in candidates if p["views"] and p["likes"] is not None
             and p["likes"] <= p["views"] and similar(p)]
    baseline = median(rates) if len(rates) >= 2 else None
    if not baseline:
        return 0.5
    adjusted = (likes + 250 * baseline) / (views + 250)
    return adjusted / (adjusted + baseline)


async def _prepare(settings: Settings, database: LearnStore, video_id: str) -> tuple[dict, list[dict], list[str]]:
    metadata = await youtube.describe(settings.learn_yt_dlp, video_id, settings.learn_collect_comments)
    if metadata.get("is_live") or metadata.get("live_status") == "is_upcoming":
        raise youtube.YouTubeError("Choose a recorded video with captions.")
    language, source = youtube.caption_track(metadata)
    if not isinstance(metadata.get("title"), str) or not metadata["title"].strip():
        raise youtube.YouTubeError("The video has no usable title.")
    segments = database.captions(video_id, language, source)
    if segments is None:
        segments = await youtube.get_captions(settings.learn_yt_dlp, video_id, language, source)
        database.save_captions(video_id, language, source, segments)
    video = {
        "video_id": video_id, "title": metadata["title"], "url": youtube.watch_url(video_id),
        "channel": metadata.get("channel") or metadata.get("uploader") or "",
        "duration": _number(metadata.get("duration")),
        "views": _number(metadata.get("view_count")), "likes": _number(metadata.get("like_count")),
        "subscribers": _number(metadata.get("channel_follower_count")), "published": _date(metadata.get("upload_date")),
        "caption_language": language, "caption_source": source,
    }
    return video, segments, youtube.comment_sample(metadata)


@router.get("/course")
def list_course():
    settings = get_settings()
    database = store(settings)
    history = database.history()
    best_scores: dict[str, int] = {}
    for attempt in history:
        slot = attempt["slot_id"]
        best_scores[slot] = max(best_scores.get(slot, 0), attempt["score"])
    return {
        "topics": course()["topics"], "videos": database.videos(), "best_scores": best_scores,
        "attempt_count": len(history), "question_count": QUESTION_COUNT,
        "space_token_configured": bool(settings.learn_space_token),
    }


@router.post("/topics/{topic_id}/discover")
async def discover_topic(topic_id: str, authorization: str | None = Header(default=None, max_length=2300)):
    topic = get_topic(topic_id)
    settings = get_settings()
    database = store(settings)
    async with discovery_run:
        searches = await asyncio.gather(
            *(youtube.search(settings.learn_yt_dlp, slot["seed_query"]) for slot in topic["video_slots"]),
            return_exceptions=True,
        )
        if all(isinstance(i, Exception) for i in searches):
            reason = next((str(i) for i in searches if isinstance(i, youtube.YouTubeError)), "Video search failed.")
            raise HTTPException(502, detail=reason)
        by_slot = {
            slot["slot_id"]: [item["id"] for item in entries[:3]] if isinstance(entries, list) else []
            for slot, entries in zip(topic["video_slots"], searches)
        }
        prepared_ids = dict.fromkeys(video for ids in by_slot.values() for video in ids)
        outcomes = await asyncio.gather(
            *(_prepare(settings, database, video) for video in prepared_ids), return_exceptions=True,
        )
        prepared = {video_id: result for video_id, result in zip(prepared_ids, outcomes) if isinstance(result, tuple)}
        signals = {}
        warnings = []
        if settings.learn_collect_comments:
            try:
                if prepared:
                    signals = await SpaceClient(settings, authorization).comment_signals({
                        v: c for v, (_, _, c) in prepared.items() if c
                    })
            except SpaceError:
                warnings.append("Comment analysis was unavailable. Video ranking uses transcripts and engagement.")
        if any(isinstance(result, Exception) for result in outcomes):
            warnings.append("Candidates with unavailable captions were skipped.")
        candidates = [v for v, _, _ in prepared.values()]
        picks = {}
        reserved: set[str] = set()
        for slot in topic["video_slots"]:
            ranked = []
            for video_id in by_slot[slot["slot_id"]]:
                if video_id not in prepared:
                    continue
                video, segments, _ = prepared[video_id]
                fit = transcript_fit(slot, video, segments)
                if fit < 0.16:
                    continue
                metrics = signals.get(video_id)
                rating = engagement(video, candidates)
                score, weight = 0.7 * fit, 0.7
                if metrics:
                    comment_score = (metrics["helpful"] + 2) / (metrics["count"] + 4)
                    score += 0.25 * comment_score
                    weight += 0.25
                if rating is not None:
                    score += 0.05 * rating
                    weight += 0.05
                ranked.append({
                    **video, "quality_score": round(100 * score / weight),
                    "transcript_fit": round(100 * fit), "comments": metrics,
                })
            ranked.sort(key=lambda v: v["quality_score"], reverse=True)
            winner = next((v for v in ranked if v["video_id"] not in reserved), None)
            if winner:
                reserved.add(winner["video_id"])
                alternatives = [v for v in ranked if v["video_id"] != winner["video_id"]]
                picks[slot["slot_id"]] = {**winner, "alternatives": alternatives}
        if not picks:
            warnings.append("No candidates met the lesson and caption checks. Try adding a video by URL.")
        elif len(picks) < len(topic["video_slots"]):
            warnings.append("Some lessons still need a video. You can add one by URL.")
        if picks:
            database.save_videos(picks)
        videos = database.videos()
    return {"videos": {k: v for k, v in videos.items() if k.startswith(topic_id + "-")}, "warnings": warnings}


@router.put("/slots/{slot_id}/video")
async def choose_video(slot_id: str, body: PickRequest):
    get_slot(slot_id)
    settings = get_settings()
    database = store(settings)
    try:
        video_id = youtube.parse_video_id(body.video)
        video, _, _ = await _prepare(settings.model_copy(update={"learn_collect_comments": False}), database, video_id)
    except youtube.YouTubeError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    async with discovery_run:
        current = database.videos().get(slot_id, {})
        alternatives = [v for v in current.get("alternatives", []) if v["video_id"] != video_id]
        if current and current["video_id"] != video_id:
            alternatives.insert(0, {k: v for k, v in current.items() if k != "alternatives"})
        video["alternatives"] = alternatives[:3]
        database.save_videos({slot_id: video})
    return video


def _fingerprint(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


# Fill-in-the-blank items can be graded against the exact source wording.
# They let a short transcript yield a full quiz when the model repeats itself.
CLOZE_STOP = COMMON | set(
    "about after also been because before both can could did does doing down even every going good had have "
    "here just know like make meanwhile might more most much need now only other our out really said say "
    "see some still such take than then there these they thing things think those through time too try very "
    "want was way well were where who while would yeah yes yet actually but not first last next "
    "always never almost around something everything anything someone anyone kind sort maybe simply "
    "basically probably also very quite sure whatever however".split()
)
CLOZE_START_STOP = {"ask", "describe", "explain", "consider", "find", "give", "keep",
                    "remember", "talk", "learn", "understand", "start", "finish", "check", "click"}
CLOZE_WORDS = re.compile(r"[\w]+(?:[’'-][\w]+)*")


def _cloze_candidates(slot: dict, segments: list[dict]) -> list[tuple[int, dict, str, str]]:
    terms = _terms(" ".join((slot["seed_query"], slot["brief_title"], slot["coverage_to_check"])))
    candidates = []
    for cue in segments:
        text = cue["text"]
        words = list(CLOZE_WORDS.finditer(text))
        for index in range(2, len(words) - 4):
            for size in (2, 3):
                span = words[index:index + size]
                words_in_phrase = [word.group() for word in span]
                tags = [word.casefold() for word in words_in_phrase]
                if (any(len(tag) < 3 or tag in CLOZE_STOP for tag in tags)
                        or tags[0] in CLOZE_START_STOP
                        or (words_in_phrase[0][0].islower() and any(w[0].isupper() for w in words_in_phrase[1:]))):
                    continue
                first, last = span[0].start(), span[-1].end()
                phrase = text[first:last]
                if len(phrase) > 90 or not all(c.isalpha() or c in " ’'-" for c in phrase):
                    continue
                begin = words[max(0, index - 8)].start()
                end = words[min(len(words) - 1, index + size + 7)].end()
                snippet = text[begin:first] + "____" + text[last:end]
                topic_match = len(_terms(phrase) & terms)
                question = f'Complete the phrase used in the video. “{snippet}”'
                if len(question) <= 260:
                    candidates.append((topic_match, cue, question, phrase))
    return candidates


def _anchor_questions(slot: dict, segments: list[dict], seen: list[str], total: int) -> list[dict]:
    """Choose fresh key phrases and give each one an exact transcript reference."""
    pool = _cloze_candidates(slot, segments)
    RNG.shuffle(pool)
    pool.sort(key=lambda row: row[0], reverse=True)
    phrases = list(dict.fromkeys(row[3] for row in pool))
    used: set[str] = set()
    added_cues: set[str] = set()
    items: list[dict] = []
    if len(phrases) < 4:
        return items
    for diversify in (True, False):
        for _, cue, stem, answer in pool:
            if len(items) == total:
                return items
            if diversify and cue["id"] in added_cues:
                continue
            key = _fingerprint(stem)
            answer_key = _fingerprint(answer)
            if answer_key in used or any(ratio(key, other, score_cutoff=88) for other in seen):
                continue
            other_phrases = [p for p in phrases if len(p.split()) == len(answer.split())]
            RNG.shuffle(other_phrases)
            choices = [answer]
            used_choices = {answer_key}
            for phrase in other_phrases + phrases:
                if len(choices) == 4:
                    break
                norm = _fingerprint(phrase)
                if norm not in used_choices and not ratio(answer_key, norm, score_cutoff=84):
                    choices.append(phrase)
                    used_choices.add(norm)
            if len(choices) != 4:
                continue
            RNG.shuffle(choices)
            items.append({
                "question": stem, "anchor_question": stem, "choices": choices,
                "answer_index": choices.index(answer),
                "explanation": f'The presenter used the phrase “{answer}” in this passage.',
                "evidence_id": cue["id"], "start": cue["start"], "end": cue["end"],
            })
            seen.append(key)
            used.add(answer_key)
            added_cues.add(cue["id"])
    return items


def _apply_frames(cards: dict[str, dict], result: dict, seen: list[str], cues: dict[str, str]) -> None:
    raw = result.get("questions")
    if not isinstance(raw, list):
        return
    for row in raw:
        try:
            frame = QuestionFrame.model_validate(row)
        except ValidationError:
            continue
        if frame.id not in cards:
            continue
        card = cards[frame.id]
        answer = card["choices"][card["answer_index"]]
        key, fixed_answer = _fingerprint(frame.question), _fingerprint(answer)
        focus = _terms(frame.question) - CLOZE_STOP - {"primary", "goal", "purpose", "step", "process"}
        evidence = _terms(cues[card["evidence_id"]])
        if (not frame.question.endswith("?") or f" {fixed_answer} " in f" {key} "
                or not re.match(r"^(how|what|which|when|where|who|why) ", key)
                or {"first", "last", "next"}.intersection(key.split())
                or len(focus & evidence) < max(1, min(2, len(focus)))
                or any(ratio(key, other, score_cutoff=88) for other in seen)):
            continue
        card["question"] = frame.question
        seen.append(key)
        options = [answer, *frame.distractors]
        keys = [_fingerprint(choice) for choice in options]
        if len(set(keys)) == 4 and all(keys) and all(not ratio(fixed_answer, k, score_cutoff=84) for k in keys[1:]):
            RNG.shuffle(options)
            card["choices"] = options
            card["answer_index"] = options.index(answer)


async def _build_questions(
    space: SpaceClient, slot: dict, segments: list[dict], previous: list[str],
) -> tuple[list[dict], str, int]:
    # Six multiple-choice questions, three blanks with choices, and one three-pair match.
    seen = [_fingerprint(q) for q in previous]
    anchors = _anchor_questions(slot, segments, seen, total=12)
    if len(anchors) < 12:
        raise SpaceError("This transcript has too few distinct passages for a fresh quiz. Try another video.")
    RNG.shuffle(anchors)
    multiple_choice, blanks, matching = anchors[:6], anchors[6:9], anchors[9:]
    cues = {s["id"]: s["text"] for s in segments}
    cards = {f"A{i}": card for i, card in enumerate(multiple_choice + matching, 1)}
    model = "Qwen2.5-1.5B-Instruct"
    prompt = 3
    ids = list(cards)
    for start in range(0, len(ids), 3):
        group = {i: cards[i] for i in ids[start:start + 3]}
        request = [{"id": i, "excerpt": cues[c["evidence_id"]], "clue": c["anchor_question"],
                    "answer": c["choices"][c["answer_index"]]} for i, c in group.items()]
        try:
            result = await space.questions(slot["brief_title"], request, previous)
        except SpaceError:
            if start == 0:
                raise
            break
        _apply_frames(group, result, seen, cues)
        if isinstance(result.get("model"), str):
            model = result["model"]
        if isinstance(result.get("prompt_version"), int):
            prompt = result["prompt_version"]
    for card in multiple_choice:
        card["type"] = "mcq"
    for card in blanks:
        card["type"] = "fill_blank"
    terms = [card["choices"][card["answer_index"]] for card in matching]
    choices = terms[:]
    RNG.shuffle(choices)
    match = {
        "type": "match", "question": "Match each clue to its phrase from the video.",
        "choices": choices, "answer_indices": [choices.index(term) for term in terms],
        "pairs": [{
            "prompt": card["question"], "anchor_question": card["anchor_question"],
            **{name: card[name] for name in ("evidence_id", "start", "end")},
        } for card in matching],
    }
    questions = [*multiple_choice, *blanks, match]
    RNG.shuffle(questions)
    return questions, model, prompt


def _public_attempt(attempt: dict) -> dict:
    result = {k: v for k, v in attempt.items() if k != "questions"}
    answered = bool(attempt.get("submitted_at"))
    result["questions"] = []
    for q in attempt["questions"]:
        item = {"type": q.get("type", "mcq"), "question": q["question"], "choices": q["choices"]}
        if answered:
            item.update(q)
        elif item["type"] == "match":
            item["pairs"] = [{"prompt": pair["prompt"]} for pair in q["pairs"]]
        result["questions"].append(item)
    return result


@router.post("/slots/{slot_id}/quiz", status_code=201)
async def generate_quiz(slot_id: str, authorization: str | None = Header(default=None, max_length=2300)):
    slot = get_slot(slot_id)
    settings = get_settings()
    database = store(settings)
    video = database.videos().get(slot_id)
    if video is None:
        raise HTTPException(409, detail="Choose a video for this lesson first.")
    if quiz_run.locked():
        raise HTTPException(429, detail="A fresh quiz is already being generated. Try again shortly.")
    async with quiz_run:
        try:
            space = SpaceClient(settings, authorization)
            segments = database.captions(video["video_id"], video["caption_language"], video["caption_source"])
            if segments is None:
                segments = await youtube.get_captions(
                    settings.learn_yt_dlp, video["video_id"], video["caption_language"], video["caption_source"],
                )
                database.save_captions(video["video_id"], video["caption_language"], video["caption_source"], segments)
            previous = database.previous_questions(video["video_id"])
            questions, model, prompt = await _build_questions(space, slot, segments, previous)
        except (SpaceError, youtube.YouTubeError) as exc:
            raise HTTPException(502, detail=str(exc)) from None
        attempt = {
            "id": secrets.token_urlsafe(18), "slot_id": slot_id, "video_id": video["video_id"],
            "video_title": video["title"], "created_at": now_iso(), "model": model,
            "prompt_version": prompt, "questions": questions,
        }
        database.create_attempt(attempt)
    return _public_attempt(attempt)


@router.get("/attempts")
def attempt_history():
    return {"attempts": store(get_settings()).history()}


def require_attempt(database: LearnStore, attempt_id: str) -> dict:
    attempt = database.attempt(attempt_id)
    if attempt is None:
        raise HTTPException(404, detail="Quiz attempt not found.")
    return attempt


@router.get("/attempts/{attempt_id}")
def read_attempt(attempt_id: str):
    return _public_attempt(require_attempt(store(get_settings()), attempt_id))


@router.post("/attempts/{attempt_id}/submit")
def grade_attempt(attempt_id: str, body: AnswersRequest):
    database = store(get_settings())
    attempt = require_attempt(database, attempt_id)
    correct = []
    for question, answer in zip(attempt["questions"], body.answers):
        if question.get("type") == "match":
            if not isinstance(answer, list) or set(answer) != set(range(len(question["choices"]))):
                raise HTTPException(422, detail="Choose a different phrase for each matching clue.")
            correct.append(answer == question["answer_indices"])
        else:
            if not isinstance(answer, int) or not 0 <= answer < len(question["choices"]):
                raise HTTPException(422, detail="Choose one answer for each question.")
            correct.append(answer == question["answer_index"])
    return _public_attempt(database.submit(attempt_id, body.answers, sum(correct)))

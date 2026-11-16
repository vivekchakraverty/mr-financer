"""Local persistence for Learn selections, captions, and attempt history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_videos (
    slot_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learn_captions (
    video_id TEXT NOT NULL,
    language TEXT NOT NULL,
    source TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (video_id, language, source)
);
CREATE TABLE IF NOT EXISTS learn_attempts (
    id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    video_title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    questions TEXT NOT NULL,
    submitted_at TEXT,
    answers TEXT,
    score INTEGER
);
CREATE INDEX IF NOT EXISTS learn_attempts_video ON learn_attempts (video_id, created_at DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LearnStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(SCHEMA)
            with db:
                yield db
        finally:
            db.close()

    def videos(self) -> dict[str, dict]:
        with self.connect() as db:
            return {row["slot_id"]: json.loads(row["data"]) for row in db.execute("SELECT * FROM learn_videos")}

    def save_videos(self, rows: dict[str, dict]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT INTO learn_videos VALUES (?, ?) ON CONFLICT(slot_id) DO UPDATE SET data=excluded.data",
                [(slot, json.dumps(video, ensure_ascii=False)) for slot, video in rows.items()],
            )

    def captions(self, video_id: str, language: str, source: str) -> list[dict] | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT data FROM learn_captions WHERE video_id=? AND language=? AND source=?
                AND julianday(saved_at) > julianday('now', '-7 days')""",
                (video_id, language, source),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_captions(self, video_id: str, language: str, source: str, segments: list[dict]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM learn_captions WHERE julianday(saved_at) <= julianday('now', '-7 days')")
            db.execute(
                """INSERT INTO learn_captions VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id, language, source) DO UPDATE SET saved_at=excluded.saved_at, data=excluded.data""",
                (video_id, language, source, now_iso(), json.dumps(segments, ensure_ascii=False)),
            )

    def previous_questions(self, video_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT questions FROM learn_attempts WHERE video_id=? ORDER BY created_at DESC LIMIT 1",
                (video_id,),
            )
            previous = []
            for row in rows:
                for question in json.loads(row["questions"]):
                    if question.get("type") == "match":
                        for pair in question["pairs"]:
                            previous.extend([pair["prompt"], pair.get("anchor_question", pair["prompt"])])
                    else:
                        previous.extend([question["question"], question.get("anchor_question", question["question"])])
            return list(dict.fromkeys(previous))

    def create_attempt(self, attempt: dict) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO learn_attempts
                (id, slot_id, video_id, video_title, created_at, model, prompt_version, questions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt["id"], attempt["slot_id"], attempt["video_id"], attempt["video_title"],
                 attempt["created_at"], attempt["model"], attempt["prompt_version"],
                 json.dumps(attempt["questions"], ensure_ascii=False)),
            )

    def attempt(self, attempt_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM learn_attempts WHERE id=?", (attempt_id,)).fetchone()
        return _attempt(row) if row else None

    def submit(self, attempt_id: str, answers: list[int | list[int]], score: int) -> dict:
        with self.connect() as db:
            db.execute(
                """UPDATE learn_attempts SET submitted_at=?, answers=?, score=?
                WHERE id=? AND submitted_at IS NULL""",
                (now_iso(), json.dumps(answers), score, attempt_id),
            )
            row = db.execute("SELECT * FROM learn_attempts WHERE id=?", (attempt_id,)).fetchone()
        return _attempt(row)

    def history(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT id, slot_id, video_id, video_title, created_at, submitted_at, score
                FROM learn_attempts WHERE submitted_at IS NOT NULL
                ORDER BY submitted_at DESC, id DESC"""
            )
            return [dict(row) for row in rows]


def _attempt(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["questions"] = json.loads(result["questions"])
    if result["answers"] is not None:
        result["answers"] = json.loads(result["answers"])
    return result

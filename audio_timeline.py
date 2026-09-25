"""Build a timed audio plan from real TTS durations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List

from models import AudioCue, DialogueLine, ScenePlan


@dataclass
class TimelineEvent:
    start: float
    end: float
    kind: str  # ambience | dialogue | sfx | music
    label: str
    path: str = ""
    volume: float = 1.0
    character_id: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class SceneTimeline:
    scene_id: str
    events: List[TimelineEvent] = field(default_factory=list)
    duration: float = 0.0

    def to_dict(self):
        return {
            "scene_id": self.scene_id,
            "duration": self.duration,
            "events": [e.to_dict() for e in self.events],
        }


PRE_ROLL = 0.35
GAP_BETWEEN_LINES = 0.18
TAIL = 0.45


def build_scene_timeline(scene: ScenePlan) -> SceneTimeline:
    """Lay dialogue end-to-end using actual TTS durations, then place cues."""
    events: List[TimelineEvent] = []
    t = PRE_ROLL

    for line in scene.dialogue or []:
        dur = float(line.duration or 0.0)
        if dur <= 0 and line.text:
            dur = max(1.0, 0.28 * len(line.text.split()))
        start = t
        end = start + dur
        line.start = start
        line.end = end
        line.duration = dur
        events.append(TimelineEvent(
            start=start,
            end=end,
            kind="dialogue",
            label=f"{line.character_id}: {line.text[:40]}",
            path=line.audio_path or "",
            volume=1.0,
            character_id=line.character_id,
        ))
        t = end + GAP_BETWEEN_LINES

    spoken_end = max((e.end for e in events), default=PRE_ROLL)
    duration = max(scene.target_duration or 0.0, spoken_end + TAIL, 2.0)

    for cue in scene.cues or []:
        start = float(cue.start or 0.0)
        cue_dur = cue.duration if cue.duration is not None else max(0.4, duration - start)
        cue_dur = min(float(cue_dur), max(0.2, duration - start))
        events.append(TimelineEvent(
            start=start,
            end=start + cue_dur,
            kind=cue.kind,
            label=cue.name,
            path=cue.path or "",
            volume=float(cue.volume),
        ))

    # Default ambience if the scene didn't specify one.
    if not any(e.kind == "ambience" for e in events):
        events.insert(0, TimelineEvent(
            start=0.0,
            end=duration,
            kind="ambience",
            label="soft_grove",
            path="",
            volume=0.12,
        ))

    events.sort(key=lambda e: (e.start, e.kind))
    scene.duration = duration
    return SceneTimeline(scene_id=scene.scene_id, events=events, duration=duration)


def format_timeline(timeline: SceneTimeline) -> str:
    lines = [f"Scene {timeline.scene_id}  ({timeline.duration:.1f}s)"]
    for e in timeline.events:
        lines.append(f"  {e.start:4.1f}–{e.end:4.1f}s → {e.kind:8s} {e.label}")
    return "\n".join(lines)

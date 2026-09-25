"""Resumable episode checkpoints.

State lives at output/episodes/<episode_id>/checkpoint.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import config


def episode_dir(episode_id: str) -> Path:
    path = config.EPISODES_DIR / episode_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "scenes").mkdir(exist_ok=True)
    return path


def _path(episode_id: str) -> Path:
    return episode_dir(episode_id) / "checkpoint.json"


def load(episode_id: str) -> Dict[str, Any]:
    path = _path(episode_id)
    if not path.is_file():
        return {"episode_id": episode_id, "flags": {}, "updated_at": None}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"episode_id": episode_id, "flags": {}, "updated_at": None, "corrupt": True}


def save(episode_id: str, state: Dict[str, Any]) -> None:
    state = dict(state)
    state["episode_id"] = episode_id
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _path(episode_id)
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def is_done(episode_id: str, flag: str) -> bool:
    return bool(load(episode_id).get("flags", {}).get(flag))


def mark_done(episode_id: str, flag: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    state = load(episode_id)
    flags = state.setdefault("flags", {})
    flags[flag] = True
    if extra:
        state.setdefault("meta", {}).update(extra)
    save(episode_id, state)
    print(f"[checkpoint] {episode_id}: {flag}")
    return state


def plan_path(episode_id: str) -> Path:
    return episode_dir(episode_id) / "production_plan.json"


def save_plan(plan) -> Path:
    path = plan_path(plan.episode_id)
    path.write_text(json.dumps(plan.to_dict(), indent=2))
    return path


def load_plan(episode_id: str):
    from models import ProductionPlan

    path = plan_path(episode_id)
    if not path.is_file():
        return None
    try:
        return ProductionPlan.from_dict(json.loads(path.read_text()))
    except Exception as e:
        print(f"[!] production_plan.json is invalid ({e})")
        return None

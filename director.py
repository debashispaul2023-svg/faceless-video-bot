"""AI Director — single structured handoff between pipeline stages.

Does not invent vendor capabilities. Animation fallback is still + camera move.
"""

from __future__ import annotations

from typing import Optional

import config
from characters import character_bible_text, is_forbidden_text
from checkpoint import mark_done, save_plan
from models import ProductionPlan
from story_engine import (
    generate_episode_outline,
    generate_idea,
    generate_production_plan,
    generate_story,
    validate_plan,
)


class Director:
    def conceive(self, idea: Optional[str] = None, demo: bool = False):
        idea = generate_idea(idea)
        if is_forbidden_text(idea):
            raise ValueError("Director rejected a third-party copy idea.")
        story = generate_story(idea, demo=demo)
        return story

    def plan_episode(self, idea: Optional[str] = None, demo: bool = False):
        story = self.conceive(idea, demo=demo)
        return generate_episode_outline(story, demo=demo)

    def direct(self, idea: Optional[str] = None, demo: bool = False) -> ProductionPlan:
        """Idea → story → episode outline → validated ProductionPlan."""
        print("[director] Character Bible loaded")
        print(character_bible_text().splitlines()[0])
        plan = generate_production_plan(idea=idea, demo=demo)
        plan.target_duration = config.TARGET_EPISODE_SECONDS
        check = validate_plan(plan)
        if not check.valid:
            raise RuntimeError("Director rejected invalid production plan: " + "; ".join(check.errors))
        mark_done(plan.episode_id, "idea_done")
        mark_done(plan.episode_id, "story_done")
        save_plan(plan)
        mark_done(plan.episode_id, "plan_done")
        print(f"[director] ProductionPlan ready: {plan.title} ({len(plan.scenes)} scenes)")
        return plan


def direct_episode(idea: Optional[str] = None, demo: bool = False) -> ProductionPlan:
    return Director().direct(idea=idea, demo=demo)

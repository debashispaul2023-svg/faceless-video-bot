"""Original episode ideas, stories, and structured production plans."""

from __future__ import annotations

import json
import re
import time
import traceback
import uuid
from typing import List, Optional

import config
from audio_cues import default_cues_for_scene
from characters import (
    CAST_IDS,
    VISUAL_STYLE,
    WORLD_BIBLE,
    character_bible_text,
    get_character,
    is_forbidden_text,
)
from models import (
    DialogueLine,
    EpisodeOutline,
    ProductionPlan,
    ScenePlan,
    StoryDraft,
    ValidationResult,
)


DEMO_PLAN = ProductionPlan(
    episode_id="demo_grove_picnic",
    title="The Upside-Down Picnic",
    logline="Toto packs a picnic, Piku's gadget flips the blanket, and Fiko spots the missing berries.",
    synopsis=(
        "On a sunny morning in Sunny Grove the friends pack a picnic. "
        "Piku's new blanket-flipping spring does exactly what it says. "
        "Together they fix the mess and share the snacks."
    ),
    moral="A helper idea works better when everyone checks it first.",
    characters=["toto", "momo", "piku", "fiko"],
    style=VISUAL_STYLE,
    series=config.SERIES_NAME,
    thumbnail_prompt="Toto the golden meerkat and Momo the lavender hippo at a flipped picnic blanket on a sunny hill, 3D kids cartoon, no text",
    tags=["kids cartoon", "animal friends", "sunny grove", "comedy", "friendship"],
    description="An original Sunny Grove Friends short. Four animal friends learn to test a new idea together.",
    scenes=[
        ScenePlan(
            scene_id="scene_001",
            index=1,
            title="Picnic plan",
            environment="Sunny Grove hillside orchard with round cottages and a wooden picnic blanket on green grass",
            action="Toto stands on a picnic basket pointing at a checklist while Momo hugs a lunchbox",
            camera="wide establishing shot, gentle eye-level, morning light",
            style=VISUAL_STYLE,
            characters=["toto", "momo"],
            dialogue=[
                DialogueLine("narrator", "One bright morning in Sunny Grove, Toto had a very important picnic plan."),
                DialogueLine("toto", "Blankets, berries, and Momo's famous crunch cakes. I've got a plan!"),
                DialogueLine("momo", "I packed extra crunch cakes. Just in case the plan gets hungry."),
            ],
            cues=default_cues_for_scene(0),
            transition="cut",
            target_duration=10.0,
        ),
        ScenePlan(
            scene_id="scene_002",
            index=2,
            title="The spring surprise",
            environment="same hillside picnic, blanket half-flipped in the air, fruit bouncing",
            action="Piku wears goggles and holds a spring gadget while the picnic blanket flips upward",
            camera="medium shot with a slight low angle on Piku",
            style=VISUAL_STYLE,
            characters=["piku", "toto", "momo"],
            dialogue=[
                DialogueLine("piku", "I built a blanket flippity spring! It saves folding time."),
                DialogueLine("toto", "Piku, wait—"),
                DialogueLine("momo", "My crunch cakes are flying!"),
            ],
            cues=default_cues_for_scene(1),
            transition="cut",
            target_duration=9.0,
        ),
        ScenePlan(
            scene_id="scene_003",
            index=3,
            title="Fiko finds the berries",
            environment="creek and wooden bridge below the hillside, berries in the grass",
            action="Fiko the blue penguin points at berries by the creek while friends gather the picnic",
            camera="close group shot, warm sunset rim light",
            style=VISUAL_STYLE,
            characters=["fiko", "toto", "momo", "piku"],
            dialogue=[
                DialogueLine("fiko", "Wait... look at that. The berries rolled to the creek, not the clouds."),
                DialogueLine("toto", "Good eyes, Fiko. Next time we test the gadget on a leaf, not lunch."),
                DialogueLine("piku", "Leaf test. Noted. Also, the spring is very good at flying cakes."),
                DialogueLine("narrator", "And that is how the Sunny Grove picnic was saved — a little messy, and a lot more fun."),
            ],
            cues=default_cues_for_scene(2),
            transition="fade",
            target_duration=12.0,
        ),
    ],
)


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (title or "episode").lower()).strip("_")
    return (base[:40] or "episode") + "_" + uuid.uuid4().hex[:6]


def format_youtube_title(episode_title: str, lead_ids: List[str], year: int = 2026) -> str:
    leads = []
    for cid in lead_ids or []:
        if cid == "narrator":
            continue
        try:
            leads.append(get_character(cid).name)
        except KeyError:
            continue
    lead_txt = " & ".join(leads[:2]) or "Friends"
    raw = f"{episode_title} | {config.SERIES_NAME}: {lead_txt} | Kids Cartoon {year}"
    return raw[:100]


def validate_plan(plan: ProductionPlan) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    if not (plan.title or "").strip():
        errors.append("Plan is missing a title.")
    if is_forbidden_text(" ".join([plan.title, plan.synopsis, plan.logline, plan.description])):
        errors.append("Plan contains forbidden third-party names.")
    if not plan.scenes:
        errors.append("Plan has no scenes.")
    ids = set()
    for scene in plan.scenes:
        if scene.scene_id in ids:
            errors.append(f"Duplicate scene_id {scene.scene_id}")
        ids.add(scene.scene_id)
        if not scene.environment or not scene.action:
            errors.append(f"{scene.scene_id} missing environment/action.")
        if not scene.camera:
            warnings.append(f"{scene.scene_id} missing camera.")
        if not scene.dialogue:
            warnings.append(f"{scene.scene_id} has no dialogue.")
        for cid in scene.characters or []:
            if cid not in CAST_IDS + ["narrator"]:
                errors.append(f"{scene.scene_id} unknown on-screen character {cid}")
        for line in scene.dialogue:
            if line.character_id not in CAST_IDS + ["narrator"]:
                errors.append(f"{scene.scene_id} unknown speaker {line.character_id}")
            if not (line.text or "").strip():
                errors.append(f"{scene.scene_id} empty dialogue line")
            if is_forbidden_text(line.text):
                errors.append(f"{scene.scene_id} dialogue copies forbidden names")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _plan_from_json(data: dict) -> ProductionPlan:
    title = data.get("title") or "Sunny Grove Adventure"
    scenes_in = data.get("scenes") or []
    scenes: List[ScenePlan] = []
    for i, raw in enumerate(scenes_in, start=1):
        dialogue = []
        for d in raw.get("dialogue") or []:
            dialogue.append(DialogueLine(
                character_id=str(d.get("character_id") or d.get("character") or "narrator").lower(),
                text=str(d.get("text") or "").strip(),
                emotion=str(d.get("emotion") or "neutral"),
            ))
        scenes.append(ScenePlan(
            scene_id=raw.get("scene_id") or f"scene_{i:03d}",
            index=int(raw.get("index") or i),
            title=raw.get("title") or f"Scene {i}",
            environment=raw.get("environment") or "Sunny Grove hillside",
            action=raw.get("action") or "friends talking",
            camera=raw.get("camera") or "medium shot, eye level",
            style=raw.get("style") or VISUAL_STYLE,
            characters=[c.lower() for c in (raw.get("characters") or [])],
            dialogue=dialogue,
            cues=default_cues_for_scene(i - 1),
            transition=raw.get("transition") or "cut",
            target_duration=float(raw.get("target_duration") or 8.0),
        ))
    return ProductionPlan(
        episode_id=data.get("episode_id") or _slug(title),
        title=title,
        logline=data.get("logline") or "",
        synopsis=data.get("synopsis") or "",
        moral=data.get("moral") or "",
        characters=[c.lower() for c in (data.get("characters") or CAST_IDS)],
        scenes=scenes,
        style=data.get("style") or VISUAL_STYLE,
        series=data.get("series") or config.SERIES_NAME,
        thumbnail_prompt=data.get("thumbnail_prompt") or f"{title}, 3D kids cartoon friends, no text",
        tags=data.get("tags") or ["kids cartoon", "sunny grove", "friendship"],
        description=data.get("description") or data.get("synopsis") or title,
    )


def _catalog() -> List[ProductionPlan]:
    """Original local episodes. Same format as a 3D animal-comedy kids channel,
    but none of these copy another show's plots or characters."""
    picnic = ProductionPlan.from_dict(DEMO_PLAN.to_dict())
    extras = [
        {
            "episode_id": "toto_lost_mango",
            "title": "The Giant Mango",
            "idea": "a funny story about Toto losing a giant mango",
            "logline": "Toto's giant mango rolls into the jungle and Momo tries to help before it becomes lunch for the creek.",
            "synopsis": (
                "Toto finds a giant mango. It slips. Momo climbs, Piku has a loud idea, "
                "and Fiko spots the mango hiding in the ferns. They share it."
            ),
            "moral": "Asking for help is part of a good plan.",
            "characters": ["toto", "momo", "piku", "fiko"],
            "setting": "jungle clearing",
            "beats": [
                ("The shiny mango", "sunny jungle clearing under a giant mango tree",
                 "Toto the young elephant looks up at one huge golden mango",
                 "wide establishing shot, morning light",
                 "hook", "curious + hopeful",
                 [("narrator", "One sunny morning, Toto saw the biggest mango in Sunny Grove."),
                  ("toto", "That mango is as big as my plan!")]),
                ("A careful wiggle", "same clearing, Toto's trunk around the mango stem",
                 "Toto stretches his short trunk toward the mango while Momo watches from a branch",
                 "medium shot",
                 "setup", "focused + excited",
                 [("toto", "Slow trunk. Slow trunk."),
                  ("momo", "I can reach it! Want a boost?")]),
                ("The roll-away", "mossy ground, giant mango bouncing toward the ferns",
                 "the mango pops free and rolls past Toto's feet",
                 "low tracking shot",
                 "problem", "surprised",
                 [("toto", "The mango is leaving!"),
                  ("momo", "I will bounce after it!")]),
                ("Branch bounce", "canopy path, Momo on a branch above the rolling mango",
                 "Momo jumps from branch to branch while Toto trots below",
                 "medium shot to quick close-up",
                 "escalation", "excited + determined",
                 [("momo", "Left fern! No — right fern!"),
                  ("toto", "I've got a new plan. A running plan.")]),
                ("Loud idea", "leafy path, Piku hovering with goggles down",
                 "Piku flaps in with a leaf-net idea that catches a bush instead",
                 "slight low angle on Piku",
                 "comedy", "proud then oops",
                 [("piku", "Leaf net! I can build that!"),
                  ("toto", "That net caught a bush.")]),
                ("Fern hide", "thick ferns beside the creek",
                 "Fiko the young fox points at the mango tucked under ferns",
                 "close group shot",
                 "resolution", "relieved + clever",
                 [("fiko", "Wait... look at that. The mango took a nap."),
                  ("momo", "Good eyes, Fiko!")]),
                ("Shared snack", "creek bank picnic light, mango sliced into friendly pieces",
                 "friends sit together sharing mango slices",
                 "warm wide ending shot",
                 "ending", "happy",
                 [("toto", "Next plan: hold the mango with two friends."),
                  ("narrator", "And the giant mango became a giant snack.")]),
            ],
        },
        {
            "episode_id": "kite_on_the_bridge",
            "title": "The Bridge Kite",
            "idea": "a kite stuck under the wooden bridge",
            "logline": "Toto's kite slips under the creek bridge and Fiko finds the quiet way to free it.",
            "synopsis": "A windy afternoon sends Toto's kite under the bridge. Gadgets splash. Fiko waits for the calm ripple.",
            "moral": "Slow looking can beat a fast gadget.",
            "characters": ["toto", "piku", "fiko"],
            "setting": "creek and wooden bridge",
            "beats": [
                ("Windy hill", "hillside orchard, a red kite dipping toward the creek",
                 "Toto runs after a dipping kite while Piku opens a gadget bag",
                 "wide tracking shot",
                 [("toto", "The wind took my kite! I've got a plan!"),
                  ("piku", "I can build a kite-catcher claw!")]),
                ("Splash plan", "creek under the wooden bridge, claw gadget dripping",
                 "Piku's claw splashes the water and the kite slips farther",
                 "medium shot from the bank",
                 [("piku", "Almost... wait, that was a splash."),
                  ("toto", "The kite is swimming now.")]),
                ("Quiet ripple", "still water under the bridge, kite on a dry stone",
                 "Fiko points at the kite resting on a stone while friends watch",
                 "close group shot",
                 [("fiko", "Wait... look at that. The water put it on the stone."),
                  ("toto", "Good eyes. Next plan: ask the creek first."),
                  ("narrator", "And the kite came home only a little soggy.")]),
            ],
        },
        {
            "episode_id": "market_melon_roll",
            "title": "The Rolling Melon",
            "idea": "a melon rolls through the village square",
            "logline": "Momo's melon rolls through the square and the friends turn the chase into a gentle catch.",
            "synopsis": "Market day in Sunny Grove. A round melon decides to travel. Everyone helps without squashing it.",
            "moral": "Chasing together works if nobody knocks the others over.",
            "characters": ["momo", "toto", "piku"],
            "setting": "village square",
            "beats": [
                ("Market morning", "tiny village square with fruit stalls",
                 "Momo hugs a striped melon at a stall while Toto points at a shopping list",
                 "wide establishing shot",
                 [("momo", "This melon looks like a snack planet."),
                  ("toto", "Hold it with two hands. I've got a plan!")]),
                ("Melon race", "cobblestone square, melon rolling past a fountain",
                 "the melon rolls past Piku who builds a soft cushion ramp",
                 "low angle following the melon",
                 [("piku", "Cushion ramp incoming!"),
                  ("momo", "Please be a gentle ramp!")]),
                ("Soft stop", "fountain edge with a pillow pile and the melon safe",
                 "friends sit around the stopped melon",
                 "warm close group shot",
                 [("toto", "Plan updated: snacks do not race."),
                  ("narrator", "The melon stayed round, and lunch was saved.")]),
            ],
        },
        {
            "episode_id": "workshop_echo",
            "title": "The Echo Bell",
            "idea": "a tiny bell that echoes too loudly in the workshop",
            "logline": "Piku invents an echo bell. It works. Too well. Momo covers her ears and Fiko finds the mute button: a sock.",
            "synopsis": "The hillside workshop rings. Friends try clever fixes. The kindest fix is the simplest.",
            "moral": "A big idea can use a small quiet solution.",
            "characters": ["piku", "momo", "fiko"],
            "setting": "hillside workshop",
            "beats": [
                ("Ring one", "Piku's hillside workshop, wooden benches and springs",
                 "Piku holds a small brass bell while Momo covers her ears",
                 "medium workshop shot",
                 [("piku", "Echo bell, version one!"),
                  ("momo", "It is very... echo.")]),
                ("Louder plan", "same workshop, springs bouncing",
                 "Piku adds another spring and the bell rings again",
                 "slight low angle on Piku",
                 [("piku", "More spring means better echo."),
                  ("momo", "Snack first. Ears later.")]),
                ("Sock mute", "workshop table with a sock on the bell",
                 "Fiko places a sock over the bell and everyone smiles",
                 "close shot on the quiet bell",
                 [("fiko", "Wait... look at that. Quiet fits."),
                  ("narrator", "The echo learned manners, and the workshop could think again.")]),
            ],
        },
    ]
    plans = [picnic]
    for item in extras:
        scenes = []
        for i, beat in enumerate(item["beats"], start=1):
            if len(beat) == 5:
                title, env, action, camera, lines = beat
                beat_name, expressions = "", "playful"
            else:
                title, env, action, camera, beat_name, expressions, lines = beat
            per_scene = 26.0 if len(item["beats"]) >= 6 else 9.0
            scenes.append(ScenePlan(
                scene_id=f"scene_{i:03d}",
                index=i,
                title=title,
                environment=env,
                action=action,
                camera=camera,
                style=VISUAL_STYLE,
                characters=list(item["characters"]),
                dialogue=[
                    DialogueLine(cid, text, estimated_duration=max(1.2, 0.28 * len(text.split())), voice_id=cid)
                    for cid, text in lines
                ],
                cues=default_cues_for_scene(i - 1),
                transition="fade" if i == len(item["beats"]) else "cut",
                target_duration=per_scene,
                location=env,
                expressions=expressions,
                lighting="warm daylight",
                visual_prompt="",
                animation_instructions=f"{action}. Keep character designs locked. Gentle kids-cartoon motion.",
                continuity_notes=f"Same Sunny Grove nature world. Beat={beat_name or i}.",
                beat=beat_name,
            ))
        leads = item["characters"]
        title = item["title"]
        plans.append(ProductionPlan(
            episode_id=item["episode_id"],
            title=title,
            logline=item["logline"],
            synopsis=item["synopsis"],
            moral=item["moral"],
            characters=list(leads),
            scenes=scenes,
            style=VISUAL_STYLE,
            series=config.SERIES_NAME,
            thumbnail_prompt=f"{title}, {', '.join(leads)} in Sunny Grove, 3D kids cartoon, no text",
            tags=["kids cartoon", "sunny grove", "animal friends", "comedy"],
            description=item["synopsis"],
            youtube_title=format_youtube_title(title, leads),
            idea=item["idea"],
        ))
    picnic.youtube_title = format_youtube_title(picnic.title, picnic.characters)
    picnic.idea = "an upside-down picnic"
    return plans


def generate_idea(seed: Optional[str] = None) -> str:
    if seed and seed.strip():
        return seed.strip()
    return "a small everyday problem in Sunny Grove that friends solve together"


def generate_story(idea: Optional[str] = None, demo: bool = False) -> StoryDraft:
    idea = generate_idea(idea)
    plan = _pick_local_plan(idea, demo=demo)
    return StoryDraft(
        idea=idea,
        title=plan.title,
        logline=plan.logline,
        synopsis=plan.synopsis,
        moral=plan.moral,
        characters=list(plan.characters),
        setting=plan.scenes[0].environment if plan.scenes else "Sunny Grove",
    )


def generate_episode_outline(story: StoryDraft, demo: bool = False) -> EpisodeOutline:
    plan = _pick_local_plan(story.idea, demo=demo)
    beats = []
    for scene in plan.scenes:
        beats.append({
            "title": scene.title,
            "environment": scene.environment,
            "action": scene.action,
            "camera": scene.camera,
            "characters": list(scene.characters),
        })
    return EpisodeOutline(
        episode_id=plan.episode_id,
        title=plan.title,
        youtube_title=plan.youtube_title or format_youtube_title(plan.title, plan.characters),
        logline=plan.logline,
        synopsis=plan.synopsis,
        moral=plan.moral,
        characters=list(plan.characters),
        scene_beats=beats,
        series=plan.series,
    )


def generate_scene_plans(outline: EpisodeOutline, demo: bool = False) -> List[ScenePlan]:
    plan = _pick_local_plan(outline.logline or outline.title, demo=demo)
    return plan.scenes


def _pick_local_plan(idea: Optional[str], demo: bool = False) -> ProductionPlan:
    catalog = _catalog()
    if demo:
        return catalog[0]
    if not idea:
        chosen = catalog[0]
    else:
        key = idea.lower()
        tokens = [t for t in key.split() if len(t) > 3]
        best = catalog[0]
        best_score = -1
        for plan in catalog:
            blob = " ".join([plan.episode_id, plan.title, plan.logline, plan.idea, plan.synopsis]).lower()
            score = sum(3 if t in (plan.idea or "").lower() else 1 for t in tokens if t in blob)
            if plan.episode_id.replace("_", " ") in key:
                score += 5
            if score > best_score:
                best, best_score = plan, score
        chosen = best if best_score > 0 else catalog[abs(hash(key)) % len(catalog)]
    plan = ProductionPlan.from_dict(chosen.to_dict())
    if idea and idea.strip() and chosen.episode_id == catalog[0].episode_id and idea.lower() not in chosen.title.lower():
        plan.idea = idea.strip()
    if not plan.youtube_title:
        plan.youtube_title = format_youtube_title(plan.title, plan.characters)
    return plan


def _local_plan(idea: Optional[str] = None, demo: bool = False) -> ProductionPlan:
    return _pick_local_plan(idea, demo=demo)


def generate_production_plan(idea: Optional[str] = None, demo: bool = False) -> ProductionPlan:
    """Idea → Story → Episode outline → Scenes → ProductionPlan."""
    idea = generate_idea(idea)
    if is_forbidden_text(idea):
        raise ValueError("Idea uses forbidden third-party names. Use original Sunny Grove ideas only.")

    if demo or config.STORY_PROVIDER == "local" or not config.GEMINI_API_KEY:
        story = generate_story(idea, demo=demo)
        outline = generate_episode_outline(story, demo=demo)
        plan = _local_plan(idea=idea, demo=demo)
        plan.idea = story.idea
        plan.youtube_title = outline.youtube_title
        print(f"[+] Local story pipeline: {story.title} -> {len(plan.scenes)} scenes")
        return plan

    try:
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        prompt = f"""
You write ORIGINAL kids cartoon episodes for "{config.SERIES_NAME}".
{character_bible_text()}
Do not copy any existing TV show, names, plots, or jokes from other series.
Wholesome physical comedy only. No scares, no romance, no insults.

Idea seed: {idea}

Return ONLY JSON with keys:
title, logline, synopsis, moral, characters, thumbnail_prompt, tags, description,
scenes: [{{scene_id, index, title, environment, action, camera, characters,
dialogue: [{{character_id, text, emotion}}], target_duration}}]
Use 3 to 5 short scenes. Each scene 2-4 dialogue lines. Keep language simple.
"""
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        text = (response.text or "").strip()
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```$", "", text).strip()
        data = json.loads(text)
        plan = _plan_from_json(data)
        plan.idea = idea
        plan.youtube_title = format_youtube_title(plan.title, plan.characters)
        if is_forbidden_text(json.dumps(plan.to_dict())):
            raise ValueError("Model output contained forbidden names")
        print(f"[+] Gemini story ready: {plan.title}")
        return plan
    except Exception as e:
        print(f"[!] Story provider failed ({e}); using local original fallback.")
        traceback.print_exc()
        return _local_plan(idea=idea, demo=False)


def run_phase1_pipeline(idea: Optional[str] = None, demo: bool = False) -> dict:
    """Public Phase 1 check: Idea → Story → Episode → Scenes → Plan → Validation."""
    idea = generate_idea(idea)
    story = generate_story(idea, demo=demo)
    outline = generate_episode_outline(story, demo=demo)
    plan = generate_production_plan(idea=idea, demo=demo)
    result = validate_plan(plan)
    return {
        "idea": idea,
        "story": story.to_dict(),
        "episode": outline.to_dict(),
        "plan": plan.to_dict(),
        "validation": result.to_dict(),
    }

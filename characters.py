"""Canonical original characters for Sunny Grove Friends.

These are original designs. They are NOT Jungle Beat (or any other
existing show) characters, names, or designs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CharacterCanon:
    id: str
    name: str
    species: str
    personality: str
    proportions: str
    colors: str
    accessories: str
    style_notes: str
    canonical_prompt: str
    catchphrase: str = ""
    appearance: str = ""
    voice_id: str = ""
    reference_images: tuple = ()
    negative_prompt: str = (
        "do not change species, colors, or accessories; no extra logos; "
        "no photoreal humans; no scary teeth; no text"
    )


CHARACTERS: Dict[str, CharacterCanon] = {
    "toto": CharacterCanon(
        id="toto",
        name="Toto",
        species="young elephant",
        personality="gentle, determined, a little clumsy, never gives up on a plan",
        proportions="small young elephant, oversized round head, short trunk, stubby legs, tiny tail",
        colors="warm peach-gray skin, cream inner ears, soft pink trunk tip",
        accessories="small red scarf tied in a knot",
        style_notes="friendly 3D kids cartoon, rounded shapes, big shiny eyes, no text",
        canonical_prompt=(
            "Toto, a young cartoon elephant calf with peach-gray skin, cream inner ears, "
            "a short friendly trunk, stubby legs, wearing a small red scarf, big shiny "
            "eyes, original 3D kids animation style, rounded proportions"
        ),
        catchphrase="I've got a plan!",
        appearance="young elephant calf, red scarf, short trunk, peach-gray skin",
        voice_id="toto",
    ),
    "momo": CharacterCanon(
        id="momo",
        name="Momo",
        species="young monkey",
        personality="bouncy, helpful, curious, jumps first and thinks second",
        proportions="small young monkey, long tail, expressive hands, slightly oversized head",
        colors="caramel-brown fur, cream face mask, peach hands and feet",
        accessories="tiny blue vest",
        style_notes="friendly 3D kids cartoon, rounded shapes, big shiny eyes, no text",
        canonical_prompt=(
            "Momo, a young cartoon monkey with caramel-brown fur, a cream face, "
            "peach hands, a long curling tail, wearing a tiny blue vest, big shiny "
            "eyes, original 3D kids animation style, rounded proportions"
        ),
        catchphrase="I can reach it!",
        appearance="young monkey, blue vest, caramel fur, cream face",
        voice_id="momo",
    ),
    "piku": CharacterCanon(
        id="piku",
        name="Piku",
        species="colorful parrot",
        personality="chatty, bright, invents loud ideas, repeats the important word",
        proportions="small plump parrot, short wings, round head, compact tail fan",
        colors="teal chest, mango-yellow head, coral wing tips, lime tail feathers",
        accessories="tiny inventor goggles pushed up on the head",
        style_notes="friendly 3D kids cartoon, rounded shapes, big shiny eyes, no text",
        canonical_prompt=(
            "Piku, a colorful cartoon parrot with a mango-yellow head, teal chest, "
            "coral wing tips and lime tail feathers, tiny inventor goggles on the head, "
            "big shiny eyes, original 3D kids animation style, rounded proportions"
        ),
        catchphrase="I can build that!",
        appearance="colorful parrot, yellow head, teal chest, goggles",
        voice_id="piku",
    ),
    "fiko": CharacterCanon(
        id="fiko",
        name="Fiko",
        species="young fox",
        personality="shy, clever, careful, notices the one detail everyone missed",
        proportions="small young fox, big ears, fluffy tail, compact body",
        colors="soft orange fur, cream chest, white-tipped tail, brown paws",
        accessories="tiny green leaf pin on the chest",
        style_notes="friendly 3D kids cartoon, rounded shapes, big shiny eyes, no text",
        canonical_prompt=(
            "Fiko, a young cartoon fox with soft orange fur, a cream chest, a white-tipped "
            "fluffy tail, a tiny green leaf pin, big shiny eyes, original 3D kids animation "
            "style, rounded proportions"
        ),
        catchphrase="Wait... look at that.",
        appearance="young fox, orange fur, cream chest, leaf pin",
        voice_id="fiko",
    ),
    "narrator": CharacterCanon(
        id="narrator",
        name="Narrator",
        species="off-screen storyteller",
        personality="warm, playful, never mean",
        proportions="not on screen",
        colors="n/a",
        accessories="n/a",
        style_notes="voice only",
        canonical_prompt="off-screen warm narrator, not visible",
        catchphrase="",
    ),
}

CAST_IDS: List[str] = ["toto", "momo", "piku", "fiko"]
ALL_SPEAKER_IDS: List[str] = CAST_IDS + ["narrator"]

VISUAL_STYLE = (
    "original 3D kids cartoon, Pixar-like appealing lighting, soft global illumination, "
    "clean colorful materials, family-friendly, no logos, no text, no watermark, "
    "no photoreal humans, wholesome comedy"
)

WORLD_BIBLE = (
    "Sunny Grove is an original colorful nature village at the edge of a friendly "
    "jungle: fruit trees, a creek, a wooden bridge, round cottages, and a hillside "
    "workshop. Comedy comes from everyday mishaps, not copying any existing show."
)


def _custom_bible_path() -> Path:
    import config
    return config.ASSETS_DIR / "character_bible_extra.json"


def load_custom_characters() -> Dict[str, CharacterCanon]:
    path = _custom_bible_path()
    extras: Dict[str, CharacterCanon] = {}
    if not path.is_file():
        return extras
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return extras
    for item in raw if isinstance(raw, list) else raw.get("characters", []):
        cid = str(item.get("id") or "").strip().lower()
        if not cid or cid in CHARACTERS:
            continue
        extras[cid] = CharacterCanon(
            id=cid,
            name=item.get("name") or cid.title(),
            species=item.get("species") or "animal friend",
            personality=item.get("personality") or "kind",
            proportions=item.get("proportions") or "rounded kids-cartoon proportions",
            colors=item.get("colors") or "bright friendly colors",
            accessories=item.get("accessories") or "",
            style_notes=item.get("style_notes") or "friendly 3D kids cartoon",
            canonical_prompt=item.get("canonical_prompt") or item.get("name") or cid,
            catchphrase=item.get("catchphrase") or "",
            appearance=item.get("appearance") or "",
            voice_id=item.get("voice_id") or cid,
            reference_images=tuple(item.get("reference_images") or []),
            negative_prompt=item.get("negative_prompt") or CharacterCanon.__dataclass_fields__["negative_prompt"].default,
        )
    return extras


def all_characters() -> Dict[str, CharacterCanon]:
    merged = dict(CHARACTERS)
    merged.update(load_custom_characters())
    return merged


def get_character(character_id: str) -> CharacterCanon:
    key = (character_id or "").strip().lower()
    roster = all_characters()
    if key not in roster:
        raise KeyError(f"Unknown character id: {character_id}")
    return roster[key]


def register_character(data: dict) -> CharacterCanon:
    """Add an extra original character without editing core cast."""
    cid = str(data.get("id") or "").strip().lower()
    if not cid:
        raise ValueError("character id required")
    if cid in CHARACTERS:
        raise ValueError(f"{cid} is a core character")
    path = _custom_bible_path()
    current = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text())
            current = loaded if isinstance(loaded, list) else loaded.get("characters", [])
        except Exception:
            current = []
    current = [c for c in current if str(c.get("id", "")).lower() != cid]
    current.append(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2))
    return get_character(cid)


def list_on_screen_characters() -> List[CharacterCanon]:
    roster = all_characters()
    core = [roster[cid] for cid in CAST_IDS if cid in roster]
    extras = [ch for cid, ch in roster.items() if cid not in CAST_IDS and cid != "narrator"]
    return core + extras


# ---------------------------------------------------------------------------
# Character Bible (original world only)
# ---------------------------------------------------------------------------

FORBIDDEN_IP_NAMES = (
    "jungle beat", "munki", "sunrise jungle", "munki & trunk",
)

LOCATIONS = {
    "jungle_clearing": "sunny jungle clearing with giant mango trees and soft moss",
    "hillside_orchard": "Sunny Grove hillside orchard with round cottages and fruit trees",
    "creek_bridge": "shallow creek and a small wooden bridge below the hillside",
    "workshop": "Piku's hillside workshop with harmless spring gadgets and wooden benches",
    "market_square": "tiny village square with fruit stalls and a round stone fountain",
    "picnic_lawn": "soft green picnic lawn with a checkered blanket",
    "canopy_path": "leafy canopy path with thick branches over a nature trail",
}

RELATIONSHIPS = {
    "toto-momo": "Toto makes the plan; Momo climbs and fetches.",
    "toto-piku": "Toto wants a simple plan; Piku wants a noisy new idea.",
    "piku-fiko": "Fiko notices the one detail Piku's idea forgot.",
    "momo-fiko": "Momo jumps; Fiko watches the landing spot.",
}

BIBLE_RULES = [
    "Core cast is Toto (elephant), Momo (monkey), Piku (parrot), Fiko (fox), plus optional extras.",
    "Never use names, logos, plots, or designs from other cartoon series.",
    "Comedy is physical and kind. No teasing that hurts.",
    "Stories follow hook, setup, problem, escalation, comedy, resolution, ending.",
    "Keep character colors, accessories, and species locked in every scene.",
]


def character_bible_text() -> str:
    lines = [WORLD_BIBLE, ""]
    for ch in list_on_screen_characters():
        lines.append(
            f"- {ch.name} ({ch.id}): {ch.species}. {ch.personality}. "
            f"Look: {ch.colors}; {ch.accessories}. Says: {ch.catchphrase}"
        )
    lines.append("")
    lines.append("Rules: " + " ".join(BIBLE_RULES))
    return "\n".join(lines)


def is_forbidden_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in FORBIDDEN_IP_NAMES)


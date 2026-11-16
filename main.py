#!/usr/bin/env python3
"""Sunny Grove Friends production entrypoint.

Default path: original kids-cartoon pipeline.
Legacy faceless-shorts path remains available via --legacy.
New: --clip-url for YouTube stream clipping.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import config
from checkpoint import episode_dir, is_done, load_plan, mark_done, save_plan
from compilation_engine import compile_episodes, find_completed_episodes
from quality_control import validate_episode_package
from director import direct_episode
from story_engine import generate_production_plan, run_phase1_pipeline, validate_plan
from thumbnail_generator import generate_cartoon_thumbnail
from tts_engine import generate_episode_dialogue
from video_renderer import render_cartoon_episode
from visual_generator import generate_episode_visuals
try:
    from clipper import process_url
except Exception:
    def process_url(*a, **k):
        print("[!] clipper not available")
        return []
try:
    from youtube_uploader import build_cartoon_metadata, upload_cartoon_episode
except Exception:
    def build_cartoon_metadata(plan):
        return {"title": getattr(plan, "youtube_title", None) or plan.title, "description": plan.description, "hashtags": plan.tags}
    def upload_cartoon_episode(*args, **kwargs):
        print("[i] YouTube client libraries not installed — upload skipped.")
        return None


def run_pipeline(episode_id: str | None = None, idea: str | None = None,
                 demo: bool = False, resume: bool = False, upload: bool = False) -> Path:
    if resume and episode_id:
        plan = load_plan(episode_id)
        if plan is None:
            raise FileNotFoundError(f"No production plan for {episode_id}. Cannot resume.")
        print(f"[+] Resuming {episode_id}: {plan.title}")
    else:
        plan = direct_episode(idea=idea, demo=demo)
        if episode_id:
            plan.episode_id = episode_id
        mark_done(plan.episode_id, "story_generated")
        save_plan(plan)
        mark_done(plan.episode_id, "plan_generated")

    workdir = episode_dir(plan.episode_id)
    save_plan(plan)

    plan_check = validate_plan(plan)
    if not plan_check.valid:
        print("[!] Invalid production plan:")
        print(json.dumps(plan_check.to_dict(), indent=2))
        raise RuntimeError("Production plan failed validation.")
    for w in plan_check.warnings:
        print(f"[i] plan warning: {w}")

    generate_episode_visuals(plan, workdir, skip_done=True)
    save_plan(plan)
    generate_episode_dialogue(plan, workdir, skip_done=True)
    save_plan(plan)

    video_path = render_cartoon_episode(plan, workdir, skip_done=True)
    save_plan(plan)

    thumb = workdir / "thumbnail.jpg"
    if not (resume and thumb.is_file()):
        try:
            generate_cartoon_thumbnail(plan, workdir, thumb)
        except Exception as e:
            print(f"[!] Thumbnail failed ({e}); continuing without blocking the package.")
            traceback.print_exc()

    result = validate_episode_package(plan, workdir, video_path=video_path, thumbnail_path=thumb)
    (workdir / "validation.json").write_text(json.dumps(result.to_dict(), indent=2))
    print(json.dumps(result.to_dict(), indent=2))
    if not result.valid:
        print("[!] Quality control failed — upload skipped.")
        return video_path

    meta = build_cartoon_metadata(plan)
    (workdir / "youtube_metadata.json").write_text(json.dumps(meta, indent=2))
    mark_done(plan.episode_id, "package_ready", extra={"video": str(video_path)})

    if upload or config.AUTO_UPLOAD:
        upload_cartoon_episode(video_path, plan, thumbnail_path=thumb, privacy="private")
    else:
        print("[i] Upload not requested. Package is ready for review.")
    return video_path


def run_demo() -> Path:
    print("=" * 60)
    print("Sunny Grove Friends — DEMO (local assets, no paid APIs required)")
    print("=" * 60)
    config.STORY_PROVIDER = "local"
    config.VISUAL_PROVIDER = "local"
    config.TTS_PROVIDER = "local"
    config.AUTO_UPLOAD = False
    return run_pipeline(episode_id="demo_grove_picnic", demo=True)


def run_compile(paths: list[str] | None = None) -> Path:
    videos = [Path(p) for p in (paths or [])] or find_completed_episodes(limit=8)
    if not videos:
        raise FileNotFoundError("No completed episodes found to compile.")
    return compile_episodes(videos, compilation_id="compilation_001")


def run_legacy_shorts():
    """Preserved original faceless-shorts daily bot."""
    from researcher import generate_script
    from tts_engine import generate_voiceover
    from visual_generator import generate_visual_assets
    from video_renderer import render_final_video
    from youtube_uploader import schedule_video_upload, find_next_available_slots
    from instagram_uploader import upload_to_instagram
    import analytics_feedback

    print("=" * 60)
    print("LEGACY faceless-shorts pipeline")
    print("=" * 60)
    try:
        analytics_feedback.update_category_weights()
    except Exception as e:
        print(f"[i] Analytics skipped: {e}")
    script_data = generate_script()
    audio_path = generate_voiceover(script_data["script"], output_filename="voiceover_0.mp3")
    images = generate_visual_assets(script_data.get("visual_scenes", []))
    final_video_path = render_final_video(
        image_paths=images,
        audio_path=audio_path,
        narration_text=script_data["script"],
        output_filename="final_short_1.mp4",
    )
    print(f"[✓] Legacy short created: {final_video_path}")
    return final_video_path


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Sunny Grove Friends video pipeline")
    p.add_argument("--demo", action="store_true", help="Run a short local orchestration demo")
    p.add_argument("--resume", metavar="EPISODE_ID", help="Resume from last checkpoint")
    p.add_argument("--idea", type=str, default=None, help="Optional story seed")
    p.add_argument("--episode-id", type=str, default=None)
    p.add_argument("--upload", action="store_true", help="Upload only after QC passes (still private)")
    p.add_argument("--compile", action="store_true", help="Build a compilation from finished episodes")
    p.add_argument("--episodes", nargs="*", help="Episode mp4 paths for --compile")
    p.add_argument("--legacy", action="store_true", help="Run the original faceless-shorts bot")
    p.add_argument("--self-test", action="store_true", help="Run lightweight failure-path tests")
    p.add_argument("--plan-only", action="store_true", help="Phase 1 only: idea → story → plan → validate")
    p.add_argument("--clip-url", type=str, default=None, help="YouTube URL to analyze and clip")
    p.add_argument("--max-clips", type=int, default=10, help="How many best clips to produce (GTA 6: aim 10+)")
    return p.parse_args(argv)


def self_test() -> int:
    from models import ProductionPlan
    from story_engine import validate_plan

    print("[self-test] phase1 idea→plan")
    payload = run_phase1_pipeline(idea="a kite stuck under the wooden bridge", demo=False)
    assert payload["validation"]["valid"], payload["validation"]
    assert payload["plan"]["scenes"], "no scenes"
    assert "Kids Cartoon" in payload["episode"]["youtube_title"]

    print("[self-test] invalid JSON / plan")
    bad = ProductionPlan(
        episode_id="bad", title="", logline="", synopsis="", moral="",
        characters=["toto"], scenes=[], style="x",
    )
    result = validate_plan(bad)
    assert result.valid is False and result.errors

    print("[self-test] missing asset QC")
    from quality_control import validate_episode_package
    from story_engine import DEMO_PLAN
    qc = validate_episode_package(DEMO_PLAN, Path("/tmp/does-not-exist"))
    assert qc.valid is False

    print("[self-test] demo pipeline")
    video = run_demo()
    assert Path(video).is_file()

    print("[self-test] resume does not rebuild checkpointed scenes")
    run_pipeline(episode_id="demo_grove_picnic", resume=True, demo=True)

    print("[self-test] ok")
    return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        if args.clip_url:
            produced = process_url(args.clip_url, max_clips=args.max_clips, schedule=args.upload)
            if not produced:
                print("[!] No clips produced - failing the job so this is not reported as success")
                return 1
            return 0
        if args.legacy:
            run_legacy_shorts()
            return 0
        if args.compile:
            run_compile(args.episodes)
            return 0
        if args.plan_only:
            config.STORY_PROVIDER = config.STORY_PROVIDER or "local"
            payload = run_phase1_pipeline(idea=args.idea, demo=args.demo or not args.idea)
            print(json.dumps({
                "idea": payload["idea"],
                "story_title": payload["story"]["title"],
                "episode_title": payload["episode"]["youtube_title"],
                "scenes": [s["scene_id"] for s in payload["plan"]["scenes"]],
                "validation": payload["validation"],
            }, indent=2))
            if not payload["validation"]["valid"]:
                return 1
            workdir = episode_dir(payload["plan"]["episode_id"])
            (workdir / "story.json").write_text(json.dumps(payload["story"], indent=2))
            (workdir / "episode_outline.json").write_text(json.dumps(payload["episode"], indent=2))
            from models import ProductionPlan
            save_plan(ProductionPlan.from_dict(payload["plan"]))
            mark_done(payload["plan"]["episode_id"], "story_generated")
            mark_done(payload["plan"]["episode_id"], "plan_generated")
            print(f"[+] Phase 1 package written to {workdir}")
            return 0
        if args.demo:
            run_demo()
            return 0
        if args.resume:
            run_pipeline(episode_id=args.resume, resume=True, upload=args.upload)
            return 0
        run_pipeline(episode_id=args.episode_id, idea=args.idea, upload=args.upload)
        return 0
    except Exception as e:
        print(f"[!] Pipeline failed: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

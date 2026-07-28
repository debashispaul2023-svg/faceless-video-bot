import asyncio
import edge_tts
from pathlib import Path
import config

async def _synthesize_speech(text: str, output_path: Path):
    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
    await communicate.save(str(output_path))

def generate_voiceover(text: str, output_filename: str = "voiceover.mp3") -> Path:
    """Converts text to natural audio using Microsoft Edge Neural TTS."""
    output_path = config.OUTPUT_DIR / output_filename
    print(f"[+] Synthesizing Neural Voiceover ({config.TTS_VOICE})...")
    
    asyncio.run(_synthesize_speech(text, output_path))
    
    print(f"[+] Voiceover generated at: {output_path}")
    return output_path

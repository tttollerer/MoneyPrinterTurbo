"""Create a self-contained local demo using synthetic media and macOS speech."""
import argparse
import io
import json
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from spotforge.models import Audio, Brand, Cue, Format, Project, Scene
from spotforge.store import Store


def run(*args):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True, check=True)


def duration(path):
    return float(json.loads(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path).stdout)["format"]["duration"])


def create_demo(root):
    store = Store(root)
    with tempfile.TemporaryDirectory(prefix="spotforge-demo-") as tmp:
        tmp = Path(tmp)
        phrases = ["Deine Bilder bestimmen die Richtung.", "Deine Marke gibt dem Video seinen Stil."]
        waves, cues, start = [], [], 0
        for i, text in enumerate(phrases):
            aiff, wav = tmp / f"voice{i}.aiff", tmp / f"voice{i}.wav"
            run("say", "-v", "Anna", "-r", "145", "-o", aiff, text)
            run("ffmpeg", "-y", "-i", aiff, "-ar", "48000", "-ac", "2", wav)
            d = duration(wav)
            cues.append(Cue(text=text, start_ms=round(start * 1000), end_ms=round((start + d) * 1000)))
            start += d
            waves.append(wav)
        audio = tmp / "narration.wav"
        run("ffmpeg", "-y", "-i", waves[0], "-i", waves[1], "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", audio)
        narration = store.add_asset(audio.read_bytes(), "Demo-Stimme.wav", "audio", "audio/wav")
        length = math.ceil(duration(audio) + 0.8)
        bg = Image.new("RGB", (1080, 1920))
        draw = ImageDraw.Draw(bg)
        for y in range(1920):
            t = y / 1920
            draw.line((0, y, 1080, y), fill=(int(17+12*t), int(23+10*t), int(32+15*t)))
        for radius in [180, 320, 460, 600]:
            draw.ellipse((540-radius, 820-radius, 540+radius, 820+radius), outline=(44, 62, 77), width=3)
        draw.rounded_rectangle((230, 620, 850, 1030), radius=48, fill=(40, 53, 68), outline=(90, 125, 142), width=3)
        draw.polygon([(470,730),(470,930),(650,830)], fill=(234,118,91))
        png = tmp / "background.png"
        bg.save(png)
        clip = tmp / "scene.mp4"
        run("ffmpeg", "-y", "-loop", "1", "-i", png, "-t", str(length), "-vf", "scale=540:960", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", clip)
        media = store.add_asset(clip.read_bytes(), "Demo-Szene.mp4", "video", "video/mp4")
        logo = Image.new("RGBA", (240, 100), (0,0,0,0))
        ld = ImageDraw.Draw(logo)
        ld.rounded_rectangle((5,5,235,95), radius=20, fill=(234,118,91))
        ld.polygon([(92,25),(92,75),(150,50)], fill=(255,255,255))
        stream = io.BytesIO()
        logo.save(stream, format="PNG")
        logo_asset = store.add_asset(stream.getvalue(), "Demo-Logo.png", "image", "image/png")
        font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
        font = store.add_asset(font_path.read_bytes(), "Arial.ttf", "font", "font/ttf")
        brand = Brand(name="SpotForge Demo", font_family="Arial", font_asset_id=font.id,
                      logo_asset_id=logo_asset.id, required_text="LOKAL ERSTELLT · SPOTFORGE", visual_style="Klare Formen, dunkler Hintergrund und warme Akzentfarbe.")
        store.write("brands", brand.id, brand)
        store.write("brand_versions", f"{brand.id}_1", brand)
        p = Project(title="SpotForge · erster lokaler Film", brand_snapshot=brand,
                    scenes=[Scene(title="Dein Video. Dein Stil.", mode="local", source_asset_id=media.id,
                                  duration_s=length, onscreen_text="Dein Video.\nDein Stil.")],
                    audio=Audio(narration_asset_id=narration.id), captions=cues)
        store.write("projects", p.id, p)
        landscape = p.model_copy(deep=True)
        from spotforge.models import new_id
        landscape.id, landscape.title = new_id(), "SpotForge · Querformat"
        landscape.format = Format(width=1920, height=1080, fps=30)
        store.write("projects", landscape.id, landscape)
        return {"project_id": p.id, "landscape_id": landscape.id, "duration_s": length}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(create_demo(args.data_dir)))

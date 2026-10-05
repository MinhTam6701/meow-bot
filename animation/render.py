"""Render a Mochi animation scene to frames, then MP4 (for Telegram) and GIF (for previews).

    python animation/render.py neutral 4.4
"""
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FPS = 25


def render(scene: str, seconds: float, out_dir: Path) -> None:
    frames = out_dir / f"frames_{scene}"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 480, "height": 480}, device_scale_factor=1)
        page.goto((HERE / "mochi.html").as_uri())
        svg = page.locator("#stage")
        for i in range(int(seconds * FPS)):
            page.evaluate(f"draw('{scene}', {i / FPS})")
            svg.screenshot(path=str(frames / f"f{i:04d}.png"))
        browser.close()
    mp4 = out_dir / f"{scene}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", "-an", str(mp4)],
                   check=True)
    gif = out_dir / f"{scene}.gif"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
                    "-vf", "fps=20,scale=320:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse",
                    str(gif)], check=True)
    shutil.rmtree(frames)
    print(scene, mp4.stat().st_size // 1024, "KB mp4,", gif.stat().st_size // 1024, "KB gif")


if __name__ == "__main__":
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / "out"
    out.mkdir(exist_ok=True)
    render(sys.argv[1], float(sys.argv[2]), out)

"""Turn the window's screenshots (video/public/tui/<clip>/NNNN.svg, from capture_tui.py) into PNGs.

Each SVG is opened as a page by headless Chrome, so its fonts load (an SVG shown through <img> cannot load
them), and saved on a transparent background at its own size.

    python video/tools/svg_to_png.py [--chrome PATH]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TUI = Path(__file__).resolve().parents[1] / "public" / "tui"
CHROMES = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium"]


def convert(chrome: str, svg: Path) -> str | None:
    png = svg.with_suffix(".png")
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text(encoding="utf-8")[:400])
    w, h = (round(float(box.group(1))), round(float(box.group(2)))) if box else (1238, 1466)
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--default-background-color=00000000", "--virtual-time-budget=2500",
                    f"--window-size={w},{h}", f"--screenshot={png}", svg.as_uri()],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    return None if png.exists() else str(svg)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--chrome", default="")
    args = p.parse_args()
    chrome = args.chrome or next((c for c in CHROMES if Path(c).exists()), "")
    if not chrome:
        sys.exit("No Chrome or Edge found: pass --chrome PATH.")
    svgs = sorted(TUI.glob("*/*.svg"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        failed = [f for f in pool.map(lambda s: convert(chrome, s), svgs) if f]
    print(f"{len(svgs) - len(failed)} of {len(svgs)} converted" + (f"; failed: {failed[:5]}" if failed else ""))


if __name__ == "__main__":
    main()

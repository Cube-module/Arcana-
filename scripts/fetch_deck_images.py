"""Fetch existing public-domain scans; never synthesize or modify illustrations."""
import hashlib
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
MAJOR = ["Fool", "Magician", "High Priestess", "Empress", "Emperor", "Hierophant", "Lovers", "Chariot", "Strength", "Hermit", "Wheel of Fortune", "Justice", "Hanged Man", "Death", "Temperance", "Devil", "Tower", "Star", "Moon", "Sun", "Judgement", "World"]


def get(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "ArcanaEducationalMVP/1.0 (public domain deck archival download)"}), timeout=30) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def main():
    deck = json.loads((ROOT / "assets/deck.json").read_text(encoding="utf-8"))
    targets = []
    for item in deck:
        code, n = item["code"].split("_")
        suit = "Pents" if code == "pentacles" else code.title()
        name = f"RWS Tarot {n} {MAJOR[int(n)]}.jpg" if code == "major" else f"{suit}{n}.jpg"
        targets.append((item["image_path"], name))
    targets.append(("assets/images/back.jpg", "Waite–Smith Tarot Roses and Lilies cropped.jpg"))
    manifest_path = ROOT / "assets/image_sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for path, name in targets:
        if (ROOT / path).exists() and path in manifest:
            continue
        page = "https://commons.wikimedia.org/wiki/File:" + quote(name.replace(" ", "_"))
        try:
            markup = get(page).decode("utf-8")
            if "public domain" not in markup.lower():
                raise ValueError("Public domain notice missing; manual review required")
            section = re.search(r'<div class="fullImageLink".*?</div>', markup, re.S).group(0)
            original = html.unescape(re.search(r'<a href="([^"]+)"', section).group(1))
            # Download Wikimedia's existing preview when the scan is large.
            preview = re.search(r'<img[^>]+src="([^"]+)"', section)
            url = html.unescape(preview.group(1)) if preview else original
            if url.startswith("//"):
                url = "https:" + url
            content = get(url)
            if len(content) > 1024 * 1024:
                raise ValueError("Image exceeds 1 MiB")
            if not content.startswith(b'\xff\xd8'):
                raise ValueError("Expected JPEG")
            (ROOT / path).write_bytes(content)
            manifest[path] = {"file": name, "page": page, "download": url, "rights": "Public domain notice on Wikimedia Commons file page", "sha256": hashlib.sha256(content).hexdigest()}
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("OK", path, flush=True)
        except Exception as exc:
            print("MISSING", path, type(exc).__name__, flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()

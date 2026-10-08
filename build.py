"""Downloads the AI model files that are too large for the Git repository.

Vercel runs this during the build. You can also run it by hand: python build.py
"""
import sys
import urllib.request
from pathlib import Path

MODELS = Path(__file__).parent / "models"
FILES = {
    "clip_vision.onnx": "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model.onnx",
    "detector.onnx": "https://github.com/pandeylakshya207-max/clean-city-app/releases/download/models-v1/detector.onnx",
}


def download(url, target):
    request = urllib.request.Request(url, headers={"User-Agent": "clean-city-build"})
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    partial.replace(target)


def main():
    MODELS.mkdir(exist_ok=True)
    for name, url in FILES.items():
        target = MODELS / name
        if target.exists() and target.stat().st_size > 1000000:
            print(f"{name}: already present ({target.stat().st_size / 1e6:.0f} MB)", flush=True)
            continue
        print(f"{name}: downloading...", flush=True)
        try:
            download(url, target)
        except Exception as exc:
            print(f"{name}: download failed: {exc}", flush=True)
            sys.exit(1)
        print(f"{name}: done ({target.stat().st_size / 1e6:.0f} MB)", flush=True)


if __name__ == "__main__":
    main()

"""One-time step on a laptop: turn the garbage type descriptions into CLIP text vectors.

    python export_types.py

Saves models/clip_types.json, which the server uses to tell plastic from debris, e-waste and so on.
Run it again whenever the descriptions in garbage_types.py change. Needs the packages in
requirements-dev.txt (PyTorch and transformers).
"""
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image

import checks_torch
from garbage_types import TYPES

MODELS = Path(__file__).parent / "models"


def main():
    model, processor = checks_torch._load()
    blank = Image.new("RGB", (224, 224), "gray")
    result = []
    for key, name, _, prompts in TYPES:
        inputs = processor(text=prompts, images=blank, return_tensors="pt", padding=True)
        with torch.no_grad():
            out = model(**inputs)
        vecs = out.text_embeds.float().numpy()
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        mean = vecs.mean(axis=0)
        mean = mean / np.linalg.norm(mean)
        result.append({"key": key, "embedding": [round(float(x), 6) for x in mean]})
        print(f"  {name}: {len(prompts)} descriptions")
    data = {"logit_scale": float(model.logit_scale.exp()), "types": result}
    MODELS.mkdir(exist_ok=True)
    (MODELS / "clip_types.json").write_text(json.dumps(data))
    print(f"Saved {len(result)} garbage types to models/clip_types.json")


if __name__ == "__main__":
    main()

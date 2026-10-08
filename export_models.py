"""One-time conversion of the AI models to the light ONNX format, with a comparison against the originals."""
import json
import shutil
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from huggingface_hub import hf_hub_download
from PIL import Image

import checks_torch as checks
from database import get_db

BASE = Path(__file__).parent
MODELS = BASE / "models"
MODELS.mkdir(exist_ok=True)

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype="float32")
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype="float32")


def clip_pixels(img):
    w, h = img.size
    if w <= h:
        new_w, new_h = 224, max(224, int(224 * h / w))
    else:
        new_w, new_h = max(224, int(224 * w / h)), 224
    img = img.resize((new_w, new_h), Image.Resampling.BICUBIC)
    left = (new_w - 224) // 2
    top = (new_h - 224) // 2
    img = img.crop((left, top, left + 224, top + 224))
    arr = np.asarray(img, dtype="float32") / 255.0
    arr = (arr - CLIP_MEAN) / CLIP_STD
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def detector_pixels(img, meta):
    size = meta["size"]
    img = img.resize((size["width"], size["height"]), Image.Resampling(meta["resample"]))
    arr = np.asarray(img, dtype="float32") / 255.0
    arr = (arr - np.array(meta["mean"], dtype="float32")) / np.array(meta["std"], dtype="float32")
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def export_clip_text():
    model, processor = checks._load()
    labels = checks.GARBAGE_LABELS + checks.OTHER_LABELS
    blank = Image.new("RGB", (224, 224), "gray")
    inputs = processor(text=labels, images=blank, return_tensors="pt", padding=True)
    with torch.no_grad():
        out = model(**inputs)
    text = out.text_embeds.float().numpy()
    text = text / np.linalg.norm(text, axis=1, keepdims=True)
    scale = float(model.logit_scale.exp())
    data = {
        "labels": labels,
        "garbage_count": len(checks.GARBAGE_LABELS),
        "logit_scale": scale,
        "embeddings": [[round(float(x), 6) for x in row] for row in text],
    }
    (MODELS / "clip_text.json").write_text(json.dumps(data))
    print(f"Saved text embeddings for {len(labels)} descriptions (logit scale {scale:.2f}).")


def download_clip_vision():
    files = (
        ("onnx/vision_model.onnx", "clip_vision.onnx"),
        ("onnx/vision_model_quantized.onnx", "clip_vision_small.onnx"),
    )
    for remote, local in files:
        target = MODELS / local
        if not target.exists():
            path = hf_hub_download("Xenova/clip-vit-base-patch32", remote)
            shutil.copyfile(path, target)
        print(f"{local}: {target.stat().st_size / 1e6:.0f} MB")


class LogitsOnly(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, pixel_values):
        return self.model(pixel_values=pixel_values).logits


def export_detector():
    model, processor, index = checks._load_ai()
    cfg = processor.to_dict()
    size = cfg.get("size") or {}
    try:
        resample = int(cfg.get("resample", 3))
    except Exception:
        resample = 3
    meta = {
        "ai_index": int(index),
        "size": {
            "height": int(size.get("height") or size.get("shortest_edge") or 224),
            "width": int(size.get("width") or size.get("shortest_edge") or 224),
        },
        "resample": resample,
        "mean": [float(x) for x in (cfg.get("image_mean") or [0.5, 0.5, 0.5])],
        "std": [float(x) for x in (cfg.get("image_std") or [0.5, 0.5, 0.5])],
    }
    (MODELS / "detector.json").write_text(json.dumps(meta))
    print("Detector settings:", meta)

    target = MODELS / "detector.onnx"
    if not target.exists():
        dummy = torch.zeros(1, 3, meta["size"]["height"], meta["size"]["width"])
        wrapper = LogitsOnly(model).eval()
        try:
            torch.onnx.export(
                wrapper, (dummy,), str(target),
                input_names=["pixel_values"], output_names=["logits"],
                dynamo=True, external_data=False,
            )
        except Exception as exc:
            print("New exporter failed, trying the older one:", type(exc).__name__, str(exc)[:300])
            if target.exists():
                target.unlink()
            torch.onnx.export(
                wrapper, (dummy,), str(target),
                input_names=["pixel_values"], output_names=["logits"],
                opset_version=17, dynamo=False,
            )
    print(f"detector.onnx: {target.stat().st_size / 1e6:.0f} MB")

    small = MODELS / "detector_small.onnx"
    if not small.exists():
        try:
            from onnxruntime.quantization import QuantType, quantize_dynamic

            quantize_dynamic(str(target), str(small), weight_type=QuantType.QUInt8)
        except Exception as exc:
            print("Could not make the small detector:", type(exc).__name__, str(exc)[:300])
    if small.exists():
        print(f"detector_small.onnx: {small.stat().st_size / 1e6:.0f} MB")


def session(name):
    path = MODELS / name
    if not path.exists():
        return None
    try:
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:
        print(f"Could not open {name}:", type(exc).__name__, str(exc)[:300])
        return None


def clip_embed(sess, img):
    names = [o.name for o in sess.get_outputs()]
    outputs = sess.run(None, {sess.get_inputs()[0].name: clip_pixels(img)})
    vec = outputs[names.index("image_embeds")] if "image_embeds" in names else outputs[0]
    vec = np.asarray(vec, dtype="float32").reshape(-1)
    return vec / np.linalg.norm(vec)


def garbage_from(vec, text):
    logits = text["scale"] * (text["emb"] @ vec)
    logits = logits - logits.max()
    probs = np.exp(logits)
    probs = probs / probs.sum()
    return float(probs[: text["n"]].sum())


def detector_score(sess, img, meta):
    logits = sess.run(None, {sess.get_inputs()[0].name: detector_pixels(img, meta)})[0][0]
    logits = logits - logits.max()
    probs = np.exp(logits)
    probs = probs / probs.sum()
    return float(probs[meta["ai_index"]])


def fmt(value):
    return "n/a " if value is None else f"{value:.2f}"


def compare():
    raw = json.loads((MODELS / "clip_text.json").read_text())
    text = {
        "emb": np.array(raw["embeddings"], dtype="float32"),
        "scale": raw["logit_scale"],
        "n": raw["garbage_count"],
    }
    meta = json.loads((MODELS / "detector.json").read_text())
    clip_big = session("clip_vision.onnx")
    clip_small = session("clip_vision_small.onnx")
    det_big = session("detector.onnx")
    det_small = session("detector_small.onnx")
    if clip_big:
        print("CLIP vision outputs:", [o.name for o in clip_big.get_outputs()])

    conn = get_db()
    rows = conn.execute("SELECT id, photo_path FROM complaints ORDER BY id").fetchall()
    conn.close()

    vectors = []
    print("Scores for each report (original / ONNX / small ONNX):")
    for row in rows:
        path = BASE / "uploads" / row["photo_path"]
        if not path.exists():
            continue
        img = Image.open(path).convert("RGB")
        t_score, _, t_vec = checks.analyze(img)
        t_ai = checks.ai_generated_score(img)
        b_vec = clip_embed(clip_big, img) if clip_big else None
        s_vec = clip_embed(clip_small, img) if clip_small else None
        g_big = garbage_from(b_vec, text) if b_vec is not None else None
        g_small = garbage_from(s_vec, text) if s_vec is not None else None
        a_big = detector_score(det_big, img, meta) if det_big else None
        a_small = detector_score(det_small, img, meta) if det_small else None
        vectors.append((row["id"], t_vec, b_vec, s_vec))
        print(
            f"  Report #{row['id']}: garbage {fmt(t_score)} / {fmt(g_big)} / {fmt(g_small)}"
            f" | AI-generated {fmt(t_ai)} / {fmt(a_big)} / {fmt(a_small)}"
        )

    print("Similarity between reports (original / ONNX / small ONNX):")
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            a, b = vectors[i], vectors[j]
            sims = []
            for k in (1, 2, 3):
                sims.append(float(np.dot(a[k], b[k])) if a[k] is not None and b[k] is not None else None)
            print(f"  #{a[0]} vs #{b[0]}: {fmt(sims[0])} / {fmt(sims[1])} / {fmt(sims[2])}")


if __name__ == "__main__":
    export_clip_text()
    download_clip_vision()
    export_detector()
    compare()


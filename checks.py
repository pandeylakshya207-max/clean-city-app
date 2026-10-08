"""Photo checks. The AI models run on ONNX Runtime, which is light enough for small servers."""
import json
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageStat

MODELS = Path(__file__).parent / "models"
MODEL_FILES = ("clip_vision.onnx", "detector.onnx", "clip_text.json", "detector.json")

GARBAGE_THRESHOLD = 0.55
AI_THRESHOLD = 2.0  # never reached: the detector only flags photos for staff, it does not reject
DUPLICATE_DISTANCE = 5
SAME_SPOT_SIMILARITY = 0.90

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype="float32")
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype="float32")

_clip = None
_text = None
_detector = None
_detector_meta = None
_lock = threading.Lock()


def models_status():
    """Size of each model file in MB, or None if it is missing."""
    status = {}
    for name in MODEL_FILES:
        path = MODELS / name
        status[name] = round(path.stat().st_size / 1e6, 1) if path.exists() else None
    return status


def _session(name):
    import onnxruntime as ort

    path = MODELS / name
    if not path.exists():
        raise RuntimeError(f"Model file {name} is missing from the models folder.")
    return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _load():
    global _clip, _text
    with _lock:
        if _clip is None:
            raw = json.loads((MODELS / "clip_text.json").read_text())
            _text = {
                "labels": raw["labels"],
                "emb": np.array(raw["embeddings"], dtype="float32"),
                "scale": float(raw["logit_scale"]),
                "n": int(raw["garbage_count"]),
            }
            _clip = _session("clip_vision.onnx")
    return _clip, _text


def _load_ai():
    global _detector, _detector_meta
    with _lock:
        if _detector is None:
            _detector_meta = json.loads((MODELS / "detector.json").read_text())
            _detector = _session("detector.onnx")
    return _detector, _detector_meta


def _clip_pixels(img):
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


def _detector_pixels(img, meta):
    size = meta["size"]
    img = img.resize((size["width"], size["height"]), Image.Resampling(meta["resample"]))
    arr = np.asarray(img, dtype="float32") / 255.0
    arr = (arr - np.array(meta["mean"], dtype="float32")) / np.array(meta["std"], dtype="float32")
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])


def _softmax(logits):
    logits = logits - logits.max()
    probs = np.exp(logits)
    return probs / probs.sum()


def analyze(img):
    """One pass of the CLIP model: garbage score, closest description, and a vector describing the scene."""
    sess, text = _load()
    names = [o.name for o in sess.get_outputs()]
    outputs = sess.run(None, {sess.get_inputs()[0].name: _clip_pixels(img)})
    vec = outputs[names.index("image_embeds")] if "image_embeds" in names else outputs[0]
    vec = np.asarray(vec, dtype="float32").reshape(-1)
    vec = vec / (np.linalg.norm(vec) + 1e-12)
    probs = _softmax(text["scale"] * (text["emb"] @ vec))
    score = float(probs[: text["n"]].sum())
    best = text["labels"][int(probs.argmax())]
    return score, best, vec


def ai_generated_score(img):
    """How sure the model is that the image was made by an AI generator (0 to 1)."""
    sess, meta = _load_ai()
    logits = sess.run(None, {sess.get_inputs()[0].name: _detector_pixels(img, meta)})[0][0]
    return float(_softmax(np.asarray(logits, dtype="float32"))[meta["ai_index"]])


def similarity(a, b):
    return float(np.dot(a, b))


def to_bytes(vec):
    return np.asarray(vec, dtype="float32").tobytes()


def from_bytes(data):
    return np.frombuffer(bytes(data), dtype="float32")


def quality_problem(img):
    stat = ImageStat.Stat(img.convert("L"))
    if stat.mean[0] < 30:
        return "The photo is too dark. Please take it again with more light."
    if stat.stddev[0] < 10:
        return "The photo looks blank. Please point the camera at the garbage and try again."
    return None


def photo_hash(img):
    """A 64-bit fingerprint that stays almost the same for near-identical photos."""
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hash_distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def rejected(reason, score=None, digest=None):
    return {"ok": False, "reason": reason, "score": score, "hash": digest, "embedding": None}


def check_photo(img, existing_hashes, nearby=()):
    """Run every check. nearby is a list of (report id, scene vector) for open reports close by."""
    problem = quality_problem(img)
    if problem:
        return rejected(problem)

    digest = photo_hash(img)
    for other in existing_hashes:
        if other and hash_distance(digest, other) <= DUPLICATE_DISTANCE:
            return rejected("This photo looks the same as one that was already reported.", None, digest)

    ai_score = ai_generated_score(img)
    print(f"Photo check: AI-generated score {ai_score:.2f}", flush=True)
    if ai_score >= AI_THRESHOLD:
        return rejected(
            "This looks like a computer-generated image, not a real photo. Please take a live photo of the garbage.",
            None,
            digest,
        )

    score, best, vec = analyze(img)
    print(f"Photo check: garbage score {score:.2f} | looks most like: {best}", flush=True)
    if score < GARBAGE_THRESHOLD:
        return rejected(
            "We could not see garbage in this photo. Please take a clear photo of the garbage itself.",
            score,
            digest,
        )

    for report_id, other_vec in nearby:
        sim = similarity(vec, other_vec)
        print(f"Photo check: similarity to report #{report_id} is {sim:.2f}", flush=True)
        if sim >= SAME_SPOT_SIMILARITY:
            return rejected(
                f"This garbage has already been reported (report #{report_id}). The municipal team has it on their list.",
                score,
                digest,
            )

    return {
        "ok": True,
        "reason": None,
        "score": score,
        "hash": digest,
        "embedding": to_bytes(vec),
        "fake_score": ai_score,
    }


if __name__ == "__main__":
    import sys

    from database import get_db

    print("Model files (MB):", models_status())
    print("PyTorch loaded:", "torch" in sys.modules)
    conn = get_db()
    rows = conn.execute("SELECT id, photo_path FROM complaints ORDER BY id").fetchall()
    conn.close()
    for row in rows:
        path = Path(__file__).parent / "uploads" / row["photo_path"]
        if not path.exists():
            continue
        photo = Image.open(path).convert("RGB")
        score, best, _ = analyze(photo)
        ai_score = ai_generated_score(photo)
        print(f"Report #{row['id']}: garbage {score:.2f} | AI-generated {ai_score:.2f} | {best}")


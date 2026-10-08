import threading

import numpy as np
from PIL import Image, ImageStat

MODEL_NAME = "openai/clip-vit-base-patch32"
AI_MODEL_NAME = "Ateeqq/ai-vs-human-image-detector"

GARBAGE_LABELS = [
    "a photo of garbage dumped on a street",
    "a photo of a pile of trash and litter on the roadside",
    "a photo of an overflowing garbage bin",
    "a photo of plastic waste and rubbish lying on the ground",
]
OTHER_LABELS = [
    "a selfie or a photo of a person",
    "a photo of a clean street or road",
    "a photo of the inside of a room",
    "a screenshot of a phone or computer screen",
    "a photo of a screen showing a picture",
    "a photo of food on a plate",
    "a photo of a document or printed text",
    "a photo of an animal",
    "a photo of a vehicle",
    "a photo of buildings, sky or scenery",
    "a cartoon, drawing or computer generated image",
]
GARBAGE_THRESHOLD = 0.55
AI_THRESHOLD = 0.99
DUPLICATE_DISTANCE = 5
SAME_SPOT_SIMILARITY = 0.90

_model = None
_processor = None
_ai_model = None
_ai_processor = None
_ai_index = None
_lock = threading.Lock()


def _load():
    global _model, _processor
    with _lock:
        if _model is None:
            from transformers import CLIPModel, CLIPProcessor

            _processor = CLIPProcessor.from_pretrained(MODEL_NAME)
            _model = CLIPModel.from_pretrained(MODEL_NAME)
            _model.eval()
    return _model, _processor


def _load_ai():
    global _ai_model, _ai_processor, _ai_index
    with _lock:
        if _ai_model is None:
            from transformers import AutoImageProcessor, AutoModelForImageClassification

            processor = AutoImageProcessor.from_pretrained(AI_MODEL_NAME)
            model = AutoModelForImageClassification.from_pretrained(AI_MODEL_NAME)
            model.eval()
            labels = {int(i): str(name).lower() for i, name in model.config.id2label.items()}
            ai_words = ("ai", "fake", "artificial", "generated", "synthetic")
            ai_ids = [i for i, name in labels.items() if name.startswith(ai_words)]
            if len(ai_ids) != 1:
                raise RuntimeError(f"Could not find the AI label in {labels}")
            _ai_index = ai_ids[0]
            _ai_processor = processor
            _ai_model = model
    return _ai_model, _ai_processor, _ai_index


def analyze(img):
    """One pass of the CLIP model: garbage score, closest description, and a vector describing the scene."""
    import torch

    model, processor = _load()
    labels = GARBAGE_LABELS + OTHER_LABELS
    inputs = processor(text=labels, images=img, return_tensors="pt", padding=True)
    with torch.no_grad():
        out = model(**inputs)
    probs = out.logits_per_image.softmax(dim=1)[0]
    score = float(probs[: len(GARBAGE_LABELS)].sum())
    best = labels[int(probs.argmax())]
    vec = out.image_embeds[0].float().numpy().astype("float32")
    vec = vec / (np.linalg.norm(vec) + 1e-12)
    return score, best, vec


def similarity(a, b):
    return float(np.dot(a, b))


def to_bytes(vec):
    return np.asarray(vec, dtype="float32").tobytes()


def from_bytes(data):
    return np.frombuffer(data, dtype="float32")


def ai_generated_score(img):
    """How sure the model is that the image was made by an AI generator (0 to 1)."""
    import torch

    model, processor, index = _load_ai()
    inputs = processor(images=img, return_tensors="pt")
    with torch.no_grad():
        probs = model(**inputs).logits.softmax(dim=1)[0]
    return float(probs[index])


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

    return {"ok": True, "reason": None, "score": score, "hash": digest, "embedding": to_bytes(vec), "fake_score": ai_score}


if __name__ == "__main__":
    from pathlib import Path

    from database import get_db

    print("Loading the AI models (a first run downloads about 370 MB)...")
    _load()
    _load_ai()
    print("Models ready.")
    conn = get_db()
    rows = conn.execute("SELECT id, photo_path FROM complaints ORDER BY id").fetchall()
    conn.close()
    uploads = Path(__file__).parent / "uploads"
    vectors = []
    for row in rows:
        path = uploads / row["photo_path"]
        if not path.exists():
            continue
        photo = Image.open(path).convert("RGB")
        score, best, vec = analyze(photo)
        ai_score = ai_generated_score(photo)
        vectors.append((row["id"], vec))
        print(f"Report #{row['id']}: garbage {score:.2f} | AI-generated {ai_score:.2f} | {best}")
    print("How similar the photos are to each other (1.00 = identical):")
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            a, b = vectors[i], vectors[j]
            print(f"  Report #{a[0]} vs #{b[0]}: {similarity(a[1], b[1]):.2f}")


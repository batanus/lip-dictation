"""Adapting the model to one person: phrases they mouth, and a small LoRA adapter trained on them.

Enrollment keeps only the mouth crops (96x96 px around the lips) of each phrase, never the full camera
picture. Training adds low-rank adapters (LoRA) to the attention and feed-forward layers of the encoder,
about 2M parameters next to the model's 250M, so a hundred clips adapt how the model reads this
person's lips without it forgetting the rest. The decoder, which acts as the model's language model,
is left alone: adapting it to a few dozen phrases taught it their vocabulary, and it then turned any
text into those words ("builds", "run"). One clip in five is held out to measure the word error rate
before and after; the adapter is kept only when that rate drops.
"""

import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from . import paths

MIN_CLIPS = 20
RANK = 8
ALPHA = 16
TARGETS = ("linear_q", "linear_k", "linear_v", "linear_out", "w_1", "w_2")


class TrainError(RuntimeError):
    pass


def folder() -> Path:
    return paths.home() / "personal"


def manifest_path() -> Path:
    return folder() / "clips.jsonl"


def adapter_path() -> Path:
    return folder() / "adapter.pt"


def disabled_path() -> Path:
    """Present while the person has switched their personal model off (Settings, `lipread.sh --personal off`)."""
    return folder() / "disabled"


def normalize(text: str) -> str:
    """The model's alphabet: uppercase words and apostrophes."""
    return " ".join(re.sub(r"[^A-Za-z' ]", " ", text).upper().split())


def phrases() -> list[str]:
    lines = Path(__file__).with_name("phrases.txt").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def clips() -> list[dict]:
    try:
        lines = manifest_path().read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def next_phrase() -> str | None:
    done = {clip["text"] for clip in clips()}
    return next((phrase for phrase in phrases() if normalize(phrase) not in done), None)


def add_clip(crops: np.ndarray, phrase: str) -> None:
    (folder() / "clips").mkdir(parents=True, exist_ok=True)
    name = f"clips/{time.time_ns()}.npz"
    np.savez_compressed(folder() / name, crops=crops)
    with manifest_path().open("a") as manifest:
        manifest.write(json.dumps({"file": name, "text": normalize(phrase)}) + "\n")


def forget() -> None:
    """Delete the recorded clips and the adapter."""
    shutil.rmtree(folder(), ignore_errors=True)


def word_errors(reference: str, hypothesis: str) -> int:
    """Word-level edit distance (substitutions + insertions + deletions)."""
    ref, hyp = reference.split(), hypothesis.split()
    row = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (word != other))
    return row[-1]


def _lora_layers(model) -> dict:
    """The linear layers that get an adapter, by name."""
    import torch

    return {
        name: module
        for name, module in model.named_modules()
        if isinstance(module, torch.nn.Linear)
        and name.split(".")[0] == "encoder"
        and name.split(".")[-1] in TARGETS
    }


def merge_adapter(model) -> dict | None:
    """Add a saved adapter into the model's weights; returns its training summary, or None."""
    import torch

    path = adapter_path()
    if not path.is_file() or disabled_path().exists():
        return None
    try:
        saved = torch.load(str(path), map_location="cpu", weights_only=True)
        layers = _lora_layers(model)
        with torch.no_grad():
            for name, (a, b) in saved["layers"].items():
                if name in layers:  # adapters saved before 0.1.7 also had decoder layers: skip those
                    layers[name].weight += saved["scale"] * (b @ a)
        return saved["info"]
    except Exception as error:  # a broken adapter must never break dictation
        print(f"lipread: ignoring the personal adapter: {error}", file=sys.stderr)
        return None


def train(emit, epochs: int = 8, learning_rate: float = 3e-4) -> dict:
    """Train an adapter on the recorded clips; emits progress events and returns the summary."""
    samples = [(np.load(folder() / clip["file"])["crops"], clip["text"]) for clip in clips()]
    if len(samples) < MIN_CLIPS:
        raise TrainError(f"Record at least {MIN_CLIPS} phrases first ({len(samples)} so far).")

    import torch

    from .model import Recognizer

    emit("progress", step="Loading the model", done=0, total=1)
    recognizer = Recognizer(device="cpu", personal=False)  # the adapters train on the CPU
    model = recognizer.model
    held_out = samples[4::5]
    training = [sample for i, sample in enumerate(samples) if i % 5 != 4]

    def error_rate(step: str) -> float:
        model.eval()
        errors = words = 0
        for i, (crops, text) in enumerate(held_out):
            emit("progress", step=step, done=i, total=len(held_out))
            best = (recognizer.hypotheses(crops) or [""])[0]
            errors += word_errors(text, normalize(best))
            words += len(text.split())
        return errors / max(words, 1)

    before = error_rate("Measuring accuracy before")

    # Low-rank adapters next to the frozen layers: y = W x + scale * B A x, B starting at zero.
    for parameter in model.parameters():
        parameter.requires_grad = False
    adapters = {}
    for name, layer in _lora_layers(model).items():
        a = torch.nn.Parameter(torch.randn(RANK, layer.in_features) / layer.in_features**0.5)
        b = torch.nn.Parameter(torch.zeros(layer.out_features, RANK))
        adapters[name] = (a, b)
        layer.register_forward_hook(lambda _, inputs, output, a=a, b=b: output + (ALPHA / RANK) * (inputs[0] @ a.T @ b.T))

    from datamodule.transforms import VideoTransform

    augment = VideoTransform(subset="train")  # random crop and time masking, as in auto_avsr's training
    plain = recognizer.video_transform
    optimizer = torch.optim.AdamW([p for pair in adapters.values() for p in pair], lr=learning_rate, weight_decay=0.01)

    def loss_of(crops, text, transform):
        video = transform(torch.tensor(crops).permute((0, 3, 1, 2)))
        label = recognizer.text_transform.tokenize(text).unsqueeze(0)
        return model(video.unsqueeze(0), torch.tensor([len(video)]), label)[0]

    def train_mode():
        model.train()
        for module in model.modules():  # one clip per step: keep the batch norm statistics as trained
            if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
                module.eval()

    best_loss, best = float("inf"), None
    for epoch in range(epochs):
        emit("progress", step="Training", done=epoch, total=epochs)
        train_mode()
        random.shuffle(training)
        for crops, text in training:
            optimizer.zero_grad()
            loss_of(crops, text, augment).backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            held_out_loss = sum(float(loss_of(crops, text, plain)) for crops, text in held_out)
        if held_out_loss < best_loss:  # keep the epoch that generalizes best, not the last one
            best_loss = held_out_loss
            best = {name: (a.detach().clone(), b.detach().clone()) for name, (a, b) in adapters.items()}

    with torch.no_grad():
        for name, (a, b) in adapters.items():
            a.copy_(best[name][0])
            b.copy_(best[name][1])
    after = error_rate("Measuring accuracy after")

    info = {
        "clips": len(samples),
        "held_out": len(held_out),
        "before": round(before, 3),
        "after": round(after, 3),
        "trained_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    info["kept"] = after < before
    if info["kept"]:
        torch.save(
            {"scale": ALPHA / RANK, "layers": best, "info": info},
            str(adapter_path()),
        )
        # Readable without Python, for the settings row; and a fresh model starts switched on.
        (folder() / "adapter.json").write_text(json.dumps(info))
        disabled_path().unlink(missing_ok=True)
    return info

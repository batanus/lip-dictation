import os
from pathlib import Path

# Auto-AVSR model zoo, LRS3 visual-only, trained on LRS2+LRS3+VoxCeleb2+AVSpeech (20.3% WER on LRS3).
# scripts/setup.sh downloads it and checks out the matching auto_avsr code.
MODEL_NAME = "vsr_trlrs2lrs3vox2avsp_base.pth"


def home() -> Path:
    """Where the venv, vendored code and model live. A fixed folder (not the plugin's data folder),
    so a reinstall keeps the download and scripts/setup.sh run from a terminal sets up the same place."""
    value = os.environ.get("LIPREAD_HOME")
    return Path(value) if value else Path.home() / ".cache" / "lip-dictation"


def vendor_dir() -> Path:
    return home() / "vendor" / "auto_avsr"


def model_path() -> Path:
    override = os.environ.get("LIPREAD_MODEL")
    return Path(override) if override else home() / "models" / MODEL_NAME


def log_path() -> Path:
    """One JSON line per dictation (what was read, the other readings, what was sent): the History pane."""
    return home() / "dictations.jsonl"

"""Visual speech recognition with Auto-AVSR (https://github.com/mpc001/auto_avsr, Apache-2.0).

The model code is not copied into this repository: setup.sh checks out a pinned commit of
auto_avsr into the plugin's data folder and this module imports it from there. The inference
steps below follow auto_avsr's tutorials/inference.ipynb and lightning.py.
"""

import sys

import numpy as np

from . import paths


class NoFaceError(RuntimeError):
    pass


def use_vendor() -> None:
    """Make the checked-out auto_avsr code importable."""
    vendor = str(paths.vendor_dir())
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


class Cropper:
    """Finds the face in each frame and cuts out the mouth: (T, 96, 96, 3) uint8, what the model reads."""

    def __init__(self):
        use_vendor()
        from preparation.detectors.mediapipe.detector import LandmarksDetector
        from preparation.detectors.mediapipe.video_process import VideoProcess

        self.landmarks_detector = LandmarksDetector()
        self.video_process = VideoProcess(convert_gray=False)

    def __call__(self, frames: np.ndarray) -> np.ndarray:
        try:
            landmarks = self.landmarks_detector(frames)
        except AssertionError as error:  # auto_avsr asserts when no frame has a face
            raise NoFaceError("No face found in the recording.") from error
        try:
            crops = self.video_process(frames, landmarks)
        except OverflowError as error:  # auto_avsr's cut_patch: mouth too far from where it expects
            raise NoFaceError("Your face is not centered in the camera.") from error
        if crops is None:
            raise NoFaceError("No face found in the recording.")
        return crops


class Recognizer:
    def __init__(self, device: str = "auto", beam_size: int = 10, personal: bool = True):
        use_vendor()
        import torch
        from datamodule.transforms import TextTransform, VideoTransform
        from espnet.nets.pytorch_backend.e2e_asr_conformer import E2E

        from . import personal as personal_adapter

        self.torch = torch
        if device == "auto":  # the encoder is ~30x faster on Apple's GPU than on the CPU
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = torch.device(device)
        self.beam_size = beam_size

        self.text_transform = TextTransform()
        self.token_list = self.text_transform.token_list
        self.model = E2E(len(self.token_list), "video")
        # weights_only: a plain state dict needs no pickled code, so never run any from the download.
        state = torch.load(str(paths.model_path()), map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        # The user's own adapter (see personal.py), merged into the weights: no cost at inference.
        self.personal = personal_adapter.merge_adapter(self.model) if personal else None
        self.model.eval()
        # Only the encoder half may run on a GPU: espnet's CTC prefix scorer keeps its index tensors
        # on the CPU unless CUDA is used, so beam search (decoder + CTC) must stay on the CPU.
        for part in (self.model.frontend, self.model.proj_encoder, self.model.encoder):
            part.to(self.device)

        self.cropper = Cropper()
        self.video_transform = VideoTransform(subset="test")

    def warm_up(self) -> None:
        """A GPU compiles its kernels on the first pass (seconds): do one before it is needed."""
        if self.device.type == "cpu":
            return
        with self.torch.no_grad():
            x = self.model.frontend(self.torch.zeros(1, 25, 1, 88, 88, device=self.device))
            self.model.encoder(self.model.proj_encoder(x), None)

    def _beam_search(self):
        # Same decoder settings as auto_avsr's lightning.get_beam_search_decoder, smaller beam for speed.
        from espnet.nets.batch_beam_search import BatchBeamSearch
        from espnet.nets.scorers.length_bonus import LengthBonus

        ctc_weight = 0.1
        scorers = self.model.scorers()
        scorers["lm"] = None
        scorers["length_bonus"] = LengthBonus(len(self.token_list))
        weights = {"decoder": 1.0 - ctc_weight, "ctc": ctc_weight, "lm": 0.0, "length_bonus": 0.0}
        return BatchBeamSearch(
            beam_size=self.beam_size,
            vocab_size=len(self.token_list),
            weights=weights,
            scorers=scorers,
            sos=self.model.sos,
            eos=self.model.eos,
            token_list=self.token_list,
            pre_beam_score_key="decoder",
        ).eval()

    def hypotheses(self, crops: np.ndarray, count: int = 1) -> list[str]:
        """Mouth crops -> up to `count` distinct raw uppercase transcripts, best first."""
        torch = self.torch
        video = torch.tensor(crops).permute((0, 3, 1, 2))
        video = self.video_transform(video).to(self.device)
        with torch.no_grad():
            x = self.model.frontend(video.unsqueeze(0))
            x = self.model.proj_encoder(x)
            encoded, _ = self.model.encoder(x, None)
            hyps = self._beam_search()(encoded.squeeze(0).cpu())
        texts: list[str] = []
        for hyp in hyps:
            token_ids = torch.tensor([int(t) for t in hyp.asdict()["yseq"][1:]])
            text = self.text_transform.post_process(token_ids).replace("<eos>", "").strip()
            if text and text not in texts:
                texts.append(text)
            if len(texts) == count:
                break
        return texts

    def transcribe(self, frames: np.ndarray, count: int = 1) -> list[str]:
        """RGB frames (T, H, W, 3) at 25 fps -> up to `count` raw uppercase transcripts, best first."""
        return self.hypotheses(self.cropper(frames), count)


def tidy(raw: str) -> str:
    """The model emits uppercase words without punctuation; make it read like typed text."""
    words = raw.lower().split()
    if not words:
        return ""
    words = ["I" if w == "i" else "I" + w[1:] if w.startswith("i'") else w for w in words]
    text = " ".join(words)
    return text[0].upper() + text[1:]

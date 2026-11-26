import bisect
import os
import time

import cv2
import numpy as np

MODEL_FPS = 25  # Auto-AVSR is trained on 25 fps video (LRS3).
MAX_WIDTH = 640  # plenty for the face detector; keeps 20 s of frames around 0.5 GB


class CameraError(RuntimeError):
    pass


def _to_rgb(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    if width > MAX_WIDTH:  # some cameras (Continuity Camera, 1080p) ignore the requested size
        frame = cv2.resize(frame, (MAX_WIDTH, round(height * MAX_WIDTH / width)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


class MouthMeter:
    """How much the mouth is moving, 0..1, for the recording waveform. None when no face is visible.

    Uses MediaPipe Face Mesh on a small copy of the frame: the inner-lip gap (landmarks 13/14) over the
    mouth width (78/308), plus how fast that gap changes, since silent speech opens the mouth only a little."""

    def __init__(self):
        import mediapipe as mp

        self.mesh = mp.solutions.face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=False)
        self.previous = None

    def __call__(self, rgb: np.ndarray):
        small = cv2.resize(rgb, (320, round(rgb.shape[0] * 320 / rgb.shape[1])), interpolation=cv2.INTER_AREA)
        result = self.mesh.process(small)
        if not result.multi_face_landmarks:
            self.previous = None
            return None
        points = result.multi_face_landmarks[0].landmark
        gap = abs(points[14].y - points[13].y)
        width = abs(points[308].x - points[78].x) or 1e-6
        openness = gap / width
        motion = 0.0 if self.previous is None else abs(openness - self.previous)
        self.previous = openness
        return float(min(1.0, max((openness - 0.04) / 0.35, motion * 6)))

    def close(self):
        self.mesh.close()


def open_camera(camera_index: int = 0, wait_seconds: float = 15):
    """Open the camera, waiting while macOS shows its first-use permission prompt.

    OpenCV gives up at once while the prompt is open, so retry until the user answers."""
    deadline = time.monotonic() + wait_seconds
    while True:
        cap = cv2.VideoCapture(camera_index)
        if cap.isOpened():
            return cap
        cap.release()
        if time.monotonic() >= deadline:
            raise CameraError(
                "Cannot open the camera. Allow camera access for Lip Dictation "
                "(System Settings > Privacy & Security > Camera), then try again."
            )
        time.sleep(1)


def record(stop_file: str, max_seconds: float, on_started, camera_index: int = 0, on_level=None):
    """Record until `stop_file` appears, `max_seconds` pass or the parent process dies.

    Calls `on_level(seconds, level)` about 10 times a second while recording.
    Returns RGB frames resampled to 25 fps, or None when stopped before recording began."""
    parent = os.getppid()
    meter = MouthMeter() if on_level else None
    next_level = 0.0
    cap = open_camera(camera_index)
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        # The first frames after opening are often black while exposure settles.
        for _ in range(5):
            cap.read()
        if os.path.exists(stop_file):
            return None

        on_started()
        stamps, frames = [], []
        failures = 0
        start = time.monotonic()
        while True:
            ok, frame = cap.read()
            now = time.monotonic() - start
            if ok:
                stamps.append(now)
                frames.append(_to_rgb(frame))
                failures = 0
                if meter and now >= next_level:
                    next_level = now + 0.1
                    on_level(now, meter(frames[-1]))
            else:
                failures += 1
                if failures > 30:
                    raise CameraError("The camera stopped delivering frames.")
            if os.path.exists(stop_file) or now >= max_seconds or os.getppid() != parent:
                break
    finally:
        cap.release()
        if meter:
            meter.close()

    return resample(stamps, frames)


def resample(stamps, frames) -> np.ndarray:
    """Pick the nearest captured frame for each 1/25 s tick, so any webcam rate maps to the model's rate."""
    if not frames:
        return np.zeros((0, 0, 0, 3), dtype=np.uint8)
    count = max(1, int(stamps[-1] * MODEL_FPS))
    picked = []
    for k in range(count):
        t = k / MODEL_FPS
        i = bisect.bisect_left(stamps, t)
        if i > 0 and (i == len(stamps) or t - stamps[i - 1] <= stamps[i] - t):
            i -= 1
        picked.append(frames[i])
    return np.stack(picked)


def save_video(frames: np.ndarray, path: str) -> None:
    """Write RGB frames to an mp4 (for debugging and building a personal test set)."""
    height, width = frames.shape[1:3]
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), MODEL_FPS, (width, height))
    for frame in frames:
        writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    writer.release()


def load_video(path: str) -> np.ndarray:
    """Read a video file as RGB frames resampled to 25 fps."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or MODEL_FPS
    stamps, frames = [], []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        stamps.append(len(frames) / fps)
        frames.append(_to_rgb(frame))
    cap.release()
    return resample(stamps, frames)

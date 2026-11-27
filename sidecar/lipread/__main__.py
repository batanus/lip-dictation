"""lipread CLI. `dictate` speaks JSON lines on stdout, one event per line, for the Claude Code mod:

  {"event": "started", "stop_file": "..."}    create this file to stop (or cancel) the recording
  {"event": "ready"}, {"event": "expired"}    with --wait: loaded and waiting for "<stop_file>.go"; gave up
  {"event": "recording", "max_seconds": 20}   camera is on
  {"event": "level", "t": 3.2, "level": 0.4}  ~10/s while recording: mouth movement 0..1, null = no face
  {"event": "processing", "seconds": 3.2}     recording stopped, recognizing
  {"event": "result", "text": "...", "alternatives": ["..."], "raw": "..."}
  {"event": "empty", "message": "..."}        nothing to insert (too short, no face, no words)
  {"event": "cancelled"}                      stopped before the camera started recording
  {"event": "error", "code": "...", "message": "..."}

`enroll` records the next training phrase with the same events, plus:
  {"event": "phrase", "text": "...", "recorded": 12, "total": 100}   first: the phrase to mouth
  {"event": "saved", "recorded": 13, "total": 100}                    its mouth crops are stored
  {"event": "done", "recorded": 100, "total": 100}                    every phrase is recorded
Creating "<stop_file>.discard" before the stop file ends the recording without saving it (cancelled).

Each dictation is also appended to ~/.cache/lip-dictation/dictations.jsonl (`lipread.sh --history`).

`train` adapts the model to them:
  {"event": "progress", "step": "Training", "done": 3, "total": 8}
  {"event": "trained", "before": 0.52, "after": 0.31, "kept": true, "clips": 60, ...}
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time

from . import paths

MIN_SECONDS = 1.0


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def log_dictation(event: str, **fields) -> None:
    """Append one dictation to the history (dictations.jsonl); never fails the dictation."""
    try:
        paths.home().mkdir(parents=True, exist_ok=True)
        with paths.log_path().open("a") as log:
            log.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}) + "\n")
    except OSError:
        pass


def missing_files() -> list[str]:
    missing = []
    if not (paths.vendor_dir() / ".lipread-complete").is_file():
        missing.append(f"auto_avsr code ({paths.vendor_dir()})")
    if not paths.model_path().is_file():
        missing.append(f"model weights ({paths.model_path()})")
    return missing


def load_recognizer_in_background(device: str, beam_size: int):
    """Load the ~1 GB model while the user is still mouthing, so it adds no wait after they stop."""
    box: dict = {}

    def load():
        try:
            from .model import Recognizer

            recognizer = Recognizer(device=device, beam_size=beam_size)
            recognizer.warm_up()
            box["recognizer"] = recognizer
        except Exception as error:  # reported once recording ends
            box["error"] = error

    thread = threading.Thread(target=load, daemon=True)
    thread.start()
    return thread, box


def wait_for_go(go_file: str, stop_file: str, seconds: float) -> bool:
    """True once the go file appears; False on the stop file, the timeout or the caller's exit."""
    emit("ready")
    parent = os.getppid()
    deadline = time.monotonic() + seconds
    while not os.path.exists(go_file):
        if os.path.exists(stop_file) or time.monotonic() > deadline or os.getppid() != parent:
            return False
        time.sleep(0.03)
    return True


def capture(args, before_camera=None):
    """Records until the caller stops us, emitting started / recording / level; returns the frames,
    or None once the run's outcome (cancelled, empty or error) has been emitted."""
    # The caller stops us by creating this file; tell it where, before the slow imports.
    own_dir = None if args.stop_file else tempfile.mkdtemp(prefix="lipread-")
    stop_file = args.stop_file or os.path.join(own_dir, "stop")
    # With --wait, a standby run: everything loads now, and the camera starts once the caller creates
    # the go file, so a press opens the camera at once. Without a press it exits after that many seconds.
    wait = getattr(args, "wait", 0)
    go_file = stop_file + ".go" if wait else None
    emit("started", stop_file=stop_file, **({"go_file": go_file} if go_file else {}))

    # Import the heavy libraries before the camera starts: imports hold the GIL, and doing them during
    # capture would delay frame timestamps. Loading the weights in the background is fine.
    import mediapipe  # noqa: F401
    import torch  # noqa: F401

    from .camera import CameraError, record

    if before_camera:
        before_camera()
    # The caller creates "<stop file>.discard" before the stop file to throw the recording away.
    discard_file = stop_file + ".discard"
    try:
        if go_file and not wait_for_go(go_file, stop_file, wait):
            emit("expired")
            return None
        frames = record(
            stop_file,
            args.max_seconds,
            lambda: emit("recording", max_seconds=args.max_seconds),
            args.camera,
            on_level=lambda t, level: emit("level", t=round(t, 1), level=None if level is None else round(level, 2)),
        )
        discarded = os.path.exists(discard_file)
    except CameraError as error:
        emit("error", code="camera", message=str(error))
        return None
    finally:
        if own_dir:
            shutil.rmtree(own_dir, ignore_errors=True)
        else:
            for leftover in (stop_file, go_file, discard_file):
                if leftover and os.path.exists(leftover):
                    os.remove(leftover)

    if frames is None or discarded:
        emit("cancelled")
        return None
    seconds = len(frames) / 25
    emit("processing", seconds=round(seconds, 1))
    if seconds < MIN_SECONDS:
        emit("empty", message="Recording was too short: mouth a whole phrase (at least 1 second).")
        return None
    return frames


def cmd_dictate(args) -> int:
    missing = missing_files()
    if missing:
        emit("error", code="not-installed", message="Run /lip-setup first. Missing: " + ", ".join(missing))
        return 2

    loading = {}

    def start_loading():
        loading["thread"], loading["box"] = load_recognizer_in_background(args.device, args.beam_size)

    frames = capture(args, start_loading)
    if frames is None:
        return 0
    from .camera import save_video
    from .model import NoFaceError, tidy

    if args.save:
        save_video(frames, args.save)
    loading["thread"].join()
    box = loading["box"]
    if "error" in box:
        emit("error", code="model", message=f"Could not load the model: {box['error']}")
        return 1
    started = time.monotonic()
    try:
        raws = box["recognizer"].transcribe(frames, count=5)
    except NoFaceError as error:
        log_dictation("empty", message=str(error), seconds=round(len(frames) / 25, 1))
        emit("empty", message=f"{error} Face the camera with your mouth visible.")
        return 0
    texts = list(dict.fromkeys(text for text in map(tidy, raws) if text))
    if not texts:
        log_dictation("empty", message="No words recognized.", seconds=round(len(frames) / 25, 1))
        emit("empty", message="No words recognized.")
        return 0
    result = {
        "text": texts[0],
        "alternatives": texts[1:],  # other readings from the beam search, for Claude to weigh
        "raw": raws[0],
        "seconds": round(len(frames) / 25, 1),
        "decode_seconds": round(time.monotonic() - started, 1),
        "personal": box["recognizer"].personal is not None,
    }
    log_dictation("result", **result)
    emit("result", **result)
    return 0


def cmd_enroll(args) -> int:
    """Record the next unrecorded training phrase: emits phrase, the capture events, then saved."""
    from . import personal

    missing = missing_files()
    if missing:
        emit("error", code="not-installed", message="Run /lip-setup first. Missing: " + ", ".join(missing))
        return 2
    if args.redo_last:  # the last phrase was mouthed wrong: drop it, so it is the next one again
        personal.remove_last_clip()
    phrase = personal.next_phrase()
    recorded, total = len(personal.clips()), len(personal.phrases())
    if phrase is None:
        emit("done", recorded=recorded, total=total)
        return 0
    emit("phrase", text=phrase, recorded=recorded, total=total)
    frames = capture(args)
    if frames is None:
        return 0
    from .model import Cropper, NoFaceError

    try:
        crops = Cropper()(frames)
    except NoFaceError as error:
        emit("empty", message=f"{error} Face the camera with your mouth visible, then record it again.")
        return 0
    personal.add_clip(crops, phrase)
    emit("saved", recorded=recorded + 1, total=total)
    return 0


def cmd_train(args) -> int:
    """Train the personal adapter: emits progress events, then trained (or error)."""
    from . import personal

    try:
        info = personal.train(emit, epochs=args.epochs)
    except personal.TrainError as error:
        emit("error", code="train", message=str(error))
        return 1
    emit("trained", **info)
    return 0


def cmd_forget(args) -> int:
    from . import personal

    personal.forget()
    paths.log_path().unlink(missing_ok=True)
    print(f"Deleted the training phrases, the personal model and the history in {paths.home()}")
    return 0


def cmd_file(args) -> int:
    from .camera import load_video
    from .model import NoFaceError, Recognizer, tidy

    recognizer = Recognizer(device=args.device, beam_size=args.beam_size)
    try:
        raw = recognizer.transcribe(load_video(args.video))
    except NoFaceError as error:
        print(error, file=sys.stderr)
        return 1
    print(tidy(raw[0]) if raw else "")
    return 0


def cmd_check(args) -> int:
    ok = True
    missing = missing_files()
    for item in missing:
        print(f"MISSING  {item}")
    ok = ok and not missing
    if not missing:
        try:
            from .model import Recognizer

            started = time.monotonic()
            Recognizer(device=args.device)
            print(f"OK       model loads ({time.monotonic() - started:.1f}s)")
        except Exception as error:
            print(f"FAIL     model: {error}")
            ok = False
    if args.camera:
        from .camera import CameraError, open_camera

        print("...      opening the camera (allow access if macOS asks)", flush=True)
        try:
            cap = open_camera()
            got, _ = cap.read()
            cap.release()
            print("OK       camera" if got else "FAIL     camera opened but sent no frame")
        except CameraError as error:
            got = False
            print(f"FAIL     camera: {error}")
        ok = ok and got
    print("Ready." if ok else "Not ready.")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="lipread", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--device", default=os.environ.get("LIPREAD_DEVICE", "auto"), help="where the encoder runs: auto (mps when available), mps or cpu"
    )
    parser.add_argument("--beam-size", type=int, default=10)
    sub = parser.add_subparsers(dest="command", required=True)

    dictate = sub.add_parser("dictate", help="record from the webcam until the stop file appears")
    dictate.add_argument("--stop-file", help="stop when this file appears (default: a temp file, see the started event)")
    dictate.add_argument("--max-seconds", type=float, default=20, help="the model works best on short phrases")
    dictate.add_argument("--camera", type=int, default=0)
    dictate.add_argument("--save", help="also save the 25 fps recording to this mp4 path")
    dictate.add_argument(
        "--wait", type=float, default=0, help="load everything, then wait up to this many seconds for <stop file>.go"
    )
    dictate.set_defaults(run=cmd_dictate)

    from_file = sub.add_parser("file", help="transcribe a video file")
    from_file.add_argument("video")
    from_file.set_defaults(run=cmd_file)

    enroll = sub.add_parser("enroll", help="record the next training phrase (see personal.py)")
    enroll.add_argument("--stop-file", help="stop when this file appears (default: a temp file, see the started event)")
    enroll.add_argument("--max-seconds", type=float, default=20)
    enroll.add_argument("--camera", type=int, default=0)
    enroll.add_argument("--redo-last", action="store_true", help="delete the last recorded phrase and record it again")
    enroll.set_defaults(run=cmd_enroll)

    train = sub.add_parser("train", help="adapt the model to the recorded phrases")
    train.add_argument("--epochs", type=int, default=8)
    train.set_defaults(run=cmd_train)

    forget = sub.add_parser("forget", help="delete the recorded phrases, the personal adapter and the history")
    forget.set_defaults(run=cmd_forget)

    check = sub.add_parser("check", help="verify the install")
    check.add_argument("--camera", action="store_true", help="also try to open the camera")
    check.set_defaults(run=cmd_check)

    args = parser.parse_args()
    try:
        return args.run(args)
    except Exception as error:
        if args.command in ("dictate", "enroll", "train"):
            emit("error", code="crash", message=f"{type(error).__name__}: {error}")
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())

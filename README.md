# lip-dictation

Silent dictation for Claude Code. Press a button above the prompt, mouth English words at your webcam without making a sound, press again, and the words appear in the prompt box. Recognition runs locally with the [Auto-AVSR](https://github.com/mpc001/auto_avsr) visual speech recognition model: video frames stay in memory on your machine and are never sent anywhere.

> Status: prototype. Lip reading is hard: expect word errors, especially on technical terms. By default the plugin tells Claude which words were lip-read, so it reads them in context and confirms before acting on anything destructive.

## Requirements

- macOS on Apple Silicon (PyTorch no longer ships Intel Mac wheels; the camera helper app is macOS-only).
- Claude Code with mod support (hooks modules, 2.1.287 or newer; developed against 2.1.331).
- [uv](https://docs.astral.sh/uv/) (`brew install uv`) and the Xcode command line tools (`xcode-select --install`) for git and clang.
- A webcam, and about 2.5 GB of disk in `~/.cache/lip-dictation` (about 1.5 GB to download).

## Install

At the prompt of a terminal session:

```
/plugin install lip-dictation --marketplace batanus/lip-dictation
```

In the desktop app: click + next to the prompt box, then Plugins, Add marketplace with `batanus/lip-dictation`, then Add plugin.

Then press **👄 Set up lip dictation** (or run `/lip-setup`). It shows the download size and the model's license terms and asks before downloading anything. A progress bar above the prompt shows each step and the model download in percent; the log is in `~/.cache/lip-dictation/setup.log`. You can also run `scripts/setup.sh` from a terminal.

## Use

1. Press **👄 Lip dictation** above the prompt. The camera turns on (the camera light shows it).
2. When the row shows **■ Stop ● 0:03 ▁▁▃▆█▅▂▁▁**, mouth one phrase clearly, facing the camera in good light. The timer counts up and the waveform follows your lips: bars rise when your mouth moves and settle to a flat line when it is still, and a warning if your face leaves the frame. Short phrases work best; recording stops by itself after 20 seconds, with a countdown for the last 5.
3. Press **■ Stop**. A "Reading lips" progress bar runs for a few seconds, then the text appears at the cursor. Nothing is sent until you press Enter.

The first dictation of a session takes about 2 seconds to turn the camera on, while Python and the model load. After it, the next dictation is loaded ahead and waits (about 1 GB of memory, for up to 10 minutes), so the camera starts as soon as you press. The model's encoder runs on the Mac's GPU, so the text appears about half a second after you press Stop.

In a terminal session, focus the button with ctrl+x then tab and press `l`, or bind a key to the `/lip` command in `~/.claude/keybindings.json`:

```json
{ "bindings": [{ "context": "Chat", "bindings": { "meta+l": "command:lip" } }] }
```

`meta+l` needs "Use Option as Meta key" turned on in Terminal.app or iTerm2; otherwise pick a combination that is free in your terminal. Typing `/lip` also works, but it replaces whatever is in the prompt box, so use the button or a key binding to add to a draft.

## Train it on your lips

The stock model was trained on people speaking aloud; silent mouthing moves the lips less, so it makes more mistakes on you than on its test set. Press **⚙ Settings** (at the right of the row), then **🎯 Train on my lips** (or run `/lip-train`) to adapt it:

1. The row shows a phrase to mouth, such as «run the tests», with the usual **■ Stop** and waveform. Mouth it and press Stop. Only the mouth area (96×96 px) of each phrase is kept, in `~/.cache/lip-dictation/personal/`.
2. Press **● Next phrase** for the next one, as many as you like: **Train** appears after 20, and about 100 (10 to 15 minutes) works best. **Done** stops; the next `/lip-train` continues where you left off.
3. Press **Train**. A progress bar runs for a few minutes (about 1 second per phrase per pass, 8 passes). It adds small LoRA adapters (about 2M parameters) to the model's encoder, the part that reads the lips, and leaves the decoder, its language model, as it was, holds one phrase in five out, and measures the word error rate on those before and after. A toast reports both; the personal model replaces the stock one only when it makes fewer errors.

The phrases mix everyday English with short requests to a coding assistant, so the adapter learns your lips rather than one topic.

Once trained, **⚙ Settings** shows **🧠 Personal model: On**. Press it to switch to the stock model (**Off**) and back, for example to compare the two in the history; training a new model switches it on.

Each dictation also sends Claude the recognizer's other readings of the phrase (with the note below), since the right words are often among them.

## History

**⚙ Settings → 🕘 History** opens a pane with the last dictations, newest first: the text that was inserted, the other readings the model considered, the clip length, the time it took to read, whether your personal model was used, and whether you sent the text as read or edited it first (with the prompt you sent). It is the quickest way to see what the model gets wrong for you. The same data is in `~/.cache/lip-dictation/dictations.jsonl`, one JSON line per dictation or sent prompt, also printed by `scripts/lipread.sh --history`.

**⚙ Settings → 🗑 Delete my data** (or `scripts/lipread.sh forget`) deletes the training phrases, the personal model and the history.

The button is only shown on this computer's terminal and desktop app, never on remote surfaces such as the phone app, and `/lip` only starts the camera when typed here. Settings in `/config`: hide the button (the status line then shows the state) and turn the note to Claude off.

## How it works

```
button / /lip ──► mod (hooks/register.tsx) ──spawn──► scripts/lipread.sh ──► Lip Dictation.app ──► python -m lipread dictate
                    │   press: go file (standby) or a new process ──────────────► camera, resampled to 25 fps
                    │   2nd press writes a stop file                                (model loads meanwhile)
                    ◄── JSON lines: started / recording / level ×10/s / processing / result (+ other readings)
                          (level = lip opening from MediaPipe Face Mesh, drawn as the waveform)
  result: $.prompt.fill inserts at the cursor; on Enter a prompt.submit hook adds a note for Claude
  after it: the next dictation loads ahead (`dictate --wait`) and waits for the go file
```

- `sidecar/lipread/model.py`: mouth crops (MediaPipe) → Auto-AVSR encoder (on the GPU) → beam search, plus the personal adapter when it is on.
- `sidecar/lipread/personal.py`: the training phrases, LoRA training of the encoder and the word error rate check.
- `scripts/camera-app/`: the helper app macOS grants camera access to (see the comment in `main.c`).
- Each dictation is a line in `~/.cache/lip-dictation/dictations.jsonl`, the History pane's source.

## Privacy

- Camera permission is granted to a small "Lip Dictation" helper app that setup builds from `scripts/camera-app` into `~/.cache/lip-dictation`, not to your terminal or Claude. The sidecar runs through it because macOS refuses the camera without asking when the app running Claude Code declares no camera use, as under Claude Desktop.
- Dictation frames are kept in memory for the length of one recording and discarded; nothing is written to disk unless you pass `--save` to the command-line tool. Training phrases (`/lip-train`) are the exception: their mouth crops stay in `~/.cache/lip-dictation/personal/`. The history keeps the text of each dictation and of prompts that contained one in `~/.cache/lip-dictation/dictations.jsonl`. **⚙ Settings → 🗑 Delete my data** removes all of it.
- The only network access is the one-time setup: PyPI (Python packages), GitHub (Auto-AVSR code) and Google Drive (model weights).

## Licenses

The code in this repository is MIT licensed. It does not include model code or weights:

- Auto-AVSR code is Apache-2.0; setup checks it out from the upstream repository at a pinned commit.
- The pretrained weights have **no explicit license** and were trained on LRS2/LRS3, VoxCeleb2 and AVSpeech, whose terms restrict use to research and non-commercial purposes. You download them yourself during setup and are responsible for following those terms.

## Uninstall

```
/plugin uninstall lip-dictation
rm -rf ~/.cache/lip-dictation
```

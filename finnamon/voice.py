"""Talk to Finnamon's ears: whisper.cpp and a ggml speech model, both on this machine, so no audio leaves it.

The dashboard (web/talk.js) finds them the same way, on every call: WHISPER_CPP_BIN or whisper-cli / whisper-cpp on PATH
(plus Homebrew's directories, which a launchd PATH may lack), and WHISPER_MODEL or the first of MODELS under
FINNAMON_HOME/whisper. So `finnamon voice setup` needs no restart and writes no setting; `finnamon doctor` reports the
result. Replies are spoken by macOS `say` there, or by the browser. Nothing here ever sees the household's audio.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import wave
from pathlib import Path

from . import config

REPO = "ggerganov/whisper.cpp"
MODELS = [("ggml-base.en.bin", "~150 MB", "English"), ("ggml-small.bin", "~500 MB", "multilingual, more accurate")]   # web/talk.js MODELS
NAMES = ("whisper-cli", "whisper-cpp")
EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")


def model_url(name: str) -> str:
    return f"https://huggingface.co/{REPO}/resolve/main/{name}"


def model_dir() -> Path:
    return config.home() / "whisper"


def find_bin() -> str | None:
    path = os.pathsep.join([os.environ.get("PATH", ""), *EXTRA_PATH])
    want = os.environ.get("WHISPER_CPP_BIN", "").strip()
    return shutil.which(want, path=path) if want else next((p for n in NAMES if (p := shutil.which(n, path=path))), None)


def find_model() -> Path | None:
    want = os.environ.get("WHISPER_MODEL", "").strip()
    if want:
        return Path(want) if Path(want).is_file() else None
    return next((model_dir() / n for n, *_ in MODELS if (model_dir() / n).is_file()), None)


def vad_dir() -> Path:
    """The page's voice detector (Silero VAD and the onnxruntime it runs on), served by the dashboard from web/node_modules."""
    from . import scheduler
    return Path(scheduler.repo_dir()) / "web" / "node_modules" / "@ricky0123" / "vad-web"


def status() -> tuple[bool, str]:
    """(ready, one line): what doctor prints."""
    b, m = find_bin(), find_model()
    if b and m and not vad_dir().exists():
        return False, f"whisper.cpp and a model are here, but the dashboard's voice detector is not installed ({vad_dir().parent.parent} predates it)"
    if b and m:
        return True, f"{b}, model {m}: the dashboard's Talk button transcribes here"
    missing = [x for x, ok in (("whisper.cpp", b), (f"a speech model in {model_dir()}", m)) if not ok]
    return False, "not set up (" + " and ".join(missing) + " missing); Talk falls back to the browser's own recognition, which may send audio to its maker"


def _published(name: str, opener) -> tuple[int, str] | None:
    """The size and sha256 Hugging Face publishes for a model, or None."""
    try:
        with opener(f"https://huggingface.co/api/models/{REPO}/tree/main", timeout=30) as r:
            hit = next((f.get("lfs") for f in json.load(r) if f.get("path") == name), None)
        return (int(hit["size"]), str(hit["oid"])) if hit else None
    except Exception:  # noqa: BLE001 - no checksum to compare is reported, not fatal
        return None


def download(name: str, dest: Path, opener=urllib.request.urlopen, out=print) -> str:
    """The model to dest via dest.part, checked against the published size and sha256; nothing is kept on a failure."""
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    want = _published(name, opener)
    h, got = hashlib.sha256(), 0
    try:
        with opener(model_url(name), timeout=60) as r, open(part, "wb") as f:
            total = want[0] if want else int(r.headers.get("content-length") or 0)
            shown = -1
            while chunk := r.read(1 << 20):
                f.write(chunk); h.update(chunk); got += len(chunk)
                if total and (pct := got * 10 // total * 10) != shown:
                    shown = pct; out(f"  {pct}%")
        if want and got != want[0]:
            raise ValueError(f"got {got} of {want[0]} bytes")
        if want and h.hexdigest() != want[1]:
            raise ValueError("checksum does not match the one Hugging Face publishes")
        part.rename(dest)
        return "size and sha256 verified" if want else "no checksum published to verify"
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _verify(bin_: str, model: Path) -> str:
    """What whisper heard of a spoken test sentence (macOS), or of silence elsewhere. Raises when it cannot run the model."""
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "check.wav"
        said = platform.system() == "Darwin" and shutil.which("say") and subprocess.run(
            ["say", "-o", str(wav), "--file-format=WAVE", "--data-format=LEI16@16000", "Testing one two three."], capture_output=True).returncode == 0
        if not said:   # 1 s of 16 kHz mono silence: enough to prove the model loads
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0" * 32000)
        r = subprocess.run([bin_, "-m", str(model), "-f", str(wav), "-nt", "-np"], capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1])
        return " ".join(r.stdout.split()) if said else "(silence, so no words expected)"


def setup(yes: bool = False, ask=input, out=print, opener=urllib.request.urlopen) -> int:
    """`finnamon voice setup`: whisper.cpp (Homebrew on a Mac; instructions elsewhere), then a model, then a test run."""
    out("Setting up Talk to Finnamon's speech recognition (whisper.cpp and a speech model, both local).")
    web = vad_dir().parents[2]
    if not vad_dir().exists() and (web / "node_modules").exists():   # a dashboard installed before Talk: its voice detector is a new package
        npm = shutil.which("npm")
        if npm and (yes or not ask(f"Install the dashboard's voice detector (npm install in {web})? [Y/n] ").strip().lower().startswith("n")):
            subprocess.run([npm, "install"], cwd=web)
        out("✓ Voice detector installed" if vad_dir().exists() else f"✗ The dashboard's voice detector is missing: cd {web} && npm install")
    b = find_bin()
    if b:
        out(f"✓ whisper.cpp: {b}")
    elif platform.system() == "Darwin" and shutil.which("brew"):
        if yes or not ask("Install whisper.cpp with Homebrew (brew install whisper-cpp)? [Y/n] ").strip().lower().startswith("n"):
            subprocess.run(["brew", "install", "whisper-cpp"])
        b = find_bin()
        out(f"✓ whisper.cpp: {b}" if b else "✗ whisper.cpp is still not installed: run `brew install whisper-cpp`, then this again.")
    else:
        out("✗ whisper.cpp is not installed. On a Mac: install Homebrew (https://brew.sh), then `brew install whisper-cpp`. On Linux, build it:\n"
            "    git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp && cmake -B build && cmake --build build -j --config Release\n"
            "  and put build/bin/whisper-cli on PATH (or set WHISPER_CPP_BIN to it).")
    m = find_model()
    if m:
        out(f"✓ Model: {m}")
    else:
        name = MODELS[0][0]
        if not yes:
            pick = ask("Which speech model?\n" + "\n".join(f"  {i}) {n} {s}, {about}" for i, (n, s, about) in enumerate(MODELS, 1)) + "\n[1]: ").strip()
            name = MODELS[int(pick) - 1][0] if pick.isdigit() and 1 <= int(pick) <= len(MODELS) else name
            if ask(f"Download {name} from {model_url(name)} to {model_dir()}? [Y/n] ").strip().lower().startswith("n"):
                out("Skipped the download; nothing was changed.")
                return 1
        out(f"Downloading {name} from {model_url(name)}")
        try:
            how = download(name, model_dir() / name, opener=opener, out=out)
            m = model_dir() / name
            out(f"✓ Model: {m} ({how})")
        except Exception as e:  # noqa: BLE001 - a network or disk failure is this command's answer
            out(f"✗ Download failed: {e}. Nothing was kept; run `finnamon voice setup` again.")
            return 1
    if not b:
        return 1
    try:
        out(f"✓ whisper.cpp ran the model and heard: {_verify(b, m)}")
    except Exception as e:  # noqa: BLE001
        out(f"✗ whisper.cpp could not run {m}: {e}. Delete the file and run `finnamon voice setup` again.")
        return 1
    out("Talk to Finnamon is ready: open the dashboard (finnamon open), open the assistant, and press the phone button. No restart needed.")
    return 0

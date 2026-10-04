"""`finnamon voice`: whisper.cpp and a model found the way web/talk.js finds them, a download checked against Hugging Face's
published sha256, and doctor's line. No brew, no network, no microphone: every one is faked."""
import hashlib
import io
import json

import pytest

from finnamon import cli, voice


class Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"content-length": str(len(data))}


def opener_for(model: bytes, published_sha: str | None):
    def opener(url, timeout=None):
        if "/api/models/" in url:
            return Resp(json.dumps([{"path": "ggml-base.en.bin", "lfs": {"size": len(model), "oid": published_sha}}] if published_sha else []).encode())
        return Resp(model)
    return opener


@pytest.fixture
def no_whisper(monkeypatch):
    monkeypatch.delenv("WHISPER_CPP_BIN", raising=False); monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setattr(voice, "EXTRA_PATH", ())
    monkeypatch.setenv("PATH", "/nonexistent")


def test_found_where_the_dashboard_looks(home, no_whisper, tmp_path, monkeypatch):
    assert voice.find_bin() is None and voice.find_model() is None
    assert not voice.status()[0]
    b = tmp_path / "bin"; b.mkdir(); w = b / "whisper-cli"; w.write_text(""); w.chmod(0o755)
    monkeypatch.setenv("PATH", str(b))
    (voice.model_dir()).mkdir(); (voice.model_dir() / "ggml-small.bin").write_text("")
    assert voice.find_bin() == str(w) and voice.find_model() == voice.model_dir() / "ggml-small.bin"
    monkeypatch.setattr(voice, "vad_dir", lambda: tmp_path)   # the dashboard's voice detector is installed
    assert voice.status()[0]


def test_download_is_checked_and_nothing_is_kept_on_a_mismatch(home, tmp_path):
    data = b"ggml" * 1000
    dest = tmp_path / "m" / "ggml-base.en.bin"
    assert voice.download("ggml-base.en.bin", dest, opener=opener_for(data, hashlib.sha256(data).hexdigest()), out=lambda *_: None) == "size and sha256 verified"
    assert dest.read_bytes() == data
    dest.unlink()
    with pytest.raises(ValueError, match="checksum"):
        voice.download("ggml-base.en.bin", dest, opener=opener_for(data, "0" * 64), out=lambda *_: None)
    assert not dest.exists() and not list(dest.parent.iterdir()), "no model and no .part left behind"


def test_setup_declined_changes_nothing(home, no_whisper, tmp_path, monkeypatch):
    monkeypatch.setattr(voice.platform, "system", lambda: "Linux")
    monkeypatch.setattr(voice, "vad_dir", lambda: tmp_path / "a" / "b" / "c" / "vad")   # no web/node_modules there: no npm prompt
    out = []
    assert voice.setup(ask=lambda q: "n" if "Download" in q else "1", out=out.append, opener=lambda *a, **k: pytest.fail("downloaded")) == 1
    assert not voice.model_dir().exists()
    assert any("whisper.cpp is not installed" in l for l in out)


def test_setup_is_for_a_person_and_status_is_json(home, no_whisper, capsys, monkeypatch):
    cli.main(["voice", "status"])
    assert json.loads(capsys.readouterr().out)["ready"] is False
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit):
        cli.main(["voice", "setup", "--yes"])
    assert "for a person at a terminal" in capsys.readouterr().err

# Launch video (HyperFrames source)

The launch piece, in two cuts, both 1080×1080 and captioned: the Mint cut for selfh.st and Reddit,
[finnamon-launch-v2.mp4](https://github.com/bill10/finnamon/releases/download/pr-assets/finnamon-launch-v2.mp4) (37.7 s, opens on "Remember Mint?"), and the
alert-first cut for X, [finnamon-launch-x.mp4](https://github.com/bill10/finnamon/releases/download/pr-assets/finnamon-launch-x.mp4) (35.0 s, opens on the duplicate-charge
alert; see "The X cut" below). The 1-minute demo video (`scripts/demo/launch-video.mjs`) is a separate thing and stays
the README's demo. The rendered MP4 is never committed: it lives on the `pr-assets` release.

Built with the HyperFrames `product-launch-video` workflow. `BRIEF.md` is the brief, `STORYBOARD.md` the
frame-by-frame plan (8 frames), `SCRIPT.md` the narration, `frame.md` the look (the `code-editorial` preset on
Finnamon's colors and Geist), and `compositions/frames/*.html` the frames themselves.

Honesty: the dashboard, alerts, budgets, intercom, chart and phone screens are real captures of `finnamon demo` (a made-up
household) on current main. The intercom's `claude` is the demo's stand-in (`scripts/demo/bin/claude`), which runs
the real `finnamon chart`. The message notification in frame 4 is re-typeset from the real alert text. Every frame
says "Demo household, made-up data · voice is AI-generated".

Sources and licenses: the narration is the local Kokoro voice `af_heart`. The music bed is "Chill Lofi Inspired"
by omfgdude (CC0, from OpenGameArt), trimmed to the cut; `assets/bgm/NOTICE` has the source URL, hash and the ffmpeg line. The sound
effects are HyperFrames' bundled Pixabay set (Pixabay Content License): they're in the rendered video but not committed
here. On a fresh checkout, copy them back before rendering (otherwise `assemble-index` skips them, and rendering the committed `index.html` leaves them out):
`mkdir -p assets/sfx && cp ~/.claude/skills/media-use/audio/assets/sfx/{impact-bass-1,key-press,notification,pop,whoosh-short}.mp3 assets/sfx/`. Fonts: Geist (SIL OFL, `assets/fonts/LICENSE.TXT`). Nothing paid, and no HeyGen account.

## Rebuild

Needs Node 22+, ffmpeg, Google Chrome and the HyperFrames skills (`npx hyperframes skills update product-launch-video`).
Run everything from this folder. `PLV` is the skill's scripts folder:

```sh
PLV=~/.claude/skills/product-launch-video/scripts
```

1. **Screens** (only when the UI changed). Needs libfaketime (`brew install libfaketime`), `web/node_modules`
   (`npm --prefix ../../../web install`) and Finnamon's Python deps, as for `finnamon demo`.
   ```sh
   npm i --no-save --prefix tools playwright && node tools/capture-screens.mjs
   node $PLV/stage-assets.mjs --storyboard ./STORYBOARD.md --hyperframes .
   ```
2. **Voice** (only when `SCRIPT.md` changed). Needs a Python with `kokoro-onnx soundfile`; point `HYPERFRAMES_PYTHON`
   at it. Then pad each line with a short trailing silence (the gaps, and the held end card), refresh the
   durations and write them into the storyboard:
   ```sh
   node $PLV/audio.mjs --script ./SCRIPT.md --storyboard ./STORYBOARD.md --hyperframes . --out ./audio_meta.json --provider kokoro --voice af_heart
   i=0; for pad in 0.4 0.5 0.6 0.5 1.2 1.7 0.6 2.8; do i=$((i+1)); f=assets/voice/0$i.wav
     ffmpeg -y -loglevel error -i $f -af apad=pad_dur=$pad $f.tmp.wav && mv $f.tmp.wav $f; done
   python3 tools/fix-audio-meta.py
   node $PLV/audio.mjs sync-durations --audio-meta ./audio_meta.json --storyboard ./STORYBOARD.md
   node $PLV/audio.mjs fetch-sfx --storyboard ./STORYBOARD.md --hyperframes .
   ```
   `fetch-sfx` rebuilds `audio_meta.json` from the engine's sidecar (written by the first command); step 3 puts the
   fixes back. Never run `fetch-sfx` on its own on a fresh checkout: without the sidecar it empties the voices.
3. **Captions**: `tools/build-captions.sh`. It runs `tools/fix-audio-meta.py` first, which takes the caption words from
   `SCRIPT.md` (the transcriber hears "Finnamon" as "Finemann"), sets the SFX cues, registers the music bed, and leaves
   out frames 2, 7 and 8, whose type already says the line.
4. **Assemble**:
   ```sh
   node $PLV/assemble-index.mjs --storyboard ./STORYBOARD.md --hyperframes .
   node $PLV/transitions.mjs inject --storyboard ./STORYBOARD.md --hyperframes .
   python3 tools/finish-index.py
   ```
   `finish-index.py` adds the "made-up data · AI voice" line to every frame. Never publish without it.
5. **Check and render** with the pinned CLI: `npm run check`, then `npm run render -- --quality high --output renders/video.mp4`.
   Watch the render before uploading. Upload a new take to the `pr-assets` release under a new name (never overwrite
   a published take), then point the links in `../x-thread.md` and `../README.md` at it. HyperFrames sends anonymous
   usage telemetry; `npx hyperframes@0.8.155 telemetry disable` turns it off.

## The X cut

X decides in a second or two, so the X cut opens on the alert, not on Mint: frame `x1-hook` shows the real Shell
duplicate-charge alert from the first frame while the voice says "Shell charged you twice. Want me to flag it?" (the
demo's own line), then `x2-meet` ("That's Finnamon, an AI that watches your money. Like Mint, but it warns you."), then
frames 03–08 as they are, with less trailing silence. `SCRIPT.md` has the three new lines.

It is a separate entry, not a second `index.html` (a project holds one root): `tools/build-x.py` writes
`renders/x-cut/index.html`, which shares this folder's `assets/` and `compositions/` by symlink, plus
`compositions/captions-x.html` (the hook's caption, then the Mint cut's captions for frames 03–06, shifted) and the
bed cut to length (`assets/bgm/track-x.mp3`). The Mint cut's steps above don't touch any of it. After the Mint cut's
steps 3–4 (it reads their `index.html` and `caption_groups.json`), with the sound effects copied back:

```sh
python3 tools/build-x.py
npx --yes hyperframes@0.8.155 check renders/x-cut
npx --yes hyperframes@0.8.155 render renders/x-cut --quality high --output renders/finnamon-launch-x.mp4
```

New X lines: synthesise them with the same Kokoro voice as step 2 (`synth-v3.py` from the HyperFrames TTS cache,
`af_heart`, speed 1.0, `en-us`) into `assets/voice/x0N.wav`, then update the word timings in `HOOK` and the slots in
`FRAMES` (`tools/build-x.py`), and the cue times in `compositions/frames/x2-meet.html`.

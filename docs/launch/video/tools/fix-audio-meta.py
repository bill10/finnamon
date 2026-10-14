# Re-applies the hand fixes to audio_meta.json after any audio.mjs pass rewrites it:
# caption words taken from SCRIPT.md (the transcriber hears "Finnamon" as "Finemann"),
# durations read from the padded voice files, SFX moved onto their cues, and the music bed registered.
import json, re, subprocess
d = json.load(open('audio_meta.json'))
# The spoken text is each "## Line N — … (Frame N)" section's indented block, matched by frame number.
spoken = {int(f): t.strip() for f, t in re.findall(r'\(Frame (\d+)\)\n(?:(?!## ).*\n)*?\n    (\S.*)', open('SCRIPT.md').read())}
assert sorted(v['frame'] for v in d['voices']) == sorted(spoken), 'audio_meta.json lost voices: rerun step 2 of README.md'
lines = [spoken[v['frame']] for v in d['voices']]
for v, line in zip(d['voices'], lines):
    toks, ws = line.split(), v['words']
    if len(ws) == len(toks) - 1:   # "Remember Mint?" heard as one word
        w = ws[0]; mid = round(w['start'] + (w['end'] - w['start']) * 0.6, 2)
        ws[0:1] = [dict(w, end=mid), dict(w, start=mid)]
    assert len(ws) == len(toks), (v['frame'], len(ws), len(toks))
    for i, (w, t) in enumerate(zip(ws, toks)): w.update(id=f'w{i}', text=t)
    v['duration_s'] = round(float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', v['path']])), 3)
cues = {(4, 'notification'): 0.15, (5, 'key-press'): 2.4, (5, 'pop'): 3.7, (7, 'impact-bass-1'): 3.85, (8, 'key-press'): 1.1}
for s in d.get('sfx') or []:
    for (f, name), at in cues.items():
        if s['frame'] == f and name in s['file']: s['offset_s'] = at
# The bed: local MusicGen (facebook/musicgen-small), looped and trimmed to the cut; a bed under the voice.
bgm = 'assets/bgm/track.mp3'
d['bgm'] = {'path': bgm, 'volume': 0.14, 'mode': 'generate', 'provider': 'musicgen',
            'duration_s': round(float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', bgm])), 3)}
d['bgm_pending'] = False
json.dump(d, open('audio_meta.json', 'w'), indent=2)
print('fixed', sum(v['duration_s'] for v in d['voices']))
# Captions read a copy without frames 2, 7 and 8: their on-screen type already says the line.
for v in d['voices']:
    if v['frame'] in (2, 7, 8): v['words'] = []
json.dump(d, open('.hyperframes/audio_meta.captions.json', 'w'), indent=2)

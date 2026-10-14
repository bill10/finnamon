# Builds the X cut (alert-first) from the Mint cut's parts: renders/x-cut/index.html (its own project, sharing this
# one's assets/ and compositions/ by symlink, since one project holds one root), compositions/captions-x.html and
# assets/bgm/track-x.mp3. It reuses frames 03-08, their voices and their captions, shifted to follow the two new
# opening frames, and leaves the Mint cut's files alone. Run it after the Mint cut's steps 3-4 (README.md).
import json, os, re, subprocess

SFX = 0.35
# (id, composition, voice files with their offset in the frame, slot in s, sfx (file, offset), transition in)
# A slot is how long the frame owns the screen; frames 03-08 lose some of the Mint cut's trailing silence.
FRAMES = [
    ('x1-hook', 'x1-hook', [('x01', 0)], 2.85, [('notification', 0.0)], None),
    ('x2-meet', 'x2-meet', [('x02', 0), ('x03', 2.8)], 4.65, [('whoosh-short', 0.0)], 'zoom'),
    ('03-dashboard', '03-dashboard', [('03', 0)], 3.9, [], 'fade'),
    ('04-alerts', '04-alerts', [('04', 0)], 6.55, [('notification', 0.15)], 'push'),
    ('05-ask-chart', '05-ask-chart', [('05', 0)], 4.9, [('key-press', 2.4), ('pop', 3.7)], 'push'),
    ('06-phone', '06-phone', [('06', 0)], 2.3, [], 'fade'),
    ('07-yours', '07-yours', [('07', 0)], 5.6, [('impact-bass-1', 3.85)], 'zoom'),
    ('08-cta', '08-cta', [('08', 0)], 4.2, [('key-press', 1.1)], 'fade'),
]
# Captions: the hook's line (its screen shows the alert, not the words); frames 03-06 keep the Mint cut's groups.
HOOK = [('Shell charged you twice.', [('Shell', .07, .29), ('charged', .35, .8), ('you', .8, 1.0), ('twice.', 1.0, 1.48)]),
        ('Want me to flag it?', [('Want', 1.54, 1.75), ('me', 1.75, 1.89), ('to', 1.89, 2.03), ('flag', 2.03, 2.3), ('it?', 2.3, 2.46)])]

dur = lambda p: float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p]))
mint = open('index.html').read()
mint_start = {m[0]: float(m[1]) for m in re.findall(r'id="el-([\w-]+)"\s+class="scene"[^>]*?data-start="([\d.]+)"', mint)}

t, starts = 0.0, {}
for f in FRAMES:
    starts[f[0]] = round(t, 3)
    t += f[3]
total = round(t, 3)

scenes, voices, sfx, trans = [], [], [], []
for n, (fid, comp, vs, slot, cues, tin) in enumerate(FRAMES):
    s, last = starts[fid], n == len(FRAMES) - 1
    host = slot if last else slot + 0.5  # the Mint cut's overlap, for the transition
    scenes.append(f'''      <div
        id="el-{fid}"
        class="scene"
        data-composition-id="{comp}"
        data-composition-src="compositions/frames/{comp}.html"
        data-start="{s}"
        data-duration="{round(host, 3)}"
        data-track-index="{n % 2}"
      ></div>''')
    for v, off in vs:
        p = f'assets/voice/{v}.wav'
        voices.append(f'''      <audio
        id="el-{fid}-voice-{v}"
        src="{p}"
        data-start="{round(s + off, 3)}"
        data-duration="{round(min(dur(p), slot - off - 0.05), 3)}"
        data-track-index="10"
        data-volume="1"
      ></audio>''')
    for name, off in cues:
        p = f'assets/sfx/{name}.mp3'
        sfx.append(f'''      <audio
        id="el-sfx-{len(sfx)}"
        src="{p}"
        data-start="{round(s + off, 3)}"
        data-duration="{round(dur(p), 3)}"
        data-track-index="{20 + len(sfx)}"
        data-volume="{SFX}"
      ></audio>''')
    if tin:
        prev = f'#el-{FRAMES[n - 1][0]}'
        cur = f'#el-{fid}'
        if tin == 'zoom':
            trans += [f'tl.to("{prev}", {{ scale: 2.5, opacity: 0, filter: "blur(8px)", duration: 0.4, ease: "power3.in" }}, {s});',
                      f'tl.fromTo("{cur}", {{ scale: 0.5, opacity: 0, filter: "blur(8px)" }}, {{ scale: 1, opacity: 1, filter: "blur(0px)", duration: 0.4, ease: "power3.out" }}, {s});']
        elif tin == 'fade':
            trans += [f'tl.to("{prev}", {{ opacity: 0, duration: 0.5, ease: "power2.inOut" }}, {s});',
                      f'tl.fromTo("{cur}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, ease: "power2.inOut" }}, {s});']
        else:
            trans += [f'tl.to("{prev}", {{ x: -1080, duration: 0.5, ease: "power3.inOut" }}, {s});',
                      f'tl.fromTo("{cur}", {{ x: 1080, opacity: 1 }}, {{ x: 0, opacity: 1, duration: 0.5, ease: "power3.inOut" }}, {s});']

# The bed: the Mint cut's track, cut to this length with its own fade-out.
subprocess.check_call(['ffmpeg', '-y', '-loglevel', 'error', '-i', 'assets/bgm/track.mp3', '-af',
                       f'atrim=0:{total},afade=t=out:st={round(total - 2.5, 3)}:d=2.5', '-ar', '44100', '-b:a', '160k', 'assets/bgm/track-x.mp3'])

head = mint.split('    <div\n      id="root"')[0]
body = '\n\n'.join(scenes + voices) + f'''

      <!-- BGM -->
      <audio
        id="el-bgm"
        src="assets/bgm/track-x.mp3"
        data-start="0"
        data-duration="{total}"
        data-track-index="11"
        data-volume="0.14"
      ></audio>

      <!-- captions -->
      <div
        id="el-captions"
        data-track-kind="captions"
        class="scene"
        data-composition-id="captions"
        data-composition-src="compositions/captions-x.html"
        data-start="0"
        data-duration="{total}"
        data-track-index="2"
      ></div>

      <!-- SFX -->
''' + '\n'.join(sfx) + f'''

      <!-- The honesty note, on every frame -->
      <div
        id="el-note"
        class="clip"
        data-start="0"
        data-duration="{total}"
        data-track-index="3"
        style="position:absolute;left:0;top:22px;width:1080px;text-align:center;z-index:50;font-family:'Geist Mono',ui-monospace,monospace;font-size:19px;letter-spacing:0.02em;color:rgba(27,27,27,0.72)"
      >Demo household, made-up data · voice is AI-generated</div>'''
os.makedirs('renders/x-cut', exist_ok=True)
for d in ('assets', 'compositions'):
    if not os.path.islink(f'renders/x-cut/{d}'): os.symlink(f'../../{d}', f'renders/x-cut/{d}')
open('renders/x-cut/index.html', 'w').write(f'''{head}    <!-- The X cut (alert-first), written by tools/build-x.py: edit that, not this file. -->
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{total}"
      data-width="1080"
      data-height="1080"
    >
{body}
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
      (function () {{ var tl = window.__timelines["main"];
        {(chr(10) + '        ').join(trans)}
        tl.to({{}}, {{ duration: {total} }}, 0);
      }})();
    </script>
  </body>
</html>
''')

# Captions: the hook, then the Mint cut's groups for frames 03-06, each moved by its frame's shift.
groups = [{'frame': 1, 'start': ws[0][1], 'end': ws[-1][2], 'text': text,
           'words': [{'text': w, 'start': a, 'end': b} for w, a, b in ws]} for text, ws in HOOK]
shift = {i: starts[fid] - mint_start[fid] for i, fid in [(3, '03-dashboard'), (4, '04-alerts'), (5, '05-ask-chart'), (6, '06-phone')]}
for g in json.load(open('caption_groups.json'))['groups']:
    if g['frame'] in shift:
        d = shift[g['frame']]
        groups.append(dict(g, start=round(g['start'] + d, 3), end=round(g['end'] + d, 3),
                           words=[dict(w, start=round(w['start'] + d, 3), end=round(w['end'] + d, 3)) for w in g['words']]))
for i, g in enumerate(groups):
    g['id'] = f'caption-group-{i}'
    for j, w in enumerate(g['words']):
        w['id'] = f'caption-word-{i}-{j}'
cap = open('compositions/captions.html').read()
cap, n1 = re.subn(r'var GROUPS = .*?;\n', lambda _: f'var GROUPS = {json.dumps(groups)};\n', cap, count=1)
cap, n2 = re.subn(r'var DURATION = [\d.]+;', f'var DURATION = {total};', cap, count=1)
cap, n3 = re.subn(r'(id="captions-root"[^>]*?data-duration=")[\d.]+', rf'\g<1>{total}', cap, count=1, flags=re.S)
assert (n1, n2, n3) == (1, 1, 1), 'captions.html changed shape'
open('compositions/captions-x.html', 'w').write(cap)
print('renders/x-cut/index.html', total, 's')

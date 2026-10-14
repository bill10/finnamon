# Puts back the two edits assemble-index.mjs doesn't make, so a rebuild can't drop the disclosure:
# the "made-up data · AI voice" line on every frame, and the captions track's kind. Safe to run twice.
import re
s = open('index.html').read()
total = re.search(r'id="root"[^>]*?data-duration="([\d.]+)"', s, re.S).group(1)
if 'id="el-note"' not in s:
    note = f'''
      <!-- The honesty note, on every frame (tools/finish-index.py) -->
      <div
        id="el-note"
        class="clip"
        data-start="0"
        data-duration="{total}"
        data-track-index="3"
        style="position:absolute;left:0;top:22px;width:1080px;text-align:center;z-index:50;font-family:'Geist Mono',ui-monospace,monospace;font-size:19px;letter-spacing:0.02em;color:rgba(27,27,27,0.72)"
      >Demo household, made-up data · voice is AI-generated</div>
    </div>

    <script>'''
    assert s.count('    </div>\n\n    <script>') == 1
    s = s.replace('    </div>\n\n    <script>', note, 1)
if 'GeistMono-Regular.woff2' not in s.split('</style>')[0]:
    s = s.replace('      .scene {', '      @font-face { font-family: "Geist Mono"; src: url("assets/fonts/GeistMono-Regular.woff2") format("woff2"); font-weight: 400; }\n      .scene {', 1)
if 'data-track-kind="captions"' not in s:
    s = s.replace('id="el-captions"\n', 'id="el-captions"\n        data-track-kind="captions"\n', 1)
open('index.html', 'w').write(s)
assert 'Demo household, made-up data · voice is AI-generated' in s and 'data-track-kind="captions"' in s
assert 'GeistMono-Regular.woff2' in s.split('</style>')[0], 'no .scene rule to hang the note font on'
print('index.html finished')

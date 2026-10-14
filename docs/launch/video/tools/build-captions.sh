#!/bin/sh
# Captions for every frame whose screen doesn't already say the line (see fix-audio-meta.py), and the last
# group ends with its words instead of lingering to the end of the video (the skin's default).
set -e
cd "$(dirname "$0")/.."
python3 tools/fix-audio-meta.py
node ~/.claude/skills/product-launch-video/scripts/captions.mjs build --storyboard ./STORYBOARD.md \
  --audio-meta ./.hyperframes/audio_meta.captions.json --hyperframes . --out ./caption_groups.json
sed -i.bak 's/var end = isLast ? DURATION :/var end = isLast ? Math.min(DURATION, Number(group.end) + 0.3) :/' compositions/captions.html
rm compositions/captions.html.bak
grep -q 'isLast ? Math.min(DURATION' compositions/captions.html

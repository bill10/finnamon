// Text to be spoken, for Talk to Finnamon: the server's `say` (web/talk.js) and the page's speechSynthesis fallback
// (public/talk.js) both start here, so they say the same thing. Pure; lifted from agent-007's public/modules/readaloud.js (MIT).

// Markdown, links and tables into what a person would say.
export function plainForSpeech(text) {
  let t = String(text ?? '');
  t = t.replace(/```[\s\S]*?(```|$)/g, ' ');
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');           // images: alt text
  t = t.replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, '$1');         // [text](url): text
  t = t.replace(/<(https?:\/\/[^>\s]+)>/g, 'link');
  t = t.replace(/\b(?:https?:\/\/|www\.)[^\s)>\]]+/gi, 'link');
  t = t.replace(/`([^`]*)`/g, '$1');
  t = t.replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, '');         // a table's |---|---| rule
  t = t.replace(/[ \t]*\|[ \t]*/g, ', ');                          // its cells, as a list
  t = t.replace(/^\s{0,3}#{1,6}\s+/gm, '');                  // headings
  t = t.replace(/^\s{0,3}>\s?/gm, '');                       // quotes
  t = t.replace(/^\s*(?:[-*+•]|\d+[.)])\s+/gm, '');          // list markers
  t = t.replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '');     // rules
  t = t.replace(/(\*\*|__)(.+?)\1/g, '$2');
  t = t.replace(/(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)/gm, '$1$2');
  t = t.replace(/~~(.+?)~~/g, '$1');
  t = t.replace(/(^|\s)#(\d+)\b/g, '$1number $2');
  t = t.replace(/\s+->\s+|\s+→\s+/g, ' to ');
  // A line break is a pause: end the line as a sentence if it isn't one.
  t = t.split('\n').map(line => line.trim().replace(/^,\s*|,\s*$/g, '')).filter(Boolean)
    .map(line => /[.!?:;,]$/.test(line) ? line : `${line}.`).join(' ');
  return t.replace(/\s+/g, ' ').replace(/\s+([.,;:!?])/g, '$1').replace(/\.{2,}/g, '.').trim();
}

// Cut text into pieces of at most max characters, on sentence ends where it can, then on commas, then on spaces.
// Short neighbours go back together, so the voice doesn't pause every few words.
export function chunkForSpeech(text, max = 180) {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim();
  if (!t) return [];
  const pieces = [];
  const split = (s, re) => s.split(re).map(x => x.trim()).filter(Boolean);
  for (const sentence of split(t, /(?<=[.!?])\s+/)) {
    if (sentence.length <= max) { pieces.push(sentence); continue; }
    for (const clause of split(sentence, /(?<=[,;:])\s+/)) {
      if (clause.length <= max) { pieces.push(clause); continue; }
      let line = '';
      for (const word of clause.split(' ')) {
        if (line && line.length + 1 + word.length > max) { pieces.push(line); line = ''; }
        line = line ? `${line} ${word}` : word;
        while (line.length > max) { pieces.push(line.slice(0, max)); line = line.slice(max); }
      }
      if (line) pieces.push(line);
    }
  }
  const chunks = [];
  for (const piece of pieces) {
    const last = chunks.at(-1);
    if (last !== undefined && last.length + 1 + piece.length <= max) chunks[chunks.length - 1] = `${last} ${piece}`;
    else chunks.push(piece);
  }
  return chunks;
}

// The best installed English voice for the browser fallback: en-US first, the high-quality ones (Premium, Enhanced,
// Siri, Google, Natural) ahead of the rest.
export function pickVoice(voices) {
  let best = null, bestScore = -1;
  for (const v of Array.from(voices || [])) {
    const lang = String(v.lang || '').replace('_', '-').toLowerCase(), name = String(v.name || '');
    if (!lang.startsWith('en')) continue;
    const s = (lang === 'en-us' ? 20 : 10) + (/premium/i.test(name) ? 8 : /enhanced|natural|neural/i.test(name) ? 6 : 0)
      + (/siri/i.test(name) ? 5 : 0) + (/google/i.test(name) ? 4 : 0) + (v.default ? 1 : 0);
    if (s > bestScore) { best = v; bestScore = s; }
  }
  return best;
}

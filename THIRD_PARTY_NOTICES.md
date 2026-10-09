# Third-party notices

Finnamon is MIT-licensed (see LICENSE). This repository vendors no third-party source: `finnamon/qr.py` is written
from ISO/IEC 18004, and the dashboard's Talk voice detector is installed by npm into `web/node_modules` (never copied
into the repo) and served from there. Those packages keep their own licences, shipped inside each package:

- `@ricky0123/vad-web` and the Silero VAD model it carries (`silero_vad_v5.onnx`): MIT
- `onnxruntime-web`: MIT
- `express`, `ws`, `node-pty`: MIT

Talk's browser code derives from agent-007, which is MIT and has the same owner.

The dashboard also loads these from a CDN at run time (nothing is copied into the repo); each keeps its own licence:

- xterm.js (`@xterm/xterm` 5.5.0) and its addons `@xterm/addon-fit` 0.10.0 and `@xterm/addon-web-links` 0.11.0, from
  jsDelivr: MIT
- Vega (`vega` 5.33.1), Vega-Lite (`vega-lite` 5.23.0), `vega-embed` 6.29.0 and `vega-interpreter` 1.2.1, from jsDelivr:
  BSD-3-Clause
- The Geist and Geist Mono fonts, from Google Fonts: SIL Open Font License 1.1

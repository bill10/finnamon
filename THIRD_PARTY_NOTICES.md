# Third-party notices

Finnamon is MIT-licensed (see LICENSE). This repository vendors no third-party source: `finnamon/qr.py` is written
from ISO/IEC 18004, and the dashboard's Talk voice detector is installed by npm into `web/node_modules` (never copied
into the repo) and served from there. Those packages keep their own licences, shipped inside each package:

- `@ricky0123/vad-web` and the Silero VAD model it carries (`silero_vad_v5.onnx`): MIT
- `onnxruntime-web`: MIT
- `express`, `ws`, `node-pty`: MIT

Talk's browser code derives from agent-007, which is MIT and has the same owner.

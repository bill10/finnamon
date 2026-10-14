---
workflow: product-launch-video
flow: automation
storyboard: no
message: "Finnamon is the Mint replacement that's yours: Claude or Codex as your personal finance assistant, warning you when something's off and drawing any chart you ask for."
destination: x-feed
aspect: 1080x1080
language: en
audience: "Ex-Mint users and self-hosters on X who already pay for Claude or ChatGPT"
length: 30-45s
angle: "Mint's gone → meet Finnamon → it warns you, it sorts, it draws → yours to keep → try the demo"
---

## Intent

The launch piece for X (post 1 of docs/launch/x-thread.md) and the other launch posts. Brisk and product-forward,
about 30–45 seconds. Separate from the 1-minute demo video (scripts/demo/launch-video.mjs), which stays the demo.
Settled pitch: "Your money. Your financial AI. Turn Claude or Codex into your personal finance assistant." / "Like
Mint, but it warns you when something's off, sorts your transactions with AI, and draws any chart you ask for." Self-hosted,
no ads, nobody can shut it down. Story beats from docs/launch/show-hn-facts.md (Mint origin), retold in a narrator's voice.

## Assets

- capture/assets/*.png — real screens from `finnamon demo` (made-up household, clock pinned to Sep 20 2026) on current main: dashboard, alerts, intercom asked "Show dining by month", the chart it drew, the phone view.
- capture/assets/logo.png — web/public/logo.png.

## Customizations

- One narrator voice (local Kokoro, free), music bed ducked under it, burned-in captions (X autoplays muted).
- Persistent note: "Demo household, made-up data · voice is AI-generated".

## Notes

- Never claim "nothing leaves your machine": Plaid, Anthropic/OpenAI and Telegram all see data. Say "your data lives on your machine".
- Show only features that exist. Never "watchdog".
- Free only: no HeyGen sign-in, no paid APIs.
- Owner's taste: one consistent voice, brisk rhythm with short gaps, music ducked under the voice.

---
format: 1080x1080
duration: 40s
message: "Finnamon is the Mint replacement that's yours: Claude or Codex as your personal finance assistant, warning you when something's off and drawing any chart you ask for."
arc: Hook (Mint's gone) → Product intro → Key features (assistant, alerts, AI sorting + charts, phone) → Benefit (yours to keep) → CTA (finnamon demo)
audience: Ex-Mint users and self-hosters on X who already pay for Claude or ChatGPT
mode: autonomous
music: warm upbeat minimal tech underscore, light piano and soft pulse, optimistic
---

## Video direction

- **palette system** (frame.md): cream `#F8F8F8` ground on every frame; ink `#1B1B1B` for all type; tile `#FBEAE1` for soft panels; coral `#CE623C` (Finnamon's cinnamon) is the ONE voltage moment per frame (a ✱ kicker, an underline, the CTA pill); mint `#7CC7A4` only where the product's own UI already uses it (charts). Screens are real captures: never recolor, never redraw them.
- **type**: Geist display (400, sentence case, negative tracking) for every headline; Geist body; Geist Mono for kickers (UPPERCASE, coral ✱ prefix) and commands.
- **screens**: every product screen sits in a cream-on-white window card: 12px radius, 1px ink@12% hairline, the one soft warm shadow. No browser chrome, no fake cursor.
- **persistent note** (added once at the index, every frame): top edge, Geist Mono small, ink@55%: "Demo household, made-up data · voice is AI-generated". Workers keep the top ~70px of the 1080 canvas free of important content.
- **motion grammar**: power3 long-tail eases, smooth over bouncy; every reveal lands on its spoken cue (VO-paced), never front-loaded; held reads stay still (no breathing, no drift beyond a barely-there push).
- **rhythm**: brisk. Busy frames 3–5; Frame 6 (phone) is the breather hold; Frame 7 slams type on the beat; Frame 8 resolves still.
- **captions**: on; the bottom ~17% (≈184px) is the caption band, kept clear on every frame.
- **negative list**: no "AI" purple/blue gradients, bokeh, glows, tilted 3D gimmicks, stock icons standing in for UI, emoji confetti; no claim that data never leaves the machine; no slideshow (front-load then freeze) and no screensaver (everything floating independently).

## Frame 1 — Mint is gone

- scene: A giant "Mint" in ink on cream; "2007" ticks to "2024" beside it; a coral line strikes through it on "shut down".
- voiceover: "Remember Mint? Every account, in one clean place. Then it shut down."
- duration: 4.368s
- transition_in: cut
- status: animated
- src: compositions/frames/01-mint-is-gone.html
- type: hook
- persuasion: Pain validation (shared loss)
- beat: nostalgia → frustration
- blueprint: kinetic-type-beats
- asset_candidates:
- focal: kinetic headline (type only)
- roles: none
- sfx: none

Adapt kinetic-type-beats: keep one fixed word whose state changes in place (the signature in-place token cycle); the captions carry the spoken line, so the screen shows only the word "Mint" and its years, never the sentence.
Scene 1 (0.0–0.9s): cream ground; "Mint" in display-cover (very large, ~60% frame width) rises in, centered in the upper two-thirds, on "Mint?" (0.60s), a simple rise-and-settle.
Scene 2 (0.9–3.0s): under it, a Geist Mono line "2007 —" appears, and the end year counts 2008→2024 in place as the VO says "every account, in one clean place" (in-place token cycle) → discrete-text-sequence.
Scene 3 (3.0–end): on "shut down" (3.53s) a 6px coral line draws left→right through "Mint" (a scaleX draw) and the word drops to ink@35%; a small Geist Mono "shut down, 2024" settles under it; hold still.

narrativeRole: Name the loss every ex-Mint user still feels, in the owner's origin story.
keyMessage: Mint is gone and nothing replaced it.

## Frame 2 — Meet Finnamon

- scene: The Finnamon wordmark settles center; the headline "Your money. Your financial AI." lands beneath it word by word.
- voiceover: "Meet Finnamon. Your money. Your financial AI."
- duration: 3.337s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/02-meet-finnamon.html
- type: product_intro
- persuasion: Ownership framing
- beat: curiosity → relief
- blueprint: logo-assemble-lockup
- asset_candidates: assets/logo.png — the Finnamon wordmark, cinnamon F
- focal: assets/logo.png
- roles: logo.png = cutout (centered wordmark)
- sfx: whoosh-short

Reproduce logo-assemble-lockup (spring-bloom on a cleared stage): the wordmark blooms from ~0.9 to 1 with an opacity rise, then the headline extends the lockup.
Scene 1 (0.0–1.1s): wordmark blooms centered in the upper half (~45% frame width) as the VO says "Meet Finnamon" → logo bloom.
Scene 2 (1.1–2.3s): "Your money." lands under it in display Geist → per-word staggered reveal (dynamic-content-sequencing).
Scene 3 (2.3–end): "Your financial AI." lands on the next line, "AI" underlined by a coral 1px rule that draws left→right (svg-path-draw); lockup holds still.

narrativeRole: The answer to the hook: the product and its settled headline.
keyMessage: Finnamon. Your money. Your financial AI.

## Frame 3 — Your assistant, your dashboard

- scene: The real dashboard (net worth, budgets, alerts) arrives as a floating window; "Claude" and "Codex" chips pin to it.
- voiceover: "It turns Claude or Codex into your personal finance assistant."
- duration: 4.376s
- transition_in: crossfade
- status: animated
- src: compositions/frames/03-dashboard.html
- type: key_feature
- persuasion: Show-don't-tell proof
- beat: clarity
- blueprint: device-surface-showcase
- asset_candidates: assets/dashboard.png — the desktop dashboard of the demo household: net worth, budgets, recent alerts
- focal: assets/dashboard.png
- roles: dashboard.png = cutout (window card, the hero, ≥70% of frame width)
- sfx: none

Adapt device-surface-showcase (floating-window push-scroll): keep the floating window held as hero with a slow push; the "flow" is a short push toward the net-worth and alerts region rather than a screen cycle (one real screen).
Scene 1 (0.0–1.4s): kicker "✱ YOUR ASSISTANT" upper-left; the dashboard window card rises from below into the upper 80%, ~88% wide, cropped to the top-left two-thirds of the screenshot (logo, demo badge, net worth, budgets, alerts) → multi-phase-camera.
Scene 2 (0.6–2.0s): on "Claude" (0.64s) an ink pill "Claude" pins to the window's upper-right edge, and on "Codex" (1.38s) a second pill "Codex" pins beside it (press-release-spring); meanwhile the camera pushes gently toward the net-worth figure → coordinate-target-zoom.
Scene 3 (3.0–end): kicker stays; nothing else enters; hold still on the window.

narrativeRole: First look at the real product surface while the VO says what it is.
keyMessage: The AI you already pay for becomes your finance assistant.

## Frame 4 — It warns you

- scene: A message notification slides in with the real duplicate-charge alert; behind it the dashboard's Recent alerts card, three alert kinds highlighted as the VO names them.
- voiceover: "It warns you when something's off. A duplicate charge. A new subscription. A budget about to go over."
- duration: 6.772s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/04-alerts.html
- type: key_feature
- persuasion: Negative contrast (Mint rarely told you)
- beat: tension → control
- blueprint: compose
- asset_candidates: assets/alerts.png — the Recent alerts card: Peloton new recurring, low balance, Shell possible duplicate, shopping over budget, dining on pace to go over
- focal: assets/alerts.png
- roles: alerts.png = supporting (window card, right 60%), notification card = foreground (reconstructed text of the real Shell alert)
- sfx: notification

Compose: a reconstructed message notification (white card, 20px radius, small round cinnamon app dot, "Finnamon · now", body exactly: "⚠️ Possible duplicate: Shell $52.18 on Total Checking …4821, charged Sep 17 and again Sep 18. Same merchant, same amount." with "Reply “it's normal” if it was meant." in muted) over the real Recent alerts card.
Scene 1 (0.0–1.8s): kicker "✱ ALERTS" upper-left; the notification card slides down from the top into the upper third, full width minus margins (cursor-free slide).
Scene 2 (1.9–3.4s): on "duplicate" (2.03s) the notification lifts and shrinks to the top-right corner while the alerts card (the screenshot) rises into the main area (~85% width) and the Shell "Possible duplicate" row gets a coral 2px outline → coordinate-target-zoom toward it.
Scene 3 (3.4–4.9s): on "new subscription" (3.51s) the outline glides to the Peloton "New recurring charge" row.
Scene 4 (4.9–end): on "budget" (4.94s) the outline glides to the "Shopping is over budget" row; hold.

narrativeRole: The feature Mint never had, proven with the demo's real alerts.
keyMessage: It tells you when something needs you, and only then.

## Frame 5 — Ask, and it draws

- scene: Budgets card with AI-sorted categories; then the intercom: "Show dining by month" typed, "Drawing dining by month…", and the bar chart lands on the board.
- voiceover: "It sorts your transactions with AI. And it draws any chart you ask for."
- duration: 5.317s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/05-ask-chart.html
- type: key_feature
- persuasion: Show-don't-tell proof
- beat: ease → delight
- blueprint: prompt-type-submit-generate
- asset_candidates: assets/budgets.png — budgets card with categories (shopping, dining, coffee, groceries); assets/intercom-ask.png — the intercom after "Show dining by month": the finnamon chart call and "Done. Dining by month is on your board."; assets/chart-dining.png — the "Dining by month" bar chart it drew
- focal: assets/chart-dining.png
- roles: budgets.png = supporting (Scene 1 hero), intercom-ask.png = supporting (the ask), chart-dining.png = cutout (the payoff)
- sfx: key-press, pop

Adapt prompt-type-submit-generate (full loop): keep "watch me ask, watch it answer"; the ask is shown in the real intercom capture, and the generated artifact is the real chart. Preceded by one beat on the AI-sorted budgets.
Scene 1 (0.0–2.4s): kicker "✱ SORTED BY AI"; the budgets card window centered (~80% width); its category rows (shopping, dining, coffee, groceries) light in turn with a tile-color wash as the VO says "sorts your transactions" (simple staggered wash).
Scene 2 (2.4–4.4s): scale-swap to the intercom: the intercom capture as a tall window card on the left 45%; to its right, a large Geist Mono prompt line types "Show dining by month" behind a caret and a coral ↵ press → discrete-text-sequence (caret blink by hand).
Scene 3 (4.4–end): on "any chart you ask for", the dining chart window card rises over the right side and lands across the lower-middle (~90% width), bars grow up from the baseline via a bottom-up clip reveal → stat-bars-and-fills; kicker becomes "✱ ASK, AND IT DRAWS"; hold.

narrativeRole: The AI payoff: no recategorising by hand, and any chart on request.
keyMessage: Ask in plain words; the chart is on your board.

## Frame 6 — On your phone

- scene: The same dashboard on a phone rises into frame; a small kicker says "✱ PHONE".
- voiceover: "On your phone, too."
- duration: 2.852s
- transition_in: crossfade
- status: animated
- src: compositions/frames/06-phone.html
- type: benefits
- persuasion: Friction reduction
- beat: ease
- blueprint: device-surface-showcase
- asset_candidates: assets/phone.png — the dashboard on a phone: net worth, account mix, chart, budgets
- focal: assets/phone.png
- roles: phone.png = cutout (inside a hand-built phone frame, ink bezel, 44px radius)
- sfx: none

Reproduce device-surface-showcase (static tour): the phone is the held hero; this is the breather.
Scene 1 (0.0–1.0s): the phone rises from below and settles centered, its top near y≈90 and its visible bottom at the caption cutoff (the phone may be cropped by the band edge, faded out into the cream there); kicker "✱ PHONE" upper-left → multi-phase-camera.
Scene 2 (1.2–end): inside the phone the screenshot scrolls gently from the top to show net worth then the chart (3d-page-scroll, flat), then holds still.

narrativeRole: A breather beat: it goes where you go.
keyMessage: The same dashboard on your phone.

## Frame 7 — Yours to keep

- scene: Kinetic type: "On your own computer." → "No ads." → "No data selling." → "Nobody can shut it down."
- voiceover: "It runs on your own computer. No ads. No data selling. Nobody can shut it down."
- duration: 5.827s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/07-yours.html
- type: benefits
- persuasion: Risk reversal (it can't be taken away like Mint was)
- beat: peace of mind → triumph
- blueprint: kinetic-type-beats
- asset_candidates:
- focal: kinetic statement (type only)
- roles: none
- sfx: impact-bass-1

Reproduce kinetic-type-beats (kinetic beat-slam): four short phrases slam on the VO's beats, resolving on the locked finale.
Scene 1 (0.0–1.4s): "On your own computer." headline, centered, slides up with a hairline rule under it → kinetic-beat-slam.
Scene 2 (1.4–2.2s): "No ads." slams in below.
Scene 3 (2.2–3.2s): "No data selling." slams in below.
Scene 4 (3.2–end): the three clear; "Nobody can shut it down." lands display-cover, centered, "Nobody" in coral (the one voltage); hold still.

narrativeRole: Close the Mint loop: this one can't be shut down.
keyMessage: Self-hosted, no ads, yours to keep.

## Frame 8 — Try the demo

- scene: End card: wordmark, a terminal pill typing `finnamon demo`, "Made-up household. No keys needed.", github.com/bill10/finnamon · MIT.
- voiceover: "Try the demo. No keys needed."
- duration: 4.827s
- transition_in: crossfade
- status: animated
- src: compositions/frames/08-cta.html
- type: cta
- persuasion: Friction reduction (free demo, no keys)
- beat: urgency-to-act
- blueprint: prompt-type-submit-generate
- asset_candidates: assets/logo.png — the Finnamon wordmark
- focal: assets/logo.png
- roles: logo.png = cutout (wordmark, upper center)
- sfx: key-press

Adapt prompt-type-submit-generate (install-command CTA end card): keep the typed ask as the show; the command is `finnamon demo` on a warm-navy terminal pill.
Scene 1 (0.0–1.0s): wordmark settles upper-center; "Your money. Your financial AI." beneath in lead size.
Scene 2 (1.0–2.4s): a warm-navy terminal pill types "$ finnamon demo" in Geist Mono behind a caret → discrete-text-sequence.
Scene 3 (2.4–end): beneath it "Made-up household. No keys needed." in body; then "github.com/bill10/finnamon · MIT" in Geist Mono with a coral underline (the voltage); everything holds still to the end.

narrativeRole: One clear next step.
keyMessage: Run `finnamon demo`; the link is github.com/bill10/finnamon.

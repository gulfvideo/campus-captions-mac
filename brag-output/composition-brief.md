# Hyperframes Composition Brief: Campus Captions

## Objective
A short launch-style brag video for Campus Captions — live captioning for campus
events. Restrained, not celebratory.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 21.6s (flexed to the generated narration)

## Source Material
- Project root: `/Users/jackson/Desktop/campus-captions-main`
- Primary files read: `README.md`, `SETUP.md`, `viewer/wall.html`, `viewer/phone.html`,
  `viewer/status.html`, `relay/preflight.py`, `relay/hub.py`,
  `integrations/companion-module-campus-captions/companion/HELP.md`
- Product name: Campus Captions
- Strongest claim: *"A deaf attendee has no other channel."* — the sentence the
  codebase justifies its design decisions against, in `relay/hub.py`, `relay/event.py`,
  `viewer/wall.html`, and `capture/relay_client.py`.
- Key UI to recreate: **the wall caption bar** — `rgba(0,0,0,0.85)` slab, 1.5vh radius,
  86vw wide, 9vh off the bottom, white Helvetica, two lines maximum, word-by-word fill.
- Copy that must appear verbatim:
  - "The campus chapel service continues this morning." (the project's own soak text)
  - "Captions reconnecting…" (`viewer/wall.html` status line)
  - "Fault alarm" / "no capture client" (Companion module + relay fault codes)
  - "A deaf attendee has no other channel."

## Creative Direction
- Tone preset: `polished`
- Creative direction: quiet infrastructure film — the thing nobody in the room notices working
- Interpretation: four scenes, long holds, soft crossfades, mixed-case medium-weight
  type. The subject touches disability: plain and factual, never inspirational.
- Angle: this is not a joke project, so the video does not try to be. Show the captions
  working, show them reaching two surfaces, show what happens when the microphone dies,
  then say who it is for.
- Hook: black frame, the real caption bar fills word by word. No title card, no logo.
- Outro: the bar clears; one line alone; then the product name.
- Avoid:
  - Generic SaaS language
  - Abstract filler visuals
  - Unrelated visual redesign
  - Any triumphant swell, confetti, or "inspiring" register

## Visual Identity
- Background: `#000000` for wall scenes, `#14161a` for operator surfaces
- Caption bar: `rgba(0,0,0,0.85)`, radius 1.5vh, 86vw wide, 9vh from bottom
- Text: `#ffffff` wall, `#eceff4` operator, `#8b8b8b` dim/scrollback
- Accent: `#3fbf6f` ok, `#d95c5c` fault, `#d8a33a` warn, `#5a6270` idle
- Display + body font: Helvetica Neue / system sans (the wall's real stack)
- Visual references: the wall bar, the phone scrollback column, the wall's small
  bottom-right status pill, an operator key tile

## Storyboard
`brag-output/brag-plan.md` is the creative contract. Scene summary:
1. **The bar** — 0 → 5.28s — caption bar fills word by word, wraps to exactly two lines
2. **Two surfaces** — 5.28 → 11.60s — same committed line on the wall and a phone with scrollback
3. **When it fails** — 11.60 → 17.38s — captions stall, "Captions reconnecting…", fault key goes red
4. **Who it is for** — 17.38 → 21.6s — "A deaf attendee has no other channel.", then the name

## Audio
- Audio role: sparse professional accents over a low bed, under narration
- Audio arc: bed fades in under the first captions, opens slightly at the two-surface
  moment, thins when the mic dies with one dry hit, gone by the closing line
- Music: `assets/music/happy-beats-business-moves-vol-9-by-ende-dot-app.mp3`
- Music treatment: fade in 1.2s, sit ~0.16-0.20, duck to ~0.08 under every narration
  line, fade to silence across the last 2s
- Music cue guidance: bundled preset at `assets/music/*.music-cues.json`; 114.84 BPM.
  Beat-lock scene 2 entry at **5.28s**, scene 3 entry at **11.60s**, and the fault key
  turning red at **12.65s** (strongCue 1.00). Do not snap caption words to the grid.
- Audio-reactive treatment: **none** — deliberate. A caption bar pulsing to music is
  wrong for this product. Document this rather than adding it.
- Audio-coupled moments:
  - Scene 1, bar lights — one soft presence, no per-word ticks
  - Scene 3, fault key turns red — one dry low hit, beat-locked 12.65s
- SFX selection guidance: at most three cues in the whole video. Prefer low
  high-frequency-risk files from `~/.claude/skills/brag/assets/sfx/`.
- SFX analysis guidance: `~/.claude/skills/brag/assets/sfx/sfx-analysis.md`
- Exact SFX choice: Hyperframes decides files, timestamps and volume.
- Voiceover: four Kokoro lines already generated in `assets/vo/` (af_heart, 0.9-0.95x).
  Durations 3.669 / 3.285 / 4.949 / 3.371s. Scene lengths were flexed to these.
  Starts: 0.90 / 6.34 / 11.90 / 17.70s. Music ducks under each.

## Hyperframes Instructions
Requirements:
- Show the real wall caption bar with the project's real text shaping (two-line cap).
- Keep all text readable — the caption fill is the hook and must be legible.
- 21.6s total.
- Include music + the sparse SFX layer; audio-reactive is intentionally omitted.
- Beat-lock 3 moments (5.28 / 11.60 / 12.65) within ±0.15s; mark them.
- Never snap caption words to the beat grid — word-by-word fill is motion, not rhythm.
- Run `npx hyperframes check` before render.

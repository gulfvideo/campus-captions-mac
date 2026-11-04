# Brag Plan: Campus Captions

## What is this app?
Live captioning for campus events — lectures, chapel, panels, ceremonies. Room audio
goes into a capture client, a relay fans it out, and it lands in two places at once:
a broadcast caption bar on the LED wall, and a phone page with scrollback.

## The angle
This is not a funny project, so the video does not try to be. It is infrastructure
that exists for one person in the room. Every design decision in the codebase is
justified against the same sentence — *"a deaf attendee has no other channel"* — and
that is the video. Show the captions working, show the two surfaces, show what
happens when the microphone dies, then say who it is all for.

The brag is restraint: this thing is built to not be noticed.

## Hook (first 2-3 seconds)
Black frame. The real caption bar fades up at the bottom of frame and fills in
**word by word** — the product's actual `reveal=word` behaviour, with the project's
own soak text. No title card, no logo. The first thing on screen is the thing itself,
doing the only trick it has.

## Key moments (the middle)
- The caption bar wrapping to **exactly two lines, never three** — the line cap is
  real, `?lines=4` clamps to 2.
- The **same sentence on two surfaces at once** — the wall bar and a phone with
  scrollback above it.
- The **fault alarm going red** on an operator key, with the wall's own status line
  reading "Captions reconnecting…" — the product's one exception to "never paint an
  error on the wall."

## Outro / punchline
The bar clears to black, and the sentence the whole codebase is organised around
appears alone: *no other channel.* Then the name.

## User flow worth showing
Three beats, all from the working app:
1. **Entry** — room audio arrives; the caption bar lights and fills word by word.
2. **Key action** — the committed line reaches the LED wall and every phone at once.
3. **Result / failure case** — a microphone dies; the operator key goes red and the
   wall says so, rather than sitting blank.

## Tone
- Preset: `polished`
- Creative direction: quiet infrastructure film — the thing nobody in the room notices working
- Interpretation: four scenes, long holds, soft crossfades, mixed-case medium-weight
  type. No triumphant swell, no stat cards, no exclamation. The subject touches
  disability, so the register stays plain and factual — dignity, not inspiration.

## Format: landscape — 1920x1080
## Duration: 21s

## Visual identity (from the project)
- Background: `#14161a` (operator dark), with true black behind the wall scenes
- Caption bar: `rgba(0,0,0,0.85)`, corner radius 1.5vh, 86vw wide, sat 9vh off the bottom
- Text: `#ffffff` on the wall, `#eceff4` on operator surfaces, `#8b8b8b` dim
- Accent: `#3fbf6f` ok / `#d95c5c` fault / `#d8a33a` warn
- Display + body font: Helvetica Neue / system sans — the wall's real stack.
  Atkinson Hyperlegible is the project's shipped accessible option (`?font=atkinson`)
  and is the right face for the closing line.
- Strongest visual element: the caption bar itself — white text, dark rounded slab,
  bottom of frame, two lines, with the project's real text shaping.

## Share copy (draft)
Built live captioning for campus events — LED wall, every phone in the room, and a
Stream Deck key that goes red when the mic dies. Two lines, never three.

## Audio direction
- Role: sparse professional accents over a low bed
- Music: `happy-beats-business-moves-vol-9-by-ende-dot-app.mp3` — held well back
- Music treatment: fade in over 1.2s, sit at roughly 0.16-0.20, duck under every
  narration line, fade out across the final two seconds to land the last words in near-silence
- Music cue guidance: preset read; tempo 114.84 BPM. Target scene changes on the
  strong cues at **5.28s**, **11.60s** and **17.38s**. Beat grid is available at
  ~0.53s spacing for the word-by-word fill, but text must never be snapped to it —
  see the restraint rule.
- Audio-reactive treatment: none. A caption bar that pulses to music is exactly wrong
  for this product.
- SFX posture: sparse. At most three: a soft presence as the bar lights, one dry low
  hit when the fault key turns red, nothing else.
- Audio-coupled moments: the word-by-word fill (motion only, no per-word tick — a
  clicking caption would be grotesque), the fault key turning red.
- Restraint rule: no swell on the closing line, no riser into the outro, no per-word
  sound on the captions, and nothing celebratory anywhere. The music must never be
  the loudest thing in a scene about people who cannot hear it.

## Storyboard

### Scene 1 — The bar — 5.28s
True black. The caption bar fades up at the bottom of frame and fills word by word
with the project's own text: *"The campus chapel service continues this morning."*
It wraps onto a second line exactly as the wall does, and stops at two.
Sequential/interaction: yes — words append one at a time over ~3s, the real
`reveal=word` behaviour; the second line begins only when the first is full.
Audio intent: near silence, then a low bed fading in under the first words.
Audio-coupled idea: a single soft presence as the bar lights. No per-word ticks.
Music: low bed, fading in.
Transition mood: soft crossfade → Scene 2

### Scene 2 — Two surfaces — 6.32s
The frame splits without a hard line: the wall bar stays anchored bottom-left of
frame, and a phone rises at right showing the same sentence with two earlier lines
of scrollback dimmed above it. The committed line lands on both at the same moment.
Sequential/interaction: yes — the finished line commits on the wall, then appears on
the phone a beat later, with the older lines dimming upward.
Audio intent: the bed opens up slightly; this is the one moment with any lift.
Audio-coupled idea: the commit beat aligned to the 6.34s strong cue.
Music: low bed, marginally wider.
Transition mood: soft crossfade → Scene 3

### Scene 3 — When it fails — 5.78s
The caption bar holds its last line but stops advancing. The wall's small status
line appears in the corner: **"Captions reconnecting…"**. At right, an operator key
labelled **Fault alarm** turns from idle grey to `#d95c5c` and reads *no capture
client*. The wall never shows an error — only that one short line does.
Sequential/interaction: yes — captions stall, then status line fades in, then the
key turns red, in that order, about 0.8s apart.
Audio intent: the bed thins out. One dry low hit on the key turning red.
Audio-coupled idea: the fault hit on the 12.65s strong cue.
Music: thinned, quieter.
Transition mood: slow crossfade to black → Scene 4

### Scene 4 — Who it is for — 3.68s
Black. The bar is gone. One line, centred, in Atkinson Hyperlegible:
*"A deaf attendee has no other channel."*
It holds alone, then **Campus Captions** appears small beneath it.
Sequential/interaction: none — one line, one hold. The space is the point.
Audio intent: music fading to near-silence under the line; the last word lands dry.
Audio-coupled idea: none. Nothing lands on this line but the line.
Music: fading out to silence.

**Music mood for this video:** restrained corporate bed, kept deliberately low
**Audio summary:** a quiet bed fades in under the first captions, opens slightly as
the line reaches both surfaces, thins out when the mic dies with one dry hit on the
fault, and is gone by the closing line.

## Voiceover script
Four lines, written to sit against the visuals rather than read them. Roughly 49
words across 21s, leaving real gaps between lines.

1. (~0.9-4.6s) "Room audio in. Captions out — two lines, never three."
2. (~5.6-10.4s) "The same words, on the wall and on every phone in the room."
3. (~11.9-16.8s) "When a microphone dies, a key goes red — before anyone waves from the third row."
4. (~17.8-20.8s) "Campus Captions. For the person who has no other channel."

Delivery: even, unhurried, no upward sell. Line 4 slower than the rest.

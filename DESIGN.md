---
name: Campus Captions
description: An instrument panel for a dark booth, and captions a room can read.
colors:
  stage-black: "#000000"
  booth-charcoal: "#161618"
  card-graphite: "#1C1C1E"
  raised-slate: "#2C2C2E"
  raised-slate-high: "#3A3A3C"
  divider-grey: "#38383A"
  text-primary: "#F5F5F7"
  text-secondary: "#A1A1A6"
  text-tertiary: "#8E8E93"
  text-spent: "#6E6E73"
  signal-blue: "#0A84FF"
  signal-blue-lit: "#5AB0FF"
  signal-blue-filled: "#0A6FD9"
  signal-blue-filled-hover: "#0B5FB8"
  running-green: "#30D158"
  holding-amber: "#FF9F0A"
  fault-red: "#FF453A"
  fault-red-soft: "#FF6961"
  idle-grey: "#5A5A5F"
  caption-white: "#f2f2f2"
  caption-amber: "#ffd54a"
  caption-green: "#8ee08a"
  caption-blue: "#8ab4ff"
  caption-red: "#ff9b9b"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', Figtree, 'Segoe UI', system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', Figtree, 'Segoe UI', system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Display', Figtree, 'Segoe UI', system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', Figtree, 'Segoe UI', system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'SF Pro Text', Figtree, 'Segoe UI', system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    letterSpacing: "0.02em"
  caption:
    fontFamily: "'Helvetica Neue', Helvetica, Arial, 'Segoe UI', system-ui, sans-serif"
    fontSize: "6.5vh"
    fontWeight: 600
    lineHeight: 1.28
  caption-hyperlegible:
    fontFamily: "'Atkinson Hyperlegible', 'Helvetica Neue', Helvetica, Arial, sans-serif"
    fontSize: "6.5vh"
    fontWeight: 400
    lineHeight: 1.28
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "0.12em"
rounded:
  field: "10px"
  list: "14px"
  card: "16px"
  card-lg: "18px"
  stage: "22px"
  full: "9999px"
spacing:
  hairline: "2px"
  xs: "6px"
  sm: "10px"
  md: "14px"
  lg: "18px"
  xl: "22px"
  xxl: "26px"
components:
  pill:
    backgroundColor: "{colors.raised-slate}"
    textColor: "{colors.signal-blue}"
    rounded: "{rounded.full}"
    height: "36px"
    padding: "0 16px"
  pill-filled:
    backgroundColor: "{colors.signal-blue}"
    textColor: "#FFFFFF"
    rounded: "{rounded.full}"
    height: "36px"
    padding: "0 16px"
  pill-hover:
    backgroundColor: "{colors.raised-slate-high}"
    textColor: "{colors.signal-blue}"
  cta:
    backgroundColor: "{colors.signal-blue}"
    textColor: "#FFFFFF"
    rounded: "{rounded.full}"
    height: "56px"
    width: "100%"
  cta-hover:
    backgroundColor: "{colors.signal-blue-lit}"
    textColor: "#FFFFFF"
  card:
    backgroundColor: "{colors.card-graphite}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.card}"
    padding: "18px"
  list-row:
    backgroundColor: "{colors.card-graphite}"
    textColor: "{colors.text-primary}"
    height: "52px"
    padding: "10px 16px"
  input:
    backgroundColor: "{colors.raised-slate}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.field}"
    padding: "9px 12px"
  status-pill:
    backgroundColor: "{colors.raised-slate}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.full}"
    height: "32px"
    padding: "0 14px"
  badge:
    backgroundColor: "{colors.raised-slate}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.full}"
    height: "22px"
    padding: "0 9px"
  caption-stage:
    backgroundColor: "{colors.stage-black}"
    textColor: "#FFFFFF"
    rounded: "{rounded.stage}"
    padding: "30px 40px"
---

# Design System: Campus Captions

## Overview

**Creative North Star: "The Dark Booth"**

Black here is not a mood. It is what a projector and an LED wall physically
render as *off*, and it is the ambient light level of the room where every
one of these surfaces is actually used. The palette is Apple's dark system
palette because an operator console that runs in an unlit booth beside an
unlit stage has the same problem Apple solved: stay readable without
becoming the brightest object in a dark room.

Two populations use this system and they get opposite treatments. The
**operator** gets an instrument panel — dense, precise, every state legible
at a glance by someone who is also running the switcher. The **audience**
gets almost nothing: words, large, on black, with the interface removed. The
same tokens serve both, but restraint runs in opposite directions. In the
console, restraint means one blue control among many grey ones. On the wall,
restraint means there is no interface at all.

The system is flat by construction. Depth is carried by surface lightness
alone — `#000` to `#161618` to `#1C1C1E` to `#2C2C2E` — with no borders doing
a surface's job. Shadows exist, but only for things that genuinely float
above the page or emit light. Everything else sits flat on the black.

**Key Characteristics:**
- Pure black ground, four lifted greys, three weights of text attention
- One accent, used rarely and deliberately
- Flat surfaces; shadow reserved for float and glow
- Fully round pills; soft 16–22px radii on cards and stages
- Audience surfaces hand typography control to the reader
- Nothing on an audience-facing screen ever reports a fault

## Colors

A near-monochrome greyscale on true black, with a single blue accent and
three status hues that only ever appear as state.

### Primary
- **Signal Blue** (`#0A84FF`): The one action worth taking on a given screen
  — the start control, the primary CTA, a link. It is the only saturated
  colour in the resting interface, and that is what makes it findable in the
  dark without looking for it.
- **Signal Blue Lit** (`#5AB0FF`): The hover and focus state, and the correct
  blue for body links on pure black, where it clears AA comfortably while the
  base blue only just does.
- **Signal Blue Filled** (`#0A6FD9`, hover `#0B5FB8`): The same hue taken down
  in lightness, for any surface carrying white label text. Signal Blue itself
  holds white at only 3.6:1 — under AA for anything short of large bold — and
  its lit variant is worse at 2.3:1. Use Signal Blue for marks, carets, dots,
  focus rings and text on dark; use this one the moment a blue rectangle has
  words on it. Note that hover here goes *darker*, not lighter: lightening a
  filled button is what broke the contrast in the first place.

### Secondary
- **Running Green** (`#30D158`): A thing is live and working — the stream is
  open, audio is arriving, the switch is on. Never decorative.
- **Holding Amber** (`#FF9F0A`): Paused, degraded, or proceeding with a
  warning. Also the "ready with warnings" preflight verdict.
- **Fault Red** (`#FF453A`, soft `#FF6961`): Something is wrong and an
  operator must act. Appears on operator surfaces only.

### Tertiary
- **Caption Palette** (`#f2f2f2` white, `#ffd54a` amber, `#8ee08a` green,
  `#8ab4ff` blue, `#ff9b9b` red): The colours an audience member picks for
  their own caption text on their own phone, plus a free-form custom picker.
  These are not brand colours and must not be borrowed as such. They exist
  because contrast preference is personal and medical, not aesthetic.

### Neutral
- **Stage Black** (`#000000`): The ground everywhere. On the wall and the QR
  page it is literally transparency — what the projector does not light.
- **Booth Charcoal** (`#161618`): The console's navigation rail.
- **Card Graphite** (`#1C1C1E`): Cards, grouped lists, panels.
- **Raised Slate** (`#2C2C2E`) and **Raised Slate High** (`#3A3A3C`): Controls
  resting on a card, and their hover state. The segmented control, pills,
  fields, and the switch track.
- **Divider Grey** (`#38383A`): Half-pixel hairlines between list rows.
- **Text Primary** (`#F5F5F7`), **Text Secondary** (`#A1A1A6`), **Text
  Tertiary** (`#8E8E93`): Three weights of attention, in that order.
- **Text Spent** (`#6E6E73`): Reserved for one thing — a caption line the
  room has already read. Do not use it for ordinary secondary text; at small
  sizes it does not clear AA, and its whole job is to recede.
- **Idle Grey** (`#5A5A5F`): An indicator that is neither good nor bad yet —
  an unrun check, a chevron.

### Named Rules

**The One Blue Rule.** Signal Blue marks a single action per screen. If two
things on a screen are blue, one of them is wrong. On a dense status page the
operator must be able to find the control that matters without reading, and
that only works while blue stays scarce.

**The Off-Is-Black Rule.** Audience-facing surfaces use `#000000`, never a
near-black. On an LED wall or a projected key, black is the absence of light;
`#0A0A0C` is a visible grey rectangle hanging in a dark room.

**The Status-Only Rule.** Green, amber and red report machine state and
nothing else. They are never used to categorise, decorate, or brand.

## Typography

**Display Font:** SF Pro Display, falling back to Figtree, then Segoe UI
**Body Font:** SF Pro Text, same fallbacks
**Caption Font:** Helvetica Neue / Arial by default, with Atkinson
Hyperlegible available to the reader
**Measurement Font:** the platform monospace — SF Mono, Menlo, Consolas

**Character:** The console speaks in the operating system's own voice —
tight, neutral, slightly condensed at display sizes, with no personality of
its own competing for attention. Figtree is the deliberate fallback because
it matches SF's proportions closely enough that a layout does not reflow
between the designer's Mac and the Windows machine in the booth.

Caption text is a separate decision from interface text and must stay that
way. It uses a plain grotesque at the largest size the frame allows, because
it is read once, at distance, by someone who cannot ask for it to be repeated.

### Hierarchy
- **Display** (700, 34px, 1.12, −0.025em): Page titles in the console.
- **Headline** (600, 30px, 1.3, −0.01em): The live caption line in a preview
  stage — the one currently being spoken.
- **Title** (600, 22px, 1.2): Stat tile values, section heads.
- **Body** (400, 15px, 1.45): Everything else in the console.
- **Label** (500, 13px, +0.02em, uppercase): Group headings above lists.
- **Caption** (600, 6.5vh, 1.28): The wall. Sized in viewport height so it
  scales with the output resolution, never in pixels. The reader may swap the
  face for **Caption Hyperlegible** — Atkinson Hyperlegible, drawn for
  low-vision legibility — on the phone view or with `?font=atkinson` on the
  wall. That option must remain available and must not be substituted for a
  visually similar face.
- **Mono** (700, 13px, +0.12em, uppercase for labels): Measured facts only —
  figures, specs, column rubrics, anything with a unit or a count behind it.
  Never prose. A sentence set in the measurement face is costume, and the one
  reliable way this system starts looking like a theme instead of a document.

### Named Rules

**The Reader's Choice Rule.** On audience surfaces, text size, colour and
typeface belong to the reader, not the designer. The phone view sizes in
`rem` so an OS text-size preference already set by an older or low-vision
reader carries through. Never hard-code a caption size, never remove the
controls, and never ship a layout that breaks at the largest one.

**The Outline Always Rule.** Caption glyphs carry a dark text-shadow at all
times, so that whatever an operator does to the background bar's opacity, the
words keep an edge against bright video underneath.

## Layout

The console is a two-column shell: a sticky 236px navigation rail against a
fluid main column, max 820px when the content is a settings form. Under
860px the rail becomes a horizontal scrolling strip and the labels drop to
icons — the console is used on everything from a 13-inch laptop to a booth
monitor, and it is the laptop that is tight.

Breakpoints in use: 760px, 860px, 900px, 1080px, 1240px. There is no formal
spacing scale; spacing is a small recurring set (6, 10, 14, 18, 22, 26px)
applied as flex gaps rather than margins. Cards sit in
`repeat(auto-fit, minmax(190px, 1fr))` grids so a dashboard reflows by
content rather than by breakpoint.

Audience surfaces have no layout system at all. The wall is a single bar
positioned 9vh from the bottom at 86vw wide, and every one of those numbers
is overridable from the URL, because the right value depends on a venue's
screen and lower-third safe area, not on a designer's preference.

## Elevation & Depth

**Flat, with two exceptions.** Depth is carried by surface lightness alone,
stepping `#000` → `#161618` → `#1C1C1E` → `#2C2C2E` → `#3A3A3C`. No borders
do double duty as separators, which is what keeps a dense console from
reading like a spreadsheet.

Shadows appear only where something genuinely floats above the page, or
genuinely emits light.

### Shadow Vocabulary
- **CTA glow** (`0 10px 30px rgba(10,132,255,0.35)`): Under the primary
  action only. It is the accent emitting, not a card lifting.
- **Stage shadow** (`0 30px 80px rgba(0,0,0,0.6)`): Under a caption preview,
  to make it read as a screen rather than as another card on the page.
- **Float shadow** (`0 12px 40px rgba(0,0,0,0.5)`): The toast, which is the
  only element that actually floats over content.

The caption stage also carries the system's one permitted gradient —
`radial-gradient(120% 140% at 50% 120%, #1A1A1D 0%, #000 60%)` — the lit
floor beneath the words.

### Named Rules

**The Lightness-Carries-Depth Rule.** If a surface needs to separate from the
one behind it, change its lightness. Reach for a border only when lightness
genuinely cannot do the job.

**The Shadow-Means-Float Rule.** A shadow says this element is above the
page or emitting light. A card is neither. Three shadows exist; there is no
elevation scale and there should not be one.

## Shapes

Radius is assigned by what a thing *is*, not by size. Anything you press that
is wider than it is tall is fully round — pills at 36px and 40px, the CTA at
56px, badges at 22px, the switch at 31px. Anything that *contains* takes a
soft rectangle: fields 10px, lists 14px, cards 16–18px, the caption stage
22px.

Icon tiles and marks are squircle-adjacent: a 30px tile takes 7–8px, a 26px
mark is a full circle. Borders are rare and almost always a half-pixel
hairline (`0.5px solid #38383A`) between list rows, or a 1px `#2C2C2E` edge
where a stage needs a defined boundary against the page.

### Named Rules

**The Press-It-Round Rule.** Interactive and wider than tall → fully round.
Container → soft rectangle. Small indicators → half their own height, which
makes them capsules and circles rather than tiny rectangles. There is no
fourth option, and no sharp corner anywhere in this system.

## Components

### Buttons
- **Shape:** Fully round (`9999px`). Pills are 36px tall, 40px when tall,
  padded `0 16px`.
- **Primary (filled):** Signal Blue on white text. The CTA variant is 56px
  tall, full width, round, and carries the blue glow.
- **Default (pill):** Raised Slate background with Signal Blue text — the
  console's workhorse. Hover lifts the background to Raised Slate High; the
  text does not change.
- **Light:** `#F5F5F7` background, black text. For an action that must out-rank
  a blue one on the same row.
- **Danger:** Red wash background (`rgba(255,69,58,0.16)`) with soft red text.
  Never solid red except on a full-width CTA.
- **Disabled:** 38–45% opacity, shadow removed, hover suppressed.

### Cards / Containers
- **Corner Style:** 16px, 18px for stat tiles.
- **Background:** Card Graphite (`#1C1C1E`) on black. No border.
- **Shadow Strategy:** None. See Elevation.
- **Internal Padding:** 18px.

### Grouped Lists
- Rows are 52px minimum, padded `10px 16px`, separated by half-pixel
  hairlines. The list itself is a 14px-radius Card Graphite block with
  `overflow: hidden` so rows clip to the corners.
- **The Last Hairline Rule.** Hairlines go *between* rows and never after the
  final one. A trailing divider is the single clearest giveaway that a list
  was built to look like iOS rather than built correctly.

### Inputs / Fields
- **Style:** Raised Slate fill, transparent 1px border, 10px radius,
  `9px 12px` padding, 15px text.
- **Focus:** Border becomes Signal Blue; a separate `:focus-visible` ring in
  Signal Blue Lit at 2px with 1–2px offset.
- **Keyboard only.** The focus ring is `:focus-visible`, never `:focus` — a
  volunteer clicking through a console should not have a box following their
  mouse.

### Navigation
- 40px-tall rows in the rail, 8px radius, 14px text. Hover is a 6% white
  wash; the active item takes a Raised Slate background, 500 weight, and
  turns its icon Signal Blue. The brand mark is a 30px blue squircle.

### Status Pills, Badges and Marks
- **Status pill:** 32px tall, fully round, 13px/600 text with a 7px dot in
  `currentColor`. Four variants: neutral, ok, warn, bad — each a wash
  background with the matching saturated text colour.
- **Mark:** A 26px filled circle carrying a tick, a cross or nothing — the
  preflight checklist's verdict glyph. Idle grey until a check has run.

### The Composer
A single 26px-radius capsule holding a borderless text field and a 38px round
blue send button, 52px tall overall. It is the one place in the console where
a control and its field are drawn as one object, because typing a caption by
hand is a single gesture.

### The Caption Stage
The system's signature. A 22px-radius black panel over a radial gradient,
with lines bottom-aligned and stacked upward: spent lines in Text Spent, the
live line in white at Headline weight. A 13px footer strip
(`rgba(28,28,30,0.85)`) carries the live dot and output metadata.

- **The No-Fault-On-Air Rule.** The wall and any stage representing it never
  display a spinner, an error, a placeholder or a reconnect message. When
  something breaks, the bar empties. A dark rectangle reads as "nothing is
  being said"; a red error reads as "this organisation is broken", eight feet
  tall, behind a speaker.

### Motion
Transitions are short and unfussy: 180ms ease for state changes, 200ms for
opacity, 90–110ms linear for meters tracking live audio. The wall's caption
bar is deliberately asymmetric.

- **The Fast-In, Slow-Out Rule.** The caption bar fades in over 80ms and out
  over 600ms. The first words after a pause must not ramp up from invisible,
  but a bar that vanishes abruptly reads as a fault.
- `prefers-reduced-motion` collapses every duration to 0.01ms system-wide.

## Do's and Don'ts

### Do:
- **Do** keep Signal Blue to one action per screen. If a second element wants
  to be blue, make it a Raised Slate pill instead.
- **Do** use `#000000` on anything the audience sees. Black is *off* on a
  projector, not a dark grey.
- **Do** separate surfaces by lightness before reaching for a border.
- **Do** round anything pressable that is wider than it is tall, fully.
- **Do** put hairlines between list rows and never after the last one.
- **Do** size audience text in `rem` or `vh`, and leave the reader's size,
  colour and typeface controls intact.
- **Do** let audience surfaces fail silently and empty.
- **Do** use `:focus-visible` for focus rings, never `:focus`.
- **Do** keep green, amber and red for machine state only.

### Don't:
- **Don't** drift toward a generic SaaS dashboard: no purple or multi-hue
  gradients, no pastel chart palettes, no rounded-everything, no spot
  illustrations. This system has one gradient and it is under the captions.
- **Don't** drift toward legacy AV software either: no grey chrome, no
  beveled toolbars, no 11px labels, no nested menu trees. Density is achieved
  with lightness and spacing, not with smaller text.
- **Don't** introduce an elevation scale. Three shadows exist, each with one
  job.
- **Don't** use Text Spent (`#6E6E73`) for ordinary secondary text — it is the
  already-read caption line and nothing else.
- **Don't** borrow the caption palette (amber, green, blue, red at caption
  weights) as brand or accent colour. Those are reader accessibility choices.
- **Don't** draw a sharp corner anywhere. Even the 3–6px radii in this system
  (the audio meter, status dots, the focus ring) are half their element's
  height — they are capsules and circles, not small rectangles.
- **Don't** show a spinner, error state, toast or placeholder on a surface an
  audience can see.
- **Don't** swap Atkinson Hyperlegible for a visually similar face. It is
  offered for a reason that is not aesthetic.

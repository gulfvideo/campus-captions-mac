# ProPresenter integration spike (N-04) — test plan

For whoever has ProPresenter in front of them. Everything on the relay side is
built and tested against a fake ProPresenter API (`tests/fake_propresenter.py`);
what is **not** known is how the real application behaves, and that is what
this plan finds out. Write the answers into the "Findings" section at the
bottom and commit them - the requirements (N-04) want a written decision.

Target: ProPresenter 21 on macOS.

## 0. Setup (10 minutes)

1. ProPresenter: **Settings → Network**. Tick **Enable Network**. Note the
   port. Leave everything else alone.
2. Relay: run Campus Captions on the same Mac (or any machine on the LAN;
   then use the Mac's IP as the address). Open the Settings page.
3. Settings → **ProPresenter (direct)** panel: address `127.0.0.1` (or the
   Mac's IP), the port from step 1, surface **Stage message**. Save. Press
   **Test connection**. The badge should read **Connected** and the line under
   it should name ProPresenter, the platform and the API version.
   - If it says **Disconnected**: check the port, check that Enable Network
     is ticked, check the Mac firewall.
   - If it says **Unsupported version**: note the API version it reports
     (that is finding F1) and carry on; it may still work.
4. Keep the Web Fill prop (SETUP.md section 7) in place. It is the fallback
   and the comparison.

## 1. Stage Message surface (20 minutes)

1. In ProPresenter, make a **stage layout** with a Stage Message element,
   large, near the bottom. Assign it to a stage screen you can see. If you
   can, also route that layout to an audience/output screen so we learn
   whether Stage Message can reach the congregation at all (finding F2).
2. Settings page: **Enable**. Open the ASR stream with the mock backend
   (free) or a real microphone, and set the event live.
3. Watch for five minutes. Record:
   - F2: Does the caption appear on a stage screen? On an audience screen?
   - F3: Does the text update **in place** without a flash, redraw or
     slide-style transition? (The status line "adds N ms (p95 M ms)" is the
     round trip the relay measures; note both numbers.)
   - F4: Long lines: does the text wrap, shrink, or get cut? Say or send:
     *"For God so loved the world that he gave his one and only Son, that
     whoever believes in him shall not perish but have eternal life
     (John 3:16)."*
   - F5: Unicode: send a hand caption `Música y aplausos — ¡gracias!` and,
     if translation is on, switch the panel's language to `es`.
4. Press **Clear captions** on the status page. Does the stage message clear?
   Pause the event. Does it clear (it should)?
5. Tick **Send in-progress text too**, Save, reopen the stream. Watch for two
   minutes. F6: is the word-by-word update readable or distracting? Does
   ProPresenter keep up (the relay only ever sends the newest interim)?

## 2. Message surface (20 minutes)

1. In ProPresenter, create a **Message** named exactly `Live Captions` with
   one text token named exactly `Caption`. Give it a theme: bottom third,
   dark bar, white text, a size you would use for a real service. Set which
   screens it shows on (audience, livestream output).
2. Settings page: surface **Message with a text token**, Save, **Test
   connection**. Badge should read Connected. If it says the Message or the
   token was not found, the names differ - fix in ProPresenter or the panel.
3. Enable, open the stream, watch for five minutes. Record F2 to F6 again for
   this surface. Specifically:
   - F7: Does re-triggering the Message with new token text update the shown
     text smoothly, or does it re-animate (fade/slide) on every line?
   - F8: Does triggering the Message change the current slide, the
     Announcement layer, or anything else? Advance a few slides while
     captions are flowing - do slides and captions coexist?
4. **Clear captions**, pause the event: does the Message hide?

## 3. Failure behaviour (15 minutes)

With whichever surface looked better:

1. Quit ProPresenter while captions are flowing. The panel should go
   **Disconnected — fallback** within about ten seconds; phones and the Web
   Fill prop must keep working. F9: did anything else break?
2. Start ProPresenter again. The panel should return to **Connected** on its
   own (it probes every few seconds), and captions should resume. F10: how
   long did that take? Did the surface need re-selecting in ProPresenter?
3. Untick Enable Network in ProPresenter, wait, tick it again. Same questions.
4. Switch ProPresenter to a different workspace/library. Does the Message
   still exist for the API (Message surface)? Does the stage layout persist?

## 4. The 60-minute soak (the actual N-06 requirement)

Run a real or mock service for 60 continuous minutes with normal slide
changes, at least one motion background, the livestream encoder running if
the church uses one, and one audience output active.

Record at the end from the Settings page's ProPresenter line:
- sent, failed
- adds N ms (p95 M ms) — N-06 asks for p95 under 500 ms

And from the status page's Captions tile: latency median and p95 (that is
speech to relay, independent of ProPresenter).

Note any visual glitch with a timestamp so it can be matched to the relay log.

## Findings

Fill in and commit.

| # | Question | Answer |
|---|---|---|
| F1 | ProPresenter version, platform, API version reported | |
| F2 | Stage Message reaches: stage screen / audience screen / neither | |
| F3 | Update behaviour and measured round trip (median, p95) | |
| F4 | Long line handling | |
| F5 | Unicode and translated text | |
| F6 | Interim (word-by-word) updates: usable? | |
| F7 | Message re-trigger: in place or re-animated | |
| F8 | Any effect on slides, announcements, props, macros | |
| F9 | Quit ProPresenter mid-service: what else broke | |
| F10 | Recovery time after restart; manual steps needed | |
| — | **Recommended surface for N-05** and why | |
| — | 60-minute soak: sent/failed, added latency p95, glitches | |

# SETUP

Everything needed to put captions on the wall, in the order you will need it.
Sections 1–4 are one-time setup on a given machine. Sections 5–8 are per event.
Section 9 is the checklist to run before doors open.

---

## 1. First-time install

On the machine that will run the captions (the "relay host" — usually the same
booth machine that runs the capture, and it may be the ProPresenter machine or
a separate one):

**macOS** (the booth machine):

1. Install **Python 3.11 or newer**. The Python that ships with macOS is too
   old. Either the installer from [python.org](https://www.python.org/downloads/)
   or `brew install python@3.12`.
2. Get the project onto the machine with **`git clone`**, not by downloading a
   zip. A downloaded folder is quarantined by Gatekeeper and macOS will refuse
   to run the launcher. (If it is already quarantined:
   `xattr -dr com.apple.quarantine /path/to/campus-captions`.)
3. Double-click **`Start Captions.command`**.

The first time you run it, macOS will ask for **Microphone** permission — the
prompt names Terminal, since that is what launches it. Say yes. If you miss it,
see the microphone note in section 5.

**Windows:**

1. Install **Python 3.11 or newer** from python.org. During setup, tick
   **"Add Python to PATH"**. Tested on 3.14.6.
2. Copy or clone the `campus-captions` folder onto the machine.
3. Double-click **`Start Captions.bat`**.

The first run creates a virtual environment and installs dependencies — about a
minute, once. Every later run starts in a couple of seconds.

You never need to activate anything, type a command, or check a port. If the
window shows `--- ready ---` and a list of URLs, it worked.

To stop everything: close that window.

### What the launcher does

Starts the relay, waits for it to answer, starts the capture client, opens the
status page in your browser, and watches both. If either process dies it is
restarted automatically (up to five times, then it gives up and says so).

---

## 2. Configuration file

Copy `config/event.example.toml` to **`config/event.toml`** and edit that. The
real file is gitignored so machine-specific settings never travel with the code.

```toml
[relay]
host = "0.0.0.0"      # all interfaces - leave this alone or phones cannot connect
port = 8000
source_token = ""     # optional shared secret; empty is fine on a booth LAN

[capture]
backend = "mock"                                   # mock | deepgram
device = ""                                        # index or part of a name
relay_url = "ws://127.0.0.1:8000/ws/source"
start_open = false                                 # leave false: see section 6

[deepgram]
model = "nova-3"
language = "en-US"
keyterms_file = "config/keyterms.txt"
endpointing = 300
```

`backend = "mock"` is the safe default — it plays a scripted transcript and
costs nothing. Switch to `"deepgram"` when you want real captions.

---

## 3. The Deepgram API key

The key lives in **`.env`** in the project folder, and nowhere else. It is
gitignored, never written to a config file, never logged, and never sent to the
relay or to any viewer.

1. Copy `.env.example` to `.env`.
2. Put the key on the `DEEPGRAM_API_KEY` line:

```
DEEPGRAM_API_KEY=your_key_here
```

3. Save. The capture client picks it up at startup.

If the key is missing, the capture client refuses to start on the Deepgram
backend and tells you exactly this. It does not fall back silently.

---

## 4. Network: static IP and firewall

The wall view URL and the phone URL both contain the relay host's IP address.
If that address changes, ProPresenter's Prop breaks and every printed phone URL
is wrong.

**Give the relay host a fixed address.** Either a static IP or — better on a
campus network — a DHCP reservation for its MAC address, arranged with IT. Do
this once and write the address down.

**Open the port inbound.** The symptom of not doing this: the status page works
on the booth machine itself, but phones and other machines cannot connect.

On **macOS**, the firewall prompts the first time the relay starts — a dialog
asking whether `python` may accept incoming network connections. Click **Allow**.
If it was denied, fix it in System Settings > Network > Firewall > Options: find
the Python entry and set it to allow incoming connections, or remove the entry
so the prompt comes back.

On **Windows**, in an Administrator PowerShell, once:

```powershell
New-NetFirewallRule -DisplayName "Campus Captions relay" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Domain,Private
```

Requirements to give IT if you need to ask:

- one inbound TCP port (8000 by default) on the relay host
- viewers and the relay must be on the same LAN/VLAN, or routed to each other
- **client isolation must be off** on the wifi the phones use, or phones cannot
  reach the relay at all — this is the single most common campus-wifi failure
- no outbound internet access is needed by the relay or the viewers
- the capture client needs outbound HTTPS/WSS to `api.deepgram.com:443`, and
  only when the Deepgram backend is running

Everything else is self-contained: the viewer pages load no fonts, scripts or
images from the internet.

---

## 5. Audio input device

Device indices are **not** stable. Windows lists the same physical microphone
several times, once per host API (MME, DirectSound, WASAPI, WDM-KS), so index 3
on your laptop is something else entirely in the booth — and they renumber when
hardware is plugged or unplugged, on the *same* machine. macOS has one host API
(Core Audio) so the list is shorter, but the numbering still shifts with USB
devices.

See what a machine actually has:

```
# macOS
.venv/bin/python -m capture --list-devices

# Windows
.venv\Scripts\python.exe -m capture --list-devices
```

```
 idx  name                                    host api            in    rate
   3  Microphone (Yeti Classic)               MME                  2   44100
  33  Microphone (Yeti Classic)               Windows DirectSound  2   44100
  78  Microphone (Yeti Classic)               Windows WASAPI       2   48000
```

**Select by name, not by index.** In `config/event.toml`:

```toml
device = "Yeti"          # any part of the name, case-insensitive
```

A name that matches several entries resolves to the WASAPI one (the modern,
lowest-latency host API on Windows), and the log line tells you which it picked
and what the alternatives were:

```
[audio] 'Yeti' matched 4 devices, using 78 [Windows WASAPI] (also 33:..., 3:..., 122:...)
```

An index still works if you want one specific entry: `device = "78"`. Leaving it
empty uses the system default input.

The sample rate is whatever the device opens at — 16 kHz if it supports it,
otherwise its own rate, typically 48 kHz. There is no resampling; the real rate
is reported to the ASR service.

### macOS microphone permission

macOS gates microphone access per application. When permission is missing the
process is not given an error — it is handed **a stream of perfect silence**,
which looks exactly like a muted microphone or a dead cable.

The status page tells these apart. Real silence still carries a noise floor, so
if every sample is exactly zero for five seconds the Audio tile says:

> no signal at all from this device — on macOS grant Microphone access in
> System Settings > Privacy & Security > Microphone, then restart

Fix it in **System Settings > Privacy & Security > Microphone** and enable the
app that launches the system — **Terminal** if you use `Start Captions.command`.
The warning clears itself as soon as real audio arrives; no restart of the
capture client is needed once permission is granted.

**For a live event, take the feed from the sound desk**, not from a laptop's
built-in microphone: a mix-minus or matrix output into a USB interface. Room
mics pick up the PA and the audience and the transcript suffers badly.

---

## 6. Running an event

Open the status page — **`http://<relay-ip>:8000/status`**. It is the only page
you need. Four tiles:

| Tile | Green means |
|---|---|
| **Audio input** | speech is arriving from the device |
| **Captions** | the backend is producing text |
| **ASR stream** | the connection is open — **and billing** |
| **Viewers** | how many wall and phone views are connected |

**The ASR stream starts closed.** Audio levels flow immediately, so you can
check the microphone and set levels with nothing running up a bill. Deepgram
charges for connection time, not speech time, which means an open stream during
a 90-minute rehearsal costs the same as 90 minutes of the event.

So the running order is:

1. Start the system when you get into the booth. Audio tile goes green as soon
   as someone speaks into the mic. Stream stays closed.
2. Frame the wall view in ProPresenter, sort out levels, rehearse. Still closed,
   still costing nothing.
3. A minute before the event starts, press **Open ASR stream**.
4. At the end, press **Close ASR stream**. Billing stops there.

**Clear captions** blanks the wall immediately — use it between segments, or if
something is transcribed that should not stay on screen. It also empties the
replay buffer, so someone joining afterwards does not see the cleared text.

**Reload keyterms** re-reads `config/keyterms.txt` mid-event (section 8).

---

## 7. The wall view in ProPresenter

The wall view is a transparent page with an opaque caption bar near the bottom.
It composites over camera and slides; everything outside the bar is transparent.

### Adding it as a Prop

1. **Props** → add a new Prop.
2. Add an object to the prop and size it to the **full canvas** — the page
   positions the caption bar itself, in screen-relative units, so the web
   element must cover the whole output or the maths is wrong.
3. Set the object's **Fill** to **Web**.
4. Paste the URL: `http://<relay-ip>:8000/wall`
5. Name the prop something like `Captions` and save it.

Trigger the prop to put captions on the output; clear it to take them off.
Because the page is transparent, the prop can sit enabled for the whole event.

### Notes that will save you time

- **Changing a query parameter needs the prop re-triggered.** ProPresenter keeps
  the page loaded. Edit the URL, then toggle the prop off and on. The pages are
  served with no-cache headers so you always get the current file.
- **Nothing ever appears except caption text.** No spinner, no "connecting", no
  error. If the relay is down, the page is simply blank and keeps retrying
  forever. That is deliberate: an error message on an LED wall in front of an
  audience is much worse than no captions.
- **A dropped connection leaves the last line up**, then fades it out. It never
  blanks abruptly, and it never says why.
- If the connection stays dead for two minutes, the page reloads itself.

### Framing and sizing

**The easy way: the tuner.** Open `http://<relay-ip>:8000/tune` (there is a link
on the status page). Sliders for text size, position, bar width, opacity and
colours, with a live preview of the wall over a choice of backgrounds, and the
finished URL in a box to copy straight into the ProPresenter Prop.

Because every size is a percentage of the output, the preview is accurate at any
window size — pick your wall's shape from the **Output shape** menu and what you
see is what the LED gets. It warns you when a line is too long to fit, and the
**Width ruler** button fills the sample with the widest possible characters so
you can check the worst case.

The tuner also saves named presets — `Chapel`, `Auditorium` — in that browser,
so you can flip between venues. The URL itself is the portable version: that is
what you paste into ProPresenter, and the Prop is what stores it for good.

The tuner only builds a URL. It never touches the live wall, so it is safe to
use during an event.

**The manual way.** Use `?text=` to put sample text on the wall so you can size
and position it against the physical LED:

```
http://<relay-ip>:8000/wall?text=The quick brown fox jumps over the lazy dog
```

A width ruler — 42 W's, the widest common character, at the default line length:

```
http://<relay-ip>:8000/wall?text=WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW
```

If that overflows the bar, lower `maxchars` or `size`. `?text=` opens no
connection at all, so you can do this with nothing else started.

### Query parameters

All tunable from the URL — no rebuild, no redeploy. The bare URL with no
parameters works.

| Param | Default | Effect |
|---|---|---|
| `size` | `6.5` | font size, **vh** (percent of output height) |
| `bottom` | `9` | bar offset up from the bottom edge, vh |
| `width` | `86` | bar width, vw — fixed, so the bar never changes width |
| `pad` | `2` | bar padding, vh |
| `radius` | `1.5` | corner radius, vh |
| `opacity` | `0.85` | bar opacity |
| `bar` | `000000` | bar colour, hex, no `#` |
| `color` | `ffffff` | text colour, hex, no `#` |
| `idle` | `5` | seconds of no new text before the bar fades out; `0` = never |
| `fade` | `600` | fade duration, ms |
| `maxchars` | `42` | characters per line |
| `watchdog` | `120` | seconds dead before the page reloads itself |
| `debug` | off | diagnostics overlay, top left |
| `text` | — | static sample text, opens no connection |
| `soak` | — | synthetic captions for long-run testing, e.g. `?soak=1` |
| `relay` | — | point at a different relay, e.g. `relay=10.0.4.12:8000` |

Combine with `&`:

```
http://<relay-ip>:8000/wall?size=7.2&bottom=11&maxchars=38&opacity=0.9
```

Everything is in vh/vw, never pixels, so it scales to whatever resolution the
LED processor is actually running.

**`maxchars` and `size` are linked.** A line is one line — it is clipped, never
wrapped, because wrapping would reflow the graphic on air. At the defaults about
42 characters fit. If you raise `size`, lower `maxchars`. Add `&debug=1` and the
overlay prints `*** TEXT CLIPPED ***` when a line does not fit, which is the
fastest way to tune the pair against the real wall.

### Layout behaviour

Two lines, always. Space for both is reserved even when only one has text, so
the bar never jumps between one- and two-line states. New text pushes the older
line up and out. In-progress text rewrites the bottom line in place; only a
completed sentence commits a line and only a completed sentence causes a line
break.

---

## 8. Keyterms

`config/keyterms.txt` — one term per line, `#` for comments, blank lines ignored.
Edit it per event; no code changes.

```
# --- people ---
Amara Vasquez
Dean Whitfield

# --- places ---
Whitfield Science Center
Halvorsen Chapel

# --- programs ---
baccalaureate
convocation
```

Put in what the model will otherwise get wrong: people's names, building names,
program names, local jargon, hymn and book titles. Ordinary English words do not
need to be there. A few dozen well-chosen terms beat hundreds of speculative
ones. The cap is 200.

Terms are sent to Deepgram when the stream opens. To change them mid-event, edit
the file and press **Reload keyterms** on the status page — that reopens the
stream with the new list, so captions stop for about a second. Do it between
segments, not mid-sentence.

Keyterm prompting is a Nova-3 feature. On an older model the same file is sent
as keyword boosting instead, automatically.

---

## 9. Pre-event checklist

Roughly 30 minutes before doors.

**Setup**

- [ ] Relay host is on, on the network, at its expected IP address
- [ ] Launcher run (`Start Captions.command` on Mac, `.bat` on Windows) —
      window shows `--- ready ---`
- [ ] Status page open in the booth: `http://<relay-ip>:8000/status`
- [ ] `config/event.toml` has `backend = "deepgram"` for a real event
- [ ] `.env` has the Deepgram key — the capture client says
      `DEEPGRAM_API_KEY found` at startup

**Audio**

- [ ] Correct device in the log line: `[capture] input: ...`
- [ ] Someone talks into the mic — **Audio input** tile goes green
- [ ] Meter sits mid-range on normal speech, not pinned at either end

**Wall**

- [ ] Captions prop triggered in ProPresenter, sized to full canvas
- [ ] `?text=` sample framed and sized against the physical wall
- [ ] URL back to the plain `/wall` (no `?text=`) for the event
- [ ] Confirm on the actual LED, not just the ProPresenter preview

**Phones**

- [ ] `http://<relay-ip>:8000/phone` opens on a phone on the venue wifi
- [ ] URL is printed / on a slide / on a QR card where the audience can see it

**Go live**

- [ ] Press **Open ASR stream** about a minute before the start
- [ ] **Captions** tile goes green and text appears on the wall
- [ ] Watch the **Viewers** count climb as the audience joins

**Afterwards**

- [ ] Press **Close ASR stream** — this is what stops the billing
- [ ] Close the launcher window

---

## Troubleshooting

| What you see | What it is |
|---|---|
| Wall is blank, everything else fine | The relay is unreachable from ProPresenter, or the prop is not triggered. Check the viewer count on the status page — if it is 0, ProPresenter never connected. Open the URL in a browser on that machine. |
| Status page: **No capture client** | The capture client is not running or cannot reach the relay. The launcher restarts it automatically; check its window for an error. |
| Status page: **Silent** with the mic live | Wrong input device, or the device is muted in Windows. Run `--list-devices` and set `device` by name. |
| Status page: **Open, no text yet** | The stream is connected but nothing is being transcribed — usually silence, wrong input, or a mic that is muted at the desk. |
| Phones cannot connect, booth machine can | Firewall (section 4) or wifi client isolation. |
| Captions stop mid-event | The backend reconnects on its own with backoff. The wall holds the last line, then fades. If the **ASR stream** tile went red, look at the error text on the status page. |
| Wrong names in the transcript | Add them to `config/keyterms.txt` and press **Reload keyterms**. |
| Everything is fine but text is clipped | `maxchars` too high for `size`. Add `&debug=1` to the wall URL to confirm. |

For the message format between the pieces, see `docs/MESSAGES.md`. For
development against the mock backend, see `README.md`.

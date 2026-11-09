# SETUP

Everything needed to put captions on the wall, in the order you will need it.
Sections 1–4 are one-time setup on a given machine. Sections 5–8 are per event.
Section 9 is the checklist to run before doors open.

---

## 1. First-time install

This goes on the machine that will run the captions (the "relay host" — usually
the same booth machine that runs the capture, and it may be the ProPresenter
machine or a separate one).

**You do not need Python, a terminal, or a copy of the source code.** Go to
[Releases](https://github.com/koakley3/campus-captions/releases) and take the
download for your operating system.


### macOS

One download, **`CampusCaptions-<version>-AppleSilicon.dmg`**, for Macs with
an Apple M1 or later chip. Apple menu → About This Mac says which chip you
have; Intel Macs are not supported.

1. Double-click the `.dmg`. A window opens with the app and a shortcut to
   Applications.
2. Drag **Campus Captions** onto **Applications**.
3. **Control-click** it in Applications and choose **Open**, then confirm.
   (Double-clicking the first time gives an "unidentified developer" refusal
   with no way past it. Once per Mac.)
4. macOS asks for microphone access, and for speech recognition if you use the
   on-device backend. Allow both, or there is nothing to caption.

It runs as a graduation-cap icon in the **menu bar** at the top of the screen.
While it is running the Mac will not go to sleep (closing the lid still sleeps it).

### The setup wizard

The first time it runs, Campus Captions opens a **setup wizard** in your
browser. Four steps, about two minutes:

1. **Microphone** — pick the input carrying the room, and watch the level
   meter move while someone talks. This is the step that catches "we
   captioned the laptop's built-in mic all night".
2. **Captions** — pick how speech is turned into text. Only the options this
   computer can actually run are listed, and each says whether it costs money.
   See sections 3, 3a and 3d for what to pick and why.
3. **Event** — the title and accessibility notice the audience sees on their
   phones before the program starts. Both optional.
4. **Done** — the wall, phone and status addresses to hand out.

Everything it sets can be changed afterwards on the **Settings** page, and
**Run setup again** in that page's top-right corner reopens the wizard.

### The guided tour

When the wizard finishes, Campus Captions shows you round. It dims the page,
draws a ring around one control at a time and says what that control is for —
the listening switch, the level meter, the two addresses to hand out, the
sliders in the tuner, the settings worth knowing about. Twenty-three steps
across three pages, a couple of minutes, and **Skip** at any point.

Nothing in the tour changes a setting: it opens panels and switches tabs to
show you things, and that is all.

**Take the tour** in the top corner of the status, tuner and settings pages
runs it again whenever you want it — before handing the booth to a new
volunteer, or when you come back to it after a term away.

After the first run, Campus Captions goes straight to the status page instead.

### Running from source instead

For development, or on a machine where an installer cannot be run:

1. Install **Python 3.11 or newer** from python.org, or `brew install
   python@3.12`. Tested on 3.14.6.
2. Copy the whole `campus-captions` folder onto the machine.
3. Double-click **`Start Captions.command`**.

The first run creates a virtual environment and installs dependencies — about a
minute, once. Every later run starts in a couple of seconds. If the window
shows `--- ready ---` and a list of URLs, it worked. To stop everything, close
that window.

The setup wizard runs here too, on the same first-run rule.

### What the launcher does

Starts the relay, waits for it to answer, starts the capture client, opens the
status page in your browser, and watches both. If either process dies it is
restarted automatically (up to five times, then it gives up and says so). The
installed app does the same thing inside one process — see
`launcher/embedded.py`.

### Its own window, or a browser tab

Campus Captions is a web app that behaves like a program: by default the
status page opens in **its own window** with no address bar and no tabs, and
gets its own entry in the taskbar or Dock. Switch it to an ordinary browser
tab under **Settings > System > How Campus Captions opens**; the choice is
remembered.

This needs Chrome, Edge, Brave or Chromium somewhere on the machine — it is
their `--app` mode, not an extra download. On a computer with only Firefox or
Safari the pages open in a tab whichever option is selected, and the Settings
page says so rather than offering a choice that does nothing.

The app window keeps its own browser profile, so it does not inherit
extensions, a restore-tabs prompt, or anything else from everyday browsing.

### Where your settings live

Not inside the app. Settings, the API keys and the logs go in a per-user
folder, so an upgrade keeps them and nothing secret travels with a copy of the
installer:

| | |
|---|---|
| Installed app | `~/Library/Application Support/Campus Captions` (logs in `~/Library/Logs/Campus Captions`) |
| From source | the checkout itself |

The menu bar menu opens both folders for you.

---

## 2. Configuration file

**Most people never open this.** The setup wizard and the Settings page write
everything here for you. This section is for reference, and for the handful of
settings with no page of their own.

The installed app creates its `event.toml` on first run in the folder listed
under "Where your settings live" above. Running from source, copy
`config/event.example.toml` to **`config/event.toml`** and edit that — the real
file is gitignored so machine-specific settings never travel with the code.

```toml
[relay]
host = "0.0.0.0"      # all interfaces - leave this alone or phones cannot connect
port = 8000
source_token = ""     # leave empty: one is generated into .env on first start

[capture]
backend = "auto"          # auto | mock | deepgram | apple_speech | whisper
device = ""                                        # index or part of a name
relay_url = "ws://127.0.0.1:8000/ws/source"
start_open = false                                 # leave false: see section 6

[deepgram]
model = "nova-3"
language = "en-US"
keyterms_file = "config/keyterms.txt"
endpointing = 300

[apple_speech]
locale = "en-US"

[setup]
completed = true      # written by the setup wizard; false sends you back to it
```

`backend = "auto"` is what ships, and it means *whichever backend this
computer can actually run*: the Apple on-device backend on a Mac new enough
for it, and practice mode (`mock`, a scripted transcript that costs nothing)
everywhere else. The wizard replaces it with a real choice.

Naming a backend a machine cannot run is not fatal — it is substituted, and
the capture client says so — but the status page's picker only ever offers
what will work, so it is hard to end up there by accident.

| | |
|---|---|
| `deepgram` | cloud, most accurate, learns your keyterms — section 3 |
| `apple_speech` | on-device, free, macOS 26+ only — section 3a |
| `whisper` | on-device, free, any OS, needs an extra install — section 3d |
| `mock` | a scripted transcript, for rehearsal |

---

## 3. The Deepgram API key

The key lives in **`.env`** in the project folder, and nowhere else. It is
gitignored, never written to a config file, never logged, and never sent to the
relay or to any viewer.

**From the Settings page (no text editor needed):** open the collapsed
**Deepgram API key** section (click the row to expand it), paste the key, and
press **Save**. This works while the capture client is already running — no
restart needed — and writes it into `.env` for next time too.

**By hand, if you'd rather:**

1. Copy `.env.example` to `.env`.
2. Put the key on the `DEEPGRAM_API_KEY` line:

```
DEEPGRAM_API_KEY=your_key_here
```

3. Save. The capture client picks it up at startup.

Either way, if the key is missing when the capture client starts up already
pointed at the Deepgram backend, it refuses to start and tells you exactly
that rather than falling back silently. Switching to Deepgram from the status
page's backend dropdown mid-session is gentler: it just refuses the switch
with the same message, leaving whatever was running untouched.

---

## 3a. The Apple on-device backend (macOS only)

`backend = "apple_speech"` runs recognition entirely on the Mac using Apple's
SpeechAnalyzer, the framework behind system dictation. No API key, no
per-minute cost, and after the first run, no network at all.

Requirements:

- **macOS 26 or newer.** On an older Mac this
  backend is not offered at all: it is absent from the setup wizard and from
  the status page's picker, and `backend = "apple_speech"` in a config file is
  substituted for one that works, with a line in the log saying so.
- **Xcode command line tools**, so `swiftc` and `codesign` exist. If they're
  missing, run `xcode-select --install` once.

The first time you open the ASR stream, two things happen automatically:

1. `capture/backends/native/speech_helper.swift` is compiled and ad-hoc signed
   (a few seconds). Later runs skip this unless the source changes.
2. macOS downloads the on-device speech model for `locale` in
   `[apple_speech]` if it isn't already installed — the one moment this
   backend needs internet. The status page shows this as it happens.

There is no keyterm/vocabulary boosting on this backend — that's a Deepgram-
only feature for now. Pick `deepgram` instead if the event has a lot of names
the model would otherwise get wrong.

---

## 3b. Pushing captions to YouTube Live

If the event is also being streamed to YouTube, Campus Captions can push the
same finalized captions there in real time, on top of everything else.

1. In YouTube Studio, on the live stream's page, open **Closed captions** and
   turn it on. Set **Captions source** to **POST captions to URL**. Copy the
   **Captions ingestion URL** it shows you (it looks like
   `http://upload.youtube.com/closedcaption?cid=xxxx-xxxx-xxxx-xxxx`).
2. On the Settings page, click the collapsed **YouTube Live captions** row to
   open it, paste the URL into the box, and press **Save**. No config file to
   open or edit — Save writes it into `config/event.toml` for you, so it's
   still there next time the relay starts. Collapsing it back afterward is
   fine: the badge next to its name keeps showing whether it's live and how
   many captions have been sent, even while closed.
3. Press **Enable** when the stream is live, **Disable** when it's not. The
   box is masked by default (press **Show** to check it) since whoever has
   that URL can post captions to the stream.

If a config-driven default is wanted instead — the same URL every event, or
starting enabled automatically — `[youtube]` in `config/event.toml` still
works by hand:

```toml
[youtube]
url = "http://upload.youtube.com/closedcaption?cid=xxxx-xxxx-xxxx-xxxx"
lang = "en-US"
start_enabled = false     # true = start pushing as soon as the relay starts
```

Two things about this that are YouTube's behavior, not this app's:

- **YouTube's captions don't support corrections.** Only finished lines are
  ever sent — never the interim, still-being-corrected text the wall and
  phone views show. This is automatic; there is nothing to configure.
- **Expect real delay**, beyond whatever the ASR backend itself adds. YouTube
  documents 15+ seconds of latency in its own captioning pipeline for other
  vendors. If you watch the stream through YouTube itself, the captions will
  look badly behind — that's expected, not a sign something is broken.

The ingestion URL is tied to that one stream and resets if you end and restart
it, so don't expect to reuse an old URL. It's not a secret the way the
Deepgram key is (there's no cost if someone else has it, just the ability to
post junk captions to the stream), so keeping it in `config/event.toml` rather
than `.env` is fine.

---

## 3c. Letting remote viewers connect (a tunnel)

The phone view normally only works on the venue's own wifi, because the relay
is only reachable at a LAN IP. To let someone outside that network view
captions on a domain you own, expose the relay through a Cloudflare Tunnel -
set up once on the Cloudflare side, then pasted into the Settings page here.

**One-time Cloudflare setup** (needs a free Cloudflare account with your
domain's nameservers pointed at Cloudflare - if the domain isn't on
Cloudflare yet, add it there first):

1. Install `cloudflared`: `brew install cloudflared` (or download it from
   [github.com/cloudflare/cloudflared/releases](https://github.com/cloudflare/cloudflared/releases)
   if you're not using Homebrew).
2. Go to [one.dash.cloudflare.com](https://one.dash.cloudflare.com) → **Networks
   → Tunnels → Create a tunnel** → choose **Cloudflared** → give it a name
   (e.g. "campus-captions").
3. On the **Public Hostname** tab, add the subdomain you want to use (e.g.
   `captions.yourdomain.example`), type **HTTP**, and URL `localhost:8000`.
4. Copy the **token** shown in the install command on the next screen (a long
   string after `--token`) - that's the only thing to bring back to this app.

**In this app**, open `/settings`, expand **Public tunnel (Cloudflare)**, paste
the hostname from step 3 and the token from step 4, press Save, then Enable.
That's it - no restart, no editing `config/event.toml` by hand, no
`cloudflared login` or `tunnel route dns`. The panel shows Connected once the
tunnel is actually up; if `cloudflared` isn't installed it says so instead of
just hanging. This is `POST /api/tunnel` under the hood - see
`docs/MESSAGES.md` if you'd rather script it.

Enabling this sets `public_hostname` to whatever you entered, which is what
actually changes the relay's behavior: anyone reaching the relay through that
exact hostname only ever gets the phone view - `/status` and everything that
can change what's happening (opening/closing the stream, switching backends,
the Deepgram key field, the YouTube toggle) reject them with a plain 404, as
if those routes didn't exist, even with the operator token. The operator
dashboard stays usable from the booth LAN or localhost with the operator link
(section 6). Two guard rails: the tunnel will not auto-start at boot until a
hostname is set, and the hostname cannot be cleared while the tunnel is
enabled - press Disable first.

---

## 3d. The Whisper backend (any macOS version)

`backend = "whisper"` runs recognition entirely on-device via faster-whisper -
no API key, no per-minute cost, and no macOS 26 requirement (unlike
`apple_speech`).

**Whisper needs its speech model downloaded once**, a few hundred megabytes.
The installed app ships the Whisper software but not the model, so there is a
**Download the model** button on the setup wizard's captions step and under
**Settings > Captions > Whisper speech model**. Press it once, on a machine
with internet; after that Whisper runs with no internet at all. It is never
downloaded automatically - a few hundred megabytes starting by itself the
first time someone opens the stream, minutes before a service, is exactly the
surprise this avoids. The model lands in the app's own data folder (see
"Where your settings live"), so it survives updates.

Running from source, install its extras once as well:

```bash
.venv/bin/python -m pip install -r requirements-whisper.txt
```

If you pick Whisper on the status page and "Open ASR stream" never actually
opens (the ASR STREAM card stays on "Closed"), this is almost always it -
check the error banner at the top of the page, or `logs/capture.log` (see
Troubleshooting), for `ModuleNotFoundError: No module named 'faster_whisper'`.

The model (`[whisper] model`, default `base.en`, ~150MB) downloads on first
use and is cached; for a machine with no internet at all, download it
elsewhere and point `model_dir` at the cache. Expect roughly a second more
latency than Deepgram, and less stable interim text - it's a good fit for
demos, rehearsal, or a venue with no internet, not a first choice when
Deepgram or Apple on-device are both available.

Whisper can also loop on ambiguous audio, repeating one phrase until the
buffer commits. Two guards run against that: faster-whisper's own temperature
fallback, which re-decodes when a result comes back too repetitive to believe,
and a pass over the finished text that trims a phrase repeated four or more
times back to two. Ordinary repetition is left alone - people stammer, and
"I mean, I mean" is something a deaf reader is entitled to see. If you do see
a loop reach the wall, it is worth reporting with the `.srt`.

---

## 3e. Multiple languages and live translation

Optional. Open `/settings`, expand **Translation**, pick a provider, list the
languages to translate into (e.g. `es, fr, zh-Hans`), and press Save. No
restart needed, even mid-event.

**Apple, on this Mac** is the default: free, offline, nothing leaves the
machine, no account. It needs macOS 26. A language the Mac has not downloaded
yet is added under **System Settings &rarr; General &rarr; Language & Region
&rarr; Translation Languages**; until then the Settings page names it as the
last error and the other languages still go out.

**Azure Translator** is the alternative when you want Microsoft's models:

1. Get a key: [portal.azure.com](https://portal.azure.com) &rarr; Create a
   resource &rarr; search "Translator" &rarr; Create (a free tier exists) &rarr;
   once it deploys, open the resource &rarr; **Keys and Endpoint** &rarr; copy
   **Key 1** and the **Region**.
2. Choose Azure in the Settings page, paste the key (and region, if shown),
   and press Save.

The key goes into `.env` (gitignored) and the provider and language list into
`config/event.toml`, so all of it survives a relay restart. Under the hood this
is `POST /api/translation` - see `docs/MESSAGES.md` if you'd rather script it.

Prefer hand-editing the config? The same settings live in `config/event.toml`:

```toml
[languages]
source = "en"
offer = ["es", "fr"]     # languages to translate into

[translate]
provider = "apple"       # "apple", "azure" or "none"
```

and `.env`:

```
AZURE_TRANSLATOR_KEY=your_key_here
AZURE_TRANSLATOR_REGION=eastus     # only for a regional resource
```

Every **finished** caption line gets translated into each offered language
(never in-progress lines - they'd multiply the bill roughly twentyfold for
text that rewrites itself faster than anyone can read). The phone view grows
a language button once there are two or more working languages; each is an
independent stream, so someone who picks Spanish partway through still sees
Spanish history on reconnect, not English. If Azure is unreachable or the key
is wrong, captions keep working in the source language only - translation
failing never takes down the event.

---

## 3f. QR code and public site info

`http://<relay-ip>:8000/qr` is a full-screen QR code pointing at the phone
view - put it in a ProPresenter slide or on a lobby screen. It automatically
points at your Cloudflare Tunnel domain if `public_hostname` is set (section
3c), or the LAN IP otherwise, so the same page works whether or not you're
running a tunnel.

---

## 3g. Recording a transcript and exporting to YouTube

The relay quietly records every finished caption line with timing, regardless
of whether live YouTube pushing (3b) is set up. On the status page, under
**Recording & transcript**:

1. Press **Mark video start** the moment the actual recording or stream
   begins - captions are timed from there, not from whenever the relay
   happened to start.
2. After the event, download the files for each language that was recorded.
   Each language gets a row of links: `.srt` or `.vtt` for a video's caption
   track, `.txt` for a readable transcript to share or paste into notes,
   `.json` and `.csv` for anything that wants the timing as data.
   Cues are already wrapped to two lines of 42 characters with readable
   durations. If they land a beat late against the video, add `&offset=0.8`
   to the download link to shift everything earlier by the recognizer's delay.
3. Optionally upload it to a YouTube video's caption track:

   ```bash
   .venv/bin/python -m pip install -r requirements-youtube.txt
   .venv/bin/python tools/youtube_upload.py --video VIDEO_ID --from-relay --lang en
   ```

   First run opens a browser for a one-time Google sign-in; the token is
   cached at `config/google_token.json` (gitignored) after that. Uploads as a
   draft by default - add `--publish` to publish immediately.

**Reset transcript** clears the recorded record (for a rehearsal you don't
want mixed into the real event's export) - it does not affect the live
captions on the wall or phone views at all.

---

## 3h. If port 8000 is already taken

Another program on the computer sometimes has port 8000 already - a
development server, a media tool, a second copy of something. Campus
Captions no longer stops for that. The launcher (or the menu bar app on a
Mac) starts on the next free port, 8001 or so, and tells you in the launcher
window or a Mac alert. Everything works; only the addresses differ.

That matters because the wall and phone URLs and the printed QR code have the
port in them. A ProPresenter prop, an OBS scene or a poster made for port 8000
will not reach a relay on 8001. Two ways out:

- Close the other program and start Campus Captions again. It goes back to
  8000 and the existing URLs work as before.
- Make the new port permanent: Settings page → **Network — relay port** →
  type the port → **Save and restart on it**. The relay restarts on that port
  straight away, the capture client follows on its own, the Settings page
  moves with it, and the next launch uses it too. Then update the prop, the
  scene and the QR code once.

---

## 3i. Updating an installed app

**Settings → App version → Check for updates.** Three buttons — check,
download, install — and nothing on a timer: an app that downloads 75 MB over a
venue's wifi and restarts itself mid-service is not something to do to an
operator. Installing is refused outright while the event is live.

On macOS the app replaces itself and reopens. Because it is currently ad-hoc
signed, **macOS will ask for microphone access again after an update** — the
app says so next to the Install button. A Developer ID certificate ends that;
see [docs/UPDATES.md](docs/UPDATES.md).

Publishing an update is a maintainer job and is documented separately in
[docs/UPDATES.md](docs/UPDATES.md).

## 4. Network: static IP and firewall

The wall view URL and the phone URL both contain the relay host's IP address.
If that address changes, ProPresenter's Prop breaks and every printed phone URL
is wrong.

**Give the relay host a fixed address.** Either a static IP or — better on a
campus network — a DHCP reservation for its MAC address, arranged with IT. Do
this once and write the address down.

**Open the port inbound.** A firewall may block it, which shows up
as: the status page works on the booth machine, but phones and ProPresenter on
another machine cannot connect.

On macOS, if prompted to allow incoming connections for Python, choose
**Allow**. You can review this later under **System Settings > Network >
Firewall > Options**.

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

Device indices are **not** stable between machines: they renumber when an
interface is plugged in, so index 3 on your laptop is something else entirely
in the booth.

See what a machine actually has:

```bash
.venv/bin/python -m capture --list-devices
```

```
 idx  name                                    host api            in    rate
   0  MacBook Pro Microphone                  Core Audio           1   48000
   2  Scarlett 2i2 USB                        Core Audio           2   48000
```

**Select by name, not by index.** In `config/event.toml`:

```toml
device = "Scarlett"      # any part of the name, case-insensitive
```

An index still works if you want one specific entry: `device = "2"`. Leaving it
empty uses the system default input.

You can also change the input after startup. Open the status page and choose an
input under **Audio input device**. The capture client briefly pauses an open
ASR stream, changes devices, and reopens the stream automatically with the new
sample rate. If an interface is connected after startup, press **Refresh
devices** first. The choice is remembered: it is written back to this machine's
`event.toml` by name, so the next launch comes up on the same input without
anyone re-picking it. The name rather than the index, because PortAudio
renumbers devices whenever something is plugged in. If that device is missing
next time — the interface stayed at the last venue — the app falls back to the
system default and says so on the status page rather than starting deaf.

The same status page has a **Transcription backend** dropdown, next to the
device picker, for swapping backends without restarting anything. It lists
only what this computer can run, so `apple_speech` is simply absent on a PC. Switching pauses an open stream and reopens it on
the new backend, the same way changing the audio device does. Deepgram only
appears usable if `DEEPGRAM_API_KEY` is set — picking it without a key is
refused and the running backend is left untouched. This runtime choice also
lasts until the program is closed; put `backend = "..."` in `event.toml` if it
should be the default next time.

The sample rate is whatever the device opens at — 16 kHz if it supports it,
otherwise its own rate, typically 48 kHz. There is no resampling; the real rate
is reported to the ASR service.

**For a live event, take the feed from the sound desk**, not from a laptop's
built-in microphone: a mix-minus or matrix output into a USB interface. Room
mics pick up the PA and the audience and the transcript suffers badly.

---

## 6. Running an event

Open the status page from the link the launcher window prints —
**`http://<relay-ip>:8000/status?token=…`** — or from the menu bar app's
**Open Status Page**. That link signs the browser in for a month; after that
plain `http://<relay-ip>:8000/status` works in the same browser, and so does
`http://<relay-ip>:8000/settings` once you need it - the same sign-in covers
both. Opening either without the token shows a short page saying where to
find the link, not the dashboard: anyone the QR code let onto the wifi can
reach the same address. The token is `CAPTIONS_OPERATOR_TOKEN` in `.env`,
generated on first start.

The status page is what you watch during the event: four status tiles, the
backend/device pickers, the stream buttons, and the event state. Everything
you set up once before doors open - API keys, YouTube, the tunnel, keyterms
and the blocklist, the welcome-screen wording and photo - lives on the
**Settings** page instead (linked from the top right of the status page), so
the page you're glancing at all night isn't crowded with things you won't
touch again until the next event.

**Before the doors open**, go to Settings and expand **Event & welcome
screen**. Type the event title, an optional welcome message, and a notice
line - the accommodation contact belongs here, and so does "Captions are
automatic" - then press Save; optionally choose a background photo, and a
logo to sit above the captions while they are live (a transparent PNG, with
a description typed in so screen readers announce the name). Anyone who
scans the QR code early sees that welcome screen instead of a blank page,
and their phone stays connected.

Back on the status page, the compact **Event** row has the buttons you'll
actually press live: **Start event** when the program begins switches every
phone to captions at once. **Pause** puts your pause message
("Intermission") over the captions; **End event** shows that the event is
over. If something breaks while live - the microphone, the recognizer, the
capture client - attendees automatically see "Captions paused - technical
difficulty" and then "Captions resumed", so a deaf attendee can tell an
outage from a pause.

The **disclaimer** field in Settings' welcome-screen panel is different from
the notice line: it shows once per device, with a dismiss button, the first
time someone opens the phone view - live, paused, or before the event starts,
not just on the welcome screen - which makes it the right place for "These
captions are auto-generated and may not be 100% accurate" rather than
something tied to the welcome screen specifically. Blank disables it.

**During the program**, expand the collapsed **Send a caption by hand** panel
for what the recognizer cannot hear: **[Applause]**, **[Laughter]**,
**[Music]**, **[Inaudible]**, or anything you type. Type a name and press
**Speaker change** to put `>> Name:` on the next line. With the Deepgram
backend, a change of voice is marked with `>>` automatically; the name still
has to come from you. It's collapsed by default so it stays out of the way
until you actually need it - the disclosure remembers whether you left it
open, same as every other collapsible section here.

Four tiles:

| Tile | Green means |
|---|---|
| **Audio input** | speech is arriving from the device. Amber **Clipping** means the level is too hot - turn the gain down at the board or interface; the transcript suffers otherwise |
| **Captions** | the backend is producing text. The small line underneath shows caption latency - how long after words are spoken they reach the relay - as a median and a 95th percentile over the last couple of hundred lines. Deepgram and the mock report it; a backend that cannot say when words were spoken shows "not measured" rather than a guess |
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

**Reload keyterms & blocklist**, on the Settings page, re-reads both
`config/keyterms.txt` and `config/blocklist.txt` mid-event (sections 8, 8a).

---

## 6a. The preflight check

Before a service, with ProPresenter, OBS or the stream already running the way
they will run, open the status page and press **Run preflight** (top panel).
A few seconds later you get one line - **Ready**, **Ready, with warnings**,
or **Not ready** - and a list underneath. Every item that is not a tick says
what it found and what to do about it, in that order. Nothing is changed by
running it; run it as often as you like.

What it checks, and what the words mean:

| Finding | What to do |
|---|---|
| **Capture client** not connected | Start Campus Captions with the launcher; check the capture window for an error. |
| **Audio input**: another program has the device | Quit the program that has it open, or pick a different input. |
| **Audio level** silent | Say something into the mic, or check the send from the desk, and run again. |
| **Audio level** clipping | Turn the gain down until the meter peaks near "good". |
| **Transcription**: no Deepgram key / mock backend | Add the key under Settings; pick a real backend for the service. |
| **Caption latency** slow | Look at the network and computer findings; that is where the time goes. |
| **This computer** busy | Close what you can. If it stays over 90% CPU, or under 300 MB free, captions belong on another computer or an appliance - the verdict says so. |
| **Network to …** slow or unreachable | Check the internet connection, firewall or proxy. Wired beats wifi. |
| **Wall / overlay**, **Phone view** not connected | Open the wall URL where the room will see it; scan the QR code with one phone. |
| **ProPresenter (direct)** disconnected | Check Network is enabled in ProPresenter and the port under Settings; the Web Fill prop keeps working meanwhile. |

The first time a computer passes, add a row to `docs/HOST-COMPATIBILITY.md`
with the verdict and the CPU/memory line - that table is how the next church
with the same setup knows it works.

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
| `align` | `left` | text alignment: `left`, `center` or `right`. Left matches broadcast roll-up captions; a centred live row moves both ends on every word |
| `font` | — | `font=atkinson` selects Atkinson Hyperlegible, designed for legibility. Served by the relay, not fetched from the internet |
| `idle` | `5` | seconds of no new text before the bar fades out; `0` = never |
| `fade` | `600` | fade duration, ms |
| `maxchars` | `42` | characters per line |
| `lines` | `2` | rows in the bar, `1` or `2`. Two is the ceiling: a third row buys context nobody in a room reads and costs height over the video |
| `reveal` | `word` | how the caption arrives. `word` streams the in-progress text as it is spoken; `line` waits for the finished sentence, so the bottom row never rewrites itself under a reader. Same name and meaning as the ProPresenter setting |
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

Two lines, never three. Space for both is reserved even when only one has
text, so the bar never jumps between one- and two-line states. New text pushes
the older line up and out. Only a completed sentence commits a line and only a
completed sentence causes a line break; a sentence longer than the rows roll
out a row at a time, each held long enough to read.

`?reveal=word` (the default) rewrites the bottom line in place as words are
recognised - current, but the row moves under a reader. `?reveal=line` paints
nothing until the sentence is final: the bar changes once per sentence, about
a sentence behind the room. Word suits a wall people glance at; line suits one
they read. `?lines=1` drops to a single row for a short bar over a lower
third.

---

## 7a. Captions inside ProPresenter directly (no web fill)

Section 7 puts captions on the output by rendering a web page inside a Prop.
This section sends the caption *text* into ProPresenter over its network API
instead, so ProPresenter draws it with its own theme, on the screens you
choose, like any other Message or Stage Message. Keep the section-7 Prop as
well: it is the fallback when this path is off or ProPresenter is not
answering, and the Settings page says so.

**One-time, in ProPresenter:** Settings → Network → tick **Enable Network**
and note the port. Then pick a surface:

- **Stage message** (simplest): make a stage layout with a Stage Message
  element where the caption should sit, and assign that layout to the screen
  that should show captions.
- **Message** (most control over the look): create a Message named
  `Live Captions` with one text token named `Caption`. Give it the theme you
  want - position, font, size, bar colour - and choose its screens (audience,
  livestream output). Nothing is triggered by us except this one Message.

**Then, on the Settings page → ProPresenter (direct):** address (`127.0.0.1`
if the relay runs on the same machine, otherwise that machine's IP), the
port, the surface, and for Message the two names. **Save**, then **Test
connection** - the badge should read **Connected** and the line under it
should name ProPresenter and its API version. **Enable** to start sending.

What to expect:

- **Line by line** (the default) sends finished sentences only. **Word by
  word** also sends the in-progress text; only the newest is ever sent, but
  try it before a service - some layouts flicker. It is the same choice, and
  the same word, as the wall's `?reveal=`.
- **At most two lines.** ProPresenter reflows and shrinks whatever it is
  handed, so the wrapping happens on our side against **characters per line**
  (42 by default). Set it to what one line of your text box actually holds:
  too high and ProPresenter shrinks the type, too low and you waste the box.
  A sentence longer than two lines is sent as two lines and then paged, each
  page held long enough to read - nothing is dropped.
- **Clear captions**, pausing or ending the event, and **Disable** all clear
  the surface, so nothing stale sits on a screen.
- The line under the badge shows how much time the ProPresenter hop adds
  ("adds 14 ms (p95 38 ms)"). The target is under 500 ms at p95.
- If ProPresenter quits or its network is switched off, the badge goes
  **Disconnected — fallback** within about ten seconds; phones, translation,
  the transcript and the section-7 Prop carry on. It reconnects by itself.
- Nothing else in ProPresenter is touched: no slides, playlists, props,
  macros or media. If it ever appears to, stop and report it.

The spike that decides Stage Message versus Message on a real ProPresenter is
written up as a test plan in `docs/PROPRESENTER_SPIKE.md`.

---

## 7b. OBS and vMix (browser source)

The same wall URL works as a transparent overlay in OBS and vMix.

**OBS:** Sources → **+** → **Browser**. URL: the wall URL (with its output key,
see below). Width **1920**, Height **1080** (match your canvas). FPS 30. Leave
*Custom CSS* as OBS's default (it sets a transparent body). Untick *Shutdown
source when not visible* and *Refresh browser when scene becomes active* - the
page reconnects on its own and a refresh would lose the current line. Put the
source above the camera layer.

**vMix:** Add Input → **Web Browser**. URL as above, width 1920, height
1080. Use it as an overlay channel or place it in a layer above the camera.

**Output keys.** Settings page → **Wall output keys**: create one key per
output (`obs`, `lobby`, `propresenter`). Each gets its own URL, which the
tuner also offers. Once any key exists, a wall URL without one is refused, so
an old scene left in OBS or a forgotten prop can be switched off by
**Revoke**, without editing every URL. With no keys, the plain wall URL works
for everyone, as before. Phones are never affected.

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
the file and press **Reload keyterms & blocklist** on the Settings page — that
reopens the stream with the new list, so captions stop for about a second. Do
it between segments, not mid-sentence.

Keyterm prompting is a Nova-3 feature. On an older model the same file is sent
as keyword boosting instead, automatically.

## 8a. Blocking words and the profanity filter

`config/blocklist.txt` — same shape as keyterms.txt (one term or short phrase
per line, `#` comments, blank lines ignored), but for words to strip out of
every caption instead of boost. Unlike keyterms, this works the same way on
every backend, not just Deepgram — a word or phrase on this list never
reaches any viewer, on apple_speech, whisper, deepgram, or mock.

```
# --- example ---
working title
```

A multi-word phrase like `working title` is matched as that whole phrase, not
each word separately, and matching is whole-word and case-insensitive, so
`cash` on the list never touches `cashier`. Edit the file, then press
**Reload keyterms & blocklist** on the Settings page (it reloads both files
together) — no stream reopen needed, since blocking happens after
transcription rather than during it. The profanity filter toggle is on that
same **Keywords & profanity filter** panel.

Separately, the **profanity filter** toggle on that same panel catches common
strong language from a small built-in list, with nothing to maintain. Turning
it on replaces a caught word with `[bleep]` rather than deleting it — the
broadcast-captioning convention for telling a deaf reader that something was
said there without exposing the word itself. It is off by default: verbatim
is the accessible choice, the same reasoning as Deepgram's own
`profanity_filter` in `config/event.toml` — a separate, backend-specific
setting from this one, since Deepgram's filters at the API level rather than
after the fact.

---

## 9. Pre-event checklist

Roughly 30 minutes before doors.

**Setup**

- [ ] Relay host is on, on the network, at its expected IP address
- [ ] `Start Captions.command` run — window shows `--- ready ---`
- [ ] Status page open in the booth, via the launcher's link (`/status?token=…`)
- [ ] the status page shows the backend you intend for a real event — usually
      `deepgram`, never `mock`
- [ ] `.env` has the Deepgram key — the capture client says
      `DEEPGRAM_API_KEY found` at startup

**Audio**

- [ ] Correct device selected under **Audio input device** on the status page
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
- [ ] If also streaming to YouTube: press **Enable** under **YouTube Live
      captions** once that stream is live (see section 3b)

**Afterwards**

- [ ] Press **Close ASR stream** — this is what stops the billing
- [ ] Close the launcher window

---

## Troubleshooting

| What you see | What it is |
|---|---|
| Wall is blank, everything else fine | The relay is unreachable from ProPresenter, or the prop is not triggered. Check the viewer count on the status page — if it is 0, ProPresenter never connected. Open the URL in a browser on that machine. |
| Status page: **No capture client** | The capture client is not running or cannot reach the relay. The launcher restarts it automatically; check its window for an error. |
| Status page: **Silent** with the mic live | Wrong input device, or the device is muted. Choose another input on the status page; use **Refresh devices** if it was just connected. |
| Status page: **Open, no text yet** | The stream is connected but nothing is being transcribed — usually silence, wrong input, or a mic that is muted at the desk. |
| Phones cannot connect, booth machine can | Firewall (section 4) or wifi client isolation. |
| Launcher says **port in use**, captions run on a different port | Another program had port 8000, so Campus Captions started on the next free one (section 3h). The wall, phone and QR addresses carry that port; close the other program and restart, or make the new port permanent under Settings → Network and update those addresses once. |
| Captions stop mid-event | The backend reconnects on its own with backoff. The wall holds the last line, then fades. If the **ASR stream** tile went red, look at the error text on the status page. |
| Wrong names in the transcript | Add them to `config/keyterms.txt` and press **Reload keyterms**. |
| Everything is fine but text is clipped | `maxchars` too high for `size`. Add `&debug=1` to the wall URL to confirm. |
| **Open ASR stream** never actually opens | The backend hit an error trying to start — a red banner appears at the top of the status page with the reason (e.g. a missing dependency, a bad audio device). If it isn't obvious, check the log files below. |

### Log files

Both the relay and the capture client mirror everything they print into a log
file, so a crash or a silent failure still leaves something to look at after
the fact — useful for exactly the kind of "it says it's starting but never
does" problem that has no visible cause on the status page itself.

- **Packaged macOS app:** `~/Library/Logs/Campus Captions/relay.log` and
  `capture.log`. Console.app (Applications → Utilities) lists these under
  "Log Reports" automatically, or open the folder directly with
  `open ~/Library/Logs/"Campus Captions"`.
- **Running from source:** `logs/relay.log` and `logs/capture.log` in the
  project folder (gitignored — these are local, per-machine files).

Each file rotates at 2MB with a few backups kept, so it is safe to leave
running for a whole event without filling the disk.

For the message format between the pieces, see `docs/MESSAGES.md`. For
development against the mock backend, see `README.md`.

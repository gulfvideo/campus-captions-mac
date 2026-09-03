"""The preflight check (requirements N-02): is this setup ready for a service?

Runs on demand from the status page and turns everything the relay already
knows - plus a few live network probes - into a short list of plain-language
findings, each with what to do about it, and one verdict:

    ready       nothing to fix
    warnings    it will work; some things are worth a look before the event
    not_ready   something must be fixed first, or (when it is the computer
                itself) another computer or an appliance is recommended

The evaluation is a pure function of a status snapshot and the probe
results, so it is unit-tested without a relay; run() only gathers the
inputs. Nothing here changes any state.

Thresholds are deliberately conservative and named at the top so that a
venue's experience can move them. They describe a computer that is also
running presentation or streaming software, which is the N-02 case.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import statistics
import time

# Host budgets (requirements: "documented CPU, memory and network budgets").
CPU_WARN_PERCENT = 70.0
CPU_FAIL_PERCENT = 90.0
MEM_WARN_AVAILABLE_MB = 1024
MEM_FAIL_AVAILABLE_MB = 300
# Caption latency targets (C-01): median <= 2 s, p95 <= 3 s.
LATENCY_WARN_P95 = 3.0
LATENCY_FAIL_P95 = 6.0
# Network: a TLS handshake to the provider, which is what the caption stream
# starts with. Median round trips above this make interims visibly late.
NET_WARN_MS = 400
NET_TRIES = 3
NET_TIMEOUT = 3.0

PROVIDER_HOSTS = {
    "deepgram": ("api.deepgram.com", "Deepgram"),
}
TRANSLATOR_HOST = ("api.cognitive.microsofttranslator.com", "Azure Translator")
YOUTUBE_HOST = ("upload.youtube.com", "YouTube caption ingestion")

STATUS_ORDER = {"fail": 0, "warn": 1, "skip": 2, "ok": 3}


# -- probes ------------------------------------------------------------------

def probe_tls(host: str, port: int = 443, tries: int = NET_TRIES,
              timeout: float = NET_TIMEOUT) -> dict:
    """Blocking. TLS handshake round trips to `host`, as milliseconds."""
    context = ssl.create_default_context()
    times: list[float] = []
    failures: list[str] = []
    for _ in range(tries):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout) as raw:
                with context.wrap_socket(raw, server_hostname=host):
                    pass
            times.append((time.perf_counter() - started) * 1000)
        except Exception as exc:
            failures.append(f"{exc.__class__.__name__}: {exc}"[:120])
    return {
        "host": host,
        "ok": len(times),
        "failed": len(failures),
        "median_ms": round(statistics.median(times)) if times else None,
        "worst_ms": round(max(times)) if times else None,
        "error": failures[-1] if failures else None,
    }


async def run_probes(hosts: list[str]) -> dict[str, dict]:
    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *(loop.run_in_executor(None, probe_tls, host) for host in hosts),
        return_exceptions=True)
    out: dict[str, dict] = {}
    for host, result in zip(hosts, results):
        if isinstance(result, Exception):
            result = {"host": host, "ok": 0, "failed": NET_TRIES, "median_ms": None,
                      "worst_ms": None, "error": repr(result)[:120]}
        out[host] = result
    return out


# -- evaluation --------------------------------------------------------------

def _check(cid: str, title: str, status: str, detail: str, action: str = "") -> dict:
    return {"id": cid, "title": title, "status": status, "detail": detail, "action": action}


def evaluate(snapshot: dict, probes: dict[str, dict] | None = None,
             propresenter: dict | None = None) -> dict:
    """Pure: a status snapshot (hub.snapshot()) and probe results in, a
    report out. `propresenter` is that output's status() when configured."""
    probes = probes or {}
    source = snapshot.get("source") or {}
    audio = snapshot.get("audio") or {}
    asr = snapshot.get("asr") or {}
    latency = (snapshot.get("latency") or {}).get("final")
    host = snapshot.get("host") or {}
    viewers = snapshot.get("viewers") or {}
    translation = snapshot.get("translation") or {}
    youtube = snapshot.get("youtube") or {}
    event = snapshot.get("event") or {}
    checks: list[dict] = []
    host_problem = False

    # 1. The capture client and its audio input.
    if not source.get("connected"):
        checks.append(_check(
            "capture", "Capture client", "fail",
            "The capture client is not connected to the relay, so there is no audio to caption.",
            "Start Campus Captions with the launcher (it starts both), or check the capture "
            "window for an error."))
    else:
        checks.append(_check("capture", "Capture client", "ok",
                             f"Connected, using the {source.get('backend') or '?'} backend."))
        device = source.get("device") or ""
        error = source.get("error") or ""
        if not device or device.lower().startswith("no audio device"):
            checks.append(_check(
                "audio-device", "Audio input", "fail",
                "No audio input is open on the capture computer.",
                "Pick the sound-board feed or interface under Audio input device. "
                "(Running with --no-audio is for testing without hardware.)"))
        elif source.get("device_busy"):
            checks.append(_check(
                "audio-device", "Audio input", "fail",
                f"Another program has the audio input ({device or 'the selected device'}).",
                error.split(" - ", 1)[-1] if " - " in error else
                "Untick exclusive mode in the program that holds it, or pick a different input."))
        elif error and "cannot" in error.lower():
            checks.append(_check("audio-device", "Audio input", "fail", error,
                                 "Pick a working input under Audio input device, then run this again."))
        else:
            state = audio.get("state")
            if state == "clipping":
                checks.append(_check(
                    "audio-level", "Audio level", "warn",
                    f"{device}: the signal is hitting full scale (clipping).",
                    "Turn the gain down on the board's send or the interface until the meter "
                    "peaks near 'good', not 'hot'. Clipped audio transcribes badly."))
            elif state == "live":
                checks.append(_check("audio-level", "Audio level", "ok",
                                     f"{device}: sound is arriving at a usable level."))
            elif state == "silent":
                checks.append(_check(
                    "audio-level", "Audio level", "warn",
                    f"{device}: no sound in the last few seconds.",
                    "Say something into the microphone, or check the send from the sound desk, "
                    "and run this again. If the meter never moves, it is the wrong input."))
            elif state == "stale":
                checks.append(_check("audio-level", "Audio level", "fail",
                                     "The capture client stopped reporting audio levels.",
                                     "Check the capture window; the launcher restarts it if it died."))
            else:
                checks.append(_check("audio-level", "Audio level", "skip",
                                     f"Audio state: {state or 'unknown'}."))

    # 2. Transcription: backend, key, stream, latency.
    backend = source.get("backend") or ""
    if source.get("connected"):
        if backend == "deepgram" and not source.get("has_deepgram_key"):
            checks.append(_check("transcription", "Transcription", "fail",
                                 "The Deepgram backend is selected but no API key is saved.",
                                 "Paste the key under Settings > Deepgram API key."))
        elif backend == "mock":
            checks.append(_check("transcription", "Transcription", "warn",
                                 "The mock backend is selected: it plays a script, not the room.",
                                 "Fine for a rehearsal. Pick Deepgram or an on-device backend for the service."))
        else:
            if asr.get("state") == "producing":
                checks.append(_check("transcription", "Transcription", "ok",
                                     f"The {backend} stream is open and producing text."))
            elif asr.get("stream_open"):
                checks.append(_check("transcription", "Transcription", "warn",
                                     f"The {backend} stream is open but has produced no text yet.",
                                     "Speak into the microphone. If nothing appears within a few "
                                     "seconds, check the input and the key."))
            else:
                checks.append(_check("transcription", "Transcription", "skip",
                                     f"The {backend} stream is closed - normal before the event.",
                                     "Open it a minute before the start; open it now to test latency."))
        if latency:
            p95 = float(latency.get("p95") or 0.0)
            median = float(latency.get("median") or 0.0)
            samples = int(latency.get("samples") or 0)
            text = (f"Captions arrive {median:.1f} s after the words are spoken "
                    f"(95% within {p95:.1f} s), over the last {samples} "
                    f"line{'s' if samples != 1 else ''}.")
            if p95 >= LATENCY_FAIL_P95:
                checks.append(_check("latency", "Caption latency", "fail", text,
                                     "That is too slow to follow. Check the network to the provider "
                                     "below and how busy this computer is."))
            elif p95 >= LATENCY_WARN_P95:
                checks.append(_check("latency", "Caption latency", "warn", text,
                                     "Target is 2 s typical, 3 s at worst. See the network and "
                                     "computer checks below."))
            else:
                checks.append(_check("latency", "Caption latency", "ok", text))
        elif asr.get("stream_open"):
            checks.append(_check("latency", "Caption latency", "skip",
                                 "Not measured yet - this backend has not reported when its words "
                                 "were spoken, or nothing has been said since the stream opened."))

    # 3. The computer itself (N-02: while the presentation software is running).
    if source.get("connected"):
        if not host.get("available") or host.get("cpu_percent") is None:
            checks.append(_check("host", "This computer", "skip",
                                 "CPU and memory are not being measured (psutil is missing on the "
                                 "capture machine)."))
        else:
            cpu = float(host.get("cpu_percent") or 0.0)
            avail = host.get("mem_available_mb")
            busiest = host.get("busiest") or []
            who = ", ".join(f"{p['name']} {p['cpu_percent']:.0f}%" for p in busiest[:3])
            detail = f"CPU {cpu:.0f}% busy"
            if avail is not None:
                detail += f", {avail:,} MB of memory free"
            if who:
                detail += f". Busiest: {who}"
            detail += "."
            if cpu >= CPU_FAIL_PERCENT or (avail is not None and avail < MEM_FAIL_AVAILABLE_MB):
                host_problem = True
                checks.append(_check("host", "This computer", "fail", detail,
                                     "This computer is too busy to caption reliably alongside what it "
                                     "is already running. Close what you can, or run captions on "
                                     "another computer or an appliance."))
            elif cpu >= CPU_WARN_PERCENT or (avail is not None and avail < MEM_WARN_AVAILABLE_MB):
                checks.append(_check("host", "This computer", "warn", detail,
                                     "Close programs you do not need during the service. If captions "
                                     "lag, a second computer for captions is the fix."))
            else:
                checks.append(_check("host", "This computer", "ok", detail))

    # 4. Network to the services this setup depends on.
    wanted: list[tuple[str, str, str]] = []
    if backend in PROVIDER_HOSTS:
        wanted.append(("net-transcription", "Network to " + PROVIDER_HOSTS[backend][1],
                       PROVIDER_HOSTS[backend][0]))
    elif backend in ("apple_speech", "whisper"):
        checks.append(_check("net-transcription", "Network for transcription", "ok",
                             f"The {backend} backend runs on this computer; captions need no internet."))
    if translation.get("provider") and translation.get("provider") != "none":
        wanted.append(("net-translation", "Network to " + TRANSLATOR_HOST[1], TRANSLATOR_HOST[0]))
    if youtube.get("configured"):
        wanted.append(("net-youtube", "Network to " + YOUTUBE_HOST[1], YOUTUBE_HOST[0]))
    for cid, title, hostname in wanted:
        result = probes.get(hostname)
        if result is None:
            checks.append(_check(cid, title, "skip", "Not probed."))
            continue
        if result.get("ok", 0) == 0:
            checks.append(_check(cid, title, "fail",
                                 f"Could not reach {hostname}: {result.get('error') or 'no answer'}.",
                                 "Check this computer's internet connection and any firewall or "
                                 "proxy on the venue network."))
        elif result.get("failed", 0) or (result.get("median_ms") or 0) >= NET_WARN_MS:
            checks.append(_check(cid, title, "warn",
                                 f"{hostname} answers, but slowly or unevenly: "
                                 f"{result.get('median_ms')} ms typical, {result.get('worst_ms')} ms "
                                 f"worst, {result.get('failed')} of {NET_TRIES} attempts failed.",
                                 "A wired connection is much steadier than wifi for this."))
        else:
            checks.append(_check(cid, title, "ok",
                                 f"{hostname} answers in {result.get('median_ms')} ms."))

    # 5. Outputs: is anything going to show these captions?
    walls = int(viewers.get("wall") or 0)
    phones = int(viewers.get("phone") or 0)
    if walls:
        checks.append(_check("wall", "Wall / overlay", "ok",
                             f"{walls} wall view{'s' if walls != 1 else ''} connected."))
    else:
        checks.append(_check("wall", "Wall / overlay", "warn",
                             "No wall view is connected.",
                             "Open the wall URL in ProPresenter, OBS or vMix (or a browser on the "
                             "display computer) so the room shows captions."))
    if phones:
        checks.append(_check("phones", "Phone view", "ok",
                             f"{phones} phone{'s' if phones != 1 else ''} connected."))
    else:
        checks.append(_check("phones", "Phone view", "warn",
                             "No phone has connected yet.",
                             "Scan the QR code with one phone and check that captions appear; "
                             "that proves the wifi path attendees will use."))
    if propresenter and propresenter.get("enabled"):
        state = propresenter.get("state")
        if state == "connected":
            checks.append(_check("propresenter", "ProPresenter (direct)", "ok",
                                 f"Connected; {propresenter.get('pushed', 0)} captions sent so far."))
        else:
            checks.append(_check("propresenter", "ProPresenter (direct)", "warn",
                                 f"Enabled but {state}: {propresenter.get('error') or 'not answering'}.",
                                 "Check ProPresenter's Network setting and the port under Settings. "
                                 "The Web Fill prop keeps working meanwhile."))
    if youtube.get("configured") and not youtube.get("enabled"):
        checks.append(_check("youtube", "YouTube captions", "skip",
                             "A YouTube ingestion URL is saved but pushing is off.",
                             "Enable it once the stream is live, if you want captions on YouTube."))
    elif youtube.get("enabled"):
        checks.append(_check("youtube", "YouTube captions", "ok",
                             f"Pushing to YouTube; {youtube.get('pushed', 0)} sent so far."))

    # 6. The event itself.
    state = event.get("state")
    if state in ("starting", "ended"):
        checks.append(_check("event", "Event", "skip",
                             f"Event state is '{state}': phones show the welcome screen.",
                             "Press Start event when the program begins."))
    elif state:
        checks.append(_check("event", "Event", "ok", f"Event is {state}."))

    checks.sort(key=lambda c: STATUS_ORDER.get(c["status"], 9))
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    if fails:
        verdict = "not_ready"
        headline = ("Another computer or an appliance is recommended for captions."
                    if host_problem else
                    f"Not ready: fix {len(fails)} thing{'s' if len(fails) != 1 else ''} and run this again.")
    elif warns:
        verdict = "warnings"
        headline = (f"Ready, with {len(warns)} thing{'s' if len(warns) != 1 else ''} worth a look "
                    "before the event.")
    else:
        verdict = "ready"
        headline = "Ready."
    return {"verdict": verdict, "headline": headline, "checks": checks,
            "counts": {"fail": len(fails), "warn": len(warns),
                       "ok": sum(1 for c in checks if c["status"] == "ok")}}


async def run(snapshot: dict, propresenter: dict | None = None) -> dict:
    """Gather the live inputs (network probes) and evaluate. A few seconds."""
    started = time.perf_counter()
    source = snapshot.get("source") or {}
    hosts: list[str] = []
    backend = source.get("backend") or ""
    if backend in PROVIDER_HOSTS:
        hosts.append(PROVIDER_HOSTS[backend][0])
    translation = snapshot.get("translation") or {}
    if translation.get("provider") and translation.get("provider") != "none":
        hosts.append(TRANSLATOR_HOST[0])
    if (snapshot.get("youtube") or {}).get("configured"):
        hosts.append(YOUTUBE_HOST[0])
    probes = await run_probes(hosts) if hosts else {}
    report = evaluate(snapshot, probes, propresenter)
    report["ran_at"] = round(time.time(), 3)
    report["duration_ms"] = round((time.perf_counter() - started) * 1000)
    report["probes"] = probes
    return report

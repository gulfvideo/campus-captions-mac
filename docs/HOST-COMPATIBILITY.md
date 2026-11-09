# Host compatibility matrix

Which computers, presentation/streaming programs and audio devices Campus
Captions has been run alongside, and how it went. Requirements N-02 and N-10
ask for this to exist and to be kept; the preflight check on the status page
is what produces the evidence for a row.

**How to add a row.** On the church's own production computer, with
ProPresenter / OBS / vMix / the stream running the way the service runs, open
the status page and press **Run preflight**. Copy the verdict and the
"This computer" line (CPU, memory, busiest programs) into the table, note the
audio device and how it was connected, and say whether the service went
through without caption trouble. One row per distinct computer, not per
service.

| Date | OS | Presentation / streaming | Audio input (how connected) | Preflight verdict | CPU / free memory during preflight | Service outcome | Notes |
|---|---|---|---|---|---|---|---|
| _example_ | macOS 15, MacBook Pro M2 | ProPresenter 21 + OBS to YouTube | ATEM Mini Extreme, USB-C webcam audio | Ready, with warnings | 55% / 4.2 GB | Fine, no caption lag | OBS had the ATEM open too; shared without issue |
| | | | | | | | |

## Known constraints

- **macOS shares inputs freely** between programs; the exception is a
  virtual device whose owning application has not started yet, which reads
  as digital silence rather than "busy".
- **Built-in microphones** on a docked laptop with the lid shut are silent.
  The status page says so; pick another input.
- **CPU budget.** The preflight warns at 70% total CPU and recommends another
  computer at 90%, measured while the presentation software is running.
  Captions themselves are light (audio capture and a network stream); the
  budget is about what is left for the software the computer is really there
  to run.
- **Memory budget.** Warns under 1 GB free, recommends another computer
  under 300 MB free.
- **Network.** Deepgram, Azure Translator and YouTube are probed with a TLS
  handshake three times each. Over 400 ms typical, or any failed attempt,
  is a warning; wired beats wifi here every time.

## Not yet covered

- GPU pressure (N-02 asks for it "where available"). Not measured; nothing
  in the caption path uses the GPU, so it is only relevant as a proxy for
  how busy the presentation software is.
- A sustained coexistence test with ProPresenter plus a livestream encoder
  on the same machine for a full service (first deliverable 5 in the
  requirements). The preflight is the instrument; the run still has to be
  done and recorded here.

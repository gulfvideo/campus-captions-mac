# Phone slide diagnostic — 2026-09-06

## Baseline

Clean clone of `origin/main` at `b4053652ae38d24ab0d2b4cddfae9c785062fdd2`;
local main matched it. Feature branch: `feature/phone-slides-preview`.
Read GIT-ETIQUETTE.md, installed commit template and pre-push hook. GitHub CLI
reported WRITE access; Git successfully cloned using its credential helper.
Open PR #17 changes outgoing caption shaping and is not a dependency.

Installed every pin in requirements.txt in .venv using bundled Python 3.12
(CI uses 3.14). Baseline: smoke ALL CHECKS PASSED, 64 unit tests OK,
byte-compilation OK. No product changes preceded this baseline.

## Real observations

ProPresenter 21.4, build 352583705, macOS 26.6.0. `/version` reports API `v1`.
Network API was already enabled. Tests used a newly created disposable local
presentation, never an existing event. No test presentation or screenshots
are included in the repository.

`tools/propresenter_slide_probe.py` is a localhost-only GET diagnostic with
bounded bodies, two-second socket timeouts, no redirects or environment
proxies. It reports only version, visibility booleans, index and image digest;
never presentation identifiers, names, text, notes or next-slide data.
Use `--port <Network settings port>` and optionally `--output /tmp/slide.png`.
The diagnostic may save a cue thumbnail while audience output is disabled:
its report must be interpreted alongside the visibility flags.

| Action | Observed result |
| --- | --- |
| Empty library/startup | `presentation_index: null`, slide layer false, audience false |
| Create/select presentation in editor | Still no active cue index |
| Editor Show button | Slide layer true and output preview shows A, but cue index null |
| Trigger A in Show mode | Index 0; PNG 4515 bytes; repeated retrieval identical |
| Enable audience | Audience flag becomes true |
| Advance with Right to B | Index 1; PNG 5833 bytes, different digest; text and border match audience preview |
| Hide Slide in live audience Look | `look.screens[0].slide` false, global slide layer still true, cue index null |
| Restore live Look | Slide flag restored; saved Default preset was not edited |
| Disable audience | Audience flag false |
| Clear Slide via Presentation menu | Slide layer false, cue index null |
| Quit application | Diagnostic returns sanitized URLError |
| Reopen application | API responds again, no slide and audience disabled |

The thumbnail endpoint is `/v1/presentation/{uuid}/thumbnail/{index}` with
`thumbnail_type=png`. Response is image/png. Default quality produced 400×225
images. A transparent slide's thumbnail includes a checkerboard unlike the
black output preview: this is a cue thumbnail, not a composited output frame.

## Integration decision and remaining checks

Read only audience-enabled, slide-layer, current-Look and current-cue endpoints,
then fetch one thumbnail on identity change and recheck identity/visibility
before publication. Use only UUID and integer index internally. Do not request
`/status/slide`: the vendor schema explicitly includes notes and next-slide
data, and it is not needed for this approach.

Choose one operator-configured audience screen index. Suppress sharing when
that screen's Slide flag is off or a mask/theme override prevents a cue
thumbnail from representing it. Unknown or inconsistent state must clear the
phone image. Editor Show without an identifiable active cue is unavailable.

After integration, focused A in the editor while B was active: the API stayed
on index 1 and returned the identical B thumbnail. `quality=1280` was verified
with a 27383-byte PNG for B, and is now used by the reader and diagnostic.

Integration verification: 80 unit tests passed (16 new); all smoke checks and
byte-compilation passed. Chrome displayed real ProPresenter thumbnails with
synthetic captions; both layout orders, adjustable space, independent caption
scrollback, Pause/Resume and slide enlargement/return were exercised. The
captions-first layout and 50% attendee selection persisted in settings.
A 390×844 portrait viewport had no horizontal overflow, and retained 338 pixels
of caption height at the 60% maximum slide setting. Text size and Amber color
controls worked; layout/space survived reload; browser console had no errors.
Closing ProPresenter during live captions removed the image and displayed
Slides temporarily unavailable; caption history kept receiving new lines.
Reopening ProPresenter and triggering a cue restored the image without a page
reload. The final reader returned a 1280-pixel image after relay restart.

Builds, media/video thumbnails, physical audience display comparison, and an
actual phone on Wi-Fi remain to be tested.
Do not describe these as verified. Builds and composited backgrounds cannot
be assumed to track a static cue thumbnail. No NDI/video implementation is
part of this feature.

Vendor schema consulted: https://openapi.propresenter.com/ (swagger.json).

Attempted object Build In setup and generated-video import using native UI.
The build picker did not open, and the file chooser did not reliably accept
its resolved path through automation (including a clipboard-read timeout).
These attempts do not count as verified build/video behavior. The generated
video and utility live only under /tmp; no existing media was accessed.

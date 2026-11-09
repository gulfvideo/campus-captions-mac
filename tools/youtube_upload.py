"""Upload a finished .srt to a YouTube video as a caption track.

This is an after-the-event tool, not part of the relay. It needs a browser for
Google's consent screen, so it is deliberately a thing an operator runs once the
event is over rather than something the relay does on its own.

    python tools/youtube_upload.py --video VIDEO_ID --srt captions.srt
    python tools/youtube_upload.py --video VIDEO_ID --from-relay --lang es

Optional extras (not installed by default):

    python -m pip install -r requirements-youtube.txt

## One-time Google setup

1. Google Cloud console -> new project.
2. Enable **YouTube Data API v3**.
3. Credentials -> Create credentials -> **OAuth client ID** -> **Desktop app**.
4. Download the JSON and save it as `config/google_client_secret.json`
   (gitignored).
5. The first run opens a browser to sign in as the account that owns the
   channel. The resulting token is cached in `config/google_token.json`
   (gitignored) and refreshed automatically after that.

The account must own the video. Uploading a caption track costs 400 units of the
default 10,000/day quota, so roughly 25 uploads a day.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launcher.launch import read_env_var  # noqa: E402 - the environment, then .env
CLIENT_SECRET = ROOT / "config" / "google_client_secret.json"
TOKEN_FILE = ROOT / "config" / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

MISSING = """
This tool needs the optional Google libraries:

    python -m pip install -r requirements-youtube.txt

They are not in requirements.txt because the relay never uses them - only this
one after-the-event script does.
"""


def load_libraries():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        return Request, Credentials, InstalledAppFlow, build, MediaFileUpload
    except ImportError:
        print(MISSING, file=sys.stderr)
        raise SystemExit(1)


def authenticate():
    Request, Credentials, InstalledAppFlow, build, _ = load_libraries()

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as exc:
            print(f"cached token unusable ({exc}); signing in again")
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            print(f"token refresh failed ({exc}); signing in again")
            creds = None

    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            raise SystemExit(
                f"missing {CLIENT_SECRET}.\n"
                f"See the setup steps at the top of this file - you need an\n"
                f"OAuth client ID of type 'Desktop app' from the Google Cloud\n"
                f"console, saved to that path."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        print("opening a browser to sign in to the channel's Google account...")
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        print(f"signed in; token cached in {TOKEN_FILE}")

    return creds


def fetch_from_relay(relay: str, lang: str, token: str, out: Path) -> Path:
    url = f"{relay.rstrip('/')}/api/transcript.srt?lang={lang}"
    # The operator token rides in a header, never in the URL (which gets
    # printed, and would land in any proxy log on the way).
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    print(f"fetching {url}")
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit("the relay wants the operator token: set "
                             "CAPTIONS_OPERATOR_TOKEN (it is in the project's .env)")
        raise
    out.write_bytes(body)
    print(f"saved {len(body)} bytes to {out}")
    return out


def upload(video_id: str, srt_path: Path, language: str, name: str,
           draft: bool) -> None:
    _, _, _, build, MediaFileUpload = load_libraries()
    creds = authenticate()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": name,
            "isDraft": draft,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream",
                            resumable=False)
    print(f"uploading {srt_path.name} to video {video_id} as {language!r}...")
    response = youtube.captions().insert(
        part="snippet", body=body, media_body=media).execute()
    caption_id = response.get("id", "?")
    print(f"done. caption track id {caption_id}")
    if draft:
        print("uploaded as a DRAFT - publish it in YouTube Studio when ready.")
    print(f"check it at https://studio.youtube.com/video/{video_id}/translations")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload an .srt to a YouTube video as captions.")
    parser.add_argument("--video", required=True, help="YouTube video id")
    parser.add_argument("--srt", type=Path, help="path to a .srt file")
    parser.add_argument("--from-relay", action="store_true",
                        help="download the transcript from a running relay")
    parser.add_argument("--relay", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=read_env_var("CAPTIONS_OPERATOR_TOKEN"),
                        help="operator token (default: CAPTIONS_OPERATOR_TOKEN from "
                             "the environment or .env)")
    parser.add_argument("--lang", default="en",
                        help="caption language code (default: en)")
    parser.add_argument("--name", default="",
                        help="track name shown in YouTube (default: empty, "
                             "which YouTube treats as the default track)")
    parser.add_argument("--publish", action="store_true",
                        help="publish immediately instead of uploading a draft")
    args = parser.parse_args()

    if args.from_relay:
        srt_path = fetch_from_relay(
            args.relay, args.lang, args.token,
            ROOT / f"captions-{args.video}-{args.lang}.srt")
    elif args.srt:
        srt_path = args.srt
    else:
        raise SystemExit("give either --srt PATH or --from-relay")

    if not srt_path.exists() or not srt_path.stat().st_size:
        raise SystemExit(f"{srt_path} is missing or empty")

    upload(args.video, srt_path, args.lang, args.name, draft=not args.publish)


if __name__ == "__main__":
    main()

"""Read-only, localhost-only diagnostic; never print upstream metadata.

Run with --port from ProPresenter Network settings. Optional --output writes
the current cue thumbnail locally for comparison with audience output.
This is a diagnostic, not proof that a cue thumbnail matches rendered output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def probe(port, output=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def get(path, image=False):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers={"Accept": "image/png" if image else "application/json"})
        limit = 4 * 1024 * 1024 if image else 64 * 1024
        with opener.open(req, timeout=2) as response:
            content_type = response.headers.get_content_type()
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("response too large")
            if image:
                if content_type != "image/png" or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("unexpected image response")
                return raw
            if content_type != "application/json":
                raise ValueError("unexpected JSON response")
            return json.loads(raw)

    def identity():
        value = get('/v1/presentation/slide_index')['presentation_index']
        if value is None:
            return None
        index = value['index']
        if type(index) is not int or index < 0:
            raise ValueError('invalid cue index')
        return str(uuid.UUID(value['presentation_id']['uuid'])), index

    version = get('/version')
    report = {k: version.get(k) for k in ('host_description', 'api_version')}
    report['audience_enabled'] = get('/v1/status/audience_screens')
    report['slide_layer_active'] = get('/v1/status/layers').get('slide')
    look = get('/v1/look/current')
    # Only shape and visibility fields: no screen names, IDs, text or notes.
    report['look_screen_fields'] = [sorted(s.keys()) for s in look.get('screens', [])]
    report['look_slide_flags'] = [{k: v for k, v in s.items()
                                 if type(v) is bool} for s in look.get('screens', [])]
    before = identity()
    report['cue_index'] = before[1] if before else None
    if before and output:
        raw = get(f'/v1/presentation/{before[0]}/thumbnail/{before[1]}?thumbnail_type=png&quality=1280', True)
        report['identity_stable'] = identity() == before
        report['image_bytes'] = len(raw)
        report['image_sha256'] = hashlib.sha256(raw).hexdigest()
        if report['identity_stable']:
            Path(output).write_bytes(raw)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        print(json.dumps(probe(args.port, args.output), indent=2))
    except Exception as exc:
        # urllib errors can include internal URLs; don't print the exception.
        print(json.dumps({'error': type(exc).__name__}))
        raise SystemExit(1)

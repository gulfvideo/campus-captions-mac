"""Read-only slide reader against real HTTP mock responses; no ProPresenter needed."""
import asyncio
from collections import Counter
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, PngImagePlugin
from relay.slides import SlideClient, SlidePreview, JSON_LIMIT, IMAGE_LIMIT

UUID = '11111111-1111-4111-8111-111111111111'
SECRET = 'PRIVATE_PRESENTER_NOTES_NEXT_SLIDE'


def png(color='red'):
    out = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    meta.add_text('notes', SECRET)
    Image.new('RGB', (80, 45), color).save(out, 'PNG', pnginfo=meta)
    return out.getvalue()


class FakeSlides:
    def __init__(self):
        self.index = 0
        self.audience = self.layer = self.look = True
        self.override = None
        self.requests = Counter()
        self.bad = None
        self.delay = 0
        self.race = False
        self.image = png()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parent.requests[self.path] += 1
                ctype, status = 'application/json', 200
                if parent.delay:
                    time.sleep(parent.delay)
                if parent.bad == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://169.254.169.254/')
                    self.end_headers()
                    return
                if parent.bad == 'offline':
                    status, body = 503, b'upstream unavailable'
                elif self.path == '/v1/status/audience_screens':
                    body = json.dumps(parent.audience).encode()
                elif self.path == '/v1/status/layers':
                    body = json.dumps({'slide': parent.layer, 'notes': SECRET}).encode()
                elif self.path == '/v1/look/current':
                    body = json.dumps({'screens': [{'slide': parent.look, 'presentation': parent.override}]}).encode()
                elif self.path == '/v1/presentation/slide_index':
                    body = json.dumps({'presentation_index': None if parent.index is None else {
                        'index': parent.index, 'presentation_id': {'uuid': UUID, 'name': SECRET},
                        'notes': SECRET}, 'next': {'notes': SECRET}}).encode()
                    if parent.bad == 'json': body = b'{broken'
                    if parent.bad == 'oversized': body = b'x' * (JSON_LIMIT + 1)
                    if parent.bad == 'json_type': ctype = 'text/html'
                elif self.path.startswith('/v1/presentation/' + UUID + '/thumbnail/'):
                    ctype, body = 'image/png', parent.image
                    if parent.bad == 'image': body = b'\x89PNG\r\n\x1a\nnot a PNG'
                    if parent.bad == 'image_type': ctype = 'text/html'
                    if parent.bad == 'image_large': body = b'x' * (IMAGE_LIMIT + 1)
                    if parent.race: parent.index += 1
                else:
                    status, body = 404, b'forbidden mock endpoint'
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeSlides()
        self.preview = SlidePreview(SlideClient(port=self.fake.port), True)
        self.preview.set_live(True)

    async def asyncTearDown(self):
        await self.preview.stop()
        await asyncio.to_thread(self.fake.close)

    async def test_current_slide_cached_without_churn_and_strips_metadata(self):
        await self.preview.poll()
        state = self.preview.public()
        self.assertTrue(state['visible'])
        image = self.preview.image(state['revision'])
        self.assertNotIn(SECRET.encode(), image)
        self.assertNotIn(SECRET, json.dumps(state))
        self.assertNotIn(UUID, json.dumps(state))
        for _ in range(4): await self.preview.poll()
        self.assertEqual(state, self.preview.public())
        self.assertEqual(sum(n for p, n in self.fake.requests.items() if '/thumbnail/' in p), 1)
        self.assertFalse(any('status/slide' in p or 'focused' in p for p in self.fake.requests))

    async def test_slide_changes_revoke_previous_revision(self):
        await self.preview.poll()
        old = self.preview.public()['revision']
        self.fake.index = 1
        self.fake.image = png('blue')
        await self.preview.poll()
        self.assertNotEqual(old, self.preview.public()['revision'])
        self.assertIsNone(self.preview.image(old))

    async def test_clear_and_each_visibility_gate(self):
        for field, value in [('index', None), ('audience', False), ('layer', False), ('look', False)]:
            await self.preview.poll()
            self.assertTrue(self.preview.public()['visible'])
            original = getattr(self.fake, field)
            setattr(self.fake, field, value)
            await self.preview.poll()
            self.assertFalse(self.preview.public()['visible'])
            self.assertIsNone(self.preview._image)
            setattr(self.fake, field, original)

    async def test_connection_loss_clears_and_recovers(self):
        await self.preview.poll()
        self.fake.bad = 'offline'
        await self.preview.poll()
        self.assertEqual(self.preview.public()['state'], 'unavailable')
        self.assertIsNone(self.preview._image)
        self.fake.bad = None
        await self.preview.poll()
        self.assertTrue(self.preview.public()['visible'])

    async def test_bad_upstream_responses_fail_closed(self):
        for bad in ['offline', 'redirect', 'json', 'oversized', 'json_type', 'image', 'image_type', 'image_large']:
            with self.subTest(bad=bad):
                self.preview.clear('connecting')
                self.fake.bad = bad
                await self.preview.poll()
                self.assertFalse(self.preview.public()['visible'])
                self.assertEqual(self.preview.public()['state'], 'unavailable')

    async def test_timeout_does_not_block_event_loop(self):
        self.fake.delay = 0.15
        with patch('relay.slides.TIMEOUT', 0.03):
            task = asyncio.create_task(self.preview.poll())
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            await task
        self.assertEqual(self.preview.public()['state'], 'unavailable')

    async def test_race_during_thumbnail_never_publishes(self):
        self.fake.race = True
        await self.preview.poll()
        self.assertFalse(self.preview.public()['visible'])
        self.assertIsNone(self.preview._image)

    async def test_event_end_during_request_cannot_resurrect_image(self):
        self.fake.delay = 0.03
        task = asyncio.create_task(self.preview.poll())
        await asyncio.sleep(0.01)
        self.preview.set_live(False)
        await task
        self.assertIsNone(self.preview._image)
        self.assertEqual(self.preview.public()['state'], 'off')

    async def test_end_revokes_image_immediately_and_restart_refetches(self):
        await self.preview.poll()
        revision = self.preview.public()['revision']
        self.preview.set_live(False)
        self.assertIsNone(self.preview.image(revision))
        self.preview.set_live(True)
        await self.preview.poll()
        self.assertTrue(self.preview.public()['visible'])

    async def test_stale_image_expires_even_without_poll_completion(self):
        await self.preview.poll()
        self.preview._checked -= 6
        self.assertFalse(self.preview.public()['visible'])
        self.assertIsNone(self.preview._image)

    async def test_theme_override_suppresses_thumbnail(self):
        self.fake.override = UUID
        await self.preview.poll()
        self.assertFalse(self.preview.public()['visible'])
        self.assertFalse(any('/thumbnail/' in p for p in self.fake.requests))

    async def test_disabled_reader_never_connects(self):
        self.preview.enabled = False
        await self.preview.poll()
        self.assertFalse(self.fake.requests)

    async def test_unavailable_client_is_optional(self):
        self.preview.client = None
        await self.preview.poll()
        self.assertEqual(self.preview.public()['state'], 'unavailable')

    def test_arbitrary_hosts_urls_and_ports_are_rejected(self):
        for host in ['https://evil.example', 'evil.example', '8.8.8.8', '169.254.169.254',
                     '127.0.0.1@evil.example', '10.1.2.3', '127.0.0.1/path', '0.0.0.0']:
            with self.subTest(host=host), self.assertRaises(ValueError): SlideClient(host)
        SlideClient('10.1.2.3', approved_hosts=('10.1.2.3',))
        for port in [0, 65536, True, '1025']:
            with self.assertRaises(ValueError): SlideClient(port=port)


class RelayBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeSlides()
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / 'event.toml'
        cfg.write_text(f'[event]\nstate="live"\n[slides]\nenabled=true\nport={cls.fake.port}\n')
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); cls.port = sock.getsockname()[1]
        cls.proc = subprocess.Popen([sys.executable, '-m', 'relay', '--host', '127.0.0.1',
                                     '--port', str(cls.port), '--config', str(cfg)],
                                    env={**os.environ, 'CAPTIONS_OPERATOR_TOKEN': 'slide-test-operator',
                                         'CAPTIONS_SOURCE_TOKEN': 'slide-test-source'},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if cls.http('/api/slides')[0] == 200: break
            except OSError: pass
            time.sleep(.05)
        else: raise RuntimeError('test relay did not start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(timeout=10)
        cls.fake.close(); cls.tmp.cleanup()

    @classmethod
    def http(cls, path, method='GET', body=None, auth=False):
        headers = {'Content-Type': 'application/json'}
        if auth: headers['Authorization'] = 'Bearer slide-test-operator'
        req = urllib.request.Request(f'http://127.0.0.1:{cls.port}{path}',
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_public_contract_no_metadata_control_or_proxy(self):
        code, raw, _ = self.http('/api/slides')
        self.assertEqual(code, 200)
        self.assertEqual(set(json.loads(raw)), {'available','visible','revision','state','stale'})
        for secret in [UUID, SECRET, '127.0.0.1', 'presentation_id', 'next', 'notes']:
            self.assertNotIn(secret, raw.decode())
        self.assertEqual(self.http('/api/slides?url=http://169.254.169.254/')[0], 400)
        self.assertEqual(self.http('/api/slides', 'POST', {'url': 'http://evil.example'})[0], 405)
        self.assertIn(self.http('/api/slides/trigger')[0], (401,404))
        self.assertIn(self.http('/v1/presentation/active/next/trigger')[0], (401,404))
        self.assertEqual(self.http('/api/propresenter', 'POST', {'enabled': True})[0], 401)

    def test_event_end_revokes_http_image(self):
        self.http('/api/event', 'POST', {'state':'live'}, True)
        for _ in range(50):
            state = json.loads(self.http('/api/slides')[1])
            if state['visible']: break
            time.sleep(.05)
        self.assertTrue(state['visible'])
        path = '/api/slides/image/' + state['revision']
        code, body, headers = self.http(path)
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertNotIn(SECRET.encode(), body)
        self.assertEqual(self.http('/api/event','POST',{'state':'ended'},True)[0], 200)
        self.assertEqual(self.http(path)[0], 404)
        self.assertFalse(json.loads(self.http('/api/slides')[1])['visible'])
        self.assertEqual(self.http('/phone')[0], 200)


if __name__ == '__main__': unittest.main()

"""Optional read-only current-cue thumbnails; no attendee-controlled upstream.

See docs/PHONE-SLIDES-SPIKE.md for the observed API contract and limitations.
All blocking I/O/decoding stays in one worker thread, never the caption path.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import time
import urllib.request
import uuid

from PIL import Image

JSON_LIMIT = 64 * 1024
IMAGE_LIMIT = 4 * 1024 * 1024
MAX_PIXELS = 4_000_000
TIMEOUT = 1.0
FRESH_SECONDS = 5.0
POLL_SECONDS = 0.75


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SlideClient:
    def __init__(self, host='127.0.0.1', port=1025, screen=0, approved_hosts=()):
        # Literal addresses only: no DNS rebinding, URL components or redirects.
        address = ipaddress.ip_address(host)
        if not address.is_loopback:
            private = any(address in network for network in (
                ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'),
                ipaddress.ip_network('192.168.0.0/16')) if address.version == network.version)
            if not private or str(address) not in approved_hosts:
                raise ValueError('slide host must be loopback or explicitly approved RFC1918 address')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid slide port')
        if type(screen) is not int or not 0 <= screen < 32:
            raise ValueError('invalid audience screen index')
        self.base = f'http://[{address}]:{port}' if address.version == 6 else f'http://{address}:{port}'
        self.screen = screen
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def _get(self, path, image=False):
        limit = IMAGE_LIMIT if image else JSON_LIMIT
        started = time.monotonic()
        request = urllib.request.Request(self.base + path, headers={
            'Accept': 'image/png' if image else 'application/json'})
        with self.opener.open(request, timeout=TIMEOUT) as response:
            wanted = 'image/png' if image else 'application/json'
            if response.status != 200 or response.headers.get_content_type() != wanted:
                raise ValueError('unexpected upstream response')
            raw = bytearray()
            while True:
                if time.monotonic() - started > 2 * TIMEOUT:
                    raise TimeoutError('upstream deadline')
                chunk = response.read1(min(65536, limit + 1 - len(raw)))
                raw.extend(chunk)
                if len(raw) > limit:
                    raise ValueError('upstream response too large')
                if not chunk:
                    break
        return bytes(raw) if image else json.loads(raw)

    def identity(self):
        enabled = self._get('/v1/status/audience_screens')
        if type(enabled) is not bool:
            raise ValueError('invalid audience state')
        if not enabled:
            return None
        layers = self._get('/v1/status/layers')
        if type(layers.get('slide')) is not bool:
            raise ValueError('invalid slide state')
        if not layers['slide']:
            return None
        screens = self._get('/v1/look/current')['screens']
        if not isinstance(screens, list) or self.screen >= len(screens):
            raise ValueError('unknown audience screen')
        screen = screens[self.screen]
        if type(screen.get('slide')) is not bool:
            raise ValueError('unknown audience slide layer')
        if not screen['slide']:
            return None
        if screen.get('presentation') or screen.get('mask'):
            raise ValueError('composited look unsupported')
        current = self._get('/v1/presentation/slide_index')['presentation_index']
        if current is None:
            return None
        index = current['index']
        if type(index) is not int or not 0 <= index <= 100000:
            raise ValueError('invalid cue index')
        # Extract exactly the fields needed, discard the original response.
        return str(uuid.UUID(current['presentation_id']['uuid'])), index

    def sample(self, previous):
        identity = self.identity()
        if identity is None or identity == previous:
            return identity, None
        raw = self._get(f'/v1/presentation/{identity[0]}/thumbnail/{identity[1]}?thumbnail_type=png&quality=1280', True)
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != 'PNG' or source.width * source.height > MAX_PIXELS or getattr(source, 'n_frames', 1) != 1:
                raise ValueError('invalid thumbnail')
            source.load()  # Verify compressed pixel data, not just a magic header.
            clean = source.convert('RGBA')
            clean.info.clear()
            out = io.BytesIO()
            clean.save(out, format='PNG')
            image = out.getvalue()
            if len(image) > IMAGE_LIMIT:
                raise ValueError('encoded image too large')
        if self.identity() != identity:
            raise ValueError('slide changed during thumbnail retrieval')
        return identity, image


class SlidePreview:
    def __init__(self, client=None, enabled=False):
        self.client = client
        self.enabled = enabled
        self.live = False
        self.state = 'off'
        self._image = None
        self._identity = None
        self._revision = None
        self._checked = 0.0
        self._epoch = 0
        self._task = None

    def clear(self, state):
        self._image = self._identity = self._revision = None
        self.state = state

    def configure(self, client, enabled):
        """Revoke old images and in-flight results without spawning extra workers."""
        self._epoch += 1
        self.client = client
        self.enabled = enabled
        self.clear("connecting" if enabled and self.live else "off")
        self.start()

    def set_live(self, live):
        if live != self.live:
            self._epoch += 1
            self.live = live
            self.clear('connecting' if live and self.enabled else 'off')

    def public(self):
        if self._image is not None and time.monotonic() - self._checked > FRESH_SECONDS:
            self.clear('unavailable')
        visible = bool(self.enabled and self.live and self._image is not None)
        return {'available': self.enabled, 'visible': visible,
                'revision': self._revision if visible else None,
                'state': self.state, 'stale': self.state == 'unavailable'}

    def image(self, revision):
        state = self.public()
        return self._image if state['visible'] and revision == self._revision else None

    async def poll(self):
        if not self.enabled or not self.live:
            return
        epoch = self._epoch
        try:
            if self.client is None:
                raise ValueError('invalid configuration')
            identity, image = await asyncio.to_thread(self.client.sample, self._identity)
            if epoch != self._epoch or not self.live:
                return
            self._checked = time.monotonic()
            if identity is None:
                self.clear('clear')
            elif image is not None:
                self._identity, self._image = identity, image
                self._revision = hashlib.sha256(image).hexdigest()
                self.state = 'connected'
            elif self._image is None:
                # An expiry while the worker ran invalidated its cached identity.
                self._identity = None
        except Exception:
            if epoch == self._epoch:
                self.clear('unavailable')

    async def _run(self):
        while True:
            await self.poll()
            await asyncio.sleep(POLL_SECONDS)

    def start(self):
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        self.set_live(False)
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

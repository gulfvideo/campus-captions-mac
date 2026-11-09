import unittest

from relay import speech
from relay.hub import Hub


LISTING = """Albert              en_US    # Hello! My name is Albert.
Eddy (Spanish (Mexico)) es_MX    # ¡Hola! Me llamo Eddy.
Paulina (Spanish (Mexico)) es_MX    # ¡Hola! Me llamo Paulina.
Mónica (Spanish (Spain)) es_ES    # ¡Hola! Me llamo Mónica.
Luciana             pt_BR    # Olá, meu nome é Luciana.
Tingting (Chinese (China mainland)) zh_CN    # 你好！我叫婷婷。
"""


class VoiceList(unittest.TestCase):
    def test_parse_prefers_real_voice_over_novelty(self):
        v = speech.parse_voices(LISTING)
        self.assertEqual(v["es_MX"], "Paulina (Spanish (Mexico))")
        self.assertEqual(v["pt_BR"], "Luciana")

    def test_voice_for(self):
        v = speech.parse_voices(LISTING)
        self.assertEqual(speech.voice_for("es", v), "Paulina (Spanish (Mexico))")
        self.assertEqual(speech.voice_for("zh-Hans", v), "Tingting (Chinese (China mainland))")
        self.assertEqual(speech.voice_for("pt", v), "Luciana")
        self.assertIsNone(speech.voice_for("ko", v))


class ClearKeepsSpoken(unittest.TestCase):
    def test_clear_does_not_evict_queued_text(self):
        hub = Hub(session_id="s", source_language="en")
        msg = hub.broadcast({"type": "final", "text": "Hola", "lang": "es", "seq": 1})
        hub.broadcast({"type": "clear"})
        self.assertEqual(hub.final_text("es", msg["id"]), "Hola")


if __name__ == "__main__":
    unittest.main()

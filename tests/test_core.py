"""Tests unitarios para core.py de rolight (funciones puras / network mockeable)."""
import json
import unittest
from unittest.mock import MagicMock, patch

import core


class TestConvertUnits(unittest.TestCase):
    def test_length_km_to_mi(self):
        self.assertEqual(core.convert_units("100 km a mi"), "62.1371 mi")

    def test_length_miles_to_km(self):
        self.assertEqual(core.convert_units("1 mile to km"), "1.60934 km")

    def test_temperature_c_to_f(self):
        self.assertEqual(core.convert_units("100 c a f"), "212 f")

    def test_temperature_f_to_c(self):
        self.assertEqual(core.convert_units("32f to c"), "0 c")

    def test_temperature_kelvin_roundtrip(self):
        self.assertEqual(core.convert_units("300 k a c"), "26.85 c")

    def test_mass_kg_to_lb(self):
        self.assertEqual(core.convert_units("1 kg to lb"), "2.20462 lb")

    def test_data_gb_to_mb(self):
        self.assertEqual(core.convert_units("2 gb a mb"), "2000 mb")

    def test_spanish_alias(self):
        # «millas» y «kilómetros» en texto
        self.assertEqual(core.convert_units("10 millas a km"), "16.0934 km")

    def test_decimal_comma(self):
        self.assertEqual(core.convert_units("2,5 kg to lb"), "5.51156 lb")

    def test_thousands_separator(self):
        self.assertEqual(core.convert_units("1,000 m a km"), "1 km")

    def test_same_unit(self):
        self.assertEqual(core.convert_units("5 km a km"), "5 km")

    def test_not_a_conversion(self):
        self.assertIsNone(core.convert_units("hola"))
        self.assertIsNone(core.convert_units(""))
        self.assertIsNone(core.convert_units("2 + 2"))

    def test_unknown_unit(self):
        self.assertIsNone(core.convert_units("5 xyz a abc"))

    def test_mismatched_category(self):
        # temperatura contra longitud: no tiene sentido
        self.assertIsNone(core.convert_units("5 c a km"))
        # masa contra longitud
        self.assertIsNone(core.convert_units("5 kg to km"))


class TestSearchContent(unittest.TestCase):
    def _fake_run(self, stdout, returncode=0):
        m = MagicMock()
        m.stdout = stdout
        m.returncode = returncode
        return m

    def test_parse_hits(self):
        out = ("/home/u/proyecto/a.py:12:def hola_mundo():\n"
               "/home/u/notas/b.txt:3:hola de nuevo\n")
        with patch("core.shutil.which", return_value="/usr/bin/rg"), \
             patch("core.subprocess.run", return_value=self._fake_run(out)):
            res = core.search_content("hola")
        self.assertEqual(res[0], ("/home/u/proyecto/a.py", 12, "def hola_mundo():"))
        self.assertEqual(res[1], ("/home/u/notas/b.txt", 3, "hola de nuevo"))

    def test_limit_respetado(self):
        out = "".join(f"/f{i}.txt:1:match\n" for i in range(50))
        with patch("core.shutil.which", return_value="/usr/bin/rg"), \
             patch("core.subprocess.run", return_value=self._fake_run(out)):
            res = core.search_content("match", limit=5)
        self.assertEqual(len(res), 5)

    def test_sin_coincidencias(self):
        with patch("core.shutil.which", return_value="/usr/bin/rg"), \
             patch("core.subprocess.run", return_value=self._fake_run("", returncode=1)):
            self.assertEqual(core.search_content("nada"), [])

    def test_error_regex_devuelve_vacio(self):
        # returncode 2 = error de rg (regex inválida): no debe romper nada
        with patch("core.shutil.which", return_value="/usr/bin/rg"), \
             patch("core.subprocess.run", return_value=self._fake_run("error", returncode=2)):
            self.assertEqual(core.search_content("["), [])

    def test_sin_rg_devuelve_vacio(self):
        with patch("core.shutil.which", return_value=None):
            self.assertEqual(core.search_content("hola"), [])

    def test_query_vacia(self):
        with patch("core.shutil.which", return_value="/usr/bin/rg"):
            self.assertEqual(core.search_content("   "), [])

    def test_timeout_devuelve_vacio(self):
        import subprocess
        with patch("core.shutil.which", return_value="/usr/bin/rg"), \
             patch("core.subprocess.run", side_effect=subprocess.TimeoutExpired("rg", 6)):
            self.assertEqual(core.search_content("hola"), [])


class TestTimer(unittest.TestCase):
    def test_minutes_short(self):
        self.assertEqual(core.parse_timer("10m"), 600)

    def test_with_prefix(self):
        self.assertEqual(core.parse_timer("timer 5 minutos"), 300)
        self.assertEqual(core.parse_timer("alarma 2h"), 7200)

    def test_compound(self):
        self.assertEqual(core.parse_timer("1h30"), 5400)  # 30 suelto = min

    def test_bare_number_is_minutes(self):
        self.assertEqual(core.parse_timer("15"), 900)

    def test_seconds(self):
        self.assertEqual(core.parse_timer("45s"), 45)

    def test_days(self):
        self.assertEqual(core.parse_timer("2d"), 172800)

    def test_spanish_words(self):
        self.assertEqual(core.parse_timer("1 hora 30 minutos"), 5400)

    def test_rejects_garbage(self):
        self.assertIsNone(core.parse_timer("hola"))
        self.assertIsNone(core.parse_timer("5 kg"))
        self.assertIsNone(core.parse_timer("timer"))
        self.assertIsNone(core.parse_timer(""))
        self.assertIsNone(core.parse_timer("banana 5m"))
        self.assertIsNone(core.parse_timer("0"))

    def test_fmt_duration(self):
        self.assertEqual(core.fmt_duration(600), "10 min")
        self.assertEqual(core.fmt_duration(5400), "1 h 30 min")
        self.assertEqual(core.fmt_duration(45), "45 s")
        self.assertEqual(core.fmt_duration(0), "0 s")


class TestPackage(unittest.TestCase):
    def test_detect_pm_returns_str(self):
        self.assertIsInstance(core.detect_pm(), str)

    def test_package_search_pacman_parses(self):
        out = (
            "core/example 1.0-1\n"
            "    Descripción de example\n"
            "extra/foo 2.0-1\n"
            "    Descripción de foo\n"
        )
        fake = MagicMock()
        fake.stdout = out
        with patch("core.subprocess.run", return_value=fake):
            res = core.package_search("ex", pm="pacman")
        names = [r["name"] for r in res]
        self.assertIn("example", names)
        self.assertIn("foo", names)
        ex = next(r for r in res if r["name"] == "example")
        self.assertEqual(ex["repo"], "core")
        self.assertEqual(ex["ver"], "1.0-1")
        self.assertEqual(ex["desc"], "Descripción de example")

    def test_package_search_dedup(self):
        out = "core/dup 1.0-1\n    x\ncore/dup 1.0-1\n    x\n"
        fake = MagicMock()
        fake.stdout = out
        with patch("core.subprocess.run", return_value=fake):
            res = core.package_search("dup", pm="pacman")
        self.assertEqual(len(res), 1)

    def test_package_search_no_pm(self):
        with patch("core.detect_pm", return_value=""):
            self.assertEqual(core.package_search("x"), [])


class TestOverseerr(unittest.TestCase):
    def _mock_urlopen(self, payload):
        mgr = MagicMock()
        mgr.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return mgr

    def test_overseerr_search_builds_url_and_header(self):
        payload = {"results": [
            {"id": 7, "mediaType": "movie", "title": "Matrix",
             "releaseDate": "1999-03-31", "overview": "Historia larga"}
        ]}
        mgr = self._mock_urlopen(payload)
        with patch("core.urllib.request.urlopen", return_value=mgr) as m:
            res = core.overseerr_search("http://192.168.0.5:5055/", "TOKEN", "matrix")
        req = m.call_args.args[0]
        self.assertIn("/api/v1/search?query=matrix", req.full_url)
        hdr = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(hdr.get("x-api-key"), "TOKEN")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["title"], "Matrix")
        self.assertEqual(res[0]["year"], "1999")
        self.assertEqual(res[0]["type"], "movie")

    def test_overseerr_request_posts_body(self):
        mgr = self._mock_urlopen({"id": 1, "status": 1})
        with patch("core.urllib.request.urlopen", return_value=mgr) as m:
            core.overseerr_request("http://h:5055", "TK", 7, "movie")
        req = m.call_args.args[0]
        self.assertEqual(req.method, "POST")
        body = json.loads(req.data.decode())
        self.assertEqual(body, {"mediaType": "movie", "mediaId": 7})


if __name__ == "__main__":
    unittest.main()

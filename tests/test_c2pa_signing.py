"""v0.6.0 opt-in C2PA signing (--sign) and make_cert.py.

The signing tests need c2pa-python (Python 3.10+) and are skipped without it.
The tests of what happens without it run everywhere, the Python 3.9 floor too.

Run: python -m unittest discover -s tests
"""
import builtins
import contextlib
import http.server
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_realism import DEMO, realism, scene, to_image  # noqa: E402
from test_v05_io import DATES, exif_segment, run_safe, small  # noqa: E402

import make_cert  # noqa: E402  (beside realism.py, on the path test_realism set)

OPENAI_PNG = os.path.join(DEMO, "candid-male", "skill-before-script.png")  # ChatGPT output with its real C2PA manifest
C2PA, WHY = realism.load_c2pa()
needs_c2pa = unittest.skipIf(C2PA is None, "c2pa-python not available: %s" % WHY)
TRAINED = realism.IPTC_DST + "trainedAlgorithmicMedia"


def unsigned(origin="generated", size=(96, 64)):
    """A checked, unsigned output JPEG of a small scene."""
    out, _ = realism.process(to_image(scene(size[1], size[0])), "day", seed=1)
    return realism.encode_jpeg(out, 90, origin)


def make_cert_main(argv):
    """make_cert_main() with its output captured; argument errors raise SystemExit."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return make_cert.main(argv)


def app11(payload):
    seg = b"JP\x00\x01\x00\x00\x00\x01" + payload
    return b"\xff\xeb" + struct.pack(">H", 2 + len(seg)) + seg


def with_app11(jpeg, payload):
    """jpeg with an APP11 segment holding payload, right after SOI."""
    return jpeg[:2] + app11(payload) + jpeg[2:]


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def save(self, name, data):
        with open(self.path(name), "wb") as f:
            f.write(data)
        return self.path(name)

    def outputs(self):
        return sorted(n for n in os.listdir(self.tmp) if "_real" in n or n.startswith("out"))


class WithoutC2pa(Workspace):
    """What --sign and the check do when c2pa-python is missing; runs everywhere."""

    def blocked(self):
        return mock.patch.dict(sys.modules, {"c2pa": None})  # "import c2pa" raises ImportError

    def test_sign_without_c2pa_fails_with_a_one_line_hint_and_writes_nothing(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        with self.blocked():
            code, out, err = run_safe([src, "--origin", "generated", "--preset", "day",
                                       "--sign", self.path("missing-chain.pem"), self.path("missing-key.pem")])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(err.startswith("error: --sign needs "), err)
        self.assertIn("c2pa-python", err)
        if sys.version_info >= (3, 10):
            self.assertIn("python -m pip install c2pa-python", err)
        else:
            self.assertIn("Python 3.10 or newer", err)
        self.assertEqual(self.outputs(), [])

    def test_default_path_runs_without_c2pa(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        with self.blocked():
            code, out, err = run_safe([src, "--origin", "generated", "--preset", "day", "--seed", "1"])
        self.assertEqual(code, 0, err)
        with open(self.path("in_real.jpg"), "rb") as f:
            data = f.read()
        self.assertNotIn(b"jumdc2pa", data)
        with self.blocked():
            self.assertEqual(realism.disclosure_problems(data, "generated"), [])

    @unittest.skipIf(sys.version_info < (3, 10), "the version gate answers first on 3.9")
    def test_load_c2pa_names_each_reason(self):
        with self.blocked():
            self.assertEqual(realism.load_c2pa(), (None, "c2pa-python: python -m pip install c2pa-python"))
        with mock.patch.dict(sys.modules, {"c2pa": types.ModuleType("c2pa")}):  # an old release: no intents
            module, why = realism.load_c2pa()
        self.assertIsNone(module)
        self.assertIn("c2pa-python 0.38 or newer", why)
        self.assertIn("pip install --upgrade c2pa-python", why)
        real_import = builtins.__import__

        def broken(name, *a, **kw):
            if name == "c2pa":
                raise OSError("c2pa_c.dll not found\nsecond line")
            return real_import(name, *a, **kw)

        with mock.patch.dict(sys.modules), mock.patch("builtins.__import__", broken):
            sys.modules.pop("c2pa", None)
            module, why = realism.load_c2pa()
        self.assertIsNone(module)
        self.assertIn("cannot load here (c2pa_c.dll not found)", why)
        self.assertNotIn("\n", why)

    def test_check_reports_a_manifest_it_cannot_read_without_c2pa(self):
        data = with_app11(unsigned(), b"\x00\x00\x00\x20jumb\x00\x00\x00\x18jumdc2pa")
        with self.blocked():
            problems = realism.disclosure_problems(data, "generated")
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("C2PA manifest present; checking it needs c2pa-python"), problems)

    def test_unsigned_output_fails_when_a_parent_manifest_is_expected(self):
        self.assertEqual(realism.disclosure_problems(unsigned(), "generated", "urn:c2pa:x"),
                         ["C2PA manifest missing"])

    def test_label_check_skips_app11_but_reads_everything_else(self):
        data = unsigned()
        camera = realism.encode_jpeg(Image.open(io.BytesIO(data)).convert("RGB"), 90, "camera")
        foreign = with_app11(camera, b"jumdc2pa" + TRAINED.encode())
        plain, found = realism.split_app11(foreign)
        self.assertTrue(found)
        self.assertEqual(plain, camera)  # every other segment, the scan and EOI kept byte for byte
        self.assertEqual(realism.split_app11(camera), (camera, False))
        other = with_app11(camera, b"jumdjpxt not a manifest")  # APP11 that is not C2PA (JPEG XT, say)
        self.assertEqual(realism.split_app11(other), (camera, False))

    def test_make_cert_refuses_a_missing_folder_or_existing_files(self):
        with self.assertRaises(SystemExit) as cm:
            make_cert_main([self.path("nope")])
        self.assertEqual(cm.exception.code, 2)
        self.save(make_cert.KEY, b"keep me")
        with self.assertRaises(SystemExit):
            make_cert_main([self.tmp])
        with open(self.path(make_cert.KEY), "rb") as f:
            self.assertEqual(f.read(), b"keep me")
        self.assertFalse(os.path.exists(self.path(make_cert.CHAIN)))
        for bad in (["--days", "0"], ["--name", " "]):
            with self.assertRaises(SystemExit):
                make_cert_main([self.tmp] + bad)


@needs_c2pa
class Signing(Workspace):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.mkdtemp()
        cls.chain, cls.key = cls.pair("good", name="Test Signer")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.keys, True)

    @classmethod
    def pair(cls, stem, **kw):
        chain, key = make_cert.make(days=30, **kw)
        paths = []
        for suffix, data in (("-chain.pem", chain), ("-key.pem", key)):
            paths.append(os.path.join(cls.keys, stem + suffix))
            with open(paths[-1], "wb") as f:
                f.write(data)
        return paths

    def run_sign(self, src, origin="generated", *extra, chain=None, key=None):
        return run_safe([src, "--origin", origin, "--preset", "day", "--seed", "1", "-o", self.path("out.jpg"),
                         "--sign", chain or self.chain, key or self.key] + list(extra))

    def output(self, name="out.jpg"):
        with open(self.path(name), "rb") as f:
            return f.read()

    def store(self, data, mime="image/jpeg"):
        store, why = realism.c2pa_store(C2PA, data, mime)
        self.assertIsNone(why)
        return store

    def active(self, store):
        return store["manifests"][store["active_manifest"]]

    def actions(self, manifest):
        return [a for x in manifest["assertions"] if x["label"].startswith("c2pa.actions")
                for a in x["data"]["actions"]]

    def signer(self, chain=None, key=None):
        with open(chain or self.chain, "rb") as c, open(key or self.key, "rb") as k:
            return realism.signer_for(C2PA, c.read(), k.read())[0]

    def custom(self, data, generator="snapshot-realism", actions=None, parent=True, intent=True):
        """data signed with a hand-made manifest, to show the check refuses what the script never writes."""
        manifest = {"claim_generator_info": [{"name": generator, "version": realism.__version__}],
                    "title": "out.jpg", "format": "image/jpeg"}
        if not intent:
            manifest["assertions"] = [{"label": "c2pa.actions", "data": {"actions": actions}}]
        out = io.BytesIO()
        with C2PA.Builder(manifest, context=realism.c2pa_context(C2PA)) as b:
            if intent:
                b.set_intent(C2PA.C2paBuilderIntent.EDIT)
                for action in actions or [{"action": "c2pa.edited", "digitalSourceType": TRAINED}]:
                    b.add_action(action)
            if parent:
                png = io.BytesIO()
                small().save(png, "PNG")
                b.add_ingredient({"title": "in.png", "relationship": "parentOf"}, "image/png",
                                 io.BytesIO(png.getvalue()))
            b.sign(self.signer(), "image/jpeg", io.BytesIO(data), out)
        return out.getvalue()

    def camera_manifest_png(self, extra=()):
        """A PNG signed like a camera's Content Credentials: c2pa.created, digitalCapture."""
        manifest = {"claim_generator_info": [{"name": "Test Camera", "version": "1.0"}],
                    "title": "camera.png", "format": "image/png",
                    "assertions": [{"label": "c2pa.actions", "data": {"actions": [
                        {"action": "c2pa.created", "digitalSourceType": realism.IPTC_DST + "digitalCapture"}]}}]
                    + list(extra)}
        png = io.BytesIO()
        to_image(scene()).save(png, "PNG")
        out = io.BytesIO()
        with C2PA.Builder(manifest, context=realism.c2pa_context(C2PA)) as b:
            b.sign(self.signer(), "image/png", io.BytesIO(png.getvalue()), out)
        return out.getvalue()

    # the chain back to the source

    def test_openai_credentials_survive_as_the_parent_ingredient(self):
        with open(OPENAI_PNG, "rb") as f:
            raw = f.read()
        src = self.save("chatgpt.png", raw)
        code, out, err = self.run_sign(src)
        self.assertEqual(code, 0, err)
        self.assertIn("signed: C2PA manifest (ES256) by Test Signer; parent ingredient chatgpt.png with its "
                      "Content Credentials", out)
        self.assertNotIn("not carried over", out)
        data = self.output()
        self.assertEqual(realism.disclosure_problems(data, "generated"), [])
        parent = self.store(raw, "image/png")
        store = self.store(data)
        mine = self.active(store)
        self.assertEqual(mine["claim_generator_info"][0]["name"], "snapshot-realism")
        self.assertEqual([(a["action"], a.get("digitalSourceType")) for a in self.actions(mine)],
                         [("c2pa.opened", None), ("c2pa.edited", TRAINED)])
        self.assertEqual(self.actions(mine)[1]["softwareAgent"],
                         {"name": "snapshot-realism", "version": realism.__version__})
        (ingredient,) = mine["ingredients"]
        self.assertEqual((ingredient["relationship"], ingredient["title"]), ("parentOf", "chatgpt.png"))
        self.assertEqual(ingredient["active_manifest"], parent["active_manifest"])
        carried = store["manifests"][parent["active_manifest"]]
        self.assertEqual(carried["claim_generator_info"][0]["name"], "OpenAI Media Service API")
        self.assertIn({"name": "ChatGPT", "version": "gpt-image"},
                      [a.get("softwareAgent") for a in self.actions(carried)])
        failures = store["validation_results"]["activeManifest"].get("failure") or []
        self.assertEqual([f["code"] for f in failures], ["signingCredential.untrusted"])  # a private signer
        self.assertEqual(realism.disclosure_problems(data, "generated", parent["active_manifest"]), [])

    def test_edited_declares_a_composite_and_a_plain_parent(self):
        src = self.save("edit.png", b"")
        to_image(scene()).save(src)
        code, out, err = self.run_sign(src, "edited")
        self.assertEqual(code, 0, err)
        self.assertIn("parent ingredient edit.png (no Content Credentials of its own)", out)
        data = self.output()
        self.assertEqual(realism.disclosure_problems(data, "edited"), [])
        mine = self.active(self.store(data))
        self.assertEqual([a.get("digitalSourceType") for a in self.actions(mine) if a["action"] == "c2pa.edited"],
                         [realism.IPTC_DST + "compositeWithTrainedAlgorithmicMedia"])
        self.assertNotIn("active_manifest", mine["ingredients"][0])

    def test_camera_claims_no_source_type(self):
        src = self.save("photo.jpg", b"")
        small().save(src, "JPEG")
        code, out, err = self.run_sign(src, "camera")
        self.assertEqual(code, 0, err)
        data = self.output()
        self.assertEqual(realism.disclosure_problems(data, "camera"), [])
        mine = self.active(self.store(data))
        self.assertEqual([a.get("digitalSourceType") for a in self.actions(mine)], [None, None])
        self.assertEqual(realism.source_types(realism.split_app11(data)[0]), set())

    def test_camera_credentials_carry_over_with_a_personal_data_warning(self):
        exif = {"label": "stds.exif", "data": {"@context": {"exif": "http://ns.adobe.com/exif/1.0/"},
                                               "exif:GPSLatitude": "43,30.5N"}}
        raw = self.camera_manifest_png([exif])
        src = self.save("camera.png", raw)
        code, out, err = self.run_sign(src, "camera")
        self.assertEqual(code, 0, err)
        self.assertIn("warning: the input's Content Credentials hold stds.exif", out)
        data = self.output()
        self.assertIn(b"digitalCapture", data)  # inside the parent's manifest, in APP11
        self.assertEqual(realism.disclosure_problems(data, "camera"), [])
        store = self.store(data)
        parent = self.store(raw, "image/png")["active_manifest"]
        self.assertIn(parent, store["manifests"])
        self.assertEqual(realism.c2pa_personal(store), ["stds.exif"])

    def test_no_personal_data_warning_without_such_assertions(self):
        src = self.save("camera.png", self.camera_manifest_png())
        code, out, err = self.run_sign(src, "camera")
        self.assertEqual(code, 0, err)
        self.assertNotIn("warning:", out)

    def test_bmp_parent_is_recorded(self):
        src = self.save("old.bmp", b"")
        small().save(src, "BMP")
        code, out, err = self.run_sign(src)
        self.assertEqual(code, 0, err)
        mine = self.active(self.store(self.output()))
        self.assertEqual((mine["ingredients"][0]["format"], mine["ingredients"][0]["title"]), ("image/bmp", "old.bmp"))

    def test_releases_before_0_38_are_refused(self):
        # 0.37 rejects "no timestamp server" and 0.32 to 0.37 fail on an empty one, so they cannot sign here
        for have, ok in (("0.37.12", False), ("0.9.0", False), ("0.38.0", True), ("1.2.0", True)):
            with mock.patch("importlib.metadata.version", return_value=have):
                module, why = realism.load_c2pa()
            self.assertEqual(module is not None, ok, have)
            if not ok:
                self.assertEqual(why, "c2pa-python 0.38 or newer (this is %s): python -m pip install --upgrade "
                                      "c2pa-python" % have)

    # what the output check refuses

    def test_check_refuses_another_origin(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        self.assertEqual(self.run_sign(src)[0], 0)
        data = self.output()
        problems = realism.manifest_problems(data, "edited")
        self.assertIn("C2PA c2pa.edited action missing or not declaring the origin", problems)
        self.assertIn("C2PA action declares another DigitalSourceType", problems)
        self.assertIn("C2PA c2pa.edited action missing or not declaring the origin",
                      realism.manifest_problems(data, "camera"))

    def test_check_refuses_changed_pixels(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        self.assertEqual(self.run_sign(src)[0], 0)
        data = bytearray(self.output())
        data[-40] ^= 0x01  # one bit of the entropy-coded scan, before EOI
        problems = realism.manifest_problems(bytes(data), "generated")
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("C2PA validation failed ("), problems)
        self.assertIn("assertion.dataHash.mismatch", problems[0])

    def test_check_refuses_another_generator(self):
        data = self.custom(unsigned(), generator="Other Tool")
        self.assertEqual(realism.manifest_problems(data, "generated"), ["C2PA claim generator is not snapshot-realism"])

    def test_check_refuses_a_missing_parent(self):
        data = self.custom(unsigned(), intent=False, parent=False,
                           actions=[{"action": "c2pa.created", "digitalSourceType": TRAINED},
                                    {"action": "c2pa.edited", "digitalSourceType": TRAINED}])
        self.assertEqual(realism.manifest_problems(data, "generated"), ["C2PA parent ingredient missing"])

    def test_check_refuses_a_second_source_type(self):
        data = self.custom(unsigned(), actions=[{"action": "c2pa.edited", "digitalSourceType": TRAINED},
                                                {"action": "c2pa.color_adjustments",
                                                 "digitalSourceType": realism.IPTC_DST + "digitalCapture"}])
        self.assertEqual(realism.manifest_problems(data, "generated"),
                         ["C2PA action declares another DigitalSourceType"])

    def test_check_refuses_two_edits(self):
        data = self.custom(unsigned(), actions=[{"action": "c2pa.edited", "digitalSourceType": TRAINED}] * 2)
        self.assertEqual(realism.manifest_problems(data, "generated"),
                         ["C2PA c2pa.edited action missing or not declaring the origin"])

    def test_check_refuses_a_lost_parent_manifest(self):
        data = self.custom(unsigned())
        self.assertEqual(realism.manifest_problems(data, "generated"), [])
        self.assertEqual(realism.manifest_problems(data, "generated", "urn:c2pa:the-input"),
                         ["the input's C2PA manifest is not carried over as the parent's"])

    def test_check_refuses_an_unreadable_manifest(self):
        data = with_app11(unsigned(), b"\x00\x00\x00\x20jumb\x00\x00\x00\x18jumdc2pa")
        problems = realism.disclosure_problems(data, "generated")
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("C2PA manifest unreadable ("), problems)

    # credentials

    def test_a_self_signed_certificate_is_refused_with_a_hint(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import datetime
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "self")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        chain = self.save("self.pem", cert.public_bytes(serialization.Encoding.PEM))
        pem = self.save("self-key.pem", key.private_bytes(serialization.Encoding.PEM,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        code, out, err = self.run_sign(src, chain=chain, key=pem)
        self.assertEqual(code, 1)
        self.assertIn("cannot sign:", err)
        self.assertIn("self-signed certificate is refused", err)
        self.assertIn("nothing written", err)
        self.assertFalse(os.path.exists(self.path("out.jpg")))

    def test_the_key_must_belong_to_the_certificate(self):
        _, other_key = self.pair("other")
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        code, out, err = self.run_sign(src, key=other_key)
        self.assertEqual(code, 2)
        self.assertIn("--sign: the key does not belong to the first certificate", err)
        self.assertFalse(os.path.exists(self.path("out.jpg")))

    def test_unusable_key_and_certificate_files_are_argument_errors(self):
        from cryptography.hazmat.primitives import serialization
        with open(self.key, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        locked = self.save("locked.pem", key.private_bytes(serialization.Encoding.PEM,
                                                           serialization.PrivateFormat.PKCS8,
                                                           serialization.BestAvailableEncryption(b"secret")))
        junk = self.save("junk.pem", b"not a pem file")
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        for kw, message in ((dict(key=locked), "the private key is encrypted"),
                            (dict(key=junk), "the key file is not a PEM private key"),
                            (dict(chain=junk), "the certificate file does not start with a PEM certificate"),
                            (dict(key=self.path("absent.pem")), "--sign: cannot read")):
            code, out, err = self.run_sign(src, **kw)
            self.assertEqual(code, 2, message)
            self.assertIn(message, err)
        self.assertFalse(os.path.exists(self.path("out.jpg")))

    def test_the_algorithm_follows_the_key(self):
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        for stem, key, alg in (("p384", ec.generate_private_key(ec.SECP384R1()), "ES384"),
                               ("p521", ec.generate_private_key(ec.SECP521R1()), "ES512"),
                               ("rsa", rsa.generate_private_key(65537, 2048), "PS256"),
                               ("ed25519", ed25519.Ed25519PrivateKey.generate(), "ED25519")):
            chain, pem = self.pair(stem, key=key)
            code, out, err = self.run_sign(src, "generated", "--overwrite", chain=chain, key=pem)
            self.assertEqual(code, 0, "%s: %s" % (stem, err))
            self.assertIn("signed: C2PA manifest (%s)" % alg, out)
            info = self.active(self.store(self.output()))["signature_info"]
            self.assertEqual(info["alg"].upper(), alg)

    # the rest of the run

    def test_no_network_request_for_a_remote_manifest_reference(self):
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
               '<rdf:Description rdf:about="" xmlns:dcterms="http://purl.org/dc/terms/" '
               'dcterms:provenance="http://127.0.0.1:%d/manifest.c2pa"/></rdf:RDF></x:xmpmeta>'
               % server.server_address[1]).encode()
        jpeg = io.BytesIO()
        small().save(jpeg, "JPEG")
        src = self.save("remote.jpg", realism.insert_xmp(jpeg.getvalue(), xmp))
        code, out, err = self.run_sign(src, "camera")
        self.assertEqual(code, 0, err)
        self.assertEqual(hits, [])
        self.assertIn("warning: the input's C2PA manifest could not be read (", out)
        self.assertIn("not carried over", out)

    def test_compare_sheet_stays_unsigned(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        code, out, err = self.run_sign(src, "generated", "--compare")
        self.assertEqual(code, 0, err)
        sheet = self.output("out_compare.jpg")
        self.assertNotIn(b"jumdc2pa", sheet)
        self.assertEqual(realism.disclosure_problems(sheet, "generated"), [])
        self.assertIn(b"jumdc2pa", self.output())

    def test_keep_date_survives_signing(self):
        jpeg = io.BytesIO()
        small().save(jpeg, "JPEG")
        raw = jpeg.getvalue()
        src = self.save("dated.jpg", raw[:2] + exif_segment({0x010F: "Phone"}, DATES) + raw[2:])
        code, out, err = self.run_sign(src, "camera", "--keep-date")
        self.assertEqual(code, 0, err)
        data = self.output()
        self.assertEqual(realism.disclosure_problems(data, "camera"), [])
        with Image.open(io.BytesIO(data)) as im:
            self.assertEqual(dict(im.getexif().get_ifd(0x8769)).get(0x9003), DATES[0x9003])

    def test_a_signed_output_is_refused_as_input(self):
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        self.assertEqual(self.run_sign(src)[0], 0)
        code, out, err = run_safe([self.path("out.jpg"), "--origin", "generated", "--preset", "day"])
        self.assertEqual(code, 2)
        self.assertIn("already a snapshot-realism output", err)

    def test_without_sign_the_caution_names_the_flag(self):
        with open(OPENAI_PNG, "rb") as f:
            src = self.save("chatgpt.png", f.read())
        code, out, err = run_safe([src, "--origin", "generated", "--preset", "day", "--seed", "1"])
        self.assertEqual(code, 0, err)
        self.assertIn("C2PA Content Credentials are not carried over", out)
        self.assertIn("rerun with --sign CERT KEY", out)

    def test_make_cert_writes_a_pair_that_signs(self):
        code = make_cert_main([self.tmp, "--name", "Ada Example", "--days", "10"])
        self.assertEqual(code, 0)
        chain, key = self.path(make_cert.CHAIN), self.path(make_cert.KEY)
        with open(chain, "rb") as f:
            self.assertEqual(f.read().count(b"BEGIN CERTIFICATE"), 2)  # signing certificate, then its root
        if os.name != "nt":
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
        src = self.save("in.png", b"")
        to_image(scene()).save(src)
        code, out, err = self.run_sign(src, chain=chain, key=key)
        self.assertEqual(code, 0, err)
        self.assertIn("by Ada Example;", out)


if __name__ == "__main__":
    unittest.main()

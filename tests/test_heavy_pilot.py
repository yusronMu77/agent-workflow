"""Heavy-pilot infrastructure checks without inference or Blender dependencies."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import struct
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pilot_ab import prepare
from pilot_heavy import scenarios
from pilot_native import main, run_trial
from png_probe import inspect_png


class HeavyPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-heavy-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.backend = self.base / "backend"
        for name in ("scripts/mockup.mjs", "scripts/locate.mjs", "blender/mockup.py"):
            path = self.backend / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")

    def prepare(self):
        root = self.base / "pilot"
        prepare(root, suite="heavy-3d", design_studio=self.backend)
        return root, json.loads((root / "plan.json").read_text())

    def test_heavy_suite_has_real_multi_stage_artifacts_and_protected_checkers(self):
        root, plan = self.prepare()
        self.assertEqual(plan["suite"], "heavy-3d")
        self.assertEqual(len(plan["trials"]), 6)
        for trial in plan["trials"]:
            self.assertIn("check_delivery.py", trial["protected_sha256"])
            self.assertIn("tools/design-studio/blender/mockup.py", trial["protected_sha256"])
        for scenario in scenarios():
            def inventory(arm):
                target = root / (scenario + "-" + arm)
                return {str(p.relative_to(target)): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            self.assertEqual(inventory("A"), inventory("B"))
        for spec in scenarios().values():
            compile(spec["files"]["check_delivery.py"], "check_delivery.py", "exec")

    def test_missing_backend_does_not_create_partial_pilot(self):
        root = self.base / "pilot"
        with self.assertRaisesRegex(ValueError, "requires"):
            prepare(root, suite="heavy-3d")
        self.assertFalse(root.exists())

    def test_native_harness_needs_opt_in_before_settings_or_process(self):
        with patch("pilot_native.native_settings") as settings:
            self.assertEqual(main(["--plan", "missing.json", "--trial", "t"]), 2)
            settings.assert_not_called()

    def test_native_harness_refuses_tampered_snapshot_before_process(self):
        root, plan = self.prepare()
        trial = plan["trials"][0]["trial_id"]
        (root / trial / "check_delivery.py").write_text("print('fake pass')", encoding="utf-8")
        with patch("pilot_native.native_settings") as settings:
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                run_trial(root / "plan.json", trial)
            settings.assert_not_called()

    def test_native_harness_never_replays_existing_attempt(self):
        root, plan = self.prepare()
        trial = plan["trials"][0]["trial_id"]
        (root / trial / "evidence/native-session").mkdir(parents=True)
        with patch("pilot_native.native_settings") as settings:
            with self.assertRaisesRegex(ValueError, "never silently replay"):
                run_trial(root / "plan.json", trial)
            settings.assert_not_called()

    def test_checker_rejects_missing_deliverables_instead_of_passing_by_metadata(self):
        root, plan = self.prepare()
        trial = root / "packaging-3d-A"
        result = subprocess.run([sys.executable, str(trial / "check_delivery.py")],
                                capture_output=True, text=True, cwd=trial)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["passed"])


class PngProbeTests(unittest.TestCase):
    @staticmethod
    def png(kind):
        rows = [bytes([10, 20, 30, 0, 40, 50, 60, 255]), bytes([50, 30, 15, 128, 250, 240, 230, 255])]
        encoded, previous = bytearray(), bytes(8)
        for row in rows:
            encoded.append(kind)
            for i, value in enumerate(row):
                left, up, upper_left = row[i - 4] if i >= 4 else 0, previous[i], previous[i - 4] if i >= 4 else 0
                if kind == 0:
                    predictor = 0
                elif kind == 1:
                    predictor = left
                elif kind == 2:
                    predictor = up
                elif kind == 3:
                    predictor = (left + up) // 2
                else:
                    p = left + up - upper_left
                    a, b, c = abs(p - left), abs(p - up), abs(p - upper_left)
                    predictor = left if a <= b and a <= c else up if b <= c else upper_left
                encoded.append((value - predictor) & 255)
            previous = row
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(encoded)) + chunk(b"IEND", b""))

    def test_all_png_row_filters_recover_real_alpha_extrema(self):
        for kind in range(5):
            with self.subTest(filter=kind):
                result = inspect_png(self.png(kind))
                self.assertEqual((result["width"], result["height"]), (2, 2))
                self.assertEqual((result["alpha_min"], result["alpha_max"]), (0, 1))

    def test_bad_crc_and_truncation_are_rejected(self):
        body = bytearray(self.png(0))
        body[42] ^= 1
        with self.assertRaises(ValueError):
            inspect_png(body)
        with self.assertRaises((ValueError, struct.error)):
            inspect_png(self.png(0)[:-7])


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("badge_layout_generator", ROOT / "tools" / "generate_badge_layout.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class SharedBadgeLayoutTests(unittest.TestCase):
    def setUp(self):
        self.layout = generator.load_layout()

    def test_committed_header_matches_json(self):
        self.assertEqual(generator.HEADER.read_text(), generator.render_header(self.layout))

    def test_generation_is_reproducible_across_json_key_order(self):
        reordered = json.loads(json.dumps(self.layout, sort_keys=True))
        self.assertEqual(generator.render_header(self.layout), generator.render_header(reordered))

    def test_compiled_c_coordinates_equal_preview_json(self):
        rows = []
        names = sorted(self.layout["elements"])
        for name in names:
            prefix = "PASSPORT_LAYOUT_" + name.upper() + "_"
            rows.append('printf("%d %d %d %d\\n", ' + ", ".join(prefix + key for key in ("X", "Y", "WIDTH", "HEIGHT")) + ");")
        source = '#include <stdio.h>\n#include "passport_layout.h"\nint main(void) {\n' + "\n".join(rows) + '\nreturn 0;\n}\n'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "layout.c").write_text(source)
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "main"),
                           str(path / "layout.c"), "-o", str(path / "layout")], check=True, capture_output=True)
            actual = subprocess.check_output([str(path / "layout")], text=True).splitlines()
        for name, row in zip(names, actual):
            box = self.layout["elements"][name]
            self.assertEqual([int(value) for value in row.split()], [box[key] for key in ("x", "y", "width", "height")], name)

    def test_freshness_check_never_changes_stale_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "stale.h"
            output.write_text("old header\n")
            result = subprocess.run([os.sys.executable, str(ROOT / "tools" / "generate_badge_layout.py"), "--check", "--output", str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "old header\n")

    def test_out_of_bounds_or_distorted_avatar_is_rejected(self):
        for property_name, value in (("x", 239), ("source_height", 120), ("pivot_x", 64)):
            bad = copy.deepcopy(self.layout)
            bad["elements"]["avatar"][property_name] = value
            with self.assertRaises(ValueError):
                generator.validate(bad)

    def test_font_metrics_match_the_actual_lvgl_font(self):
        bad = copy.deepcopy(self.layout)
        bad["elements"]["time"]["line_height"] = 18
        with self.assertRaises(ValueError):
            generator.validate(bad)
        bad = copy.deepcopy(self.layout)
        bad["elements"]["intro"]["height"] = 32
        with self.assertRaises(ValueError):
            generator.validate(bad)

    def test_firmware_consumes_generated_badge_and_status_geometry(self):
        source = (ROOT / "main" / "passport.c").read_text()
        for name in ("TIME", "BATTERY", "NAME", "TITLE", "INTRO", "TOKEN_CAPTION", "TOKEN_VALUE"):
            self.assertIn("LAYOUT_LABEL(" + name + ",", source)
        for name in ("AVATAR_X", "AVATAR_Y", "AVATAR_SCALE_256", "SEPARATOR_X", "SEPARATOR_Y", "SEPARATOR_WIDTH", "SEPARATOR_HEIGHT"):
            self.assertIn("PASSPORT_LAYOUT_" + name, source)


if __name__ == "__main__":
    unittest.main()

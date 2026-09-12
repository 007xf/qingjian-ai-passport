"""Check original source pixels, including white matte and alpha; no device IO."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dino_generator", ROOT / "tools/generate_dino_sprites.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class OriginalSpriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = generator.load_source()

    def test_generated_headers_match_pinned_source(self):
        for path, content in generator.generated_files(self.source).items():
            self.assertEqual((ROOT / path).read_text(), content, path)

    def test_every_converted_pixel_preserves_alpha_and_night_color(self):
        for name in generator.FRAMES:
            image = generator.frame(self.source, name)
            color, stride, raw = generator.pixel_data(image)
            self.assertEqual(len(raw), stride * image.height)
            for i, (r, g, b, a) in enumerate(image.get_flattened_data()):
                if color == "AL88":
                    lum, alpha = raw[i*2:i*2+2]
                    actual = (lum, lum, lum, alpha)
                else:
                    blue, green, red, alpha = raw[i*4:i*4+4]
                    actual = (red, green, blue, alpha)
                self.assertEqual(actual, (255-r, 255-g, 255-b, a), (name, i))

    def test_white_matte_is_black_and_body_is_gray(self):
        image = generator.frame(self.source, "trex_stand")
        output = generator.night_image(image)
        samples = set(zip(image.get_flattened_data(), output.get_flattened_data()))
        self.assertIn(((255,255,255,255), (0,0,0,255)), samples)
        self.assertIn(((83,83,83,255), (172,172,172,255)), samples)
        self.assertTrue(any(a[3] == 0 and b[3] == 0 for a,b in samples))

    def test_collision_pixels_are_body_not_white_outline(self):
        for name in generator.COLLISION_FRAMES:
            image = generator.frame(self.source, name)
            rows = generator.collision_rows(image)
            self.assertLessEqual(image.width, 59)
            for y, row in enumerate(rows):
                self.assertEqual(row >> image.width, 0)
                for x in range(image.width):
                    r, _, _, alpha = image.getpixel((x,y))
                    self.assertEqual(bool(row & (1 << x)), alpha >= 128 and r < 128)

    def test_original_dimensions_and_draw_padding(self):
        self.assertEqual(generator.FRAMES["trex_stand"][2:], (44,47))
        self.assertEqual(generator.FRAMES["trex_duck_0"][2:], (59,47))
        self.assertEqual(generator.FRAMES["cactus_small"][2:], (17,35))
        self.assertEqual(generator.FRAMES["cactus_large"][2:], (25,50))
        self.assertEqual(generator.FRAMES["bird_0"][2:], (46,40))
        self.assertEqual(generator.FRAMES["game_over"][2:], (191,11))
        self.assertEqual(generator.FRAMES["restart"][2:], (36,32))
        self.assertEqual(generator.FRAMES["hi"][2:], (20,13))


if __name__ == "__main__":
    unittest.main()

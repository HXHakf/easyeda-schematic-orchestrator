import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import measure_svg_text as measure
except ModuleNotFoundError:
    measure = None


class SvgTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(measure, 'native SVG text measurer must exist')

    def test_active_svg_rejected(self):
        with self.assertRaises(ValueError):
            measure.prepare_svg('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')

    def test_external_svg_reference_rejected(self):
        with self.assertRaises(ValueError):
            measure.prepare_svg('<svg xmlns="http://www.w3.org/2000/svg"><use href="https://example.com/a.svg"/></svg>')

    def test_native_embedded_image_does_not_block_text_measurement(self):
        self.assertIn('svg', measure.prepare_svg('<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/svg+xml;base64,PHN2Zy8+"/></svg>'))

    def test_real_browser_bounds_include_group_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.svg'
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" viewBox="0 0 400 200">'
                            '<g transform="translate(100,50)"><text id="t" x="0" y="0" font-family="Arial" font-size="20">TEST</text></g>'
                            '<text id="hidden" display="none">HIDDEN</text></svg>', encoding='utf-8')
            result = measure.measure(path, [1, 0, 0, -1, 0, 0])
            self.assertEqual([x['id'] for x in result['texts']], ['t'])
            rect = result['texts'][0]['bbox']
            self.assertGreaterEqual(rect[0], 99)
            self.assertGreater(rect[2], 130)
            self.assertLess(rect[1], -45)
            self.assertLess(rect[3], -20)


if __name__ == '__main__':
    unittest.main()

import logging
import tempfile
import unittest
from pathlib import Path
from mutagen.id3 import ID3, TIT2
import convert_tags as c


class ConverterTests(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(c.decode_cp1251_text('Привет мир'.encode('cp1251').decode('latin1')), 'Привет мир')
        for value in ['Привет мир', 'One For The Road', 'Beyoncé', 'Дискотека 80х']:
            self.assertIsNone(c.decode_cp1251_text(value))

    def test_backup_dry_run_uppercase_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'test.MP3'
            tags = ID3()
            tags.add(TIT2(encoding=0, text=['Привет мир'.encode('cp1251').decode('latin1')]))
            tags.save(path)
            original = path.read_bytes()
            logger = logging.getLogger('test')
            c.HAS_TQDM = False
            stats = c.scan_directory(root, True, logger)
            self.assertEqual(stats['tags_fixed_files'], 1)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_name(path.name + '.tag-backup').exists())
            stats = c.scan_directory(root, False, logger)
            self.assertEqual(stats['errors'], 0)
            self.assertEqual(str(ID3(path)['TIT2']), 'Привет мир')
            self.assertEqual(path.with_name(path.name + '.tag-backup').read_bytes(), original)
            self.assertEqual(c.scan_directory(root, False, logger)['tags_fixed_files'], 0)


if __name__ == '__main__':
    unittest.main()

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from checkpoints import load_jsonl


class CheckpointTest(unittest.TestCase):
    def test_interrupted_tail_is_backed_up_and_complete_records_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder, 'checkpoint.jsonl')
            p.write_bytes(b'{"key":"a"}\n{"key":"b')
            self.assertEqual(load_jsonl(p), [{'key': 'a'}])
            self.assertEqual(p.read_bytes(), b'{"key":"a"}\n')
            self.assertEqual(next(pathlib.Path(folder).glob('*.bin')).read_bytes(), b'{"key":"b')
            self.assertEqual(load_jsonl(p), [{'key': 'a'}])

    def test_valid_unterminated_record_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder, 'checkpoint.jsonl')
            p.write_bytes(b'{"key":"a"}')
            self.assertEqual(load_jsonl(p), [{'key': 'a'}])
            with p.open('a') as stream:
                stream.write('{"key":"b"}\n')
            self.assertEqual(len(load_jsonl(p)), 2)

    def test_complete_corruption_is_not_silently_discarded(self):
        with tempfile.TemporaryDirectory() as folder:
            p = pathlib.Path(folder, 'checkpoint.jsonl')
            original = b'{"key":"a"}\nbroken\n'
            p.write_bytes(original)
            with self.assertRaises(ValueError):
                load_jsonl(p)
            self.assertEqual(p.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

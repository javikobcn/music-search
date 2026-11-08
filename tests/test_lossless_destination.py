import os,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('SLSKD_URL','http://slskd.invalid')
from app import monitor
class DestinationTests(unittest.TestCase):
 def test_lossless_and_lossy_files_use_correct_folder(self):
  for suffix,folder in [('.flac','lossless'),('.wav','lossless'),('.WAV','lossless'),('.alac','lossless'),('.aiff','lossless'),('.mp3','normal'),('.m4a','normal')]:
   with self.subTest(suffix=suffix),tempfile.TemporaryDirectory() as temp:
    root=Path(temp);inbox=root/'inbox';inbox.mkdir();source=inbox/('original'+suffix);source.write_bytes(b'unchanged audio bytes')
    with patch.object(monitor,'DOWNLOADS_ROOT',root),patch.object(monitor,'LOSSLESS_ROOT',root/'lossless'),patch.object(monitor,'NORMAL_ROOT',root/'normal'):
     actual=monitor.move_single_download(source,'Fisher','Losing It')
    self.assertEqual(actual.parent,root/folder);self.assertEqual(actual.read_bytes(),b'unchanged audio bytes');self.assertFalse(source.exists())
if __name__=='__main__':unittest.main()

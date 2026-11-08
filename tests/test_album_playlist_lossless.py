import os,unittest,tempfile,asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,AsyncMock
os.environ.update({'SLSKD_URL':'http://slskd.invalid','TELEGRAM_BOT_TOKEN':'123:test','YOUTUBE_API_KEY':'test'})
from app import monitor,telegram_bot as bot
from app.services import manual_playlist_service as manual

class AlbumPlaylistLosslessTests(unittest.TestCase):
 def test_each_lossless_format_classifies_like_flac(self):
  for extension in ('.flac','.wav','.alac','.aiff','.WAV','.AIFF'):
   with self.subTest(extension=extension),tempfile.TemporaryDirectory() as temp:
    root=Path(temp);folder=root/'sample';folder.mkdir();(folder/('Artist - Track'+extension)).write_bytes(b'audio')
    with patch.object(monitor,'PLAYLISTS_PENDING_ROOT',root):
     self.assertEqual(monitor.classify_playlist_folder('sample'),('lossless',1,0,1))
    self.assertEqual(bot.album_playlist_quality_name('Album',[str(folder/('song'+extension))]),'Album - lossless')

 def test_mixed_lists_keep_majority_and_tie_rule(self):
  for extensions,expected in [(('.wav','.alac','.mp3'),'lossless'),(('.aiff','.mp3'),'normal'),(('.wav','.mp3','.mp3'),'normal'),(('.mp3',),'normal')]:
   with self.subTest(extensions=extensions),tempfile.TemporaryDirectory() as temp:
    root=Path(temp);folder=root/'sample';folder.mkdir()
    files=[]
    for index,extension in enumerate(extensions):
     p=folder/(str(index)+extension);p.write_bytes(b'audio');files.append(str(p))
    with patch.object(monitor,'PLAYLISTS_PENDING_ROOT',root):self.assertEqual(monitor.classify_playlist_folder('sample')[0],expected)
    self.assertEqual(bot.album_playlist_quality_name('Album',files),'Album - lossless' if expected=='lossless' else 'Album')

 def test_finalized_playlist_goes_to_lossless_and_m3u_includes_all_formats(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);pending=root/'pending';source=pending/'sample';source.mkdir(parents=True)
   names=['01.flac','02.wav','03.alac','04.aiff']
   for name in names:(source/name).write_bytes(name.encode())
   with patch.multiple(monitor,DOWNLOADS_ROOT=root,PLAYLISTS_PENDING_ROOT=pending,LOSSLESS_ROOT=root/'lossless',NORMAL_ROOT=root/'normal',PLAYLIST_FILES_ROOT=root/'m3u'):
    target=monitor.finalize_playlist_folder('sample','Album')
    self.assertEqual(target,root/'lossless/playlists/Album')
    self.assertEqual([p.name for p in sorted(target.iterdir())],names)
    lines=(root/'m3u/Album - lossless.m3u').read_text().splitlines()
    self.assertEqual([Path(line).name for line in lines[1:]],names)

 def test_m3u_import_strips_all_lossless_extensions(self):
  for ext in ('.flac','.wav','.alac','.aiff','.ALAC'):
   tracks=bot.parse_m3u_playlist('/library/Artist - Track'+ext)
   self.assertEqual(tracks[0]['artist'],'Artist');self.assertEqual(tracks[0]['track'],'Track')

 def test_album_label_does_not_duplicate_suffix(self):
  self.assertEqual(bot.album_playlist_quality_name('Album - lossless',['track.wav']),'Album - lossless')

 def test_manual_playlist_stats_include_alac_and_aiff(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   for name in ('a.flac','b.wav','c.alac','d.aiff'):(root/name).write_bytes(b'123')
   with patch.object(manual,'get_real_audio_length',return_value=60):result=manual.get_audio_directory_stats(root)
   self.assertEqual(result['track_count'],4);self.assertEqual(result['duration_seconds'],240);self.assertEqual(result['size_bytes'],12)

class NotificationTests(unittest.IsolatedAsyncioTestCase):
 async def test_lossless_completion_message_matches_destination(self):
  for extension in ('flac','wav','alac','aiff','mp3'):
   app=NS(bot=NS(send_message=AsyncMock()))
   with patch.object(bot.asyncio,'sleep',AsyncMock()),patch.object(bot,'api_get',AsyncMock(return_value={'status':'completed','extension':extension,'local_path':None})):
    await bot.watch_job(app,1,'job','Artist','Track')
   self.assertIn('📁 '+('normal' if extension=='mp3' else 'lossless'),app.bot.send_message.call_args.kwargs['text'])

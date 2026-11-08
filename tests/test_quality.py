import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import os
os.environ.update({"TELEGRAM_BOT_TOKEN":"123:test", "YOUTUBE_API_KEY":"test", "SLSKD_URL":"http://slskd.invalid"})
from app import telegram_bot as bot, main, worker
from app.entities import DownloadJob, SearchResult
from app.models import SearchRequest
from app.services import search_service as search, music_service as music, queue_service as queue, album_service as albums
from fastapi.testclient import TestClient


def update(text='Artist - Track', document=None):
    message=NS(text=text,document=document,reply_text=AsyncMock())
    return NS(message=message,effective_chat=NS(id=1,type='private'),effective_user=NS(id=1,username='test'))


def context():
    return NS(user_data={},args=[],application=NS(create_task=Mock()),bot=NS(get_file=AsyncMock()))


class QualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_searches_prompt_before_any_lookup(self):
        for kind in ('text','album','m3u'):
            u=update(document=NS(file_name='test.m3u'));c=context();c.args=['Artist']
            handler={'text':bot.handle_text,'album':bot.album_command,'m3u':bot.m3u_playlist_handler}[kind]
            with patch.object(bot,'api_post',AsyncMock()) as api:
                await handler(u,c)
                api.assert_not_called();c.bot.get_file.assert_not_called()
            call=u.message.reply_text.call_args
            self.assertEqual(call.args[0],'🎧 ¿Qué formato prefieres?')
            self.assertEqual([row[0].text for row in call.kwargs['reply_markup'].inline_keyboard],['🎵 MP3 320 kbps','💎 Mejor calidad disponible'])

    async def test_prompt_single_use_stale_and_args(self):
        u=update();c=context();c.args=['Artist'];await bot.ask_search_quality(u,c,'album')
        first=c.user_data['pending_search_quality']['token']
        await bot.ask_search_quality(u,c,'album')
        token=c.user_data['pending_search_quality']['token']
        query=NS(data=f'quality:{first}:best',answer=AsyncMock(),edit_message_text=AsyncMock())
        with patch.object(bot,'run_album_search',AsyncMock()) as run:
            await bot.search_quality_callback(NS(callback_query=query),c);run.assert_not_called()
            query.data=f'quality:{token}:mp3_320';c.args=None
            await bot.search_quality_callback(NS(callback_query=query),c)
            run.assert_awaited_once_with(u,c,'mp3_320')
            await bot.search_quality_callback(NS(callback_query=query),c)
            self.assertEqual(run.await_count,1);self.assertIsNone(c.args)

    async def test_special_name_states_do_not_prompt(self):
        for key in ('waiting_playlist_final_name_token','waiting_album_playlist_name','waiting_new_playlist_name'):
            c=context();c.user_data[key]='expired';u=update()
            with patch.object(bot,'ask_search_quality',AsyncMock()) as ask:
                await bot.handle_text(u,c);ask.assert_not_called()

    async def test_text_download_sends_choice(self):
        u=update();u.message.date=NS(isoformat=lambda:'test');c=context()
        u.message.reply_text.return_value=NS(edit_text=AsyncMock())
        with patch.object(bot,'api_post',AsyncMock(side_effect=[{'status':'resolved','artist':'Artist','track':'Track'},{'status':'not_found'}])) as api:
            await bot.run_text_search(u,c,'mp3_320')
            self.assertEqual(api.call_args.args[1]['quality_mode'],'mp3_320')

    async def test_playlist_tracks_keep_mode_in_normal_and_streaming(self):
        for streaming in (False, True):
            tracks=[{"artist":"a","track":"one"},{"artist":"a","track":"two"}]
            q=None
            if streaming:
                q=asyncio.Queue()
                for item in tracks+[None,None]:await q.put(item)
            app=NS(bot=NS(send_message=AsyncMock()))
            with patch.object(bot,'api_post',AsyncMock(return_value={"status":"not_found"})) as api,patch.object(bot,'watch_playlist_jobs',AsyncMock()):
                await bot.process_playlist_download(app,1,None if streaming else tracks,'test',track_queue=q,quality_mode='mp3_320')
                self.assertEqual([c.args[1]['quality_mode'] for c in api.call_args_list],['mp3_320','mp3_320'])

    async def test_callbacks_capture_mode_before_background_run(self):
        for handler,key in [(bot.spotify_download_callback,'spotify'),(bot.youtube_download_callback,'youtube'),(bot.youtube_analyze_callback,'youtube')]:
            c=context();c.user_data.update({'spotify_playlist':{'playlist':{'name':'test'},'tracks':[{'artist':'a','track':'t'}]},'youtube_resolved':[{'artist':'a','track':'t'}],'youtube_playlist':[{'title':'a - t'}],key+'_quality_mode':'mp3_320'})
            query=NS(data='test',answer=AsyncMock(),edit_message_text=AsyncMock(),message=NS(message_id=1))
            u=NS(callback_query=query,effective_chat=NS(id=1))
            captured=[]
            def collect(coroutine):
                captured.append(coroutine.cr_frame.f_locals['quality_mode']);coroutine.close()
            c.application.create_task=collect
            await handler(u,c)
            c.user_data[key+'_quality_mode']='best'
            self.assertEqual(captured,['mp3_320'])



class BackendQualityTests(unittest.TestCase):
    def setUp(self):
        search._search_cache.clear()
        self.flac=SearchResult('a','track.flac',1,1,[],bitRate=1000)
        self.mp3=SearchResult('b','track.mp3',1,1,[],bitRate=320)
        self.low=SearchResult('c','track.mp3',1,1,[],bitRate=128)
        self.unknown=SearchResult('d','track.mp3',1,1,[])

    def test_search_filter_cache_and_retry(self):
        with patch.object(search,'SlskdClient') as client, patch.object(search,'_build_candidate_shortlist',side_effect=lambda results,**kw:list(results)):
            client.return_value.search.return_value=[self.flac,self.low,self.mp3,self.unknown]
            self.assertIs(search.search_best('a','t',quality_mode='best'),self.flac)
            self.assertIs(search.search_best('a','t',quality_mode='mp3_320'),self.mp3)
            self.assertIsNone(search.search_best('a','t',excluded_sources=['b'],quality_mode='mp3_320'))
            self.assertIs(search.search_best('a','t',excluded_sources=['a'],quality_mode='best'),self.low)

    def test_slash_fallback_filters_too(self):
        with patch.object(search,'SlskdClient') as client, patch.object(search,'_build_candidate_shortlist',side_effect=lambda results,**kw:list(results)):
            client.return_value.search.side_effect=[[self.flac],[self.low,self.mp3]]
            self.assertIs(search.search_best('a','t / other',quality_mode='mp3_320'),self.mp3)

    def test_job_persistence_and_legacy_default(self):
        j=DownloadJob('a','t',quality_mode='mp3_320')
        self.assertEqual(DownloadJob.from_dict(j.to_dict()).quality_mode,'mp3_320')
        data=j.to_dict();data.pop('quality_mode')
        self.assertEqual(DownloadJob.from_dict(data).quality_mode,'best')

    def test_track_to_search_and_job(self):
        with patch.object(music,'find_existing',return_value=None),patch.object(music,'search_best',return_value=self.mp3) as find,patch.object(music,'enqueue',return_value=1) as enqueue:
            music.get_track('a','t',quality_mode='mp3_320')
            self.assertEqual(find.call_args.kwargs['quality_mode'],'mp3_320')
            self.assertEqual(enqueue.call_args.args[0].quality_mode,'mp3_320')

    def test_existing_jobs_separate_formats(self):
        j=DownloadJob('a','t',quality_mode='best')
        with patch.object(queue,'_downloads',[j]):
            self.assertIsNone(queue.find_existing('a','t',quality_mode='mp3_320'))
            self.assertIs(queue.find_existing('a','t'),j)

    def test_worker_retry_uses_persisted_choice(self):
        j=DownloadJob('a','t',quality_mode='mp3_320',retry_count=1,failed_sources=['bad'])
        with patch.object(worker,'dequeue',side_effect=[j,KeyboardInterrupt]),patch.object(worker,'persist'),patch.object(worker,'search_best',return_value=None) as find:
            with self.assertRaises(KeyboardInterrupt):worker.worker()
            self.assertEqual(find.call_args.kwargs['quality_mode'],'mp3_320')
            self.assertEqual(find.call_args.kwargs['excluded_sources'],['bad'])

    def test_api_validates_and_forwards_mode(self):
        client=TestClient(main.app);headers={}
        with patch.object(main,'get_track',return_value={'status':'not_found'}) as get:
            response=client.post('/search-and-download',headers=headers,json={'artist':'a','track':'t','quality_mode':'mp3_320'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(get.call_args.kwargs['quality_mode'],'mp3_320')
            self.assertEqual(client.post('/search-and-download',headers=headers,json={'quality_mode':'invalid'}).status_code,422)

    def test_album_propagates_to_every_track_and_thread(self):
        with patch.object(albums,'get_album_tracks',return_value=['one','two']),patch.object(albums,'get_track',return_value={}) as get:
            albums.get_album('a','album',quality_mode='mp3_320')
            self.assertEqual([call.kwargs['quality_mode'] for call in get.call_args_list],['mp3_320','mp3_320'])
        with patch.object(albums,'Thread') as thread:
            albums.start_album_download('a','album',quality_mode='mp3_320')
            self.assertEqual(thread.call_args.kwargs['args'][-1],'mp3_320')

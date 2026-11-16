"""Регрессии обновления найденного текста: без плеера, ламп и запущенного сервера."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pc'))
import server
from synced_lyrics import Lyrics


class RefreshTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server.lyrics.clear()
        server.lyrics_meta.clear()
        server.lyrics_jobs.clear()
        server.lyrics_seen.clear()
        self.tid = 'abcdef123456'

    async def fetch(self, value, dur=140000):
        with patch.object(server.synced_lyrics, 'fetch', AsyncMock(return_value=value)):
            await server.fetch_lyrics({'http': None}, self.tid, 'Song', 'Artist', '', dur)

    async def test_plain_fallback_is_upgraded_and_revision_changes(self):
        await self.fetch(Lyrics([], 'Plain', 'lrclib'))
        meta = server.lyrics_meta[self.tid]
        self.assertFalse(meta['complete'])
        old_revision = meta['revision']
        meta['at'] = time.time() - 16
        self.assertTrue(server.want_lyrics(self.tid, 140000))
        await self.fetch(Lyrics([{'t': 1000, 'text': 'Word', 'words': [
            {'t': 1000, 'end': 2000, 'start': 0, 'stop': 4}]}], 'Word', 'spicy'))
        self.assertGreater(server.lyrics_meta[self.tid]['revision'], old_revision)
        self.assertEqual(server.lyrics[self.tid]['source'], 'spicy')
        self.assertTrue(server.lyrics_meta[self.tid]['complete'])

    async def test_transient_failure_does_not_erase_words(self):
        await self.fetch(Lyrics([{'t': 1000, 'text': 'Word', 'words': [
            {'t': 1000, 'end': 2000, 'start': 0, 'stop': 4}]}], 'Word', 'spicy'))
        revision = server.lyrics_meta[self.tid]['revision']
        await self.fetch(Lyrics([], 'Fallback', 'lrclib', complete=False))
        self.assertEqual(server.lyrics[self.tid]['source'], 'spicy')
        self.assertEqual(server.lyrics_meta[self.tid]['revision'], revision)

    async def test_late_duration_changes_trigger_a_new_match(self):
        await self.fetch(Lyrics([], 'Fallback', 'lrclib'), dur=111000)
        self.assertTrue(server.want_lyrics(self.tid, 141000))

    async def test_identical_response_does_not_restart_animation(self):
        value = Lyrics([{'t': 1000, 'text': 'Line'}], 'Line', 'spicy')
        await self.fetch(value)
        revision = server.lyrics_meta[self.tid]['revision']
        await self.fetch(value)
        self.assertEqual(server.lyrics_meta[self.tid]['revision'], revision)

    async def test_quality_retries_are_bounded(self):
        for _ in range(3):
            await self.fetch(Lyrics([], 'Fallback', 'lrclib'))
        server.lyrics_meta[self.tid]['at'] = 0
        self.assertFalse(server.want_lyrics(self.tid, 140000))

    async def test_local_file_remains_authoritative(self):
        await self.fetch(Lyrics([], 'My own text', 'file'))
        await self.fetch(Lyrics([{'t': 1000, 'text': 'Internet'}], 'Internet', 'spicy'))
        self.assertEqual(server.lyrics[self.tid]['source'], 'file')


if __name__ == '__main__':
    unittest.main()

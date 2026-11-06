"""Подставной плеер для проверки сервера на Linux: висит на сессионной шине как
org.mpris.MediaPlayer2.fakeplayer, «играет» один из двух треков, понимает PlayPause/Next/
Previous/SetPosition и пишет в консоль каждую команду. Обложка — файл в /tmp.

    dbus-run-session -- sh -c 'python fake_mpris.py & sleep 1; python ../../pc/server.py'
"""
import asyncio
import time

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method
from PIL import Image

TRACKS = [('Первый тестовый', 'Тестовый исполнитель', 'Альбом', 215_000_000, (200, 60, 90)),
          ('Второй тестовый', 'Другой исполнитель', 'Сингл', 180_000_000, (40, 120, 200))]


class Player(ServiceInterface):
    def __init__(self):
        super().__init__('org.mpris.MediaPlayer2.Player')
        self.i, self.playing, self.pos, self.at = 0, True, 30_000_000, time.time()
        self._cover()

    def _cover(self):
        Image.new('RGB', (300, 300), TRACKS[self.i][4]).save(f'/tmp/np-fake-{self.i}.jpg')

    def position(self):
        return int(self.pos + ((time.time() - self.at) * 1e6 if self.playing else 0))

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> 's':
        return 'Playing' if self.playing else 'Paused'

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> 'a{sv}':
        t, a, al, ln, _ = TRACKS[self.i]
        return {'mpris:trackid': Variant('o', f'/fake/track/{self.i}'), 'xesam:title': Variant('s', t),
                'xesam:artist': Variant('as', [a]), 'xesam:album': Variant('s', al),
                'mpris:length': Variant('x', ln), 'mpris:artUrl': Variant('s', f'file:///tmp/np-fake-{self.i}.jpg')}

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> 'x':
        return self.position()

    @method()
    def PlayPause(self):
        self.pos, self.at, self.playing = self.position(), time.time(), not self.playing
        print('CMD PlayPause ->', 'Playing' if self.playing else 'Paused', flush=True)

    @method()
    def Next(self):
        self.i, self.pos, self.at = (self.i + 1) % 2, 0, time.time()
        self._cover()
        print('CMD Next ->', TRACKS[self.i][0], flush=True)

    @method()
    def Previous(self):
        self.i, self.pos, self.at = (self.i - 1) % 2, 0, time.time()
        self._cover()
        print('CMD Previous ->', TRACKS[self.i][0], flush=True)

    @method()
    def SetPosition(self, trackid: 'o', position: 'x'):
        self.pos, self.at = position, time.time()
        print('CMD SetPosition', trackid, position // 1_000_000, 's', flush=True)


async def main():
    bus = await MessageBus().connect()
    bus.export('/org/mpris/MediaPlayer2', Player())
    await bus.request_name('org.mpris.MediaPlayer2.fakeplayer')
    print('fake player ready', flush=True)
    await asyncio.Event().wait()


asyncio.run(main())

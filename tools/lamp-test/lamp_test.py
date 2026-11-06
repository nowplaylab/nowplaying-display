import asyncio
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NP_YA_CLIENT_ID='test-client', NP_YA_CLIENT_SECRET='',
                  NP_YA_OAUTH='http://127.0.0.1:47840', NP_YA_IOT='http://127.0.0.1:47840')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'pc'))
import aiohttp
from PIL import Image

sys.path.insert(1, HERE)
import covers as covers_mod  # noqa  тестовые обложки
import lamp
import settings

tmp = tempfile.mkdtemp()
S = settings.Settings(data_dir=tmp)
L = lamp.Lamp(S)


def jpeg(fn):
    b = io.BytesIO()
    fn().convert('RGB').save(b, 'JPEG')
    return b.getvalue()


async def last_action(http):
    async with http.get('http://127.0.0.1:47840/') as r:
        d = await r.json()
    return d['log'][-1][1] if d['log'] else None, d['lamps'], len(d['log'])


def ok(cond, what):
    print(('OK   ' if cond else 'FAIL ') + what)
    if not cond:
        globals()['FAILS'] = globals().get('FAILS', 0) + 1


async def main():
    async with aiohttp.ClientSession() as http:
        ok(L.status() == 'off', 'до входа: не подключён')
        await L.start_login(http)
        ok(L.status() == 'login' and L.login['user_code'] == 'kx4f92qm', 'получен код для входа')
        for _ in range(20):
            await asyncio.sleep(1)
            if L.status() == 'ok':
                break
        ok(L.status() == 'ok', 'вход подтверждён, токен получен')
        raw = open(L.path, 'rb').read()
        ok(b'AT1' not in raw and len(raw) > 50, 'токен на диске зашифрован')
        L2 = lamp.Lamp(S)
        ok(L2.token.get('access') == 'AT1', 'токен читается обратно после перезапуска')
        lamps = await L.refresh_lamps(http, force=True)
        ok([x['id'] for x in lamps] == ['lamp-1', 'lamp-2'], 'нашлись 2 лампы, розетка не попала: %s' % lamps)
        ok(lamps[0]['room'] == 'Гостиная' and lamps[0]['model'] == 'hsv' and lamps[1]['model'] == 'rgb', 'комнаты и модели цвета')

        S.update({'lamp_on': True, 'lamp_ids': ['lamp-1', 'lamp-2'], 'lamp_bright': 80})
        art = {'t1': (jpeg(covers_mod.c_sunset), '', None), 't2': (jpeg(covers_mod.c_bw), '', None)}
        st = {'playing': True, 'tid': 't1'}
        now = lambda: (st['playing'], st['tid'])

        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        a1 = {a['state']['instance']: a['state']['value'] for a in act['devices'][0]['actions']}
        a2 = {a['state']['instance']: a['state']['value'] for a in act['devices'][1]['actions']}
        ok(a1['on'] is True and 'hsv' in a1 and 10 <= a1['hsv']['h'] <= 30 and a1['brightness'] == 80, 'закат: оранжевый hsv на лампе 1 %s' % a1)
        ok('rgb' in a2 and (a2['rgb'] >> 16) > 200, 'закат: rgb на лампе 2 %s' % a2)
        ok(L.saved and 'lamp-1' in L.saved, 'запомнили, как лампы светили до нас')

        await asyncio.sleep(1.1)
        await L._tick(http, now, art)
        _, _, n2 = await last_action(http)
        ok(n2 == n, 'тот же трек — повторной команды нет')

        st['tid'] = 't2'
        await asyncio.sleep(1.1)
        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        a1 = {a['state']['instance']: a['state']['value'] for a in act['devices'][0]['actions']}
        ok(a1.get('temperature_k') == 2700, 'чёрно-белая обложка: тёплый белый %s' % a1)

        st['playing'] = False
        await asyncio.sleep(1.1)
        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        a1 = {a['state']['instance']: a['state']['value'] for a in act['devices'][0]['actions']}
        ok(a1.get('brightness') == 24, 'пауза: приглушили до 30%% от 80 %s' % a1)

        L.last_play -= 400
        await asyncio.sleep(1.1)
        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        ok(ls['lamp-1']['state']['on'] is True and ls['lamp-1']['state']['brightness'] == 60
           and ls['lamp-1']['state']['temperature_k'] == 4000, 'тишина 5 мин: лампа 1 как была %s' % ls['lamp-1']['state'])
        ok(ls['lamp-2']['state']['on'] is False, 'тишина 5 мин: лампа 2 снова выключена')
        ok(L.saved is None, 'после возврата снимок сброшен')

        st['playing'] = True
        st['tid'] = 't1'
        await asyncio.sleep(1.1)
        S.update({'lamp_pause': 'off'})
        await L._tick(http, now, art)
        st['playing'] = False
        await asyncio.sleep(1.1)
        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        ok(ls['lamp-1']['state']['on'] is False, 'пауза «выключить»: лампа выключена')

        S.update({'lamp_on': False})
        await L._tick(http, now, art)
        act, ls, n = await last_action(http)
        ok(ls['lamp-1']['state']['on'] is True and ls['lamp-1']['state']['brightness'] == 60, 'подсветку выключили — лампы вернулись как были')

        L.token['expires_at'] = 1
        await L.refresh_lamps(http, force=True)
        ok(L.token['access'] == 'AT2', 'токен обновился, когда подходил к концу')

        info = L.info()
        ok(info['status'] == 'ok' and len(info['lamps']) == 2, 'info для окна настроек')
        L.logout()
        ok(L.status() == 'off' and not os.path.exists(L.path), 'отключение стирает вход')
    print('FAILS', globals().get('FAILS', 0))


asyncio.run(main())

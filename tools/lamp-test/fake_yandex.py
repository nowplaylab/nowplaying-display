"""Подставной Яндекс для проверки подсветки: вход по коду (oauth) и Умный дом (iot) с двумя лампами.
Код «подтверждается» сам через 6 секунд. Все команды лампам пишутся в журнал и видны на /."""
import json
import time

from aiohttp import web

T0 = {}
LAMPS = {
    'lamp-1': {'name': 'Лампочка', 'room': 'r1', 'model': 'hsv', 'state': {'on': True, 'hsv': None, 'temperature_k': 4000, 'brightness': 60}},
    'lamp-2': {'name': 'Лампочка', 'room': 'r2', 'model': 'rgb', 'state': {'on': False, 'rgb': None, 'temperature_k': 3000, 'brightness': 40}},
}
LOG = []


async def device_code(r):
    d = await r.post()
    assert d.get('client_id') == 'test-client', d
    T0['at'] = time.time()
    return web.json_response({'device_code': 'DC1', 'user_code': 'kx4f92qm', 'verification_url': 'https://oauth.yandex.ru/device',
                              'interval': 2, 'expires_in': 300})


async def token(r):
    d = await r.post()
    if d.get('grant_type') == 'device_code':
        if time.time() - T0.get('at', 0) < 6:
            return web.json_response({'error': 'authorization_pending'}, status=400)
        return web.json_response({'token_type': 'bearer', 'access_token': 'AT1', 'refresh_token': 'RT1', 'expires_in': 31536000})
    if d.get('grant_type') == 'refresh_token':
        return web.json_response({'token_type': 'bearer', 'access_token': 'AT2', 'refresh_token': 'RT2', 'expires_in': 31536000})
    return web.json_response({'error': 'unsupported_grant_type'}, status=400)


def auth_ok(r):
    return r.headers.get('Authorization', '').startswith('Bearer AT')


async def user_info(r):
    if not auth_ok(r):
        return web.json_response({'status': 'error', 'message': 'unauthorized'}, status=401)
    devs = []
    for i, l in LAMPS.items():
        st = l['state']
        cinst = 'hsv' if l['model'] == 'hsv' else 'rgb'
        color_state = ({'instance': cinst, 'value': st[cinst]} if st.get(cinst) is not None
                       else {'instance': 'temperature_k', 'value': st['temperature_k']})
        devs.append({'id': i, 'name': l['name'], 'type': 'devices.types.light', 'room': l['room'], 'capabilities': [
            {'type': 'devices.capabilities.on_off', 'parameters': {'split': False}, 'state': {'instance': 'on', 'value': st['on']}},
            {'type': 'devices.capabilities.range', 'parameters': {'instance': 'brightness', 'unit': 'unit.percent',
                                                                  'range': {'min': 1, 'max': 100, 'precision': 1}},
             'state': {'instance': 'brightness', 'value': st['brightness']}},
            {'type': 'devices.capabilities.color_setting', 'parameters': {'color_model': l['model'], 'temperature_k': {'min': 1700, 'max': 6500}},
             'state': color_state}]})
    devs.append({'id': 'sock-1', 'name': 'Розетка', 'type': 'devices.types.socket', 'room': 'r1', 'capabilities': []})
    return web.json_response({'status': 'ok', 'request_id': 'x', 'rooms': [{'id': 'r1', 'name': 'Гостиная'}, {'id': 'r2', 'name': 'Спальня'}],
                              'devices': devs})


async def actions(r):
    if not auth_ok(r):
        return web.json_response({'status': 'error', 'message': 'unauthorized'}, status=401)
    body = await r.json()
    out = []
    for d in body['devices']:
        st = LAMPS[d['id']]['state']
        for a in d['actions']:
            inst, val = a['state']['instance'], a['state']['value']
            if inst == 'on':
                st['on'] = val
            elif inst in ('hsv', 'rgb'):
                st[inst] = val
            elif inst == 'temperature_k':
                st['temperature_k'] = val
                st['hsv'] = st['rgb'] = None
            elif inst == 'brightness':
                st['brightness'] = val
        out.append({'id': d['id'], 'capabilities': [{'type': a['type'], 'state': {'instance': a['state']['instance'],
                    'action_result': {'status': 'DONE'}}} for a in d['actions']]})
    LOG.append((time.strftime('%H:%M:%S'), body))
    print('ACTION', json.dumps(body, ensure_ascii=False), flush=True)
    return web.json_response({'status': 'ok', 'request_id': 'y', 'devices': out})


async def page(r):
    return web.json_response({'lamps': LAMPS, 'log': LOG[-20:]})


app = web.Application()
app.add_routes([web.post('/device/code', device_code), web.post('/token', token),
                web.get('/v1.0/user/info', user_info), web.post('/v1.0/devices/actions', actions), web.get('/', page)])
web.run_app(app, host='127.0.0.1', port=47840, print=None)

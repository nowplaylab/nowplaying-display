"""Эмулятор ленты Yeelight — теперь живёт в библиотеке yeelight-lan (github.com/nowplaylab/yeelight-lan).

Отвечает на поиск SSDP, принимает команды по TCP 55443 (с лимитом 60 в минуту, как настоящая лента)
и музыкальный режим. Показ: http://<адрес компьютера>:47850 с любого устройства дома.

    python tools/yeelight-emu/yeelight_emu.py [--name "Лента"] [--port 55443]
    # то же самое: python -m yeelight_lan.emulator
"""
import asyncio

from yeelight_lan.emulator import main

if __name__ == '__main__':
    asyncio.run(main())

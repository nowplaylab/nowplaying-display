"""Полосы частот для визуализатора: то, что играет ПК (захват «что слышно в колонках»), 30 раз в секунду.

Берём звук основного устройства вывода (WASAPI loopback через soundcard), считаем спектр
(БПФ по 2048 сэмплам с окном Ханна), раскладываем на BANDS полос от 40 Гц до 16 кГц
по логарифмической шкале — как слышит ухо. Каждую полосу переводим в децибелы.
У музыки верха всегда тише басов (примерно на 20–25 дБ по всей шкале) — выравниваем наклон.
Потом меряем от общего уровня трека (плавающее среднее по всем полосам за ~5 с): окно в 24 дБ
под ним, с поджатыми вершинами — средняя полоса около середины, удары подпрыгивают вверх,
тихие и громкие треки выглядят одинаково. Сглаживание: сразу вверх, быстро вниз.

Захват — в своём потоке и только пока он нужен (играет музыка и визуализатор включён).
"""
import logging
import threading
import time
import warnings

log = logging.getLogger('nowplaying')

BANDS = 24
RATE = 48000
FRAMES = 2048
FPS = 30
LOW, HIGH = 40.0, 16000.0


class Bands:
    def __init__(self):
        self.values = [0.0] * BANDS
        self.active = False
        self.clients = 0
        self._thread = None
        self.error = ''

    def want(self, on):
        self.active = on
        if on and not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name='bands', daemon=True)
            self._thread.start()

    def line(self):
        """Строка для планшета: BANDS чисел 0–99 через пробел."""
        return ' '.join(str(int(v * 99)) for v in self.values)

    def _run(self):
        try:
            import numpy as np
            import soundcard as sc
        except Exception as e:
            self.error = str(e)
            log.warning('bands: %s', e)
            return
        warnings.filterwarnings('ignore', module='soundcard')   # «data discontinuity» при паузах — не важно
        freqs = np.fft.rfftfreq(FRAMES, 1 / RATE)
        edges = np.geomspace(LOW, HIGH, BANDS + 1)
        idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(BANDS)]
        # узкие низкие полосы могут не захватить ни одной частоты — берём ближайшую
        idx = [ix if len(ix) else np.array([int(np.argmin(np.abs(freqs - (edges[i] + edges[i + 1]) / 2)))])
               for i, ix in enumerate(idx)]
        window = np.hanning(FRAMES).astype(np.float32)
        smooth = np.zeros(BANDS, dtype=np.float32)
        tilt = np.linspace(0, 22, BANDS, dtype=np.float32)   # дБ: поднимаем верха, чтобы спектр стал ровным
        ref = None                                         # общий уровень трека, дБ
        bmean = None                                       # средний уровень каждой полосы за ~10 с
        try:
            while self.active:
                try:
                    speaker = sc.default_speaker()
                    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                    with mic.recorder(samplerate=RATE, channels=2, blocksize=FRAMES // 2) as rec:
                        buf = np.zeros(FRAMES, dtype=np.float32)
                        step = RATE // FPS
                        while self.active:
                            chunk = rec.record(numframes=step).mean(axis=1).astype(np.float32)
                            buf = np.concatenate([buf[len(chunk):], chunk])
                            spec = np.abs(np.fft.rfft(buf * window))
                            raw = np.array([spec[ix].max() for ix in idx], dtype=np.float32)
                            db = 20 * np.log10(raw + 1e-9) + tilt
                            frame = float(db.mean())
                            if frame < -20:          # тишина или пауза — полоски лежат
                                level = np.zeros(BANDS, dtype=np.float32)
                            else:
                                # громче — догоняем за ~полсекунды, тише — отпускаем за ~5 с
                                k = 0.1 if ref is None or frame > ref else 0.007
                                ref = frame if ref is None else ref * (1 - k) + frame * k
                                # полоса, которая в этом треке всегда громче остальных (жирный бас), не висит
                                # на потолке: наполовину выравниваем её средний уровень под общий
                                bmean = db.copy() if bmean is None else bmean * 0.997 + db * 0.003
                                db = db - 0.6 * (bmean - bmean.mean())
                                level = np.clip((db - (ref - 14)) / 24, 0, 1) ** 1.6
                            smooth = np.where(level > smooth, level, smooth * 0.7 + level * 0.3)
                            self.values = [float(x) for x in np.clip(smooth, 0, 1)]
                except Exception as e:   # сменилось устройство вывода и т. п. — переоткроем
                    log.warning('bands: %s', e)
                    time.sleep(1)
        finally:
            self.values = [0.0] * BANDS

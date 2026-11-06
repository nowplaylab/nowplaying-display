package com.nowplaylab.nowplaying;

import android.service.dreams.DreamService;
import android.view.KeyEvent;

/** Заставка «Сейчас играет»: когда телевизор или планшет простаивает (на планшете — пока
 *  заряжается), система показывает этот экран. Любая кнопка или касание её закрывает.
 *  Таймер сна устройства продолжает работать. Android 4.2+. */
public class NowPlayingDream extends DreamService implements Screen.Host {
    private Screen screen;

    @Override
    public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(false);    // любая кнопка или касание — выйти из заставки
        setFullscreen(true);
        setScreenBright(true);
        screen = new Screen(this, getWindow(), true, this);
        screen.onCreate();
    }

    @Override
    public void onDreamingStarted() {
        super.onDreamingStarted();
        screen.onResume();
    }

    @Override
    public void onDreamingStopped() {
        super.onDreamingStopped();
        screen.onPause();
    }

    @Override
    public void onDetachedFromWindow() {
        if (screen != null) screen.onPause();
        super.onDetachedFromWindow();
    }

    @Override
    public boolean superDispatchKeyEvent(KeyEvent e) {
        return super.dispatchKeyEvent(e);
    }
}

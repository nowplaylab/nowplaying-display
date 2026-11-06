package com.nowplaylab.nowplaying;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;

/** Приложение «Сейчас играет»: показывает Screen на весь экран. */
public class MainActivity extends Activity implements Screen.Host {
    private Screen screen;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        screen = new Screen(this, getWindow(), false, this);
        screen.onCreate();
    }

    @Override
    protected void onResume() {
        super.onResume();
        screen.onResume();
    }

    @Override
    protected void onPause() {
        super.onPause();
        screen.onPause();
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        return screen.dispatchKeyEvent(e);
    }

    @Override
    public boolean superDispatchKeyEvent(KeyEvent e) {
        return super.dispatchKeyEvent(e);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        screen.onWindowFocusChanged(hasFocus);
    }
}

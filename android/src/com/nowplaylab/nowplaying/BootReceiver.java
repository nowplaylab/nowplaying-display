package com.nowplaylab.nowplaying;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Запускает экран «Сейчас играет» после включения планшета. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(i.getAction())) {
            c.startActivity(new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }
}

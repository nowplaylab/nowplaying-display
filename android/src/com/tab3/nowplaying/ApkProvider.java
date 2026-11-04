package com.tab3.nowplaying;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;

import java.io.File;
import java.io.FileNotFoundException;

/** Отдаёт скачанное обновление окну установки на Android 7+ (там нельзя передавать file://).
 *  Наружу виден только один файл — update.apk, и только по выданному разрешению. */
public class ApkProvider extends ContentProvider {
    static final String AUTHORITY = "com.tab3.nowplaying.apk";

    static Uri uri() { return Uri.parse("content://" + AUTHORITY + "/update.apk"); }

    @Override public boolean onCreate() { return true; }

    @Override public ParcelFileDescriptor openFile(Uri u, String mode) throws FileNotFoundException {
        if (!"update.apk".equals(u.getLastPathSegment())) throw new FileNotFoundException();
        return ParcelFileDescriptor.open(new File(getContext().getFilesDir(), "update.apk"),
                ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public String getType(Uri u) { return "application/vnd.android.package-archive"; }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) { return null; }
    @Override public Uri insert(Uri u, ContentValues v) { return null; }
    @Override public int delete(Uri u, String s, String[] a) { return 0; }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { return 0; }
}

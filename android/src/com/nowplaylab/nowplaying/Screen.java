package com.nowplaylab.nowplaying;

import android.animation.ArgbEvaluator;
import android.animation.ValueAnimator;
import android.view.animation.DecelerateInterpolator;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.BlurMaskFilter;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorMatrix;
import android.graphics.ColorMatrixColorFilter;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.TransitionDrawable;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.DhcpInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.text.InputType;
import android.text.TextUtils;
import android.view.KeyEvent;
import android.util.TypedValue;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Random;

/** Весь экран «Сейчас играет»: трек, текст, часы, виджеты. Показывают его и приложение
 *  (MainActivity), и заставка (NowPlayingDream) — поэтому это не Activity, а обёртка над
 *  контекстом и окном того, кто показывает. */
public class Screen extends android.view.ContextThemeWrapper {
    /** Кто показывает экран: приложение передаёт сюда стандартную обработку клавиш. */
    interface Host {
        boolean superDispatchKeyEvent(KeyEvent e);
    }

    final android.view.Window window;
    final boolean dream;      // показываем как заставку: без обновлений, клавиш и управления окном
    private final Host host;

    Screen(android.content.Context base, android.view.Window window, boolean dream, Host host) {
        super(base, android.R.style.Theme_Holo_NoActionBar_Fullscreen);
        this.window = window;
        this.dream = dream;
        this.host = host;
    }

    private static final int UDP_PORT = 47800;
    private static final long IDLE_AFTER_PAUSE_MS = 60000;

    private final Handler ui = new Handler();
    private final java.util.concurrent.ExecutorService pool = java.util.concurrent.Executors.newCachedThreadPool();
    private SharedPreferences prefs;
    private volatile String server;        // "ip:port" или null
    private volatile boolean running;
    private volatile int generation;

    private FrameLayout root;
    private GradientDrawable background;
    private int bgColor = 0xff202020;
    private LinearLayout idle;
    private FrameLayout player;            // экран трека; раскладку внутри задаёт тема (applyTheme)
    private LinearLayout times;
    private VinylView vinyl;
    private String theme = "";
    private LinearLayout growCol;           // колонка «Сцены»/«Минимала»: текст раздвигает её, когда находится
    private ValueAnimator growAnim;
    private boolean growShown;
    private boolean themeShadow = true;    // тень под обложкой возможна в этой теме
    private int coverShadowSize;
    private ImageView cover;
    private TextView title, artist, unsynced, timeLeft, timeRight, clock, date, status, flashIcon;
    private View dark;                     // чёрная шторка «экран выключен»

    // фон экрана часов: фото или зацикленное видео из папки на ПК
    private FrameLayout bgLayer;
    private ImageView bgImage;
    private android.widget.VideoView bgVideo;
    private volatile String bgId;
    private String bgType;
    private boolean bgReady, bgPlaying;

    // видео за экраном трека: видеошот из Яндекс Музыки (готовит сервер на ПК)
    private FrameLayout clipLayer;
    private android.widget.VideoView clipVideo;
    private volatile String clipId;
    private boolean clipReady, clipPlaying;

    // обновление приложения: новый APK скачивает с GitHub сервер на ПК и отдаёт по сети
    private String updateTried;   // версия, которую уже пробовали поставить (чтобы не по кругу)

    // экран и подсветка
    private long darkAfterMs = 30 * 60 * 1000;   // сколько без музыки до чёрного экрана (0 — не гасить)
    // настройки с ПК (окно настроек → /api/now → ui)
    private String uiJson = "";
    private String trackBgMode = "video";
    private int trackDim = 55, clockDim = 35, nightFrom = 23, nightTo = 8;
    private boolean nightOn = true, shadowOn = true;
    private Drawable shadowDrawable;
    private View clipDim;
    private ImageView clipImage;
    private TextView weatherText;
    private String clipType = "video";
    private JSONObject lastNow;
    private static final long WAKE_ON_TAP_MS = 60 * 1000;
    private long idleSince, wakeUntil;
    private float appliedBrightness = -2;
    private ProgressLine progress;
    private LyricsView lyrics;

    // последнее состояние с ПК
    private String trackId;
    private int artRev = -1;
    private long shownSecond = -2, shownDuration = -1;
    private volatile boolean playing;   // читается и потоком «дыхания»
    private long position, duration, receivedAt, pausedSince;
    private long seekHoldUntil;   // после перемотки не верим старой позиции с сервера
    private boolean seekDragging; // палец тянет полоску прогресса
    private boolean lyricsDrag;   // палец листает текст
    private boolean volumeDrag;   // палец меняет громкость (по обложке)
    private int volume = -1, volumeStart;   // громкость ПК 0–100 (-1 — неизвестна)
    private long volumeSentAt;
    private final java.util.concurrent.ExecutorService volumeExec = java.util.concurrent.Executors.newSingleThreadExecutor();
    private volatile int volumeTarget = -1, volumeSent = -1;
    private final Runnable sendVolume = new Runnable() {
        public void run() {
            int level = volumeTarget;
            String s = server;
            if (level < 0 || level == volumeSent || s == null) return;
            try {
                get("http://" + s + "/api/control/volume/" + level, 3000);
                volumeSent = level;
            } catch (Exception ignored) { }
        }
    };
    private boolean forceBackground;   // пересчитать фон, даже если цвета те же (сменился режим)
    private String appVersion;
    private FrameLayout coverFrame;
    private TextView volumeLabel;
    private final Runnable hideVolume = new Runnable() {
        public void run() { volumeLabel.animate().alpha(0f).setDuration(400).start(); }
    };
    private View bgDim;
    private Boolean nightShown;   // какой вид часов сейчас (ночной или нет)
    private int[] bgColors = {0xff202020, 0xff0e0e0e, 0xff0e0e0e};
    // свечение за обложкой, «дышит» под музыку (уровень звука ПК идёт потоком с /api/pulse)
    private View glow;
    private View bgFill;   // фон из цветов обложки (под всем)
    private GradientDrawable glowDrawable;
    private VisualizerView visualizer;
    private volatile String visStyle = "off";   // визуализатор: off, bars, wave, dots
    private volatile boolean pulseOn, playerShown;   // pulseOn — только если сервер его знает (1.7+)
    private volatile float pulseLevel;
    private float glowShown;
    // без видео за треком свечение — три пятна по экрану: басы, середина, верха (полосы с /api/bands)
    private static final float[][] SPOT_AT = {{0.06f, 0.9f}, {0.92f, 0.16f}, {0.62f, 1.0f}};
    private final View[] spots = new View[3];
    private final GradientDrawable[] spotDrawables = new GradientDrawable[3];
    private final float[] spotShown = new float[3];
    private final float[] spotLevel = new float[3];      // пишет сетевой поток
    private volatile long spotFreshAt;                  // когда последний раз пришли ненулевые полосы
    private boolean connected;
    private volatile boolean pcLost;   // ПК перестал отвечать (а не просто ещё не ответил после запуска)

    // виджеты на экране часов (раскладку и данные присылает ПК в /api/now → widgets)
    private FrameLayout idleBox;           // часы + виджеты; сдвигается целиком, чтобы экран не выгорал
    private final WidgetView[] slotViews = new WidgetView[4];
    private WidgetView wolView;
    private String widgetsJson = "", widgetLayout = "shelf", widgetStyle = "card";
    private JSONArray widgetSlots = new JSONArray();
    private boolean widgetMode, weatherInWidget;
    // Wake on LAN: адрес сетевой карты ПК запоминаем, пока он на связи, и будим его, когда пропал
    private String wolMac, wolName = "";
    private boolean wolOn;
    // ---- таймер: живёт на планшете, переживает перезапуск приложения (время конца — в prefs) ----
    private static final int[] TIMER_PRESETS = {3, 5, 10, 15, 20, 30, 45, 60};
    private static final long RING_MAX_MS = 90000;
    private long timerEnd;          // когда зазвонит (часы планшета, мс); 0 — не идёт
    private long timerLeft;         // сколько осталось, пока на паузе; 0 — не на паузе
    private int pomoPhase = -1;     // помодоро: -1 — обычный таймер, 0 — работа, 1 — перерыв, 2 — длинный перерыв
    private int pomoRound = 1;
    private boolean ringing;
    private long ringStarted, panelUntil;
    private int timerFg = 0xffffffff;
    private boolean longOk;          // ОК на пульте держат — уже открыли ввод адреса
    private long lastScan;           // когда последний раз перебирали адреса сети
    private LinearLayout timerPanel;
    private TextView timerPill, ringView;
    private MediaPlayer ringPlayer;
    private int ringVolumeWas = -1;   // громкость будильника до звонка — вернём, когда выключат
    private final SimpleDateFormat endFmt = new SimpleDateFormat("HH:mm", Locale.getDefault());
    private WidgetView resumeView;
    private Bitmap coverBmp;              // последняя обложка — для карточки «Продолжить»
    private boolean resumeOn = true;
    private long resumeUntil;             // нажали «Продолжить» — до этого времени показываем «Включаю…»
    private int wolState = WidgetView.WOL_READY;
    private long wolUntil;

    private final Random random = new Random();
    private final SimpleDateFormat clockFmt = new SimpleDateFormat("HH:mm", Locale.getDefault());
    private final SimpleDateFormat dateFmt = new SimpleDateFormat("EEEE, d MMMM", new Locale("ru"));

    void onCreate() {
        if (!dream) {
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_FULLSCREEN
                | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED   // поверх экрана блокировки (без пароля)
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
                | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        if (android.os.Build.VERSION.SDK_INT >= 27) {   // на новых Android флаги выше устарели
            try {
                android.app.Activity.class.getMethod("setShowWhenLocked", boolean.class).invoke(getBaseContext(), true);
                android.app.Activity.class.getMethod("setTurnScreenOn", boolean.class).invoke(getBaseContext(), true);
            } catch (Exception ignored) { }
        }
        }
        prefs = getSharedPreferences("np", MODE_PRIVATE);
        server = prefs.getString("server", null);
        if (!dream) {   // заставка не перезапускает себя, не обновляется и не трогает пакеты
            installCrashRestart();
            new java.io.File(getFilesDir(), "update.apk").delete();   // обновление уже поставлено
            removeOldPackage();
        }
        buildUi();
        window.setContentView(root);
    }

    void onResume() {
        if (!dream) hideSystemBars();
        running = true;
        final int gen = ++generation;
        new Thread(new Runnable() { public void run() { networkLoop(gen); } }, "np-net").start();
        new Thread(new Runnable() { public void run() { pulseLoop(gen); } }, "np-pulse").start();
        new Thread(new Runnable() { public void run() { bandsLoop(gen); } }, "np-bands").start();
        ui.post(ticker);
        ui.post(clockTick);
        ui.post(glowTick);
    }

    void onPause() {
        running = false;
        syncBgVideo(false);
        syncClip(false);
        ui.removeCallbacks(ticker);
        ui.removeCallbacks(clockTick);
        ui.removeCallbacks(glowTick);
    }

    // ---------------- интерфейс ----------------

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private Typeface fontMedium, fontSemiBold, fontExtraBold;

    /** Inter из assets (похож на SF Pro из Apple Music); если файла нет — системный шрифт. */
    private Typeface font(String name, Typeface fallback) {
        try { return Typeface.createFromAsset(getAssets(), "fonts/Inter-" + name + ".ttf"); }
        catch (Exception e) { android.util.Log.w("NowPlaying", "font " + name + ": " + e); return fallback; }
    }

    private TextView text(float sp, boolean bold, int alpha) {
        if (fontMedium == null) {
            fontMedium = font("Medium", Typeface.DEFAULT);                  // исполнитель, время, дата
            fontSemiBold = font("DisplaySemiBold", Typeface.DEFAULT_BOLD);  // часы
            fontExtraBold = font("DisplayBold", Typeface.DEFAULT_BOLD);     // название и текст песни
        }
        TextView t = new TextView(this);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(Color.argb(alpha, 255, 255, 255));
        t.setTypeface(bold ? fontExtraBold : fontMedium);
        t.setShadowLayer(dp(3), 0, dp(1), 0x66000000);
        return t;
    }

    private void buildUi() {
        root = new FrameLayout(this);
        background = new GradientDrawable(GradientDrawable.Orientation.TL_BR, bgColors);
        // фон из цветов обложки — отдельным слоем, а не фоном root: так к нему применяется поправка
        // цвета, а root остаётся без слоя (иначе видео под ним не видно, см. applyColor)
        bgFill = new View(this);
        bgFill.setBackgroundDrawable(background);
        root.addView(bgFill, new FrameLayout.LayoutParams(-1, -1));
        // жесты: тап — пауза/продолжить, свайп — следующий/предыдущий, долгое нажатие — адрес ПК
        final GestureDetector gestures = new GestureDetector(this, new GestureDetector.SimpleOnGestureListener() {
            @Override public boolean onDown(MotionEvent e) {
                lyricsDrag = false;
                volumeDrag = false;
                lyrics.stopFling();
                return true;
            }
            @Override public boolean onScroll(MotionEvent a, MotionEvent b, float dx, float dy) {
                if (player.getVisibility() != View.VISIBLE || dark.getVisibility() == View.VISIBLE) return false;
                float tx = b.getX() - a.getX(), ty = b.getY() - a.getY();
                if (!lyricsDrag && !volumeDrag && volume >= 0 && coverFrame.getParent() != null && inside(coverFrame, a)
                        && Math.abs(ty) >= dp(12) && Math.abs(ty) > Math.abs(tx)) {
                    volumeDrag = true;     // вверх-вниз по обложке — громкость ПК
                    volumeStart = volume;
                }
                if (volumeDrag) {
                    // вся высота обложки — 100%
                    setVolume(volumeStart + Math.round(-ty / cover.getHeight() * 100), false);
                    return true;
                }
                if (!lyrics.hasLyrics()) return false;
                if (!lyricsDrag) {
                    // листаем только вертикальным движением, начатым на тексте
                    if (Math.abs(ty) < dp(12) || Math.abs(ty) < Math.abs(tx) || !inside(lyrics, a)) return false;
                    lyricsDrag = true;
                }
                lyrics.scrollByUser(dy);
                return true;
            }
            @Override public boolean onSingleTapUp(MotionEvent e) {
                if (ringing) {
                    stopRing();
                    return true;
                }
                if (wakeFromDark()) return true;
                if (timerPanel.getVisibility() == View.VISIBLE) {   // тап мимо кнопок — закрыть панель
                    hideTimerPanel();
                    return true;
                }
                if (idleBox.getVisibility() == View.VISIBLE && resumeView.getVisibility() == View.VISIBLE && inside(resumeView, e)) {
                    long now = SystemClock.elapsedRealtime();
                    if (now > resumeUntil && !playing) {
                        control("toggle");
                        resumeUntil = now + 5000;
                        updateResume(now);
                    }
                    return true;
                }
                if (idleBox.getVisibility() == View.VISIBLE && wolView.getVisibility() == View.VISIBLE && inside(wolView, e)) {
                    if (wolState != WidgetView.WOL_WAKING) wakePc();
                    return true;
                }
                if (idleBox.getVisibility() == View.VISIBLE) {
                    showTimerPanel();
                    return true;
                }
                if (player.getVisibility() == View.VISIBLE && lyrics.isBrowsing() && inside(lyrics, e)) {
                    // текст пролистан — тап по строке перематывает на неё
                    long t = lyrics.timeAt(e.getRawY() - screenY(lyrics));
                    if (t >= 0) {
                        seekTo(t);
                        lyrics.endBrowsing();
                    }
                    return true;
                }
                if (player.getVisibility() == View.VISIBLE) {
                    control("toggle");
                    flash(playing ? "❚❚" : "▶");
                }
                return true;
            }
            @Override public boolean onFling(MotionEvent a, MotionEvent b, float vx, float vy) {
                if (wakeFromDark() || player.getVisibility() != View.VISIBLE) return true;
                if (lyricsDrag) {
                    lyrics.flingByUser(vy);
                    return true;
                }
                if (volumeDrag) return true;
                float dx = b.getX() - a.getX();
                if (Math.abs(dx) > dp(100) && Math.abs(vx) > Math.abs(vy)) {
                    control(dx < 0 ? "next" : "prev");
                    flash(dx < 0 ? "⏭" : "⏮");
                }
                return true;
            }
            @Override public void onLongPress(MotionEvent e) { askServer(); }
        });
        root.setOnTouchListener(new View.OnTouchListener() {
            public boolean onTouch(View v, MotionEvent e) {
                if (dragSeek(e)) return true;
                if (volumeDrag && (e.getActionMasked() == MotionEvent.ACTION_UP
                        || e.getActionMasked() == MotionEvent.ACTION_CANCEL)) {
                    setVolume(volume, true);   // последнее значение — точно отправить
                    volumeDrag = false;
                }
                return gestures.onTouchEvent(e);
            }
        });

        // --- экран трека ---
        player = new FrameLayout(this);

        cover = new ImageView(this);
        cover.setScaleType(ImageView.ScaleType.CENTER_CROP);
        cover.setImageDrawable(new ColorDrawable(0x33ffffff));
        // мягкая тень под обложкой: рамка шире обложки на SHADOW с каждой стороны,
        // а отрицательные отступы оставляют обложку на прежнем месте
        coverFrame = new FrameLayout(this);
        coverFrame.addView(cover, new FrameLayout.LayoutParams(dp(400), dp(400), Gravity.CENTER));
        volumeLabel = text(22, true, 255);
        volumeLabel.setShadowLayer(0, 0, 0, 0);
        volumeLabel.setGravity(Gravity.CENTER);
        GradientDrawable pill = new GradientDrawable();
        pill.setColor(0xb3000000);
        pill.setCornerRadius(dp(28));
        volumeLabel.setBackgroundDrawable(pill);
        volumeLabel.setPadding(dp(26), dp(12), dp(26), dp(12));
        volumeLabel.setAlpha(0f);
        coverFrame.addView(volumeLabel, new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER));

        vinyl = new VinylView(this);

        title = text(30, true, 255);
        title.setEllipsize(TextUtils.TruncateAt.END);
        artist = text(22, false, 200);
        artist.setSingleLine(true);
        artist.setEllipsize(TextUtils.TruncateAt.END);
        unsynced = text(13, false, 130);
        unsynced.setText("текст без синхронизации");
        unsynced.setVisibility(View.GONE);

        lyrics = new LyricsView(this);
        lyrics.setTypeface(fontExtraBold);
        lyrics.setVisibility(View.GONE);

        progress = new ProgressLine(this);
        times = new LinearLayout(this);
        timeLeft = text(14, false, 170);
        timeRight = text(14, false, 170);
        timeRight.setGravity(Gravity.RIGHT);
        // у мелкого текста у края тень обрезается и выглядит как тёмная палочка — убираем
        timeLeft.setShadowLayer(0, 0, 0, 0);
        timeRight.setShadowLayer(0, 0, 0, 0);
        times.addView(timeLeft, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        times.addView(timeRight, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        // --- видео за экраном трека (под ним) ---
        clipLayer = new FrameLayout(this);
        clipVideo = new android.widget.VideoView(this);
        clipLayer.addView(clipVideo, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        clipImage = new ImageView(this);
        clipImage.setScaleType(ImageView.ScaleType.CENTER_CROP);
        clipImage.setVisibility(View.GONE);
        clipLayer.addView(clipImage, new FrameLayout.LayoutParams(-1, -1));
        clipDim = new View(this);
        clipDim.setBackgroundColor(dimColor(trackDim));   // затемнение, чтобы текст читался
        clipLayer.addView(clipDim, new FrameLayout.LayoutParams(-1, -1));
        clipLayer.setVisibility(View.GONE);
        root.addView(clipLayer, new FrameLayout.LayoutParams(-1, -1));
        glowDrawable = new GradientDrawable();
        glowDrawable.setGradientType(GradientDrawable.RADIAL_GRADIENT);
        glowDrawable.setGradientRadius(dp(460));
        glowDrawable.setGradientCenter(0.23f, 0.5f);   // центр обложки
        glowDrawable.setColors(new int[]{0x00000000, 0x00000000});
        glow = new View(this);
        glow.setBackgroundDrawable(glowDrawable);
        glow.setLayerType(View.LAYER_TYPE_HARDWARE, null);   // меняем только прозрачность — дёшево
        glow.setAlpha(0f);
        glow.setVisibility(View.GONE);
        root.addView(glow, new FrameLayout.LayoutParams(-1, -1));
        for (int i = 0; i < 3; i++) {
            GradientDrawable d = new GradientDrawable();
            d.setGradientType(GradientDrawable.RADIAL_GRADIENT);
            d.setGradientRadius(dp(i == 2 ? 340 : 300));
            d.setGradientCenter(SPOT_AT[i][0], SPOT_AT[i][1]);
            d.setColors(new int[]{0x00000000, 0x00000000});
            View v = new View(this);
            v.setBackgroundDrawable(d);
            v.setLayerType(View.LAYER_TYPE_HARDWARE, null);
            v.setAlpha(0f);
            v.setVisibility(View.GONE);
            root.addView(v, new FrameLayout.LayoutParams(-1, -1));
            spots[i] = v;
            spotDrawables[i] = d;
        }

        visualizer = new VisualizerView(this);   // под обложкой и текстом, поверх фона
        visualizer.setVisibility(View.GONE);
        root.addView(visualizer, new FrameLayout.LayoutParams(-1, dp(150), Gravity.BOTTOM));
        root.addView(player, new FrameLayout.LayoutParams(-1, -1));
        applyTheme("classic");

        // --- фон для часов (под ними) ---
        bgLayer = new FrameLayout(this);
        bgImage = new ImageView(this);
        bgImage.setScaleType(ImageView.ScaleType.CENTER_CROP);
        bgLayer.addView(bgImage, new FrameLayout.LayoutParams(-1, -1));
        bgVideo = new android.widget.VideoView(this);
        bgVideo.setVisibility(View.GONE);
        bgLayer.addView(bgVideo, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        bgDim = new View(this);
        bgDim.setBackgroundColor(0x59000000);   // ~35% затемнение, чтобы часы читались
        bgLayer.addView(bgDim, new FrameLayout.LayoutParams(-1, -1));
        bgLayer.setVisibility(View.GONE);
        root.addView(bgLayer, new FrameLayout.LayoutParams(-1, -1));

        sunriseBg = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, new int[]{0xff000000, 0xff000000});
        sunrise = new View(this);
        sunrise.setBackgroundDrawable(sunriseBg);
        sunrise.setVisibility(View.GONE);
        root.addView(sunrise, new FrameLayout.LayoutParams(-1, -1));

        // --- часы в простое ---
        idle = new LinearLayout(this);
        idle.setOrientation(LinearLayout.VERTICAL);
        idle.setGravity(Gravity.CENTER);
        clock = text(130, false, 255);
        clock.setTypeface(fontSemiBold);
        clock.setGravity(Gravity.CENTER_HORIZONTAL);
        clock.setIncludeFontPadding(false);   // без лишнего отступа шрифта над цифрами
        date = text(26, false, 190);
        date.setGravity(Gravity.CENTER_HORIZONTAL);
        idle.addView(clock, new LinearLayout.LayoutParams(-2, -2));
        LinearLayout.LayoutParams dlp = new LinearLayout.LayoutParams(-2, -2);
        dlp.topMargin = dp(6);
        idle.addView(date, dlp);
        weatherText = text(22, false, 170);
        weatherText.setGravity(Gravity.CENTER_HORIZONTAL);
        weatherText.setVisibility(View.GONE);
        LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(-2, -2);
        wlp.gravity = Gravity.CENTER_HORIZONTAL;
        wlp.topMargin = dp(4);
        idle.addView(weatherText, wlp);
        idleBox = new FrameLayout(this);
        idleBox.addView(idle, new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER));
        for (int i = 0; i < slotViews.length; i++) {
            slotViews[i] = new WidgetView(this, fontMedium, fontSemiBold, fontExtraBold);
            slotViews[i].setVisibility(View.GONE);
            idleBox.addView(slotViews[i], new FrameLayout.LayoutParams(dp(296), dp(140)));
        }
        wolView = new WidgetView(this, fontMedium, fontSemiBold, fontExtraBold);
        wolView.setVisibility(View.GONE);
        idleBox.addView(wolView, new FrameLayout.LayoutParams(dp(296), dp(140)));
        resumeView = new WidgetView(this, fontMedium, fontSemiBold, fontExtraBold);
        resumeView.setVisibility(View.GONE);
        idleBox.addView(resumeView, new FrameLayout.LayoutParams(dp(296), dp(140)));
        root.addView(idleBox, new FrameLayout.LayoutParams(-1, -1));
        root.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            public void onLayoutChange(View v, int l, int t, int r, int b, int ol, int ot, int or, int ob) {
                if (r - l != or - ol || b - t != ob - ot) ui.post(new Runnable() { public void run() { layoutWidgets(); } });
            }
        });
        wolMac = prefs.getString("wol_mac", null);
        wolName = prefs.getString("wol_name", "");
        wolOn = prefs.getBoolean("wol_on", true);

        status = text(14, false, 150);
        FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        sp.bottomMargin = dp(10);
        root.addView(status, sp);

        flashIcon = text(90, true, 230);
        flashIcon.setTypeface(Typeface.DEFAULT_BOLD); // значков ▶ ❚❚ в Inter нет
        flashIcon.setAlpha(0f);
        root.addView(flashIcon, new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER));

        timerPill = text(20, false, 255);
        timerPill.setTypeface(fontSemiBold);
        timerPill.setPadding(dp(16), dp(7), dp(16), dp(8));
        timerPill.setVisibility(View.GONE);
        timerPill.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { showTimerPanel(); } });
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.RIGHT);
        pp.topMargin = dp(18);
        pp.rightMargin = dp(18);
        root.addView(timerPill, pp);

        timerPanel = new LinearLayout(this);
        timerPanel.setOrientation(LinearLayout.HORIZONTAL);
        timerPanel.setGravity(Gravity.CENTER_VERTICAL);
        timerPanel.setPadding(dp(14), dp(12), dp(14), dp(12));
        timerPanel.setClickable(true);   // касания по самой панели не уходят дальше
        hideTimerPanel();
        FrameLayout.LayoutParams tp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        tp.bottomMargin = dp(28);
        root.addView(timerPanel, tp);

        ringView = text(64, true, 255);
        ringView.setGravity(Gravity.CENTER);
        ringView.setBackgroundColor(0xe6000000);
        ringView.setVisibility(View.GONE);
        root.addView(ringView, new FrameLayout.LayoutParams(-1, -1));

        timerEnd = prefs.getLong("timer_end", 0);
        timerLeft = prefs.getLong("timer_left", 0);
        pomoPhase = prefs.getInt("pomo_phase", -1);
        pomoRound = prefs.getInt("pomo_round", 1);
        alarmFired = prefs.getLong("alarm_fired", 0);

        dark = new View(this);
        dark.setBackgroundColor(0xff000000);
        dark.setVisibility(View.GONE);
        root.addView(dark, new FrameLayout.LayoutParams(-1, -1));

        showIdle(true);
        status.setText("Ищу компьютер…  (долгое нажатие — ввести адрес вручную)");
    }

    private void showIdle(boolean on) {
        playerShown = !on;
        idleBox.setVisibility(on ? View.VISIBLE : View.GONE);
        player.setVisibility(on ? View.GONE : View.VISIBLE);
        bgLayer.setVisibility(on && bgReady ? View.VISIBLE : View.GONE);
        bgVideo.setVisibility(on && bgReady && "video".equals(bgType) ? View.VISIBLE : View.GONE);
    }

    /** Видео фона играет только на экране часов и пока экран не «спит». */
    private void syncBgVideo(boolean shouldPlay) {
        if (!"video".equals(bgType) || !bgReady) return;
        if (shouldPlay && !bgPlaying) { bgVideo.start(); bgPlaying = true; }
        else if (!shouldPlay && bgPlaying) { bgVideo.pause(); bgPlaying = false; }
    }

    private void clearBg() {
        bgReady = false;
        bgType = null;
        bgPlaying = false;
        bgVideo.stopPlayback();
        bgVideo.setVisibility(View.GONE);
        bgImage.setImageDrawable(null);
        bgLayer.setVisibility(View.GONE);
    }

    private void loadBg(final String srv, final String id, final String ext, final String type) {
        pool.execute(new Runnable() {
            public void run() {
                try {
                    final java.io.File dir = getCacheDir();
                    final java.io.File file = new java.io.File(dir, "bg_" + id + "." + ext);
                    if (!file.exists()) download("http://" + srv + "/bg/" + id + "." + ext, file);
                    for (java.io.File f : dir.listFiles()) {   // старые фоны больше не нужны
                        if (f.getName().startsWith("bg_") && !f.equals(file)) f.delete();
                    }
                    final Bitmap bmp = "image".equals(type) ? decodeForScreen(file) : null;
                    ui.post(new Runnable() {
                        public void run() {
                            if (id.equals(bgId)) showBg(type, file, bmp);
                        }
                    });
                } catch (Exception e) {
                    android.util.Log.w("NowPlaying", "background: " + e);
                    bgId = null;  // попробуем снова при следующем опросе
                }
            }
        });
    }

    private String devId;

    /** Постоянный номер этого устройства — чтобы на ПК у каждого экрана были свои настройки. */
    private String deviceId() {
        if (devId == null) {
            devId = prefs.getString("dev_id", null);
            if (devId == null) {
                devId = Long.toHexString(new Random().nextLong() & 0xffffffffffffL);
                prefs.edit().putString("dev_id", devId).commit();
            }
        }
        return devId;
    }

    private String myVersion() {
        if (appVersion == null) {
            try {
                appVersion = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            } catch (Exception e) {
                return "0";
            }
        }
        return appVersion;
    }

    /** Сравнение версий вида 1.4.1: >0, если a новее b. */
    static int compareVersions(String a, String b) {
        String[] x = a.split("\\D+"), y = b.split("\\D+");
        for (int i = 0; i < Math.max(x.length, y.length); i++) {
            int p = i < x.length && x[i].length() > 0 ? Integer.parseInt(x[i]) : 0;
            int q = i < y.length && y[i].length() > 0 ? Integer.parseInt(y[i]) : 0;
            if (p != q) return p < q ? -1 : 1;
        }
        return 0;
    }

    /** Скачать новый APK с ПК и поставить: с root — тихо (и запустить снова), без root — окно установки. */
    private void installUpdate(final String srv, final String version) {
        if (dream) return;   // обновляется только само приложение, не заставка
        pool.execute(new Runnable() {
            public void run() {
                try {
                    java.io.File file = new java.io.File(getFilesDir(), "update.apk");
                    download("http://" + srv + "/update/app.apk", file);
                    // окну установки (другое приложение) нужен доступ к файлу
                    file.setReadable(true, false);
                    getFilesDir().setExecutable(true, false);
                    android.util.Log.i("NowPlaying", "update " + version + " downloaded");
                    boolean silent = false;
                    try {
                        Process p = Runtime.getRuntime().exec(new String[]{"su", "-c",
                                "pm install -r '" + file.getPath() + "' && am start -n " + getPackageName() + "/.MainActivity"});
                        silent = p.waitFor() == 0;   // при успехе нас перезапустят раньше, чем дойдём сюда
                    } catch (Exception noRoot) {
                        // root нет — покажем обычное окно установки
                    }
                    if (!silent) {
                        // Android 7+ не пускает file:// в чужое приложение — отдаём через свой провайдер
                        final boolean content = android.os.Build.VERSION.SDK_INT >= 24;
                        final android.net.Uri uri = content ? ApkProvider.uri() : android.net.Uri.fromFile(file);
                        ui.post(new Runnable() {
                            public void run() {
                                android.content.Intent i = new android.content.Intent(android.content.Intent.ACTION_VIEW);
                                i.setDataAndType(uri, "application/vnd.android.package-archive");
                                i.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
                                if (content) i.addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
                                startActivity(i);
                            }
                        });
                    }
                } catch (Exception e) {
                    android.util.Log.w("NowPlaying", "update: " + e);
                    updateTried = null;  // попробуем при следующем опросе
                }
            }
        });
    }

    private static void download(String url, java.io.File file) throws java.io.IOException {
        java.io.File part = new java.io.File(file.getPath() + ".part");
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(5000);
        c.setReadTimeout(30000);
        try {
            InputStream in = c.getInputStream();
            java.io.FileOutputStream out = new java.io.FileOutputStream(part);
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            out.close();
            in.close();
        } finally {
            c.disconnect();
        }
        if (!part.renameTo(file)) throw new java.io.IOException("rename");
    }

    /** Видео на весь экран с обрезкой краёв (как centerCrop у картинки). */
    private void fillScreen(android.widget.VideoView v, android.media.MediaPlayer mp) {
        int vw = mp.getVideoWidth(), vh = mp.getVideoHeight();
        int w = root.getWidth(), h = root.getHeight();
        if (vw > 0 && vh > 0 && w > 0 && h > 0) {
            float k = Math.max((float) w / vw, (float) h / vh);
            v.setLayoutParams(new FrameLayout.LayoutParams((int) (vw * k), (int) (vh * k), Gravity.CENTER));
        }
    }

    /** key — что показываем (c:<клип> или t:<свой фон>), path — адрес на сервере. */
    private void loadClip(final String srv, final String key, final String path, final String type) {
        pool.execute(new Runnable() {
            public void run() {
                try {
                    java.io.File dir = getCacheDir();
                    final java.io.File file = new java.io.File(dir, "clip_" + key.replace(':', '_') + path.substring(path.lastIndexOf('.')));
                    if (!file.exists()) download("http://" + srv + path, file);
                    for (java.io.File f : dir.listFiles()) {   // храним только текущий
                        if (f.getName().startsWith("clip_") && !f.equals(file)) f.delete();
                    }
                    final Bitmap bmp = "image".equals(type) ? decodeForScreen(file) : null;
                    ui.post(new Runnable() {
                        public void run() {
                            if (key.equals(clipId)) showClip(file, type, bmp);
                        }
                    });
                } catch (Exception e) {
                    android.util.Log.w("NowPlaying", "clip: " + e);
                    clipId = null;  // попробуем снова при следующем опросе
                }
            }
        });
    }

    private void showClip(java.io.File file, String type, Bitmap bmp) {
        clipVideo.stopPlayback();
        clipPlaying = false;
        clipType = type;
        if ("image".equals(type)) {
            clipImage.setImageBitmap(bmp);
            clipImage.setVisibility(View.VISIBLE);
            clipReady = bmp != null;
            return;
        }
        clipImage.setImageDrawable(null);
        clipImage.setVisibility(View.GONE);
        clipVideo.setOnPreparedListener(new android.media.MediaPlayer.OnPreparedListener() {
            public void onPrepared(android.media.MediaPlayer mp) {
                mp.setLooping(true);
                mp.setVolume(0f, 0f);
                fillScreen(clipVideo, mp);
            }
        });
        clipVideo.setOnErrorListener(new android.media.MediaPlayer.OnErrorListener() {
            public boolean onError(android.media.MediaPlayer mp, int what, int extra) {
                android.util.Log.w("NowPlaying", "clip video error " + what + "/" + extra);
                clearClip();   // без системного окна: просто остаётся фон под цвет обложки
                return true;
            }
        });
        clipVideo.setVideoPath(file.getPath());
        clipReady = true;
    }

    private void clearClip() {
        clipReady = false;
        clipPlaying = false;
        clipVideo.stopPlayback();
        clipLayer.setVisibility(View.GONE);
        clipVideo.setVisibility(View.GONE);
        clipImage.setImageDrawable(null);
        clipImage.setVisibility(View.GONE);
    }

    /** Видео трека играет только на экране трека и пока экран не «спит». */
    private void syncClip(boolean shouldPlay) {
        shouldPlay &= clipReady;
        clipLayer.setVisibility(shouldPlay ? View.VISIBLE : View.GONE);
        if (!"video".equals(clipType)) return;   // картинка: только показать слой
        clipVideo.setVisibility(shouldPlay ? View.VISIBLE : View.GONE);
        if (shouldPlay && !clipPlaying) { clipVideo.start(); clipPlaying = true; }
        else if (!shouldPlay && clipPlaying) { clipVideo.pause(); clipPlaying = false; }
    }

    private Bitmap decodeForScreen(java.io.File f) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(f.getPath(), o);
        int sample = 1;
        while (o.outWidth / (sample * 2) >= 1024 && o.outHeight / (sample * 2) >= 600) sample *= 2;
        o = new BitmapFactory.Options();
        o.inSampleSize = sample;
        return BitmapFactory.decodeFile(f.getPath(), o);
    }

    private void showBg(String type, java.io.File file, Bitmap bmp) {
        bgVideo.stopPlayback();
        bgPlaying = false;
        bgType = type;
        if ("image".equals(type)) {
            bgVideo.setVisibility(View.GONE);
            Drawable from = bgImage.getDrawable();
            if (from instanceof TransitionDrawable) from = ((TransitionDrawable) from).getDrawable(1);
            if (bmp != null && from instanceof BitmapDrawable) {   // фоторамка: новое фото проявляется поверх старого
                TransitionDrawable t = new TransitionDrawable(new Drawable[]{from, new BitmapDrawable(getResources(), bmp)});
                t.setCrossFadeEnabled(true);
                bgImage.setImageDrawable(t);
                t.startTransition(1500);
            } else {
                bgImage.setImageBitmap(bmp);
            }
            bgReady = bmp != null;
        } else {
            bgImage.setImageDrawable(null);
            bgVideo.setVisibility(idleBox.getVisibility() == View.VISIBLE ? View.VISIBLE : View.GONE);
            bgVideo.setOnPreparedListener(new android.media.MediaPlayer.OnPreparedListener() {
                public void onPrepared(android.media.MediaPlayer mp) {
                    mp.setLooping(true);
                    mp.setVolume(0f, 0f);
                    fillScreen(bgVideo, mp);
                }
            });
            bgVideo.setOnErrorListener(new android.media.MediaPlayer.OnErrorListener() {
                public boolean onError(android.media.MediaPlayer mp, int what, int extra) {
                    android.util.Log.w("NowPlaying", "background video error " + what + "/" + extra);
                    clearBg();
                    return true;
                }
            });
            bgVideo.setVideoPath(file.getPath());
            bgReady = true;
        }
        bgLayer.setVisibility(idleBox.getVisibility() == View.VISIBLE && bgReady ? View.VISIBLE : View.GONE);
    }

    private static int darker(int c) {
        float[] hsv = new float[3];
        Color.colorToHSV(c, hsv);
        hsv[2] *= 0.45f;
        return Color.HSVToColor(hsv);
    }

    /** Фон: градиент из трёх цветов обложки (сервер присылает colors), иначе — из одного цвета. */
    private void animateBackground(JSONObject j) {
        int[] to;
        lastNow = j;
        JSONArray cs = "color".equals(trackBgMode) ? null : j.optJSONArray("colors");
        try {
            if (cs != null && cs.length() == 3) {
                to = new int[]{Color.parseColor(cs.getString(0)), Color.parseColor(cs.getString(1)),
                        Color.parseColor(cs.getString(2))};
            } else {
                int c = Color.parseColor(j.optString("color", "#202020"));
                to = new int[]{c, darker(c), darker(c)};
            }
        } catch (Exception e) {
            return;
        }
        if (!forceBackground && java.util.Arrays.equals(to, bgColors)) return;
        forceBackground = false;
        final int[] from = bgColors.clone(), target = to;
        final ArgbEvaluator ev = new ArgbEvaluator();
        ValueAnimator a = ValueAnimator.ofFloat(0f, 1f);
        a.setDuration(1200);
        a.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            public void onAnimationUpdate(ValueAnimator va) {
                float f = va.getAnimatedFraction();
                int[] c = new int[3];
                for (int i = 0; i < 3; i++) c[i] = (Integer) ev.evaluate(f, from[i], target[i]);
                background.setColors(c);
            }
        });
        a.start();
        bgColors = to;
        setGlowColor(to);
        bgColor = to[0];
    }

    private static final int SHADOW = 36;

    // ---------------- темы экрана трека ----------------

    private static LinearLayout.LayoutParams lin(int w, int h, float weight) {
        return new LinearLayout.LayoutParams(w, h, weight);
    }

    private LinearLayout column(int gravity) {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setGravity(gravity);
        return c;
    }

    /** Размер обложки (dp) и можно ли под ней тень в этой теме. */
    private void setCoverSize(int sizePx, boolean shadowAllowed) {
        cover.setLayoutParams(new FrameLayout.LayoutParams(sizePx, sizePx, Gravity.CENTER));
        themeShadow = shadowAllowed;
        int sh = shadowAllowed ? dp(SHADOW) : 0;
        coverFrame.setPadding(sh, sh, sh, sh);
        if (shadowAllowed && coverShadowSize != sizePx) {
            shadowDrawable = new BitmapDrawable(getResources(), coverShadow(sizePx, sh));
            coverShadowSize = sizePx;
        }
        coverFrame.setBackgroundDrawable(shadowAllowed && shadowOn ? shadowDrawable : null);
    }

    /** Обложка с тенью как элемент LinearLayout: отрицательные отступы съедают поля под тень. */
    private LinearLayout.LayoutParams coverParams(int sizePx) {
        int sh = themeShadow ? dp(SHADOW) : 0;
        LinearLayout.LayoutParams cp = lin(sizePx + 2 * sh, sizePx + 2 * sh, 0);
        cp.setMargins(-sh, -sh, -sh, -sh);
        return cp;
    }

    /** Текстовая колонка: название, исполнитель, текст песни, полоска и время. */
    private void fillTextColumn(LinearLayout col, int lyricsTopDp) {
        col.addView(title);
        col.addView(artist);
        col.addView(unsynced);
        LinearLayout.LayoutParams lp = lin(-1, 0, 1);
        lp.topMargin = dp(lyricsTopDp);
        lp.bottomMargin = dp(12);
        col.addView(lyrics, lp);
        LinearLayout.LayoutParams pp = lin(-1, dp(4), 0);
        pp.topMargin = dp(14);
        col.addView(progress, pp);
        col.addView(times);
    }

    /** Переложить элементы экрана трека под тему: classic, scene, poster, minimal, vinyl. */
    private void applyTheme(String t) {
        if (t.equals(theme)) return;
        theme = t;
        for (View v : new View[]{coverFrame, title, artist, unsynced, lyrics, progress, times, vinyl}) {
            if (v.getParent() != null) ((android.view.ViewGroup) v.getParent()).removeView(v);
        }
        player.removeAllViews();
        // общие значения, темы меняют только своё
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 30);
        title.setGravity(Gravity.LEFT);
        title.setMaxLines(2);
        artist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        artist.setGravity(Gravity.LEFT);
        artist.setAllCaps(false);
        artist.setTextColor(Color.argb(200, 255, 255, 255));
        unsynced.setGravity(Gravity.LEFT);
        lyrics.setCentered(false);
        lyrics.setTextSizeSp(26);
        lyrics.setAlpha(1f);
        if (growAnim != null) growAnim.cancel();
        growCol = null;
        times.setVisibility(View.VISIBLE);
        vinyl.setSpinning(false);
        int screenH = Math.min(getResources().getDisplayMetrics().heightPixels, getResources().getDisplayMetrics().widthPixels);

        if ("scene".equals(t)) {
            // обложка, название и полоска по центру экрана; когда приходит текст,
            // блок уезжает вверх, а строки выдвигаются из-под полоски (см. sceneLyrics)
            final LinearLayout col = column(Gravity.CENTER);
            col.setPadding(dp(48), dp(30), dp(48), dp(26));
            setCoverSize(dp(280), true);
            col.addView(coverFrame, coverParams(dp(280)));
            title.setGravity(Gravity.CENTER_HORIZONTAL);
            title.setMaxLines(1);
            artist.setGravity(Gravity.CENTER_HORIZONTAL);
            artist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
            unsynced.setGravity(Gravity.CENTER_HORIZONTAL);
            LinearLayout.LayoutParams tp = lin(-1, -2, 0);
            tp.topMargin = dp(14);
            col.addView(title, tp);
            col.addView(artist, lin(-1, -2, 0));
            LinearLayout.LayoutParams pp = lin(dp(420), dp(3), 0);
            pp.topMargin = dp(14);
            col.addView(progress, pp);
            col.addView(unsynced, lin(-1, -2, 0));
            lyrics.setCentered(true);
            lyrics.setTextSizeSp(26);
            LinearLayout.LayoutParams lp = lin(-1, 0, 0);
            lp.topMargin = dp(10);
            col.addView(lyrics, lp);
            times.setVisibility(View.GONE);
            player.addView(col, new FrameLayout.LayoutParams(-1, -1));
            glowDrawable.setGradientCenter(0.5f, 0.28f);
            attachGrow(col);
        } else if ("poster".equals(t)) {
            // обложка во всю высоту слева, без полей и тени; текст справа
            LinearLayout row = new LinearLayout(this);
            setCoverSize(screenH, false);
            row.addView(coverFrame, lin(screenH, screenH, 0));
            LinearLayout col = column(Gravity.CENTER_VERTICAL);
            col.setPadding(dp(36), dp(36), dp(36), dp(30));
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 32);
            artist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
            lyrics.setTextSizeSp(24);
            fillTextColumn(col, 16);
            row.addView(col, lin(0, -1, 1));
            player.addView(row, new FrameLayout.LayoutParams(-1, -1));
            glowDrawable.setGradientCenter(0.3f, 0.5f);
        } else if ("minimal".equals(t)) {
            // без обложки: крупная типографика по центру экрана; текст песни появляется
            // между названием и полоской и раздвигает их (см. growLyrics)
            final LinearLayout col = column(Gravity.LEFT | Gravity.CENTER_VERTICAL);
            col.setPadding(dp(64), dp(44), dp(64), dp(36));
            artist.setAllCaps(true);
            artist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
            artist.setTextColor(Color.argb(150, 255, 255, 255));
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 56);
            title.setMaxLines(1);
            col.addView(artist, lin(-1, -2, 0));
            col.addView(title, lin(-1, -2, 0));
            col.addView(unsynced, lin(-1, -2, 0));
            lyrics.setTextSizeSp(38);
            LinearLayout.LayoutParams lp = lin(-1, 0, 0);
            lp.topMargin = dp(20);
            lp.bottomMargin = dp(10);
            col.addView(lyrics, lp);
            LinearLayout.LayoutParams pp = lin(-1, dp(3), 0);
            pp.topMargin = dp(10);
            col.addView(progress, pp);
            col.addView(times, lin(-1, -2, 0));
            player.addView(col, new FrameLayout.LayoutParams(-1, -1));
            glowDrawable.setGradientCenter(0.5f, 0.5f);
            attachGrow(col);
        } else if ("vinyl".equals(t)) {
            // конверт-обложка слева, из него выезжает крутящаяся пластинка, текст справа
            FrameLayout stage = new FrameLayout(this);
            int disc = dp(400), sleeve = dp(360), sh = dp(SHADOW);
            FrameLayout.LayoutParams vp = new FrameLayout.LayoutParams(disc, disc, Gravity.LEFT | Gravity.CENTER_VERTICAL);
            vp.leftMargin = dp(250);   // пластинка выезжает из конверта, наклейка видна
            stage.addView(vinyl, vp);
            setCoverSize(sleeve, true);
            FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(sleeve + 2 * sh, sleeve + 2 * sh, Gravity.LEFT | Gravity.CENTER_VERTICAL);
            cp.leftMargin = dp(36) - sh;
            stage.addView(coverFrame, cp);
            LinearLayout col = column(Gravity.CENTER_VERTICAL);
            col.setPadding(0, dp(40), dp(36), dp(36));
            title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 26);
            artist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 19);
            lyrics.setTextSizeSp(21);
            fillTextColumn(col, 14);
            FrameLayout.LayoutParams colp = new FrameLayout.LayoutParams(-1, -1);
            colp.leftMargin = dp(250) + disc + dp(30);
            stage.addView(col, colp);
            player.addView(stage, new FrameLayout.LayoutParams(-1, -1));
            glowDrawable.setGradientCenter(0.3f, 0.5f);
        } else {
            // «Классика»: обложка слева, текст справа
            LinearLayout row = new LinearLayout(this);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(36), dp(36), dp(36), dp(36));
            setCoverSize(dp(400), true);
            row.addView(coverFrame, coverParams(dp(400)));
            LinearLayout col = column(Gravity.CENTER_VERTICAL);
            fillTextColumn(col, 18);
            LinearLayout.LayoutParams rp = lin(0, -1, 1);
            rp.leftMargin = dp(36);
            row.addView(col, rp);
            player.addView(row, new FrameLayout.LayoutParams(-1, -1));
            glowDrawable.setGradientCenter(0.23f, 0.5f);
        }
        glow.invalidate();
    }

    /** Колонка, в которой текст песни «вырастает» до свободного места, когда находится. */
    private void attachGrow(final LinearLayout col) {
        growCol = col;
        col.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            public void onLayoutChange(View v, int l, int tt, int r, int b, int ol, int ot, int or, int ob) {
                // размер экрана поменялся, а текст уже выехал — подогнать высоту
                if (growCol != col || !growShown || (growAnim != null && growAnim.isRunning())) return;
                final int h = growSpace();
                if (h != ((LinearLayout.LayoutParams) lyrics.getLayoutParams()).height)
                    col.post(new Runnable() { public void run() { if (growCol == col) setGrowH(h); } });
            }
        });
        growLyrics(lyrics.hasLyrics(), false);
    }

    /** Сколько места под текст песни: высота колонки минус всё остальное. */
    private int growSpace() {
        int h = growCol.getHeight() - growCol.getPaddingTop() - growCol.getPaddingBottom();
        for (int i = 0; i < growCol.getChildCount(); i++) {
            View c = growCol.getChildAt(i);
            if (c == lyrics || c.getVisibility() == View.GONE) continue;
            LinearLayout.LayoutParams p = (LinearLayout.LayoutParams) c.getLayoutParams();
            h -= c.getHeight() + p.topMargin + p.bottomMargin;
        }
        LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) lyrics.getLayoutParams();
        return Math.max(0, h - lp.topMargin - lp.bottomMargin);
    }

    private void setGrowH(int h) {
        LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) lyrics.getLayoutParams();
        if (lp.height != h) { lp.height = h; lyrics.setLayoutParams(lp); }
    }

    /** «Сцена» и «Минимал»: без текста блок стоит по центру; когда текст находится, он
     *  вырастает до свободного места и раздвигает остальное. */
    private void growLyrics(boolean show, final boolean animate) {
        if (growCol == null) return;
        growShown = show;
        if (growAnim != null) growAnim.cancel();
        if (!show) { setGrowH(0); lyrics.setAlpha(0f); return; }
        final LinearLayout col = growCol;
        col.post(new Runnable() {
            public void run() {
                if (growCol != col || !growShown) return;
                final int target = growSpace();
                final int from = ((LinearLayout.LayoutParams) lyrics.getLayoutParams()).height;
                if (!animate || target <= from) { setGrowH(target); lyrics.setAlpha(1f); return; }
                growAnim = ValueAnimator.ofFloat(0f, 1f);
                growAnim.setDuration(1100);
                growAnim.setInterpolator(new DecelerateInterpolator(1.8f));
                growAnim.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
                    public void onAnimationUpdate(ValueAnimator va) {
                        if (growCol != col) return;
                        float f = va.getAnimatedFraction();
                        setGrowH(from + Math.round((target - from) * f));
                        lyrics.setAlpha(Math.min(1f, f * 1.4f));
                    }
                });
                growAnim.start();
            }
        });
    }

    /** Картинка тени для обложки size×size с полями pad: размытие ~18 dp, 55%, сдвиг вниз 8 dp. */
    private Bitmap coverShadow(int size, int pad) {
        Bitmap b = Bitmap.createBitmap(size + 2 * pad, size + 2 * pad, Bitmap.Config.ARGB_8888);
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setColor(0x8c000000);
        p.setMaskFilter(new BlurMaskFilter(dp(30), BlurMaskFilter.Blur.NORMAL));
        new Canvas(b).drawRect(pad, pad + dp(8), pad + size, pad + size + dp(8), p);
        return b;
    }

    private void setCover(Bitmap bmp) {
        Drawable from = cover.getDrawable();
        // берём только последнюю картинку, иначе переходы вкладываются друг в друга и копят память
        if (from instanceof TransitionDrawable) from = ((TransitionDrawable) from).getDrawable(1);
        coverBmp = bmp;
        Drawable to = bmp != null ? new BitmapDrawable(getResources(), bmp) : new ColorDrawable(0x33ffffff);
        vinyl.setLabel(bmp);
        if (from == null) from = new ColorDrawable(0);
        TransitionDrawable t = new TransitionDrawable(new Drawable[]{from, to});
        t.setCrossFadeEnabled(true);
        cover.setImageDrawable(t);
        t.startTransition(500);
    }

    /** При падении перезапускаемся через 3 с (экран у колонки должен жить сам),
     *  но не чаще трёх раз за 5 минут — чтобы не было бесконечного цикла. */
    private void installCrashRestart() {
        final Thread.UncaughtExceptionHandler system = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            public void uncaughtException(Thread th, Throwable ex) {
                try {
                    long now = System.currentTimeMillis();
                    String[] old = prefs.getString("crashes", "").split(",");
                    StringBuilder recent = new StringBuilder();
                    int count = 0;
                    for (String c : old) {
                        try {
                            if (now - Long.parseLong(c) < 5 * 60 * 1000) { recent.append(c).append(','); count++; }
                        } catch (NumberFormatException ignored) { }
                    }
                    prefs.edit().putString("crashes", recent.append(now).toString()).commit();
                    if (count < 3) {
                        android.app.PendingIntent pi = android.app.PendingIntent.getActivity(Screen.this, 0,
                                new android.content.Intent(Screen.this, MainActivity.class)
                                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                                // Android 12+ требует явно сказать, что PendingIntent неизменяемый (FLAG_IMMUTABLE)
                                android.os.Build.VERSION.SDK_INT >= 23 ? 0x04000000 : 0);
                        ((android.app.AlarmManager) getSystemService(ALARM_SERVICE))
                                .set(android.app.AlarmManager.RTC, now + 3000, pi);
                    }
                } catch (Throwable ignored) { }
                if (system != null) system.uncaughtException(th, ex);
                else System.exit(2);
            }
        });
    }

    private boolean isNight() {
        if (!nightOn) return false;
        int h = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        return nightFrom > nightTo ? (h >= nightFrom || h < nightTo) : (h >= nightFrom && h < nightTo);
    }

    /** Подсветка: днём с музыкой — как в системе; часы — тускло; ночью — ещё тусклее;
     *  30 минут без музыки — чёрный экран с минимальной подсветкой (тап будит на минуту). */
    private void updateScreen(boolean idleNow, long t) {
        boolean night = isNight();
        boolean darkNow = idleNow && darkAfterMs > 0 && t - idleSince > darkAfterMs && t > wakeUntil
                && !timerActive() && !ringing && !sunriseOn;
        dark.setVisibility(darkNow ? View.VISIBLE : View.GONE);
        if (nightShown == null || nightShown != night) {
            // ночью часы тёмно-красные (не слепят и не светят в комнату), фон затемнён сильнее
            nightShown = night;
            clock.setTextColor(night ? 0xffa3281e : 0xffffffff);
            date.setTextColor(night ? 0x99a3281e : Color.argb(190, 255, 255, 255));
            weatherText.setTextColor(night ? 0x80a3281e : Color.argb(170, 255, 255, 255));
            bgDim.setBackgroundColor(night ? 0xd9000000 : dimColor(clockDim));
            for (WidgetView w : slotViews) w.setNight(night);
            wolView.setNight(night);
            styleTimer(night);
        }
        syncBgVideo(idleNow && !darkNow);
        syncClip(!idleNow && !darkNow);
        float b;
        if (sunriseOn) b = 0.02f + 0.78f * Math.max(0, sunriseShown);
        else if (ringing) b = night ? 0.2f : 0.7f;
        else if (darkNow) b = 0.0f;
        else if (idleNow) b = night ? 0.02f : (bgReady ? 0.35f : 0.15f);  // с фоном днём ярче, иначе его не видно
        else b = night ? 0.10f : -1f;    // -1 — яркость, выбранная в настройках
        if (b != appliedBrightness) {
            WindowManager.LayoutParams lp = window.getAttributes();
            lp.screenBrightness = b;
            window.setAttributes(lp);
            appliedBrightness = b;
        }
    }

    // ---------------- таймер ----------------

    private boolean timerActive() { return timerEnd > 0 || timerLeft > 0; }

    private long timerRemaining() {
        return timerLeft > 0 ? timerLeft : Math.max(0, timerEnd - System.currentTimeMillis());
    }

    /** 4:59, 12:00, 1:05:00 — секунды округляем вверх, чтобы 0:00 совпало со звонком. */
    private static String fmtLeft(long ms) {
        long s = (ms + 999) / 1000, h = s / 3600, m = s / 60 % 60, sec = s % 60;
        return h > 0 ? String.format(Locale.US, "%d:%02d:%02d", h, m, sec) : String.format(Locale.US, "%d:%02d", m, sec);
    }

    private void saveTimer() {
        if (!timerActive()) pomoPhase = -1;
        prefs.edit().putLong("timer_end", timerEnd).putLong("timer_left", timerLeft)
                .putInt("pomo_phase", pomoPhase).putInt("pomo_round", pomoRound).commit();
        clockTickNow();
    }

    private void clockTickNow() {
        ui.removeCallbacks(clockTick);
        clockTick.run();
    }

    private void startTimer(int minutes) {
        pomoPhase = -1;
        timerEnd = System.currentTimeMillis() + minutes * 60000L;
        timerLeft = 0;
        saveTimer();
        hideTimerPanel();
        flash(minutes + " мин");
    }

    private void addTimer(int minutes) {
        if (timerLeft > 0) timerLeft += minutes * 60000L;
        else timerEnd += minutes * 60000L;
        saveTimer();
        showTimerPanel();
    }

    private void pauseTimer() {
        if (timerLeft > 0) {
            timerEnd = System.currentTimeMillis() + timerLeft;
            timerLeft = 0;
        } else {
            timerLeft = Math.max(1000, timerRemaining());
            timerEnd = 0;
        }
        saveTimer();
        showTimerPanel();
    }

    private void resetTimer() {
        timerEnd = timerLeft = 0;
        saveTimer();
        hideTimerPanel();
    }

    private TextView chip(String label, View.OnClickListener click) {
        TextView c = text(22, false, 255);
        c.setText(label);
        c.setGravity(Gravity.CENTER);
        c.setPadding(dp(20), dp(11), dp(20), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(30));
        c.setBackgroundDrawable(bg);
        c.setOnClickListener(click);
        // пульт ТВ: кнопки выбираются стрелками, выбранная обведена
        c.setFocusable(true);
        c.setOnFocusChangeListener(new View.OnFocusChangeListener() {
            public void onFocusChange(View v, boolean has) {
                ((GradientDrawable) v.getBackground()).setStroke(has ? dp(3) : 0, timerFg);
                panelUntil = SystemClock.elapsedRealtime() + 12000;
            }
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.leftMargin = lp.rightMargin = dp(5);
        c.setLayoutParams(lp);
        return c;
    }

    /** Панель внизу: без таймера — сколько минут поставить; с таймером — пауза, добавить, сбросить. */
    private void showTimerPanel() {
        timerPanel.removeAllViews();
        TextView title = text(18, false, 170);
        title.setText(pomoPhase >= 0 ? "Помодоро" : timerActive() ? "Таймер" : "Таймер, минут");
        title.setPadding(dp(8), 0, dp(10), 0);
        timerPanel.addView(title);
        if (!timerActive()) {
            for (final int m : TIMER_PRESETS) {
                timerPanel.addView(chip(String.valueOf(m), new View.OnClickListener() {
                    public void onClick(View v) { startTimer(m); }
                }));
            }
            timerPanel.addView(chip("Помодоро", new View.OnClickListener() {
                public void onClick(View v) { startPomodoro(); }
            }));
        } else {
            timerPanel.addView(chip(timerLeft > 0 ? "Дальше" : "Пауза", new View.OnClickListener() {
                public void onClick(View v) { pauseTimer(); }
            }));
            if (pomoPhase >= 0) {
                timerPanel.addView(chip(pomoPhase == 0 ? "К перерыву" : "К работе", new View.OnClickListener() {
                    public void onClick(View v) { nextPomoPhase(false); showTimerPanel(); }
                }));
            } else {
                timerPanel.addView(chip("+1 мин", new View.OnClickListener() {
                    public void onClick(View v) { addTimer(1); }
                }));
                timerPanel.addView(chip("+5 мин", new View.OnClickListener() {
                    public void onClick(View v) { addTimer(5); }
                }));
            }
            timerPanel.addView(chip("Сбросить", new View.OnClickListener() {
                public void onClick(View v) { resetTimer(); }
            }));
        }
        styleTimer(nightShown != null && nightShown);
        timerPanel.setVisibility(View.VISIBLE);
        setWidgetsAlpha(0f);   // панель встаёт на место виджетов
        panelUntil = SystemClock.elapsedRealtime() + 12000;
    }

    private void hideTimerPanel() {
        timerPanel.setVisibility(View.GONE);
        setWidgetsAlpha(1f);
    }

    private void setWidgetsAlpha(float a) {
        for (WidgetView w : slotViews) if (w != null) w.setAlpha(a);
        if (wolView != null) wolView.setAlpha(a);
        if (resumeView != null) resumeView.setAlpha(a);
    }

    private void styleTimer(boolean night) {
        int fg = night ? 0xffa3281e : 0xffffffff;
        timerFg = fg;
        GradientDrawable panel = new GradientDrawable();
        panel.setCornerRadius(dp(36));
        panel.setColor(night ? 0xf2000000 : 0xeb161618);
        timerPanel.setBackgroundDrawable(panel);
        for (int i = 0; i < timerPanel.getChildCount(); i++) {
            TextView c = (TextView) timerPanel.getChildAt(i);
            if (i == 0) {
                c.setTextColor(night ? 0xb3a3281e : 0xb3ffffff);
                continue;
            }
            c.setTextColor(fg);
            ((GradientDrawable) c.getBackground()).setColor(night ? 0x33a3281e : 0x29ffffff);
        }
        GradientDrawable pill = new GradientDrawable();
        pill.setCornerRadius(dp(22));
        pill.setColor(night ? 0xcc000000 : 0x66000000);
        timerPill.setBackgroundDrawable(pill);
        timerPill.setTextColor(fg);
        ringView.setTextColor(fg);
    }

    /** Раз в 100 мс: вовремя зазвонить, обновить плашку на экране трека, убрать забытую панель. */
    private void updateTimer(long t) {
        if (timerEnd > 0 && System.currentTimeMillis() >= timerEnd) {
            if (pomoPhase >= 0) {
                nextPomoPhase(true);
            } else {
                timerEnd = timerLeft = 0;
                saveTimer();
                ring("Время вышло");
            }
        }
        updateAlarm(t);
        if (ringing && t - ringStarted > RING_MAX_MS) stopRing();
        boolean pill = timerActive() && player.getVisibility() == View.VISIBLE;
        if (pill) timerPill.setText(fmtLeft(timerRemaining()));
        if (pill != (timerPill.getVisibility() == View.VISIBLE)) timerPill.setVisibility(pill ? View.VISIBLE : View.GONE);
        if (timerPanel.getVisibility() == View.VISIBLE && t > panelUntil) hideTimerPanel();
    }

    /** Большая надпись и маленькая подсказка под ней. */
    private void showRingText(String title, String hint) {
        android.text.SpannableString msg = new android.text.SpannableString(title + "\n" + hint);
        int cut = title.length();
        msg.setSpan(new android.text.style.RelativeSizeSpan(0.36f), cut, msg.length(), 0);
        int hintColor = sunriseOn ? 0xd96b3010 : nightShown != null && nightShown ? 0x99a3281e : 0x99ffffff;
        msg.setSpan(new android.text.style.ForegroundColorSpan(hintColor), cut, msg.length(), 0);
        ringView.setText(msg);
        ringView.setLineSpacing(dp(18), 1f);
    }

    /** Звонок до касания: конец таймера или будильник. */
    private void ring(String title) {
        ringing = true;
        ringStarted = SystemClock.elapsedRealtime();
        hideTimerPanel();
        showRingText(title, "нажми, чтобы выключить");
        if (sunriseOn && sunriseColors != null) {   // будильник с рассветом: надпись на сплошном тёплом фоне
            ringView.setBackgroundDrawable(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, sunriseColors));
        } else {
            ringView.setBackgroundColor(0xe6000000);
        }
        ringView.setVisibility(View.VISIBLE);
        playSound(RingtoneManager.TYPE_ALARM, true);
    }

    /** Звук на потоке будильника; он не должен быть беззвучным — поднимаем хотя бы до 70%, потом возвращаем. */
    private void playSound(int type, boolean loop) {
        stopSound();
        try {
            AudioManager am = (AudioManager) getSystemService(AUDIO_SERVICE);
            int max = am.getStreamMaxVolume(AudioManager.STREAM_ALARM), cur = am.getStreamVolume(AudioManager.STREAM_ALARM);
            if (cur < max * 7 / 10) {
                ringVolumeWas = cur;
                am.setStreamVolume(AudioManager.STREAM_ALARM, max * 7 / 10, 0);
            }
            ringPlayer = new MediaPlayer();
            android.net.Uri uri = RingtoneManager.getDefaultUri(type);
            if (uri == null) uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
            ringPlayer.setDataSource(this, uri);
            ringPlayer.setAudioStreamType(AudioManager.STREAM_ALARM);
            ringPlayer.setLooping(loop);
            if (!loop) {
                ringPlayer.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
                    public void onCompletion(MediaPlayer mp) { stopSound(); }
                });
            }
            ringPlayer.prepare();
            ringPlayer.start();
        } catch (Exception e) {
            android.util.Log.w("NowPlaying", "sound: " + e);
            stopSound();
        }
    }

    private void stopSound() {
        if (ringPlayer != null) {
            try { ringPlayer.stop(); } catch (Exception ignored) { }
            ringPlayer.release();
            ringPlayer = null;
        }
        if (ringVolumeWas >= 0) {
            ((AudioManager) getSystemService(AUDIO_SERVICE)).setStreamVolume(AudioManager.STREAM_ALARM, ringVolumeWas, 0);
            ringVolumeWas = -1;
        }
    }

    private void stopRing() {
        ringing = false;
        ringView.setVisibility(View.GONE);
        stopSound();
    }

    // ---------------- помодоро ----------------

    private static final int POMO_WORK = 25, POMO_SHORT = 5, POMO_LONG = 15, POMO_ROUNDS = 4;

    private void startPomodoro() {
        pomoPhase = 0;
        pomoRound = 1;
        timerEnd = System.currentTimeMillis() + POMO_WORK * 60000L;
        timerLeft = 0;
        saveTimer();
        hideTimerPanel();
        flash("25 мин");
    }

    /** Фаза кончилась (или её пропустили): короткий сигнал и сразу следующая. */
    private void nextPomoPhase(boolean sound) {
        if (pomoPhase == 0) {
            pomoPhase = pomoRound >= POMO_ROUNDS ? 2 : 1;
        } else {
            pomoRound = pomoPhase == 2 ? 1 : pomoRound + 1;
            pomoPhase = 0;
        }
        int min = pomoPhase == 0 ? POMO_WORK : pomoPhase == 1 ? POMO_SHORT : POMO_LONG;
        timerEnd = System.currentTimeMillis() + min * 60000L;
        timerLeft = 0;
        saveTimer();
        if (sound) {
            playSound(RingtoneManager.TYPE_NOTIFICATION, false);
            wakeUntil = SystemClock.elapsedRealtime() + WAKE_ON_TAP_MS;
        }
        flash(pomoPhase == 0 ? "Работа" : "Перерыв");
    }

    private String pomoLine() {
        String until = endFmt.format(new Date(timerEnd));
        if (timerLeft > 0) return pomoPhase == 0 ? "Помодоро на паузе" : "Перерыв на паузе";
        if (pomoPhase == 0) return "Работа, " + pomoRound + " из " + POMO_ROUNDS + ", до " + until;
        return (pomoPhase == 2 ? "Длинный перерыв до " : "Перерыв до ") + until;
    }

    // ---------------- будильник с рассветом ----------------

    private boolean alarmOn, alarmMusic;
    private int alarmHour = 7, alarmMinute = 30, alarmSunriseMin = 20;
    private String alarmDays = "12345";
    private long alarmFired;        // какой звонок уже был (часы планшета, мс) — чтобы не звенеть дважды
    private boolean sunriseOn;
    private View sunrise;
    private GradientDrawable sunriseBg;
    private float sunriseShown = -1;
    private int[] sunriseColors;

    /** Ближайший звонок по часам планшета (как на экране), 0 — будильник выключен. */
    private long nextAlarm(long now) {
        if (!alarmOn || alarmDays.length() == 0) return 0;
        java.util.Calendar c = java.util.Calendar.getInstance();
        c.setTimeInMillis(now);
        c.set(java.util.Calendar.HOUR_OF_DAY, alarmHour);
        c.set(java.util.Calendar.MINUTE, alarmMinute);
        c.set(java.util.Calendar.SECOND, 0);
        c.set(java.util.Calendar.MILLISECOND, 0);
        c.add(java.util.Calendar.DAY_OF_MONTH, -1);
        for (int d = -1; d <= 7; d++) {
            long at = c.getTimeInMillis();
            int dow = (c.get(java.util.Calendar.DAY_OF_WEEK) + 5) % 7 + 1;   // пн = 1 … вс = 7
            // пропущенный давно (планшет был выключен) звонок не догоняем
            if (alarmDays.indexOf((char) ('0' + dow)) >= 0 && at > alarmFired && at > now - 5 * 60000L) return at;
            c.add(java.util.Calendar.DAY_OF_MONTH, 1);
        }
        return 0;
    }

    /** Раз в 100 мс: рассвет за N минут до звонка и сам звонок. */
    private void updateAlarm(long t) {
        long now = System.currentTimeMillis(), at = nextAlarm(now);
        float p = -1;
        if (at > 0 && now >= at) {
            alarmFired = at;
            prefs.edit().putLong("alarm_fired", at).commit();
            if (alarmSunriseMin > 0) p = 1;
            sunriseOn = p >= 0;
            if (alarmMusic && connected && !pcLost && trackId != null && !playing) control("toggle");
            ring("Доброе утро");
        } else if (at > 0 && alarmSunriseMin > 0 && at - now <= alarmSunriseMin * 60000L) {
            p = 1f - (at - now) / (alarmSunriseMin * 60000f);
        } else if (ringing && sunriseOn) {
            p = 1;   // пока звенит, рассвет держим
        }
        sunriseOn = p >= 0;
        if (Math.abs(p - sunriseShown) > 0.002f) {
            sunriseShown = p;
            paintSunrise(p);
        }
    }

    private static int mix(int a, int b, float k) {
        k = Math.max(0, Math.min(1, k));
        int ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255, aa = a >>> 24;
        int br = (b >> 16) & 255, bg = (b >> 8) & 255, bb = b & 255, ba = b >>> 24;
        return ((int) (aa + (ba - aa) * k) << 24) | ((int) (ar + (br - ar) * k) << 16)
                | ((int) (ag + (bg - ag) * k) << 8) | (int) (ab + (bb - ab) * k);
    }

    /** Рассвет: снизу тёплый свет поднимается от тёмно-красного к мягкому жёлтому. */
    private void paintSunrise(float p) {
        if (p < 0) {
            sunrise.setVisibility(View.GONE);
            return;
        }
        int bottom = p < 0.5f ? mix(0xff1a0503, 0xffc2441c, p * 2) : mix(0xffc2441c, 0xffffcf7a, p * 2 - 1);
        int top = p < 0.5f ? mix(0xff000000, 0xff3d0c08, p * 2) : mix(0xff3d0c08, 0xffff9a52, p * 2 - 1);
        sunriseColors = new int[]{top, mix(top, bottom, 0.6f), bottom};
        sunriseBg.setColors(sunriseColors);
        sunrise.setVisibility(View.VISIBLE);
    }

    /** Пульт ТВ (и клавиатура): ОК — пауза, влево/вправо — трек, вверх/вниз — громкость ПК,
     *  меню — адрес ПК, ОК на часах — таймер. Касания работают как раньше. */
    boolean dispatchKeyEvent(KeyEvent e) {
        int k = e.getKeyCode();
        boolean center = k == KeyEvent.KEYCODE_DPAD_CENTER || k == KeyEvent.KEYCODE_ENTER
                || k == KeyEvent.KEYCODE_NUMPAD_ENTER;
        boolean media = k == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || k == KeyEvent.KEYCODE_MEDIA_PLAY
                || k == KeyEvent.KEYCODE_MEDIA_PAUSE || k == KeyEvent.KEYCODE_MEDIA_NEXT
                || k == KeyEvent.KEYCODE_MEDIA_PREVIOUS || k == KeyEvent.KEYCODE_HEADSETHOOK;
        boolean arrows = k == KeyEvent.KEYCODE_DPAD_LEFT || k == KeyEvent.KEYCODE_DPAD_RIGHT
                || k == KeyEvent.KEYCODE_DPAD_UP || k == KeyEvent.KEYCODE_DPAD_DOWN;
        boolean menu = k == KeyEvent.KEYCODE_MENU;
        boolean back = k == KeyEvent.KEYCODE_BACK;
        if (!(center || media || arrows || menu || back)) return host.superDispatchKeyEvent(e);
        if (back && !ringing && timerPanel.getVisibility() != View.VISIBLE) return host.superDispatchKeyEvent(e);
        // панель таймера открыта — стрелки и ОК ходят по её кнопкам, «назад» закрывает
        if (timerPanel.getVisibility() == View.VISIBLE && !ringing && (center || arrows || back)) {
            panelUntil = SystemClock.elapsedRealtime() + 12000;
            if (back) {
                if (e.getAction() == KeyEvent.ACTION_UP) hideTimerPanel();
                return true;
            }
            if (timerPanel.findFocus() == null && e.getAction() == KeyEvent.ACTION_DOWN) {
                focusFirstChip();
                return true;
            }
            return host.superDispatchKeyEvent(e);
        }
        // долгое ОК — ввести адрес ПК (на пультах ТВ часто нет кнопки «меню»)
        if (center && e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() > 0
                && e.getEventTime() - e.getDownTime() >= 700 && !longOk && !ringing) {
            longOk = true;
            askServer();
            return true;
        }
        if (center && e.getAction() == KeyEvent.ACTION_UP && longOk) {
            longOk = false;
            return true;
        }
        if (e.getAction() == KeyEvent.ACTION_DOWN) {
            // громкость можно держать — повторяется; остальное — один раз на нажатие
            if ((k == KeyEvent.KEYCODE_DPAD_UP || k == KeyEvent.KEYCODE_DPAD_DOWN)
                    && player.getVisibility() == View.VISIBLE && volume >= 0 && dark.getVisibility() != View.VISIBLE) {
                setVolume(volume + (k == KeyEvent.KEYCODE_DPAD_UP ? 5 : -5), true);
            }
            return true;
        }
        if (e.getAction() != KeyEvent.ACTION_UP) return true;
        if (ringing) {
            stopRing();
            return true;
        }
        if (wakeFromDark()) return true;
        boolean onPlayer = player.getVisibility() == View.VISIBLE;
        if (menu) {
            askServer();
        } else if (k == KeyEvent.KEYCODE_MEDIA_NEXT || (onPlayer && k == KeyEvent.KEYCODE_DPAD_RIGHT)) {
            control("next");
            flash("⏭");
        } else if (k == KeyEvent.KEYCODE_MEDIA_PREVIOUS || (onPlayer && k == KeyEvent.KEYCODE_DPAD_LEFT)) {
            control("prev");
            flash("⏮");
        } else if (media || (onPlayer && center)) {
            control("toggle");
            if (onPlayer) flash(playing ? "❚❚" : "▶");
        } else if (center && idleBox.getVisibility() == View.VISIBLE) {
            if (resumeView.getVisibility() == View.VISIBLE) {
                long now = SystemClock.elapsedRealtime();   // «Продолжить» — то же, что тап по карточке
                if (now > resumeUntil && !playing) {
                    control("toggle");
                    resumeUntil = now + 5000;
                    updateResume(now);
                }
            } else {
                showTimerPanel();
                focusFirstChip();
            }
        }
        return true;
    }

    private void focusFirstChip() {
        if (timerPanel.getChildCount() > 1) timerPanel.getChildAt(1).requestFocus();
    }

    /** Если экран «спит» — будим его на минуту и не считаем касание командой. */
    private boolean wakeFromDark() {
        if (dark.getVisibility() != View.VISIBLE) return false;
        wakeUntil = SystemClock.elapsedRealtime() + WAKE_ON_TAP_MS;
        idleSince = SystemClock.elapsedRealtime() - darkAfterMs + WAKE_ON_TAP_MS;
        return true;
    }

    private void flash(String symbol) {
        flashIcon.setText(symbol);
        flashIcon.animate().cancel();
        flashIcon.setAlpha(1f);
        flashIcon.animate().alpha(0f).setStartDelay(350).setDuration(500).start();
    }

    private void control(final String action) {
        final String s = server;
        if (s == null) return;
        pool.execute(new Runnable() {
            public void run() {
                try { get("http://" + s + "/api/control/" + action, 3000); } catch (Exception ignored) { }
            }
        });
    }

    /** Свечение — самый яркий из цветов фона, поднятый по яркости (фон сам по себе приглушён). */
    private void setGlowColor(int[] colors) {
        float[] best = null;
        for (int c : colors) {
            float[] hsv = new float[3];
            Color.colorToHSV(c, hsv);
            if (best == null || hsv[1] * hsv[2] > best[1] * best[2]) best = hsv;
        }
        best[1] = Math.max(best[1], 0.45f);
        best[2] = 0.95f;
        int c = Color.HSVToColor(best);
        glowDrawable.setColors(new int[]{(c & 0x00ffffff) | 0x99000000, c & 0x00ffffff});
        visualizer.setColor(c);
        // у каждого пятна свой цвет фона (их три), так же поднятый по яркости
        for (int i = 0; i < 3; i++) {
            float[] hsv = new float[3];
            Color.colorToHSV(colors[Math.min(i, colors.length - 1)], hsv);
            if (hsv[1] < 0.12f) hsv = best.clone();     // серый угол обложки — берём главный цвет
            hsv[1] = Math.max(hsv[1], 0.45f);
            hsv[2] = 0.95f;
            int sc = Color.HSVToColor(hsv);
            spotDrawables[i].setColors(new int[]{(sc & 0x00ffffff) | 0x88000000, sc & 0x00ffffff});
            spots[i].invalidate();
        }
    }

    /** 25 раз в секунду: прозрачность свечения по уровню звука (с лёгким сглаживанием). */
    private final Runnable glowTick = new Runnable() {
        public void run() {
            vinyl.setSpinning("vinyl".equals(theme) && playerShown && playing && dark.getVisibility() != View.VISIBLE);
            boolean vis = !"off".equals(visStyle) && playerShown && playing && dark.getVisibility() != View.VISIBLE;
            if (vis != (visualizer.getVisibility() == View.VISIBLE)) visualizer.setVisibility(vis ? View.VISIBLE : View.GONE);
            visualizer.setRunning(vis);
            boolean on = pulseOn && playerShown && playing && dark.getVisibility() != View.VISIBLE;
            // свечение — три пятна по экрану, и только когда за треком нет видео; с видео его нет совсем
            boolean spread = on && clipLayer.getVisibility() != View.VISIBLE;
            if (false) {   // одно свечение за обложкой больше не показываем
                float target = pulseLevel;
                glowShown += (target - glowShown) * (target > glowShown ? 0.6f : 0.18f);
                if (glow.getVisibility() != View.VISIBLE) glow.setVisibility(View.VISIBLE);
                glow.setAlpha(0.15f + 0.85f * glowShown);
            } else if (glow.getVisibility() == View.VISIBLE) {
                glowShown = 0;
                glow.setVisibility(View.GONE);
            }
            // полос нет (старый сервер или звук не слышен) — пятна дышат по общему уровню, вразнобой
            boolean bands = SystemClock.uptimeMillis() - spotFreshAt < 2500;
            long t = SystemClock.uptimeMillis();
            for (int i = 0; i < 3; i++) {
                if (spread) {
                    float target = bands ? spotLevel[i]
                            : pulseLevel * (0.65f + 0.35f * (float) Math.sin(t / (900.0 + 370 * i) + i * 2.1));
                    spotShown[i] += (target - spotShown[i]) * (target > spotShown[i] ? 0.55f : 0.14f);
                    if (spots[i].getVisibility() != View.VISIBLE) spots[i].setVisibility(View.VISIBLE);
                    spots[i].setAlpha(0.12f + 0.88f * spotShown[i]);
                } else if (spots[i].getVisibility() == View.VISIBLE) {
                    spotShown[i] = 0;
                    spots[i].setVisibility(View.GONE);
                }
            }
            ui.postDelayed(this, 40);
        }
    };

    /** Держит открытым поток /api/pulse, пока показывается экран трека и играет музыка. */
    private void pulseLoop(int gen) {
        while (running && gen == generation) {
            String s = server;
            if (!pulseOn || !playerShown || !playing || s == null) {
                pulseLevel = 0;
                SystemClock.sleep(1000);
                continue;
            }
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL("http://" + s + "/api/pulse").openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(5000);
                java.io.BufferedReader r = new java.io.BufferedReader(new java.io.InputStreamReader(c.getInputStream()));
                String line;
                while (running && gen == generation && pulseOn && playerShown && playing && (line = r.readLine()) != null) {
                    try { pulseLevel = Float.parseFloat(line.trim()); } catch (NumberFormatException ignored) { }
                }
            } catch (Exception e) {
                SystemClock.sleep(2000);   // сервер старый (нет /api/pulse) или нет связи
            } finally {
                if (c != null) c.disconnect();
            }
            pulseLevel = 0;
        }
    }

    /** Держит открытым поток /api/bands (полосы частот), пока визуализатор на экране. */
    private void bandsLoop(int gen) {
        while (running && gen == generation) {
            String s = server;
            if (!wantBands() || s == null) {
                SystemClock.sleep(1000);
                continue;
            }
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL("http://" + s + "/api/bands").openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(5000);
                java.io.BufferedReader r = new java.io.BufferedReader(new java.io.InputStreamReader(c.getInputStream()));
                String line;
                while (running && gen == generation && wantBands() && (line = r.readLine()) != null) {
                    if (!"off".equals(visStyle)) visualizer.setLine(line);
                    if (pulseOn) spotLine(line);
                }
            } catch (Exception e) {
                SystemClock.sleep(2000);   // сервер старый (нет /api/bands) или нет связи
            } finally {
                if (c != null) c.disconnect();
            }
            visualizer.clear();
        }
    }

    /** Весь экран под приложение. На Android 4.4+ — «погружение»: прячем строку состояния,
     *  кнопки и панель задач (на новых планшетах), они появляются от свайпа с края и сами
     *  скрываются. На старых — только приглушаем (иначе первое касание уходит на показ кнопок). */
    private void hideSystemBars() {
        if (android.os.Build.VERSION.SDK_INT >= 19) {
            // LAYOUT_STABLE | LAYOUT_HIDE_NAVIGATION | LAYOUT_FULLSCREEN | HIDE_NAVIGATION | FULLSCREEN | IMMERSIVE_STICKY
            root.setSystemUiVisibility(0x100 | 0x200 | 0x400 | 0x2 | 0x4 | 0x1000);
        } else {
            root.setSystemUiVisibility(View.SYSTEM_UI_FLAG_LOW_PROFILE);
        }
    }

    void onWindowFocusChanged(boolean hasFocus) {
        if (hasFocus && !dream) hideSystemBars();   // после диалогов и шторки — снова на весь экран
    }

    /** До 2.8 приложение называлось com.tab3.nowplaying — для Android это другое приложение,
     *  и после переезда на устройстве стояли бы два. Убираем старое: с root — тихо, без root —
     *  системным окном «Удалить?» (один раз). */
    private void removeOldPackage() {
        final String old = "com.tab3.nowplaying";
        try {
            getPackageManager().getPackageInfo(old, 0);
        } catch (Exception notInstalled) {
            return;
        }
        pool.execute(new Runnable() {
            public void run() {
                boolean silent = false;
                try {
                    Process p = Runtime.getRuntime().exec(new String[]{"su", "-c", "pm uninstall " + old});
                    silent = p.waitFor() == 0;
                } catch (Exception noRoot) { }
                if (silent || prefs.getBoolean("old_pkg_asked", false)) return;
                prefs.edit().putBoolean("old_pkg_asked", true).commit();
                ui.post(new Runnable() {
                    public void run() {
                        try {
                            startActivity(new android.content.Intent(android.content.Intent.ACTION_DELETE,
                                    android.net.Uri.parse("package:" + old)));
                        } catch (Exception ignored) { }
                    }
                });
            }
        });
    }

    /** Полосы нужны визуализатору или пятнам свечения. */
    private boolean wantBands() {
        return (!"off".equals(visStyle) || pulseOn) && playerShown && playing;
    }

    /** 24 полосы -> уровни трёх пятен: басы (0–4), середина (5–13), верха (14–23). */
    private void spotLine(String line) {
        String[] p = line.trim().split(" ");
        if (p.length < 24) return;
        int[][] groups = {{0, 5}, {5, 14}, {14, 24}};
        boolean any = false;
        for (int g = 0; g < 3; g++) {
            float sum = 0, max = 0;
            for (int i = groups[g][0]; i < groups[g][1]; i++) {
                float v;
                try { v = Integer.parseInt(p[i]) / 99f; } catch (NumberFormatException e) { v = 0; }
                sum += v;
                max = Math.max(max, v);
            }
            float level = 0.5f * sum / (groups[g][1] - groups[g][0]) + 0.5f * max;
            spotLevel[g] = Math.min(1f, level * 1.15f);
            any |= level > 0.01f;
        }
        if (any) spotFreshAt = SystemClock.uptimeMillis();
    }

    private static int dimColor(int percent) {
        return Color.argb(Math.round(percent * 2.55f), 0, 0, 0);
    }

    /** Настройки из окна на ПК. Применяем, только если что-то поменялось. */
    private void applyUi(JSONObject u) {
        if (u == null || u.toString().equals(uiJson)) return;
        uiJson = u.toString();
        String mode = u.optString("track_bg", "video");
        boolean modeChanged = !mode.equals(trackBgMode);
        trackBgMode = mode;
        trackDim = u.optInt("track_dim", 55);
        resumeOn = u.optBoolean("resume", true);
        visStyle = u.optString("visualizer", "off");
        visualizer.setStyle(visStyle);
        alarmOn = u.optBoolean("alarm_on", false);
        alarmMusic = u.optBoolean("alarm_music", false);
        alarmSunriseMin = u.optInt("alarm_sunrise", 20);
        alarmDays = u.optString("alarm_days", "12345");
        String[] hm = u.optString("alarm_time", "07:30").split(":");
        try {
            alarmHour = Integer.parseInt(hm[0]);
            alarmMinute = Integer.parseInt(hm[1]);
        } catch (Exception ignored) { }
        clipDim.setBackgroundColor(dimColor(trackDim));
        clockDim = u.optInt("clock_dim", 35);
        nightOn = u.optBoolean("night", true);
        nightFrom = u.optInt("night_from", 23);
        nightTo = u.optInt("night_to", 8);
        nightShown = null;   // перекрасить часы при следующем тике
        darkAfterMs = u.optInt("dark_after_min", 30) * 60L * 1000;
        pulseOn = u.optBoolean("pulse", false);
        applyTheme(u.optString("theme", "classic"));
        applyColor(u.optInt("color_sat", 0), u.optInt("color_contrast", 0), u.optInt("color_bright", 0), u.optInt("color_warm", 0));
        boolean sh = u.optBoolean("shadow", true);
        if (sh != shadowOn) {
            shadowOn = sh;
            coverFrame.setBackgroundDrawable(sh && themeShadow ? shadowDrawable : null);
        }
        if (modeChanged && lastNow != null) {
            forceBackground = true;
            animateBackground(lastNow);
        }
    }

    private void showWeather(JSONObject w) {
        if (w == null || weatherInWidget) {
            weatherText.setVisibility(View.GONE);
            return;
        }
        int t = w.optInt("temp");
        String text = (t > 0 ? "+" : "") + t + "°";
        String d = w.optString("text", "");
        if (d.length() > 0) text += "  " + d;
        weatherText.setText(text);
        weatherText.setVisibility(View.VISIBLE);
    }

    /** Громкость ПК: подпись на обложке сразу, на ПК — не чаще раза в 80 мс (и всегда последнее). */
    private void setVolume(int level, boolean force) {
        level = Math.max(0, Math.min(100, level));
        volume = level;
        volumeLabel.setText("Громкость  " + level + "%");
        // аниматоры на 4.2 при частых перезапусках сразу ставят конечную прозрачность —
        // поэтому показываем сразу, а гасим один раз через секунду после последнего движения
        volumeLabel.animate().cancel();
        volumeLabel.setAlpha(1f);
        ui.removeCallbacks(hideVolume);
        ui.postDelayed(hideVolume, 1000);
        long t = SystemClock.elapsedRealtime();
        if (force || t - volumeSentAt > 80) {
            volumeSentAt = t;
            volumeTarget = level;
            if (force) volumeSent = -1;   // последнее значение отправить в любом случае
            volumeExec.execute(sendVolume);
        }
    }

    private void seekTo(long ms) {
        if (duration > 0) ms = Math.min(ms, duration - 1000);
        ms = Math.max(0, ms);
        position = ms;
        receivedAt = SystemClock.elapsedRealtime();
        seekHoldUntil = receivedAt + 2500;
        control("seek/" + ms);
    }

    private float screenY(View v) {
        int[] xy = new int[2];
        v.getLocationOnScreen(xy);
        return xy[1];
    }

    private boolean inside(View v, MotionEvent e) {
        int[] xy = new int[2];
        v.getLocationOnScreen(xy);
        float x = e.getRawX(), y = e.getRawY();
        return x >= xy[0] && x < xy[0] + v.getWidth() && y >= xy[1] && y < xy[1] + v.getHeight();
    }

    /** Перемотка: палец на полоске прогресса (зона касания выше и ниже самой полоски). */
    private boolean dragSeek(MotionEvent e) {
        int action = e.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            seekDragging = false;
            if (dark.getVisibility() == View.VISIBLE || player.getVisibility() != View.VISIBLE || duration <= 0) return false;
            int[] xy = new int[2];
            progress.getLocationOnScreen(xy);
            float x = e.getRawX(), y = e.getRawY();
            if (x < xy[0] - dp(12) || x > xy[0] + progress.getWidth() + dp(12)
                    || Math.abs(y - (xy[1] + progress.getHeight() / 2f)) > dp(22)) return false;
            seekDragging = true;
        }
        if (!seekDragging) return false;
        int[] xy = new int[2];
        progress.getLocationOnScreen(xy);
        float f = Math.max(0, Math.min(1, (e.getRawX() - xy[0]) / Math.max(1, progress.getWidth())));
        long ms = (long) (f * duration);
        progress.setFraction(f);
        progress.setActive(action != MotionEvent.ACTION_UP && action != MotionEvent.ACTION_CANCEL);
        timeLeft.setText(fmt(ms));
        if (action == MotionEvent.ACTION_UP) {
            seekDragging = false;
            seekTo(ms);
        } else if (action == MotionEvent.ACTION_CANCEL) {
            seekDragging = false;
        }
        return true;
    }

    private static String fmt(long ms) {
        long s = Math.max(0, ms / 1000);
        return String.format(Locale.US, "%d:%02d", s / 60, s % 60);
    }

    private long livePosition() {
        long p = position + (playing ? SystemClock.elapsedRealtime() - receivedAt : 0);
        return duration > 0 ? Math.min(p, duration) : p;
    }

    private final Runnable ticker = new Runnable() {
        public void run() {
            if (trackId != null && !seekDragging) {
                long p = livePosition();
                progress.setFraction(duration > 0 ? (float) p / duration : 0);
                long sec = duration > 0 ? p / 1000 : -1;
                if (sec != shownSecond || duration != shownDuration) {
                    shownSecond = sec;
                    shownDuration = duration;
                    timeLeft.setText(duration > 0 ? fmt(p) : "");
                    timeRight.setText(duration > 0 ? fmt(duration) : "");
                }
                lyrics.setPosition(p, duration);
            }
            long t = SystemClock.elapsedRealtime();
            boolean idleNow = !connected || trackId == null
                    || (!playing && t - pausedSince > IDLE_AFTER_PAUSE_MS);
            if (idleNow != (idleBox.getVisibility() == View.VISIBLE)) showIdle(idleNow);
            updateWol(t);
            updateResume(t);
            updateTimer(t);
            if (!idleNow || idleSince == 0) idleSince = t;
            updateScreen(idleNow, t);
            ui.postDelayed(this, 100);
        }
    };

    private final Runnable clockTick = new Runnable() {
        public void run() {
            Date now = new Date();
            if (timerActive()) {
                clock.setText(fmtLeft(timerRemaining()));
                date.setText(pomoPhase >= 0 ? pomoLine()
                        : timerLeft > 0 ? "Таймер на паузе" : "Таймер, звонок в " + endFmt.format(new Date(timerEnd)));
            } else {
                clock.setText(clockFmt.format(now));
                String d = dateFmt.format(now);
                d = d.substring(0, 1).toUpperCase(new Locale("ru")) + d.substring(1);
                long at = nextAlarm(now.getTime());
                if (at > 0 && at - now.getTime() < 16 * 3600000L) d += ", будильник в " + endFmt.format(new Date(at));
                date.setText(d);
            }
            if (now.getSeconds() == 0) moveClock(); // раз в минуту часы переезжают — чтобы экран не выгорал
            if (now.getSeconds() == 0) for (WidgetView w : slotViews) w.invalidate();   // второй город, календарь
            ui.postDelayed(this, 1000 - System.currentTimeMillis() % 1000);
        }
    };

    /** Плавно переносим часы в случайное место экрана (с отступом от краёв). */
    private void moveClock() {
        if (idleBox.getVisibility() != View.VISIBLE || idle.getWidth() == 0) return;
        if (widgetMode) {
            // с виджетами раскладка стоит на месте, а всё вместе чуть-чуть гуляет
            int d = dp(12);
            idleBox.animate().translationX(random.nextInt(2 * d + 1) - d).translationY(random.nextInt(2 * d + 1) - d)
                    .setDuration(4000).setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator()).start();
            return;
        }
        int margin = dp(24);
        int maxX = Math.max(0, (root.getWidth() - idle.getWidth()) / 2 - margin);
        int maxY = Math.max(0, (root.getHeight() - idle.getHeight()) / 2 - margin);
        float x = maxX == 0 ? 0 : random.nextInt(2 * maxX + 1) - maxX;
        float y = maxY == 0 ? 0 : random.nextInt(2 * maxY + 1) - maxY;
        idle.animate().translationX(x).translationY(y).setDuration(4000)
                .setInterpolator(new android.view.animation.AccelerateDecelerateInterpolator()).start();
    }

    // ---------------- виджеты ----------------

    private void applyWidgets(JSONObject w) {
        if (w == null || w.toString().equals(widgetsJson)) return;
        widgetsJson = w.toString();
        widgetLayout = w.optString("layout", "shelf");
        widgetStyle = w.optString("style", "card");
        widgetSlots = w.optJSONArray("slots");
        if (widgetSlots == null) widgetSlots = new JSONArray();
        layoutWidgets();
    }

    private int slotCount() { return "corners".equals(widgetLayout) ? 4 : 3; }

    /** Куда ставить «Включить компьютер»: на последнее место раскладки, а без виджетов — в центр полки. */
    private int wolSlot(boolean anyWidget) {
        if (!anyWidget) return 1;
        return slotCount() - 1;
    }

    /** Расставить часы и виджеты по раскладке. Без виджетов часы стоят по центру и гуляют по экрану, как раньше. */
    private void layoutWidgets() {
        int W = root.getWidth(), H = root.getHeight();
        if (W == 0 || H == 0) return;
        boolean wolShown = wolVisible();
        boolean resumeShown = !wolShown && resumeVisible();
        boolean any = false;
        weatherInWidget = false;
        int n = slotCount();
        for (int i = 0; i < n; i++) {
            JSONObject o = widgetSlots.optJSONObject(i);
            if (o != null) {
                any = true;
                if ("weather".equals(o.optString("kind"))) weatherInWidget = true;
            }
        }
        if (weatherInWidget) weatherText.setVisibility(View.GONE);
        boolean was = widgetMode;
        widgetMode = any || wolShown || resumeShown;
        String layout = any ? widgetLayout : "shelf";   // одна кнопка (Wake on LAN, «Продолжить») — под часами
        boolean corners = "corners".equals(layout);

        // часы
        FrameLayout.LayoutParams ilp = (FrameLayout.LayoutParams) idle.getLayoutParams();
        ilp.leftMargin = ilp.topMargin = 0;
        float clockSp = 130;
        int textGravity = Gravity.CENTER_HORIZONTAL;
        if (!widgetMode) {
            ilp.gravity = Gravity.CENTER;
        } else if ("column".equals(layout)) {
            ilp.gravity = Gravity.LEFT | Gravity.CENTER_VERTICAL;
            ilp.leftMargin = dp(56);
            clockSp = 150;
            textGravity = Gravity.LEFT;
        } else if (corners) {
            ilp.gravity = Gravity.CENTER;
            clockSp = 150;
        } else {
            ilp.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
            ilp.topMargin = Math.max(dp(30), (H - dp(40) - dp(140) - dp(250)) / 2);
        }
        idle.setLayoutParams(ilp);
        idle.setGravity(textGravity == Gravity.LEFT ? Gravity.LEFT : Gravity.CENTER);
        clock.setGravity(textGravity);
        date.setGravity(textGravity);
        weatherText.setGravity(textGravity);
        for (View v : new View[]{date, weatherText}) {
            LinearLayout.LayoutParams lp = (LinearLayout.LayoutParams) v.getLayoutParams();
            lp.gravity = textGravity;
            v.setLayoutParams(lp);
        }
        clock.setTextSize(TypedValue.COMPLEX_UNIT_SP, clockSp);
        if (widgetMode != was) {   // сменился режим — с чистого места
            idle.animate().cancel();
            idleBox.animate().cancel();
            idle.setTranslationX(0); idle.setTranslationY(0);
            idleBox.setTranslationX(0); idleBox.setTranslationY(0);
        }

        // места
        int m = dp(40), gap = dp(24), sw = (W - 2 * m - 2 * gap) / 3, sh = dp(140);
        int ws = wolShown || resumeShown ? wolSlot(any) : -1;
        for (int i = 0; i < slotViews.length; i++) {
            WidgetView v = slotViews[i];
            JSONObject o = i < n ? widgetSlots.optJSONObject(i) : null;
            if (o == null || i == ws) {
                v.setVisibility(View.GONE);
                continue;
            }
            v.setData(o.optString("kind"), o.optString("label"), o.optString("value"), o.optString("aside"),
                    o.optString("text"), o.optInt("offset_min"));
            if (o.has("cols")) v.setCols(o.optJSONArray("cols"));
            if (o.has("events")) v.setEvents(o.optJSONArray("events"));
            place(v, layout, i, W, H, m, gap, sw, sh);
            v.setVisibility(View.VISIBLE);
        }
        if (wolShown) {
            wolView.setWol(wolState, wolName);
            place(wolView, layout, ws, W, H, m, gap, sw, sh);
            wolView.setVisibility(View.VISIBLE);
        } else {
            wolView.setVisibility(View.GONE);
        }
        if (resumeShown) {
            resumeView.setResume(title.getText().toString(), artist.getText().toString(), coverBmp,
                    SystemClock.elapsedRealtime() < resumeUntil);
            place(resumeView, layout, ws, W, H, m, gap, sw, sh);
            resumeView.setVisibility(View.VISIBLE);
        } else {
            resumeView.setVisibility(View.GONE);
        }
        status.setVisibility(wolShown || resumeShown ? View.INVISIBLE : View.VISIBLE);
        boolean night = nightShown != null && nightShown;
        for (WidgetView w : slotViews) w.setNight(night);
        wolView.setNight(night);
        resumeView.setNight(night);
    }

    private void place(WidgetView v, String layout, int i, int W, int H, int m, int gap, int sw, int sh) {
        FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) v.getLayoutParams();
        lp.gravity = Gravity.LEFT | Gravity.TOP;
        if ("column".equals(layout)) {
            int w = Math.min(dp(296), sw);
            lp.width = w; lp.height = sh;
            lp.leftMargin = W - m - w;
            lp.topMargin = (H - 3 * sh - 2 * gap) / 2 + i * (sh + gap);
            v.setLook(widgetStyle, false, false);
        } else if ("corners".equals(layout)) {
            int w = Math.min(dp(296), sw), h = dp(96), top = dp(34);
            boolean rightSide = i % 2 == 1;
            lp.width = w; lp.height = h;
            lp.leftMargin = rightSide ? W - m - w : m;
            lp.topMargin = i < 2 ? top : H - top - h;
            v.setLook(widgetStyle, true, rightSide);
        } else {
            lp.width = sw; lp.height = sh;
            lp.leftMargin = m + i * (sw + gap);
            lp.topMargin = H - m - sh;
            v.setLook(widgetStyle, false, false);
        }
        v.setLayoutParams(lp);
    }

    // ---------------- Wake on LAN ----------------

    private void applyWol(JSONObject w) {
        if (w == null) return;   // старый сервер — помним то, что было
        boolean on = w.optBoolean("on", false);
        String mac = w.isNull("mac") ? null : w.optString("mac", null);
        String name = w.optString("name", "");
        if (mac == null && on) return;
        if (on != wolOn || (mac != null && !mac.equals(wolMac)) || !name.equals(wolName)) {
            wolOn = on;
            if (mac != null) wolMac = mac;
            wolName = name;
            prefs.edit().putBoolean("wol_on", wolOn).putString("wol_mac", wolMac).putString("wol_name", wolName).commit();
        }
    }

    private boolean wolVisible() {
        return wolOn && wolMac != null && pcLost;
    }

    /** «Продолжить» — когда ПК на связи, трек есть, но стоит на паузе. */
    private boolean resumeVisible() {
        return resumeOn && connected && !pcLost && trackId != null && !playing;
    }

    private void updateResume(long t) {
        boolean want = !wolVisible() && resumeVisible();
        boolean shown = resumeView.getVisibility() == View.VISIBLE;
        if (want != shown) layoutWidgets();
        else if (shown) resumeView.setResume(title.getText().toString(), artist.getText().toString(), coverBmp, t < resumeUntil);
    }

    /** Раз в 100 мс: показать или убрать кнопку, вовремя сменить «Включаю…» на «Не получилось». */
    private void updateWol(long t) {
        if (wolState == WidgetView.WOL_WAKING && t > wolUntil) {
            wolState = WidgetView.WOL_FAILED;
            wolUntil = t + 6000;
        } else if (wolState == WidgetView.WOL_FAILED && t > wolUntil) {
            wolState = WidgetView.WOL_READY;
        }
        boolean shown = wolView.getVisibility() == View.VISIBLE;
        if (wolVisible() != shown) layoutWidgets();
        else if (shown) wolView.setWol(wolState, wolName);
    }

    private void wakePc() {
        final String mac = wolMac;
        if (mac == null) return;
        wolState = WidgetView.WOL_WAKING;
        wolUntil = SystemClock.elapsedRealtime() + 60000;
        wolView.setWol(wolState, wolName);
        pool.execute(new Runnable() {
            public void run() {
                try { sendMagicPacket(mac); } catch (Exception e) { android.util.Log.w("NowPlaying", "wol: " + e); }
            }
        });
    }

    /** «Волшебный пакет»: 6 байт 0xFF и 16 раз MAC — широковещательно на порты 9 и 7, три раза. */
    private void sendMagicPacket(String mac) throws Exception {
        String[] hex = mac.split("[:-]");
        byte[] m = new byte[6];
        for (int i = 0; i < 6; i++) m[i] = (byte) Integer.parseInt(hex[i], 16);
        byte[] pkt = new byte[6 + 16 * 6];
        for (int i = 0; i < 6; i++) pkt[i] = (byte) 0xff;
        for (int i = 0; i < 16; i++) System.arraycopy(m, 0, pkt, 6 + i * 6, 6);
        List<InetAddress> targets = new ArrayList<InetAddress>();
        targets.add(InetAddress.getByName("255.255.255.255"));
        WifiManager wm = (WifiManager) getSystemService(WIFI_SERVICE);
        DhcpInfo dhcp = wm != null ? wm.getDhcpInfo() : null;
        if (dhcp != null && dhcp.ipAddress != 0) {
            int bc = (dhcp.ipAddress & dhcp.netmask) | ~dhcp.netmask;
            byte[] q = new byte[4];
            for (int k = 0; k < 4; k++) q[k] = (byte) ((bc >> (k * 8)) & 0xff);
            targets.add(InetAddress.getByAddress(q));
        }
        DatagramSocket sock = new DatagramSocket();
        try {
            sock.setBroadcast(true);
            for (int round = 0; round < 3; round++) {
                for (InetAddress a : targets) {
                    sock.send(new DatagramPacket(pkt, pkt.length, a, 9));
                    sock.send(new DatagramPacket(pkt, pkt.length, a, 7));
                }
                Thread.sleep(300);
            }
        } finally {
            sock.close();
        }
    }

    private void askServer() {
        final EditText e = new EditText(this);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        e.setHint("192.168.1.10:47801");
        if (server != null) e.setText(server);
        new AlertDialog.Builder(this)
                .setTitle("Адрес компьютера")
                .setMessage("Оставьте пустым для автопоиска")
                .setView(e)
                .setPositiveButton("OK", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        String s = e.getText().toString().trim();
                        if (s.length() > 0 && !s.contains(":")) s += ":47801";
                        server = s.length() > 0 ? s : null;
                        prefs.edit().putString("server", server).commit();
                    }
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    // ---------------- сеть ----------------

    private void networkLoop(int gen) {
        int failures = 0;
        while (running && gen == generation) {
            String s = server;
            if (s == null) {
                setStatus("Ищу компьютер…  (долгое нажатие — ввести адрес вручную)");
                s = discover();
                if (s == null) { sleep(3000); continue; }
                server = s;
                prefs.edit().putString("server", s).commit();
            }
            try {
                final JSONObject now = new JSONObject(new String(get("http://" + s + "/api/now?v=" + myVersion()
                        + "&dev=" + deviceId() + "&name=" + java.net.URLEncoder.encode(android.os.Build.MODEL, "UTF-8")
                        + (isTv() ? "&tv=1" : ""), 3000), "UTF-8"));
                failures = 0;
                ui.post(new Runnable() { public void run() { apply(now); } });
                setStatus("");
            } catch (Exception e) {
                failures++;
                setStatus("Нет связи с компьютером " + s);
                if (failures >= 5) {
                    connected = false;
                    pcLost = true;
                    if (failures % 5 == 0) {          // попробуем найти ПК заново (мог смениться IP)
                        String found = discover();
                        if (found != null) server = found;
                    }
                }
            }
            sleep(1000);
        }
    }

    private void setStatus(final String text) {
        ui.post(new Runnable() { public void run() { status.setText(text); } });
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) { }
    }

    private static byte[] get(String url, int timeout) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(timeout);
        c.setReadTimeout(timeout * 4);
        try {
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
            InputStream in = c.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    /** UDP-широковещательный поиск ПК в сети. */
    private String discover() {
        DatagramSocket sock = null;
        try {
            sock = new DatagramSocket();
            sock.setBroadcast(true);
            sock.setSoTimeout(1500);
            byte[] msg = "NOWPLAYING_DISCOVER".getBytes("UTF-8");
            List<InetAddress> targets = new ArrayList<InetAddress>();
            targets.add(InetAddress.getByName("255.255.255.255"));
            WifiManager wm = (WifiManager) getSystemService(WIFI_SERVICE);
            DhcpInfo dhcp = wm != null ? wm.getDhcpInfo() : null;
            if (dhcp != null && dhcp.ipAddress != 0) {
                int bc = (dhcp.ipAddress & dhcp.netmask) | ~dhcp.netmask;
                byte[] q = new byte[4];
                for (int k = 0; k < 4; k++) q[k] = (byte) ((bc >> (k * 8)) & 0xff);
                targets.add(InetAddress.getByAddress(q));
            }
            for (InetAddress a : targets) sock.send(new DatagramPacket(msg, msg.length, a, UDP_PORT));
            byte[] buf = new byte[256];
            DatagramPacket p = new DatagramPacket(buf, buf.length);
            sock.receive(p);
            String[] parts = new String(p.getData(), 0, p.getLength(), "UTF-8").trim().split(" ");
            if (parts.length >= 2 && parts[0].equals("NOWPLAYING_HERE")) {
                return p.getAddress().getHostAddress() + ":" + parts[1];
            }
        } catch (Exception ignored) {
        } finally {
            if (sock != null) sock.close();
        }
        // широковещание не дошло (бывает, когда на ТВ включён VPN или роутер режет broadcast) —
        // раз в 20 с спрашиваем по очереди все адреса своей сети
        long t = SystemClock.elapsedRealtime();
        if (t - lastScan < 20000) return null;
        lastScan = t;
        return scanSubnet();
    }

    /** Свой адрес в домашней сети (Wi-Fi или кабель), без VPN-туннелей. */
    private static byte[] lanAddress() {
        try {
            for (java.net.NetworkInterface ni : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                String n = ni.getName();
                if (!ni.isUp() || ni.isLoopback() || n.startsWith("tun") || n.startsWith("ppp") || n.startsWith("tap")) continue;
                for (InetAddress a : java.util.Collections.list(ni.getInetAddresses())) {
                    if (a instanceof java.net.Inet4Address && a.isSiteLocalAddress()) return a.getAddress();
                }
            }
        } catch (Exception ignored) { }
        return null;
    }

    /** Перебор x.x.x.1–254 на порту 47801: кто ответит «ok» на /health, тот и наш ПК. */
    private String scanSubnet() {
        byte[] me = lanAddress();
        if (me == null) return null;
        final String base = (me[0] & 0xff) + "." + (me[1] & 0xff) + "." + (me[2] & 0xff) + ".";
        final int self = me[3] & 0xff;
        final java.util.concurrent.atomic.AtomicReference<String> found = new java.util.concurrent.atomic.AtomicReference<String>();
        java.util.concurrent.ExecutorService ex = java.util.concurrent.Executors.newFixedThreadPool(32);
        final java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(254);
        for (int i = 1; i <= 254; i++) {
            final int h = i;
            ex.execute(new Runnable() {
                public void run() {
                    try {
                        if (h != self && found.get() == null) {
                            String addr = base + h + ":47801";
                            String r = new String(get("http://" + addr + "/health", 600), "UTF-8").trim();
                            if (r.equals("ok")) found.compareAndSet(null, addr);
                        }
                    } catch (Exception ignored) {
                    } finally {
                        done.countDown();
                    }
                }
            });
        }
        try {
            while (found.get() == null && !done.await(200, java.util.concurrent.TimeUnit.MILLISECONDS)) { }
        } catch (InterruptedException ignored) { }
        ex.shutdownNow();
        return found.get();
    }

    private void apply(JSONObject j) {
        connected = true;
        long serverNow = j.optLong("server_time_ms", 0);
        if (serverNow > 0) WidgetView.skewMs = serverNow - System.currentTimeMillis();
        JSONObject bg = j.optJSONObject("bg");
        String nbg = bg == null ? null : bg.optString("id", null);
        if (nbg == null ? bgId != null : !nbg.equals(bgId)) {
            bgId = nbg;
            if (nbg == null) clearBg();
            else loadBg(server, nbg, bg.optString("ext", "jpg"), bg.optString("type", "image"));
        }
        if (!volumeDrag && !j.isNull("volume")) volume = j.optInt("volume", -1);
        applyUi(j.optJSONObject("ui"));
        if (pcLost) {   // ПК снова ответил — в том числе после «Включить компьютер»
            pcLost = false;
            wolState = WidgetView.WOL_READY;
        }
        applyWol(j.optJSONObject("wol"));
        applyWidgets(j.optJSONObject("widgets"));
        showWeather(j.optJSONObject("weather"));
        // за треком: своё фото/видео или видео клипа из Яндекс Музыки
        String nclip = null, path = null, type = "video";
        JSONObject tm = j.optJSONObject("track_media");
        if (tm != null) {   // своё фото/видео или размытая обложка — сервер присылает только то, что выбрано
            nclip = "t:" + tm.optString("id");
            path = tm.has("url") ? tm.optString("url") : "/bg-track/" + tm.optString("id") + "." + tm.optString("ext", "mp4");
            type = tm.optString("type", "video");
        } else if (!j.isNull("clip") && j.optString("clip", "").length() > 0) {
            // телевизору сервер отдаёт клип в полном качестве, планшету — лёгкий и размытый
            boolean hd = j.optBoolean("clip_hd");
            nclip = (hd ? "h:" : "c:") + j.optString("clip");
            path = "/clip/" + j.optString("clip") + (hd ? "-hd" : "") + ".mp4";
        }
        if (nclip == null ? clipId != null : !nclip.equals(clipId)) {
            clipId = nclip;
            if (nclip == null) {
                clearClip();
                for (java.io.File f : getCacheDir().listFiles()) {   // видео больше не нужно
                    if (f.getName().startsWith("clip_")) f.delete();
                }
            } else {
                clearClip();   // старое видео не показываем под новым треком
                loadClip(server, nclip, path, type);
            }
        }
        String apk = j.isNull("apk_version") ? null : j.optString("apk_version", null);
        if (apk != null && !apk.equals(updateTried) && !j.optBoolean("playing")
                && compareVersions(apk, myVersion()) > 0) {
            updateTried = apk;   // ставим, только пока музыка не играет
            installUpdate(server, apk);
        }
        String id = j.isNull("track_id") ? null : j.optString("track_id", null);
        boolean wasPlaying = playing;
        playing = j.optBoolean("playing");
        // сразу после перемотки сервер может ещё отдать старую позицию
        if (SystemClock.elapsedRealtime() > seekHoldUntil) position = j.optLong("position_ms");
        duration = j.optLong("duration_ms");
        receivedAt = SystemClock.elapsedRealtime();
        if (!playing && (wasPlaying || pausedSince == 0)) pausedSince = receivedAt;
        if (playing) pausedSince = receivedAt;

        if (id != null && !id.equals(trackId)) {
            trackId = id;
            title.setText(j.optString("title"));
            String a = j.optString("artist");
            artist.setText(a);
            artist.setVisibility(a.length() > 0 ? View.VISIBLE : View.GONE);
            animateBackground(j);
            lyrics.setLyrics(null, new ArrayList<String>(), false);
            lyrics.setVisibility(View.GONE);
            unsynced.setVisibility(View.GONE);
            growLyrics(false, false);
            loadTrackExtras(server, id);
            setCover(null);
            artRev = -1;
        } else if (id == null) {
            trackId = null;
        } else {
            // цвет приходит чуть позже обложки — обновим, если поменялся
            animateBackground(j);
        }
        int rev = j.optInt("art_rev", 0);
        if (id != null && j.optBoolean("has_art") && rev != artRev) {
            artRev = rev;
            loadCover(server, id);  // сначала маленькая обложка из Windows, потом чёткая из iTunes
        }
    }

    private String colorKey = "0,0,0,0";

    /** Поправка цвета под экран устройства (из окна настроек на ПК): насыщенность, контраст,
     *  яркость и тепло. Весь экран приложения рисуется в слой видеокарты, а слой выводится
     *  через цветовой фильтр. Без поправки слоя нет, лишней работы тоже. Видео (SurfaceView)
     *  идёт мимо приложения и не меняется. */
    private void applyColor(int sat, int contrast, int bright, int warm) {
        String key = sat + "," + contrast + "," + bright + "," + warm;
        if (key.equals(colorKey)) return;
        colorKey = key;
        Paint p = null;
        if (sat != 0 || contrast != 0 || bright != 0 || warm != 0) {
            p = colorPaint(sat, contrast, bright, warm);
        }
        // Слой с фильтром — у каждой части экрана, но не у слоёв с видео: SurfaceView показывает
        // видео через «окно» в экране, а слой с фильтром это окно закрашивает (клипы на планшете
        // пропадали, 2.4–2.10). Картинки внутри видео-слоёв (своё фото, размытая обложка) — отдельно.
        root.setLayerType(View.LAYER_TYPE_NONE, null);
        for (int i = 0; i < root.getChildCount(); i++) {
            View ch = root.getChildAt(i);
            if (ch == clipLayer || ch == bgLayer) continue;
            boolean alphaLayer = ch == glow || java.util.Arrays.asList(spots).contains(ch);   // им слой нужен и без фильтра
            ch.setLayerType(p != null || alphaLayer ? View.LAYER_TYPE_HARDWARE : View.LAYER_TYPE_NONE, p);
        }
        clipImage.setLayerType(p != null ? View.LAYER_TYPE_HARDWARE : View.LAYER_TYPE_NONE, p);
        bgImage.setLayerType(p != null ? View.LAYER_TYPE_HARDWARE : View.LAYER_TYPE_NONE, p);
    }

    private static Paint colorPaint(int sat, int contrast, int bright, int warm) {
        ColorMatrix m = new ColorMatrix();
        m.setSaturation(1f + sat / 100f);
        float c = 1f + contrast / 100f, t = 127.5f * (1f - c) + bright * 2.55f;
        float w = warm / 200f;
        m.postConcat(new ColorMatrix(new float[]{
                c * (1 + w), 0, 0, 0, t,
                0, c, 0, 0, t,
                0, 0, c * (1 - w), 0, t,
                0, 0, 0, 1, 0}));
        Paint p = new Paint();
        p.setColorFilter(new ColorMatrixColorFilter(m));
        return p;
    }

    private Boolean tv;

    /** Телевизор (Android TV): ему клипы в полном качестве. */
    private boolean isTv() {
        if (tv == null) {
            boolean t = false;
            try {
                android.app.UiModeManager um = (android.app.UiModeManager) getSystemService(UI_MODE_SERVICE);
                t = um != null && um.getCurrentModeType() == android.content.res.Configuration.UI_MODE_TYPE_TELEVISION;
                t = t || getPackageManager().hasSystemFeature("android.software.leanback");
            } catch (Exception ignored) {
            }
            tv = t;
        }
        return tv;
    }

    private void loadCover(final String srv, final String id) {
        pool.execute(new Runnable() {
            public void run() {
                try {
                    byte[] img = get("http://" + srv + "/art/" + id + ".jpg?r=" + artRev, 3000);
                    final Bitmap bmp = BitmapFactory.decodeByteArray(img, 0, img.length);
                    ui.post(new Runnable() { public void run() { if (id.equals(trackId) && bmp != null) setCover(bmp); } });
                } catch (Exception ignored) {
                    artRev = -1;  // попробуем ещё раз при следующем опросе
                }
            }
        });
    }

    private void loadTrackExtras(final String srv, final String id) {
        pool.execute(new Runnable() {
            public void run() {
                // текст песни
                for (int i = 0; i < 5 && id.equals(trackId); i++) {
                    try {
                        JSONObject l = new JSONObject(new String(get("http://" + srv + "/api/lyrics/" + id, 15000), "UTF-8"));
                        if (l.optBoolean("pending")) { sleep(2000); continue; }
                        final List<Long> t = new ArrayList<Long>();
                        final List<String> lines = new ArrayList<String>();
                        JSONArray synced = l.optJSONArray("synced");
                        boolean isSynced = synced != null && synced.length() > 0;
                        if (isSynced) {
                            for (int k = 0; k < synced.length(); k++) {
                                JSONObject o = synced.getJSONObject(k);
                                t.add(o.getLong("t"));
                                lines.add(o.optString("text"));
                            }
                        } else {
                            for (String s : l.optString("plain").split("\n")) lines.add(s.trim());
                            if (lines.size() == 1 && lines.get(0).length() == 0) lines.clear();
                        }
                        final boolean fs = isSynced;
                        ui.post(new Runnable() {
                            public void run() {
                                if (!id.equals(trackId)) return;
                                lyrics.setLyrics(fs ? t : null, lines, fs);
                                lyrics.setVisibility(lines.isEmpty() ? View.GONE : View.VISIBLE);
                                unsynced.setVisibility(!fs && !lines.isEmpty() ? View.VISIBLE : View.GONE);
                                growLyrics(!lines.isEmpty(), true);
                            }
                        });
                        break;
                    } catch (Exception e) {
                        sleep(2000);
                    }
                }
            }
        });
    }

    /** Тонкая полоска прогресса. */
    static class ProgressLine extends View {
        private final Paint back = new Paint(Paint.ANTI_ALIAS_FLAG), front = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float fraction;

        ProgressLine(android.content.Context c) {
            super(c);
            back.setColor(0x40ffffff);
            front.setColor(0xe6ffffff);
        }

        private boolean active;

        void setFraction(float f) {
            if (Math.abs(f - fraction) > 0.0005f) { fraction = f; invalidate(); }
        }

        /** Пока полоску тянут пальцем, рисуем на ней кружок. */
        void setActive(boolean a) {
            if (a != active) { active = a; invalidate(); }
        }

        @Override
        protected void onDraw(Canvas c) {
            float r = getHeight() / 2f;
            float x = getWidth() * Math.max(0, Math.min(1, fraction));
            c.drawRoundRect(new android.graphics.RectF(0, 0, getWidth(), getHeight()), r, r, back);
            c.drawRoundRect(new android.graphics.RectF(0, 0, x, getHeight()), r, r, front);
            if (active) c.drawCircle(Math.max(r * 3, Math.min(getWidth() - r * 3, x)), r, r * 3, front);
        }
    }
}

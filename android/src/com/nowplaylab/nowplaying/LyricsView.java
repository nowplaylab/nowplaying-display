package com.nowplaylab.nowplaying;

import android.content.Context;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Bitmap;
import android.graphics.BlurMaskFilter;
import android.graphics.BitmapShader;
import android.graphics.ComposeShader;
import android.graphics.PorterDuff;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.net.Uri;
import android.text.Layout;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.StaticLayout;
import android.text.TextPaint;
import android.text.style.CharacterStyle;
import android.text.style.ForegroundColorSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.URLSpan;
import android.view.View;
import android.util.LruCache;

import java.util.ArrayList;
import java.util.List;
import java.util.HashMap;

/** Синхронный текст: текущая строка яркая и на 40% высоты, остальные приглушены,
 *  прокрутка плавная, края размываются и мягко затухают.
 *  Текст можно листать пальцем (жесты ловит MainActivity); через 3 с без касаний
 *  он сам возвращается к текущей строке. */
public class LyricsView extends View {
    private final List<Long> times = new ArrayList<Long>();
    private final List<String> texts = new ArrayList<String>();
    private final List<StaticLayout> layouts = new ArrayList<StaticLayout>();
    // Только неподвижные белые буквы. Анимация активных слов остаётся нативной.
    private final LruCache<Integer, Bitmap> blurredLines = new LruCache<Integer, Bitmap>(1024 * 1024) {
        @Override protected int sizeOf(Integer key, Bitmap value) { return value.getByteCount(); }
    };
    private final Paint blurredPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final RectF blurredBounds = new RectF();
    private final List<List<Word>> words = new ArrayList<List<Word>>();
    private final List<Integer> leadLengths = new ArrayList<Integer>();
    private boolean drawingLead;
    private long positionMs, positionAt;
    private boolean playing;
    private boolean wordSynced;
    private boolean drawingTimed;
    private int drawingAlpha;
    private long drawingPosition;
    private final List<List<Glow>> lineGlows = new ArrayList<List<Glow>>();
    private final LruCache<Word, GlowRaster> glowMasks = new LruCache<Word, GlowRaster>(512 * 1024) {
        @Override protected int sizeOf(Word key, GlowRaster value) { return value.bitmap.getByteCount(); }
    };
    private final Paint glowPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final RectF glowDestination = new RectF();
    private final Matrix glowMatrix = new Matrix();

    private static class GlowRaster {
        Bitmap bitmap;
        BitmapShader bitmapShader;
        ComposeShader progressShader;
    }

    private static class Glow {
        long start = Long.MAX_VALUE, end;
        final List<Word> segments = new ArrayList<Word>();

        float durationEmphasis() {
            return Math.max(0f, Math.min(1f, (end - start - 350f) / 1150f));
        }
    }

    /** Один слог/слово. Индексы UTF-16, как у Android Layout. */
    public static class Word {
        public long start, end;
        public int from, to;
        boolean backing;
        final List<RectF> boxes = new ArrayList<RectF>();
        final List<Boolean> rtl = new ArrayList<Boolean>();
        LinearGradient gradient;
        LinearGradient glowGradient;
        final RectF glowBounds = new RectF();
        final Matrix gradientMatrix = new Matrix();
        public Word(long s, long e, int f, int t) { start = s; end = e; from = f; to = t; }
    }

    /** Нативный стиль текста: без второй копии букв и прямоугольных обрезок. */
    private class TimedWordStyle extends CharacterStyle {
        private final Word word;
        TimedWordStyle(Word w) { word = w; }

        @Override public void updateDrawState(TextPaint p) {
            p.setShader(null);
            p.clearShadowLayer();
            p.baselineShift = 0;
            if (!drawingTimed || (!drawingLead && !word.backing)) return;
            float fraction = drawingPosition < word.start ? 0 : word.end <= word.start ? 1
                    : Math.min(1f, (float) (drawingPosition - word.start) / (word.end - word.start));
            p.setColor(0xffffffff);
            int alpha = word.backing ? Math.round(drawingAlpha * 0.8f) : drawingAlpha;
            p.setAlpha(alpha);
            if (fraction <= 0) {
                p.setAlpha((int) (alpha * 0.35f));
            } else if (fraction < 1f) {
                if (word.gradient != null && word.boxes.size() == 1) {
                    RectF box = word.boxes.get(0);
                    float x = word.rtl.get(0) ? box.right - box.width() * fraction
                            : box.left + box.width() * fraction;
                    word.gradientMatrix.setTranslate(x - sp(2), 0);
                    word.gradient.setLocalMatrix(word.gradientMatrix);
                    p.setShader(word.gradient);
                } else {
                    // Переносы и смешанные направления не режем по прямоугольникам.
                    p.setAlpha((int) (alpha * (0.35f + 0.65f * fraction)));
                }
                float pulse = (float) Math.sin(Math.PI * fraction);
                p.baselineShift = -Math.round(sp(1) * pulse);
                // Общее свечение слова рисуется отдельной мягкой маской позади букв.
            }
        }
    }

    public void setWords(List<List<Word>> value, List<Integer> lengths) {
        words.clear();
        leadLengths.clear();
        leadLengths.addAll(lengths);
        if (value != null) words.addAll(value);
        wordSynced = false;
        for (List<Word> line : words) if (!line.isEmpty()) { wordSynced = true; break; }
        builtWidth = -1;
        invalidate();
    }
    private final TextPaint paint = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    private final TextPaint creditPaint = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    private CharSequence attribution = "";
    private StaticLayout creditLayout;
    private float creditTop;

    public void setAttribution(CharSequence value) {
        attribution = value == null ? "" : value;
        builtWidth = -1;
        invalidate();
    }

    /** Подписи источника доступны в конце текста, ссылки открываются только по тапу. */
    public boolean openAttributionAtRaw(float rawX, float rawY) {
        if (creditLayout == null || Float.isNaN(offset)) return false;
        int[] location = new int[2];
        getLocationOnScreen(location);
        float x = rawX - location[0], y = rawY - location[1] + offset - creditTop;
        if (x < 0 || x > getWidth() || y < 0 || y > creditLayout.getHeight()) return false;
        if (attribution instanceof Spanned) {
            int row = creditLayout.getLineForVertical((int) y);
            int at = creditLayout.getOffsetForHorizontal(row, x);
            URLSpan[] links = ((Spanned) attribution).getSpans(at, at, URLSpan.class);
            if (links.length > 0 && links[0].getURL().startsWith("https://spicylyrics.org/")) {
                try {
                    getContext().startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(links[0].getURL()))
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                } catch (android.content.ActivityNotFoundException ignored) { }
            }
        }
        return true;
    }
    private float[] tops = new float[0];
    private float totalHeight;
    private boolean synced;
    private int current = -1;
    private int leaving = -1;
    private long lineChangedAt;
    private float offset;
    private int builtWidth = -1;
    private float plainProgress;
    private long lastFrame;
    private static final long BROWSE_MS = 3000;
    private long browseUntil;   // до этого момента текст листает пользователь
    private float flingVelocity; // px/мс, затухает

    public LyricsView(Context c) {
        super(c);
        paint.setColor(0xffffffff);
        paint.setTypeface(Typeface.create("sans-serif", Typeface.BOLD));
        paint.setTextSize(sp(26));
        creditPaint.setColor(0x99ffffff);
        creditPaint.linkColor = 0x99ffffff;
        creditPaint.setTextSize(sp(12));
        // Без штатного fading edge: он добавлял промежуточный слой на старом GPU.
    }

    private float sp(float v) {
        return v * getResources().getDisplayMetrics().scaledDensity;
    }

    private boolean centered;

    /** Строки по центру (тема «Сцена») или по левому краю. */
    public void setCentered(boolean c) {
        if (c == centered) return;
        centered = c;
        builtWidth = -1;
        invalidate();
    }

    public void setTextSizeSp(float size) {
        paint.setTextSize(sp(size));
        builtWidth = -1;
        invalidate();
    }

    public void setTypeface(Typeface tf) {
        paint.setTypeface(tf);
        builtWidth = -1;
        invalidate();
    }

    /** synced: times != null; иначе обычный текст без таймингов. */
    public void setLyrics(List<Long> t, List<String> lines, boolean isSynced) {
        times.clear();
        texts.clear();
        words.clear();
        leadLengths.clear();
        wordSynced = false;
        attribution = "";
        creditLayout = null;
        if (t != null) times.addAll(t);
        texts.addAll(lines);
        synced = isSynced;
        current = -1;
        leaving = -1;
        lineChangedAt = 0;
        builtWidth = -1;
        browseUntil = 0;
        flingVelocity = 0;
        offset = Float.NaN; // первый кадр — сразу на место, без пролистывания
        invalidate();
    }

    public boolean hasLyrics() {
        return !texts.isEmpty();
    }

    public boolean isSynced() {
        return synced;
    }

    /** Листает ли пользователь текст прямо сейчас (тогда тап по строке — перемотка). */
    public boolean isBrowsing() {
        return SystemClock.uptimeMillis() < browseUntil;
    }

    /** Сдвиг пальцем на dy пикселей (вверх — положительный). */
    public void scrollByUser(float dy) {
        if (texts.isEmpty() || Float.isNaN(offset)) return;
        flingVelocity = 0;
        offset = clampOffset(offset + dy);
        browseUntil = SystemClock.uptimeMillis() + BROWSE_MS;
        lastFrame = 0;
        invalidate();
    }

    /** Бросок пальцем; vy — скорость пальца в px/с, как у GestureDetector. */
    public void flingByUser(float vy) {
        if (texts.isEmpty() || Float.isNaN(offset)) return;
        flingVelocity = -vy / 1000f;
        browseUntil = SystemClock.uptimeMillis() + BROWSE_MS;
        lastFrame = 0;
        invalidate();
    }

    /** Палец коснулся экрана — останавливаем бросок. */
    public void stopFling() {
        flingVelocity = 0;
    }

    /** Конец листания: плавно вернуться к текущей строке. */
    public void endBrowsing() {
        browseUntil = 0;
        flingVelocity = 0;
        invalidate();
    }

    /** Время начала строки под точкой y (в координатах вида), или -1. */
    public long timeAt(float y) {
        if (!synced || layouts.isEmpty() || Float.isNaN(offset)) return -1;
        float gap = sp(14);
        for (int i = 0; i < layouts.size() && i < times.size(); i++) {
            float top = tops[i] - offset;
            if (y >= top - gap / 2 && y < top + layouts.get(i).getHeight() + gap / 2) return times.get(i);
        }
        return -1;
    }

    private float clampOffset(float o) {
        int h = getHeight();
        float min = -h * 0.4f, max = Math.max(min, totalHeight - h * 0.6f);
        return Math.max(min, Math.min(max, o));
    }

    /** Позиция трека в мс. */
    public void setPosition(long posMs, long durMs) {
        setPosition(posMs, durMs, false);
    }

    public void setPosition(long posMs, long durMs, boolean isPlaying) {
        boolean timed = current >= 0 && current < words.size() && !words.get(current).isEmpty();
        boolean changed = positionMs != posMs || playing != isPlaying;
        positionMs = posMs;
        positionAt = SystemClock.uptimeMillis();
        playing = isPlaying;
        if (texts.isEmpty()) return;
        if (synced) {
            // двоичный поиск последней строки, которая уже началась (чуть заранее, как в Spotify)
            long p = posMs + (wordSynced ? 0 : 250);
            int lo = 0, hi = times.size() - 1, idx = -1;
            while (lo <= hi) {
                int mid = (lo + hi) >>> 1;
                if (times.get(mid) <= p) { idx = mid; lo = mid + 1; } else hi = mid - 1;
            }
            if (idx != current) {
                leaving = current;
                current = idx;
                lineChangedAt = SystemClock.uptimeMillis();
                invalidate();
            }
        } else if (durMs > 0) {
            float f = Math.max(0, Math.min(1, (float) posMs / durMs));
            if (Math.abs(f - plainProgress) > 0.0005f) {
                plainProgress = f;
                invalidate();
            }
        }
        if (changed && timed) invalidate();
    }

    private void build(int width) {
        layouts.clear();
        blurredLines.evictAll();
        glowMasks.evictAll();
        lineGlows.clear();
        float gap = sp(14);
        tops = new float[texts.size()];
        float y = 0;
        for (int i = 0; i < texts.size(); i++) {
            String s = texts.get(i);
            CharSequence content = s.length() == 0 ? "♪" : s;
            int leadEnd = i < leadLengths.size() ? leadLengths.get(i) : s.length();
            if (i < words.size() && (!words.get(i).isEmpty() || leadEnd < s.length())) {
                SpannableString styled = new SpannableString(content);
                styleBacking(styled, i);
                for (Word word : words.get(i)) {
                    word.backing = word.from > leadEnd;
                    if (word.from >= 0 && word.to <= s.length() && word.from < word.to)
                        styled.setSpan(new TimedWordStyle(word), word.from, word.to, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                }
                content = styled;
            }
            StaticLayout l = new StaticLayout(content, paint, width,
                    centered ? Layout.Alignment.ALIGN_CENTER : Layout.Alignment.ALIGN_NORMAL, 1.05f, 0, false);
            layouts.add(l);
            if (i < words.size()) {
                for (Word word : words.get(i)) {
                    word.boxes.clear(); word.rtl.clear();
                    word.gradient = null;
                    word.glowGradient = null;
                    if (word.from < 0 || word.to > s.length() || word.from >= word.to) continue;
                    // Маленькие прямоугольники на символы учитывают переносы и смешанный RTL/LTR.
                    for (int at = word.from; at < word.to;) {
                        int next = at + Character.charCount(s.codePointAt(at));
                        int row = l.getLineForOffset(at);
                        boolean rtl = l.isRtlCharAt(at);
                        float x = l.getPrimaryHorizontal(at);
                        float endX = l.getPrimaryHorizontal(next);
                        float widthOfChar = (next < s.length() && l.getLineForOffset(next) == row
                                && l.isRtlCharAt(next) == rtl) ? Math.abs(endX - x) : paint.measureText(s, at, next);
                        RectF box = new RectF(rtl ? x - widthOfChar : x, l.getLineTop(row),
                                rtl ? x : x + widthOfChar, l.getLineBottom(row));
                        int last = word.boxes.size() - 1;
                        RectF previous = last >= 0 ? word.boxes.get(last) : null;
                        if (previous != null && previous.top == box.top && word.rtl.get(last) == rtl
                                && Math.abs(rtl ? previous.left - box.right : previous.right - box.left) < 1f) {
                            previous.union(box);
                        } else {
                            word.boxes.add(box);
                            word.rtl.add(rtl);
                        }
                        at = next;
                    }
                    if (word.boxes.size() == 1) {
                        int bright = 0xffffffff, dim = 0x59ffffff;
                        word.gradient = new LinearGradient(0, 0, sp(4), 0,
                                word.rtl.get(0) ? dim : bright, word.rtl.get(0) ? bright : dim,
                                Shader.TileMode.CLAMP);
                        word.glowGradient = new LinearGradient(0, 0, sp(4), 0,
                                word.rtl.get(0) ? 0x00ffffff : 0xffffffff,
                                word.rtl.get(0) ? 0xffffffff : 0x00ffffff, Shader.TileMode.CLAMP);
                    }
                    Path glowSelection = new Path();
                    l.getSelectionPath(word.from, word.to, glowSelection);
                    glowSelection.computeBounds(word.glowBounds, true);
                    word.glowBounds.inset(-sp(10), -sp(10));
                    word.glowBounds.left = (float) Math.floor(word.glowBounds.left);
                    word.glowBounds.top = (float) Math.floor(word.glowBounds.top);
                }
            }
            List<Glow> glows = new ArrayList<Glow>();
            HashMap<Integer, Glow> byStart = new HashMap<Integer, Glow>();
            if (i < words.size()) for (Word word : words.get(i)) {
                if (word.from < 0 || word.to > s.length() || word.from >= word.to) continue;
                int from = word.from, to = word.to;
                while (from < to && Character.isWhitespace(s.charAt(from))) from++;
                while (to > from && Character.isWhitespace(s.charAt(to - 1))) to--;
                if (from == to) continue;
                while (from > 0 && !Character.isWhitespace(s.charAt(from - 1))) from--;
                while (to < s.length() && !Character.isWhitespace(s.charAt(to))) to++;
                Glow glow = byStart.get(from);
                if (glow == null) {
                    glow = new Glow();
                    byStart.put(from, glow); glows.add(glow);
                }
                glow.start = Math.min(glow.start, word.start);
                glow.end = Math.max(glow.end, word.end);
                glow.segments.add(word);
            }
            lineGlows.add(glows);
            tops[i] = y;
            y += l.getHeight() + gap;
        }
        creditLayout = attribution.length() == 0 ? null : new StaticLayout(attribution, creditPaint, width,
                centered ? Layout.Alignment.ALIGN_CENTER : Layout.Alignment.ALIGN_NORMAL, 1.15f, 0, false);
        creditTop = y + sp(12);
        totalHeight = creditLayout == null ? y : creditTop + creditLayout.getHeight() + sp(14);
        builtWidth = width;
    }

    private Bitmap blurredLine(int index, int width) {
        Bitmap cached = blurredLines.get(index);
        if (cached != null) return cached;
        int pad = Math.round(sp(10));
        StaticLayout original = layouts.get(index);
        // Явный белый RGBA: ALPHA_8 по-разному окрашивается на старом HWUI.
        Bitmap bitmap = Bitmap.createBitmap((width + pad * 2 + 1) / 2,
                (original.getHeight() + pad * 2 + 1) / 2, Bitmap.Config.ARGB_8888);
        Canvas raster = new Canvas(bitmap);
        raster.scale(0.5f, 0.5f);
        raster.translate(pad, pad);
        // Растеризуем ту же раскладку, чтобы сохранить переносы и позиции букв.
        int alpha = paint.getAlpha();
        paint.setAlpha(255);
        paint.setMaskFilter(new BlurMaskFilter(sp(1.7f), BlurMaskFilter.Blur.NORMAL));
        try {
            original.draw(raster);
        } finally {
            paint.setMaskFilter(null);
            paint.setAlpha(alpha);
        }
        blurredLines.put(index, bitmap);
        return bitmap;
    }

    private void drawEdgeRows(Canvas canvas, StaticLayout layout, int index, int width,
                              int height, float y, int alpha) {
        float zone = Math.min(sp(44), height * 0.16f);
        for (int row = 0; row < layout.getLineCount(); row++) {
            int top = layout.getLineTop(row);
            int bottom = layout.getLineBottom(row);
            if (y + bottom <= 0 || y + top >= height) continue;
            float center = y + (top + bottom) / 2f;
            float distance = Math.min(center, height - center);
            if (distance <= 0) continue;
            boolean soft = distance < zone;
            int rowAlpha = soft ? Math.round(alpha * Math.min(1f, distance / (zone * 0.8f))) : alpha;
            canvas.save();
            if (layout.getLineCount() > 1) canvas.clipRect(0, top, width, bottom);
            if (soft) {
                Bitmap bitmap = blurredLine(index, width);
                float pad = Math.round(sp(10));
                blurredBounds.set(-pad, -pad, bitmap.getWidth() * 2f - pad, bitmap.getHeight() * 2f - pad);
                blurredPaint.setColor(0xffffffff);
                blurredPaint.setAlpha(rowAlpha);
                canvas.drawBitmap(bitmap, null, blurredBounds, blurredPaint);
            } else {
                paint.setAlpha(rowAlpha);
                layout.draw(canvas);
            }
            canvas.restore();
        }
    }

    private float targetOffset(int h) {
        if (synced) {
            if (current < 0) return -h * 0.4f;
            StaticLayout layout = layouts.get(current);
            int leadEnd = current < leadLengths.size() ? leadLengths.get(current) : 0;
            int height = leadEnd > 0 && leadEnd < texts.get(current).length()
                    ? layout.getLineBottom(layout.getLineForOffset(leadEnd - 1)) : layout.getHeight();
            return tops[current] + height / 2f - h * 0.4f;
        }
        return plainProgress * Math.max(0, totalHeight - h * 0.6f) - h * 0.1f;
    }

    private void styleBacking(SpannableString text, int line) {
        if (line < leadLengths.size() && leadLengths.get(line) + 1 < text.length())
            text.setSpan(new RelativeSizeSpan(0.76f), leadLengths.get(line) + 1, text.length(),
                    Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
    }

    private void drawWordGlow(Canvas canvas, int line, int alpha) {
        for (Glow glow : lineGlows.get(line)) {
            if (!drawingLead && !glow.segments.get(0).backing) continue;
            long elapsed = drawingPosition - glow.start;
            long tail = drawingPosition - glow.end;
            if (elapsed < 0 || tail >= 160) continue;
            // Длительность слова определяет характер свечения, отдельные слоги —
            // какую часть слова уже можно подсветить.
            float emphasis = glow.durationEmphasis();
            float attack = 65f + 95f * emphasis;
            float strength = Math.min(1f, elapsed / attack) * (1f - Math.max(0f, tail / 160f));
            if (strength <= 0f) continue;
            for (Word segment : glow.segments) {
                if (drawingPosition <= segment.start) continue;
                float fraction = segment.end <= segment.start ? 1f
                        : Math.min(1f, (float) (drawingPosition - segment.start) / (segment.end - segment.start));
                GlowRaster cached = glowMasks.get(segment);
                if (cached == null) {
                    RectF bounds = segment.glowBounds;
                    Bitmap mask = Bitmap.createBitmap(Math.max(1, (int) Math.ceil(bounds.width() / 2f)),
                            Math.max(1, (int) Math.ceil(bounds.height() / 2f)), Bitmap.Config.ARGB_8888);
                    Canvas raster = new Canvas(mask);
                    raster.scale(0.5f, 0.5f);
                    raster.translate(-bounds.left, -bounds.top);
                    TextPaint soft = new TextPaint(paint);
                    soft.setColor(0xffffffff); soft.setAlpha(255); soft.setShader(null);
                    soft.clearShadowLayer(); soft.baselineShift = 0;
                    // Сплошная мягкая маска: OUTER оставлял чёткий вырез букв,
                    // который при масштабировании выглядел как тёмный двойной контур.
                    soft.setMaskFilter(new BlurMaskFilter(sp(3), BlurMaskFilter.Blur.NORMAL));
                    SpannableString text = new SpannableString(texts.get(line));
                    styleBacking(text, line);
                    if (segment.from > 0) text.setSpan(new ForegroundColorSpan(0x00ffffff), 0, segment.from,
                            Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    if (segment.to < text.length()) text.setSpan(new ForegroundColorSpan(0x00ffffff), segment.to,
                            text.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    new StaticLayout(text, soft, getWidth(),
                            centered ? Layout.Alignment.ALIGN_CENTER : Layout.Alignment.ALIGN_NORMAL,
                            1.05f, 0, false).draw(raster);
                    cached = new GlowRaster(); cached.bitmap = mask;
                    cached.bitmapShader = new BitmapShader(mask, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP);
                    if (segment.glowGradient != null)
                        cached.progressShader = new ComposeShader(cached.bitmapShader,
                                segment.glowGradient, PorterDuff.Mode.DST_IN);
                    glowMasks.put(segment, cached);
                }
                glowPaint.setColor(0xffffffff);
                glowPaint.setShader(null);
                float visibility = strength;
                if (fraction < 1f) {
                    if (segment.glowGradient != null) {
                        RectF box = segment.boxes.get(0);
                        float x = segment.rtl.get(0) ? box.right - box.width() * fraction
                                : box.left + box.width() * fraction;
                        segment.gradientMatrix.setTranslate(x - sp(2), 0);
                        segment.glowGradient.setLocalMatrix(segment.gradientMatrix);
                        glowPaint.setShader(cached.progressShader);
                    } else visibility *= fraction;
                }
                float intensity = glow.segments.get(0).backing ? 0.62f : 1f;
                glowPaint.setAlpha(Math.round(alpha * (0.46f + 0.36f * emphasis) * visibility * intensity));
                float rise = fraction < 1f ? -Math.round(sp(1) * (float) Math.sin(Math.PI * fraction)) : 0;
                glowDestination.set(segment.glowBounds.left, segment.glowBounds.top + rise,
                        segment.glowBounds.left + cached.bitmap.getWidth() * 2f,
                        segment.glowBounds.top + rise + cached.bitmap.getHeight() * 2f);
                if (glowPaint.getShader() != null) {
                    glowMatrix.setScale(2f, 2f);
                    glowMatrix.postTranslate(glowDestination.left, glowDestination.top);
                    cached.bitmapShader.setLocalMatrix(glowMatrix);
                    canvas.drawRect(glowDestination, glowPaint);
                } else canvas.drawBitmap(cached.bitmap, null, glowDestination, glowPaint);
                }
        }
    }

    @Override
    protected void onDraw(Canvas canvas) {
        int w = getWidth(), h = getHeight();
        if (texts.isEmpty() || w <= 0 || h <= 0) return;
        if (builtWidth != w) build(w);

        float target = targetOffset(h);
        long now = System.nanoTime();
        if (Float.isNaN(offset)) offset = target;
        // плавность не зависит от частоты кадров: ~200 мс на основную часть пути
        float dt = lastFrame == 0 ? 16f : Math.min(64f, (now - lastFrame) / 1e6f);
        lastFrame = now;
        boolean browsing = isBrowsing();
        boolean moving;
        if (browsing) {
            // текст держит палец; после броска докатывается и тормозит
            if (Math.abs(flingVelocity) > 0.02f) {
                float before = offset;
                offset = clampOffset(offset + flingVelocity * dt);
                flingVelocity = offset == before ? 0 : flingVelocity * (float) Math.pow(0.95, dt / 16f);
                browseUntil = Math.max(browseUntil, SystemClock.uptimeMillis() + BROWSE_MS / 2);
            } else {
                flingVelocity = 0;
            }
            moving = true; // перерисовываем, чтобы заметить конец листания
        } else {
            float diff = target - offset;
            offset += diff * (1f - (float) Math.pow(0.88, dt / 16f));
            moving = Math.abs(diff) > 0.5f;
            if (!moving) {
                offset = target;
                lastFrame = 0;
            }
        }

        float fade = Math.min(1f, (SystemClock.uptimeMillis() - lineChangedAt) / 320f);
        float blend = 1f - (float) Math.pow(1f - fade, 3);
        if (synced && fade < 1f && !browsing) moving = true;
        drawingPosition = positionMs + (playing ? Math.min(250, SystemClock.uptimeMillis() - positionAt) : 0);
        for (int i = 0; i < layouts.size(); i++) {
            StaticLayout l = layouts.get(i);
            float y = tops[i] - offset;
            float bottom = y + l.getHeight();
            if (bottom < 0 || y > h) continue;
            int alpha;
            if (!synced) alpha = 200;
            else if (i == current) alpha = 255;
            else if (browsing) alpha = 140;  // пока листают — все строки читаемы
            else alpha = Math.abs(i - current) == 1 ? 125 : 75;
            if (synced && !browsing && fade < 1f) {
                if (i == current) alpha = (int) (125 + 130 * blend);
                else if (i == leaving) alpha = (int) (255 + (alpha - 255) * blend);
            }
            if (alpha <= 0) continue;
            List<Word> timedWords = i < words.size() ? words.get(i) : null;
            drawingLead = i == current;
            long animationEnd = -1;
            if (timedWords != null) for (Word word : timedWords) {
                if (drawingLead || (word.backing && word.start <= drawingPosition && drawingPosition < word.end + 160))
                    animationEnd = Math.max(animationEnd, word.end + 160);
            }
            drawingTimed = animationEnd >= 0;
            paint.setAlpha(alpha);
            paint.setShader(null);
            paint.clearShadowLayer();
            paint.baselineShift = 0;
            canvas.save();
            canvas.translate(0, y);
            drawingAlpha = alpha;
            if (drawingTimed) {
                drawWordGlow(canvas, i, alpha);
                if (drawingLead) paint.setAlpha((int) (alpha * 0.35f));
                l.draw(canvas);
                // Только активная строка и только при воспроизведении; на паузе кадры не тратим.
                if (playing && drawingPosition < animationEnd) moving = true;
            } else if (y >= sp(44) && bottom <= h - sp(44)) {
                l.draw(canvas);
            } else {
                drawEdgeRows(canvas, l, i, w, h, y, alpha);
            }
            canvas.restore();
        }

        if (creditLayout != null) {
            float y = creditTop - offset;
            if (y < h && y + creditLayout.getHeight() > 0) {
                canvas.save();
                canvas.translate(0, y);
                creditLayout.draw(canvas);
                canvas.restore();
            }
        }

        if (moving && browsing && flingVelocity == 0) postInvalidateDelayed(100); // ждём конца листания
        else if (moving) postInvalidateOnAnimation();
    }
}

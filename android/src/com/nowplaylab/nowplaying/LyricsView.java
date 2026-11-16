package com.nowplaylab.nowplaying;

import android.content.Context;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
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
import android.text.style.URLSpan;
import android.view.View;

import java.util.ArrayList;
import java.util.List;

/** Синхронный текст: текущая строка яркая и на 40% высоты, остальные приглушены,
 *  прокрутка плавная, края мягко затухают. Рисуется видеокартой, без отдельных слоёв.
 *  Текст можно листать пальцем (жесты ловит MainActivity); через 3 с без касаний
 *  он сам возвращается к текущей строке. */
public class LyricsView extends View {
    private final List<Long> times = new ArrayList<Long>();
    private final List<String> texts = new ArrayList<String>();
    private final List<StaticLayout> layouts = new ArrayList<StaticLayout>();
    private final List<List<Word>> words = new ArrayList<List<Word>>();
    private long positionMs, positionAt;
    private boolean playing;
    private boolean wordSynced;
    private boolean drawingTimed;
    private int drawingAlpha;
    private long drawingPosition;

    /** Один слог/слово. Индексы UTF-16, как у Android Layout. */
    public static class Word {
        public long start, end;
        public int from, to;
        final List<RectF> boxes = new ArrayList<RectF>();
        final List<Boolean> rtl = new ArrayList<Boolean>();
        LinearGradient gradient;
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
            if (!drawingTimed) return;
            float fraction = drawingPosition < word.start ? 0 : word.end <= word.start ? 1
                    : Math.min(1f, (float) (drawingPosition - word.start) / (word.end - word.start));
            p.setColor(0xffffffff);
            p.setAlpha(drawingAlpha);
            if (fraction <= 0) {
                p.setAlpha((int) (drawingAlpha * 0.35f));
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
                    p.setAlpha((int) (drawingAlpha * (0.35f + 0.65f * fraction)));
                }
                float pulse = (float) Math.sin(Math.PI * fraction);
                p.baselineShift = -Math.round(sp(1) * pulse);
                p.setShadowLayer(sp(2), 0, 0, 0x50ffffff);
            }
        }
    }

    public void setWords(List<List<Word>> value) {
        words.clear();
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
        float gap = sp(14);
        tops = new float[texts.size()];
        float y = 0;
        for (int i = 0; i < texts.size(); i++) {
            String s = texts.get(i);
            CharSequence content = s.length() == 0 ? "♪" : s;
            if (i < words.size() && !words.get(i).isEmpty()) {
                SpannableString styled = new SpannableString(content);
                for (Word word : words.get(i)) {
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
                    }
                }
            }
            tops[i] = y;
            y += l.getHeight() + gap;
        }
        creditLayout = attribution.length() == 0 ? null : new StaticLayout(attribution, creditPaint, width,
                centered ? Layout.Alignment.ALIGN_CENTER : Layout.Alignment.ALIGN_NORMAL, 1.15f, 0, false);
        creditTop = y + sp(12);
        totalHeight = creditLayout == null ? y : creditTop + creditLayout.getHeight() + sp(14);
        builtWidth = width;
    }

    private float targetOffset(int h) {
        if (synced) {
            if (current < 0) return -h * 0.4f;
            return tops[current] + layouts.get(current).getHeight() / 2f - h * 0.4f;
        }
        return plainProgress * Math.max(0, totalHeight - h * 0.6f) - h * 0.1f;
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

        float fadeZone = h * 0.15f;
        float fade = Math.min(1f, (SystemClock.uptimeMillis() - lineChangedAt) / 320f);
        float blend = 1f - (float) Math.pow(1f - fade, 3);
        if (synced && fade < 1f && !browsing) moving = true;
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
            // затухание у верхнего и нижнего края
            float edge = Math.min((bottom) / fadeZone, (h - y) / fadeZone);
            if (edge < 1f) alpha = (int) (alpha * Math.max(0f, edge));
            if (alpha <= 0) continue;
            paint.setAlpha(alpha);
            paint.setShader(null);
            paint.clearShadowLayer();
            paint.baselineShift = 0;
            canvas.save();
            canvas.translate(0, y);
            List<Word> timedWords = i < words.size() ? words.get(i) : null;
            drawingTimed = i == current && timedWords != null && !timedWords.isEmpty();
            drawingAlpha = alpha;
            drawingPosition = positionMs + (playing ? Math.min(250, SystemClock.uptimeMillis() - positionAt) : 0);
            if (drawingTimed) {
                paint.setAlpha((int) (alpha * 0.35f));
                l.draw(canvas);
                // Только активная строка и только при воспроизведении; на паузе кадры не тратим.
                if (playing && drawingPosition < timedWords.get(timedWords.size() - 1).end) moving = true;
            } else {
                l.draw(canvas);
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

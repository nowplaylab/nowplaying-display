package com.tab3.nowplaying;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.Matrix;
import android.graphics.Shader;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;
import android.util.TypedValue;
import android.view.View;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/** Один виджет на экране часов: подложка по стилю, подпись сверху, крупное значение или текст снизу.
 *  Тот же вид — кнопка «Включить компьютер» (Wake on LAN) со своими состояниями. */
public class WidgetView extends View {
    /** На сколько часы ПК впереди часов планшета, мс. На старом Android планшет может держать
     *  неверное время UTC и неверный часовой пояс так, что местные часы на экране верны,
     *  а всё, что считается от настоящего UTC (второй город, события календаря), уезжает. */
    static volatile long skewMs;

    static final int WOL_NONE = 0, WOL_READY = 1, WOL_WAKING = 2, WOL_FAILED = 3;
    private static final int NIGHT = 0xffa3281e;

    private final TextPaint label = new TextPaint(Paint.ANTI_ALIAS_FLAG), value = new TextPaint(Paint.ANTI_ALIAS_FLAG),
            aside = new TextPaint(Paint.ANTI_ALIAS_FLAG), body = new TextPaint(Paint.ANTI_ALIAS_FLAG),
            small = new TextPaint(Paint.ANTI_ALIAS_FLAG);
    private final Paint icon = new Paint(Paint.ANTI_ALIAS_FLAG), iconLine = new Paint(Paint.ANTI_ALIAS_FLAG);
    private JSONArray cols, events;
    private final SimpleDateFormat localHm = new SimpleDateFormat("HH:mm", Locale.US);
    private final SimpleDateFormat ymd = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
    private static final String[] WEEKDAYS = {"Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"};
    private Bitmap art;
    private String trackTitle = "", trackArtist = "";
    private boolean pressed;
    private final Paint artPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Matrix artMatrix = new Matrix();
    private final Paint plate = new Paint(Paint.ANTI_ALIAS_FLAG), stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final Path power = new Path();
    private final SimpleDateFormat hm = new SimpleDateFormat("HH:mm", Locale.US);

    private String kind = "", labelText = "", valueText = "", asideText = "", bodyText = "";
    private int offsetMin;
    private String style = "card";
    private boolean bare, alignRight, night;
    private int wol = WOL_NONE;
    private String pcName = "";

    public WidgetView(Context c, Typeface medium, Typeface semiBold, Typeface bold) {
        super(c);
        label.setTypeface(medium);
        label.setTextSize(sp(18));
        value.setTypeface(semiBold);
        aside.setTypeface(medium);
        aside.setTextSize(sp(16));
        body.setTypeface(bold);
        body.setTextSize(sp(24));
        small.setTypeface(semiBold);
        small.setTextSize(sp(30));
        iconLine.setStyle(Paint.Style.STROKE);
        iconLine.setStrokeCap(Paint.Cap.ROUND);
        iconLine.setStrokeWidth(dp(1.8f));
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeWidth(dp(1.5f));
        hm.setTimeZone(TimeZone.getTimeZone("UTC"));
        setColors();
    }

    private float dp(float v) {
        return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private float sp(float v) {
        return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, v, getResources().getDisplayMetrics());
    }

    /** Данные с ПК: kind — weather, city, countdown, note. */
    void setData(String kind, String label, String value, String aside, String text, int offsetMin) {
        this.kind = kind;
        labelText = label;
        valueText = value;
        asideText = aside;
        bodyText = text;
        this.offsetMin = offsetMin;
        cols = null;
        events = null;
        wol = WOL_NONE;
        invalidate();
    }

    /** Колонки прогноза: [{t: «15:00» или «Пт», v: «+3°», lo: «−1°» (только по дням), i: значок}]. */
    void setCols(JSONArray c) {
        cols = c;
        invalidate();
    }

    /** Карточка «Продолжить»: обложка, название и исполнитель последнего трека. pressed — уже нажали, ждём музыку. */
    void setResume(String title, String artist, Bitmap bmp, boolean pressed) {
        boolean changed = !"resume".equals(kind) || !title.equals(trackTitle) || !artist.equals(trackArtist)
                || bmp != art || pressed != this.pressed;
        kind = "resume";
        wol = WOL_NONE;
        trackTitle = title;
        trackArtist = artist;
        this.pressed = pressed;
        if (bmp != art) {
            art = bmp;
            artPaint.setShader(bmp == null ? null : new BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP));
        }
        if (changed) invalidate();
    }

    /** События календаря: [{text, start, end, all_day, date, last}] — время в секундах UTC, даты ГГГГ-ММ-ДД. */
    void setEvents(JSONArray e) {
        events = e;
        invalidate();
    }

    void setWol(int state, String name) {
        kind = "wol";
        if (state != wol || !name.equals(pcName)) {
            wol = state;
            pcName = name;
            invalidate();
        }
    }

    int wolState() { return "wol".equals(kind) ? wol : WOL_NONE; }

    /** bare — «Углы»: без подложки и полей; alignRight — правые углы прижаты вправо. */
    void setLook(String style, boolean bare, boolean alignRight) {
        this.style = style;
        this.bare = bare;
        this.alignRight = alignRight;
        value.setTextSize(sp(bare ? 40 : 48));
        invalidate();
    }

    void setNight(boolean n) {
        if (n == night) return;
        night = n;
        setColors();
        invalidate();
    }

    private void setColors() {
        int main = night ? NIGHT : 0xffffffff;
        value.setColor(main);
        body.setColor(main);
        label.setColor(night ? 0xbfa3281e : 0xabffffff);   // 75% ночью, 67% днём
        aside.setColor(night ? 0x8ca3281e : 0x80ffffff);
        small.setColor(main);
        icon.setColor(main);
        iconLine.setColor(main);
        for (Paint p : new Paint[]{label, value, aside, body, small, icon, iconLine}) {
            if (night) p.setShadowLayer(0, 0, 0, 0);
            else p.setShadowLayer(dp(3), 0, dp(1), 0x66000000);
        }
        plate.setColor(night ? 0x1aa3281e : 0x17ffffff);
        stroke.setColor(night ? 0x59a3281e : 0x33ffffff);
    }

    @Override
    protected void onDraw(Canvas c) {
        float w = getWidth(), h = getHeight();
        float padX = dp(22), padTop = dp(20), padBottom = dp(20);
        if (bare) {
            padX = 0;
            padTop = 0;
            padBottom = 0;
        } else if ("none".equals(style)) {
            c.drawRect(0, 0, w, dp(1.5f), stroke);
            padX = 0;
            padTop = dp(16);
        } else {
            rect.set(dp(0.75f), dp(0.75f), w - dp(0.75f), h - dp(0.75f));
            if ("line".equals(style)) c.drawRoundRect(rect, dp(22), dp(22), stroke);
            else c.drawRoundRect(rect, dp(22), dp(22), plate);
        }
        float left = padX, right = w - padX, top = padTop, bottom = h - padBottom;
        if ("wol".equals(kind)) {
            drawWol(c, left, right, top, bottom);
            return;
        }
        if ("resume".equals(kind)) {
            drawResume(c, left, right, top, bottom);
            return;
        }
        Paint.Align al = alignRight ? Paint.Align.RIGHT : Paint.Align.LEFT;
        float x = alignRight ? right : left;
        label.setTextAlign(al);
        c.drawText(fit(labelText, label, right - left), x, top - label.ascent(), label);

        if ("note".equals(kind)) {
            drawBody(c, bodyText, left, right, bottom, 2);
            return;
        }
        if ("calendar".equals(kind)) {
            drawCalendar(c, left, right, bottom);
            return;
        }
        if ("forecast".equals(kind) || "pc".equals(kind)) {
            drawForecast(c, left, right, bottom);
            return;
        }
        String v = valueText, as = asideText;
        if ("city".equals(kind)) {
            // разницу считаем от часов самого планшета, чтобы она сходилась с тем, что на экране
            long own = System.currentTimeMillis(), now = own + skewMs;
            v = hm.format(new Date(now + offsetMin * 60000L));
            // каким смещением от настоящего UTC живут часы на экране — с точностью до 15 минут
            int localMin = Math.round((TimeZone.getDefault().getOffset(own) - skewMs) / 900000f) * 15;
            as = hoursDiff(offsetMin - localMin);
        }
        if (bare && "countdown".equals(kind) && as.length() > 0) v += " " + as;   // в углах подпись справа не помещается
        value.setTextAlign(al);
        float vBase = bottom - value.descent() * 0.4f;
        c.drawText(v, x, vBase, value);
        if (!bare && as.length() > 0) {
            String[] lines = as.split("\n");
            aside.setTextAlign(Paint.Align.RIGHT);
            float lh = aside.getFontSpacing();
            float y = bottom - aside.descent() * 0.4f - lh * (lines.length - 1);
            for (String line : lines) {
                c.drawText(line, right, y, aside);
                y += lh;
            }
        }
    }

    /** Колонки снизу (прогноз, состояние ПК): сверху значок (если есть) и подпись, под ними значение. */
    private void drawForecast(Canvas c, float left, float right, float bottom) {
        if (cols == null || cols.length() == 0) return;
        int n = cols.length();
        float is = dp(22);
        float tempBase = bottom - small.descent() * 0.4f;
        float rowBase = tempBase - small.getTextSize() * 1.05f;
        aside.setTextAlign(Paint.Align.LEFT);
        small.setTextAlign(Paint.Align.LEFT);
        // ширина каждой колонки, потом равные промежутки: на подложке — по всей ширине,
        // в «Углах» — плотной группой у своего края
        float[] cw = new float[n], iw = new float[n];
        float total = measureCols(cw, iw, is);
        float asideSize = aside.getTextSize();
        // подписи не влезают с нормальными промежутками — уменьшаем их, но не мельче 13sp
        while (n > 1 && (right - left - total) / (n - 1) < dp(16) && aside.getTextSize() > sp(13.5f)) {
            aside.setTextSize(aside.getTextSize() - sp(1));
            total = measureCols(cw, iw, is);
        }
        float gap = n > 1 ? (right - left - total) / (n - 1) : 0;
        if (bare) gap = Math.min(gap, dp(28));
        float x = bare && alignRight ? right - total - gap * (n - 1) : left;
        for (int k = 0; k < n; k++) {
            JSONObject o = cols.optJSONObject(k);
            if (o == null) continue;
            String t = o.optString("t"), v = o.optString("v"), lo = o.optString("lo");
            float iy = rowBase + aside.ascent() * 0.5f - is / 2 + aside.descent() * 0.2f;
            if (iw[k] > 0) drawIcon(c, o.optString("i"), x, iy, is);
            c.drawText(t, x + iw[k], rowBase, aside);
            c.drawText(v, x, tempBase, small);
            if (lo.length() > 0) c.drawText(lo, x + small.measureText(v) + dp(4), tempBase, aside);
            x += cw[k] + gap;
        }
        aside.setTextSize(asideSize);
    }

    private float measureCols(float[] cw, float[] iw, float is) {
        float total = 0;
        for (int k = 0; k < cols.length(); k++) {
            JSONObject o = cols.optJSONObject(k);
            if (o == null) continue;
            String lo = o.optString("lo");
            iw[k] = o.optString("i").length() > 0 ? is + dp(5) : 0;
            float w1 = iw[k] + aside.measureText(o.optString("t"));
            float w2 = small.measureText(o.optString("v")) + (lo.length() > 0 ? dp(4) + aside.measureText(lo) : 0);
            cw[k] = Math.max(w1, w2);
            total += cw[k];
        }
        return total;
    }

    /** Две ближайшие записи снизу: время или день слева, название справа от него. */
    private void drawCalendar(Canvas c, float left, float right, float bottom) {
        if (events == null) return;
        long own = System.currentTimeMillis(), now = own + skewMs;   // now — настоящее время ПК
        String today = ymd.format(new Date(own));
        Calendar cal = Calendar.getInstance();
        String[] when = new String[2], what = new String[2];
        int n = 0;
        for (int k = 0; k < events.length() && n < 2; k++) {
            JSONObject e = events.optJSONObject(k);
            if (e == null) continue;
            if (e.optBoolean("all_day")) {
                String d = e.optString("date"), last = e.optString("last", d);
                if (last.compareTo(today) < 0) continue;
                when[n] = d.compareTo(today) <= 0 ? "Сегодня" : dayName(d, today, cal);
            } else {
                long st = e.optLong("start") * 1000, en = e.optLong("end") * 1000;
                if (Math.max(st, en) <= now) continue;
                if (st <= now) {
                    when[n] = "Сейчас";
                } else {
                    Date at = new Date(st - skewMs);   // в часах планшета — как на экране
                    String d = ymd.format(at), hm = localHm.format(at);
                    when[n] = d.equals(today) ? hm : dayName(d, today, cal) + " " + hm;
                }
            }
            what[n] = e.optString("text");
            n++;
        }
        if (n == 0) {
            label.setTextAlign(alignRight ? Paint.Align.RIGHT : Paint.Align.LEFT);
            c.drawText(fit("Две недели свободны", label, right - left), alignRight ? right : left,
                    bottom - label.descent() * 0.4f, label);
            return;
        }
        float bodySize = body.getTextSize();
        body.setTextSize(sp(bare ? 18 : 19));
        float lh = body.getFontSpacing() * 1.02f;
        float base = bottom - body.descent() * 0.4f - lh * (n - 1);
        float tw = 0;
        for (int k = 0; k < n; k++) tw = Math.max(tw, aside.measureText(when[k]));
        float gap = dp(12);
        aside.setTextAlign(Paint.Align.LEFT);
        body.setTextAlign(Paint.Align.LEFT);
        for (int k = 0; k < n; k++) {
            String t = fit(what[k], body, right - left - tw - gap);
            float x = alignRight ? right - tw - gap - body.measureText(t) : left;
            c.drawText(when[k], x, base, aside);
            c.drawText(t, x + tw + gap, base, body);
            base += lh;
        }
        body.setTextSize(bodySize);
    }

    /** «Завтра», день недели в ближайшую неделю, дальше — число и месяц («7.11»). */
    private String dayName(String d, String today, Calendar cal) {
        try {
            Date date = ymd.parse(d), t = ymd.parse(today);
            long days = Math.round((date.getTime() - t.getTime()) / 86400000.0);
            if (days == 1) return "Завтра";
            cal.setTime(date);
            if (days < 7) return WEEKDAYS[cal.get(Calendar.DAY_OF_WEEK) - 1];
            return cal.get(Calendar.DAY_OF_MONTH) + "." + (cal.get(Calendar.MONTH) + 1);
        } catch (Exception e) {
            return d;
        }
    }

    /** Простые значки погоды в квадрате s×s с левым верхним углом (x, y). */
    private void drawIcon(Canvas c, String kind, float x, float y, float s) {
        c.save();
        c.translate(x, y);
        boolean drops = "rain".equals(kind) || "snow".equals(kind) || "storm".equals(kind);
        if ("sun".equals(kind)) {
            drawSun(c, s * 0.5f, s * 0.5f, s);
        } else if ("fog".equals(kind)) {
            for (int i = 0; i < 3; i++) {
                float yy = s * (0.3f + 0.2f * i);
                c.drawLine(s * (0.1f + 0.08f * (i % 2)), yy, s * (0.9f - 0.08f * ((i + 1) % 2)), yy, iconLine);
            }
        } else {
            if ("partly".equals(kind)) drawSun(c, s * 0.68f, s * 0.32f, s * 0.62f);
            drawCloud(c, s, drops ? -s * 0.14f : 0);
            if ("rain".equals(kind)) {
                for (int i = 0; i < 3; i++) {
                    float xx = s * (0.3f + 0.2f * i);
                    c.drawLine(xx, s * 0.74f, xx - s * 0.08f, s * 0.95f, iconLine);
                }
            } else if ("snow".equals(kind)) {
                for (int i = 0; i < 3; i++) c.drawCircle(s * (0.3f + 0.2f * i), s * (0.82f + 0.08f * (i % 2)), s * 0.06f, icon);
            } else if ("storm".equals(kind)) {
                Path b = new Path();
                b.moveTo(s * 0.55f, s * 0.68f);
                b.lineTo(s * 0.42f, s * 0.84f);
                b.lineTo(s * 0.56f, s * 0.84f);
                b.lineTo(s * 0.44f, s * 1.0f);
                c.drawPath(b, iconLine);
            }
        }
        c.restore();
    }

    private void drawSun(Canvas c, float cx, float cy, float s) {
        c.drawCircle(cx, cy, s * 0.2f, icon);
        for (int i = 0; i < 8; i++) {
            double a = Math.PI / 4 * i;
            float cs = (float) Math.cos(a), sn = (float) Math.sin(a);
            c.drawLine(cx + cs * s * 0.32f, cy + sn * s * 0.32f, cx + cs * s * 0.45f, cy + sn * s * 0.45f, iconLine);
        }
    }

    private void drawCloud(Canvas c, float s, float dy) {
        c.drawCircle(s * 0.32f, s * 0.6f + dy, s * 0.17f, icon);
        c.drawCircle(s * 0.55f, s * 0.48f + dy, s * 0.24f, icon);
        c.drawCircle(s * 0.75f, s * 0.62f + dy, s * 0.15f, icon);
        rect.set(s * 0.32f, s * 0.6f + dy, s * 0.75f, s * 0.77f + dy);
        c.drawRect(rect, icon);
    }

    /** Текст не больше maxLines строк, лишнее — с многоточием. */
    private StaticLayout lines(String text, TextPaint p, float width, int maxLines) {
        int w = Math.max(1, (int) width);
        Layout.Alignment a = alignRight ? Layout.Alignment.ALIGN_OPPOSITE : Layout.Alignment.ALIGN_NORMAL;
        StaticLayout l = new StaticLayout(text, p, w, a, 1f, 0, false);
        if (l.getLineCount() > maxLines) {
            String cut = text.substring(0, l.getLineEnd(maxLines - 1)).trim();
            do {
                cut = cut.substring(0, cut.length() - 1).trim();
                l = new StaticLayout(cut + "…", p, w, a, 1f, 0, false);
            } while (cut.length() > 1 && l.getLineCount() > maxLines);
        }
        return l;
    }

    /** Обложка квадратом во всю высоту, значок «играть» на ней; справа «Продолжить», название и исполнитель.
     *  В правых углах всё зеркально: обложка справа, текст прижат вправо. */
    private void drawResume(Canvas c, float left, float right, float top, float bottom) {
        float s = bottom - top, r = dp(bare ? 10 : 14);
        float ax = alignRight ? right - s : left;
        rect.set(ax, top, ax + s, top + s);
        if (art != null) {   // обложка по центру квадрата, лишнее обрезается
            float k = Math.max(s / art.getWidth(), s / art.getHeight());
            artMatrix.setScale(k, k);
            artMatrix.postTranslate(ax + (s - art.getWidth() * k) / 2, top + (s - art.getHeight() * k) / 2);
            artPaint.getShader().setLocalMatrix(artMatrix);
            artPaint.setAlpha(night ? 90 : 255);
            c.drawRoundRect(rect, r, r, artPaint);
        } else {
            c.drawRoundRect(rect, r, r, plate);
        }
        // кружок с треугольником «играть»
        float cx = ax + s / 2, cy = top + s / 2, br = s * 0.2f;
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setColor(night ? 0x99000000 : 0x73000000);
        c.drawCircle(cx, cy, br, p);
        p.setColor(night ? NIGHT : 0xffffffff);
        Path tri = new Path();
        tri.moveTo(cx - br * 0.32f, cy - br * 0.45f);
        tri.lineTo(cx + br * 0.5f, cy);
        tri.lineTo(cx - br * 0.32f, cy + br * 0.45f);
        tri.close();
        c.drawPath(tri, p);

        float gap = dp(bare ? 14 : 16);
        float tl = alignRight ? left : ax + s + gap, tr = alignRight ? ax - gap : right;
        float x = alignRight ? tr : tl;
        label.setTextAlign(alignRight ? Paint.Align.RIGHT : Paint.Align.LEFT);
        c.drawText(fit(pressed ? "Включаю…" : "Продолжить", label, tr - tl), x, top - label.ascent(), label);
        float artistBase = bottom - label.descent() * 0.4f;
        if (trackArtist.length() > 0) c.drawText(fit(trackArtist, label, tr - tl), x, artistBase, label);
        float bodySize = body.getTextSize();
        body.setTextSize(sp(bare ? 17 : 18));
        float free = (trackArtist.length() > 0 ? artistBase + label.ascent() : bottom) - (top + label.getFontSpacing()) - dp(2);
        int maxLines = Math.max(1, Math.min(2, (int) (free / body.getFontSpacing())));
        StaticLayout l = lines(trackTitle, body, tr - tl, maxLines);
        float by = (trackArtist.length() > 0 ? artistBase + label.ascent() - dp(2) : bottom) - l.getHeight();
        c.save();
        c.translate(tl, by);
        l.draw(c);
        c.restore();
        body.setTextSize(bodySize);
    }

    /** Текст жирным снизу, не больше maxLines строк (лишнее — с многоточием). */
    private void drawBody(Canvas c, String text, float left, float right, float bottom, int maxLines) {
        StaticLayout l = lines(text, body, right - left, maxLines);
        c.save();
        c.translate(left, bottom - l.getHeight() + body.descent() * 0.3f);
        l.draw(c);
        c.restore();
    }

    private void drawWol(Canvas c, float left, float right, float top, float bottom) {
        boolean waking = wol == WOL_WAKING;
        float size = dp(bare ? 36 : 44);
        float alpha = 1f;
        if (waking) {   // иконка мягко пульсирует, пока ждём компьютер
            double t = (SystemClock.uptimeMillis() % 1600) / 1600.0;
            alpha = (float) (0.35 + 0.65 * (0.5 + 0.5 * Math.cos(t * 2 * Math.PI)));
        }
        Paint ic = new Paint(Paint.ANTI_ALIAS_FLAG);
        ic.setStyle(Paint.Style.STROKE);
        ic.setStrokeCap(Paint.Cap.ROUND);
        ic.setStrokeWidth(size * 0.09f);
        ic.setColor(night ? NIGHT : 0xffffffff);
        ic.setAlpha(Math.round(255 * alpha));
        if (!night) ic.setShadowLayer(dp(3), 0, dp(1), 0x66000000);
        float cx = left + size / 2, cy = top + size / 2;
        float r = size * 0.36f;
        power.reset();
        rect.set(cx - r, cy - r + size * 0.04f, cx + r, cy + r + size * 0.04f);
        power.addArc(rect, -60, 300);
        c.drawPath(power, ic);
        c.drawLine(cx, cy - size * 0.42f, cx, cy - size * 0.02f, ic);

        String title = wol == WOL_WAKING ? "Включаю…" : wol == WOL_FAILED ? "Не получилось" : "Включить компьютер";
        float tx = left + size + dp(14);
        drawTitle(c, title, tx, right, cy);
        if (waking) {
            // полоска ожидания: отрезок бежит слева направо
            float barY = bottom - aside.getFontSpacing() - dp(10);
            Paint bar = new Paint(Paint.ANTI_ALIAS_FLAG);
            bar.setColor(night ? 0x40a3281e : 0x29ffffff);
            rect.set(left, barY, right, barY + dp(4));
            c.drawRoundRect(rect, dp(2), dp(2), bar);
            float t = (SystemClock.uptimeMillis() % 1400) / 1400f;
            float seg = (right - left) * 0.38f;
            float x0 = left + (right - left + seg) * t - seg;
            bar.setColor(night ? NIGHT : 0xd9ffffff);
            rect.set(Math.max(left, x0), barY, Math.min(right, x0 + seg), barY + dp(4));
            if (rect.right > rect.left) c.drawRoundRect(rect, dp(2), dp(2), bar);
            aside.setTextAlign(Paint.Align.LEFT);
            c.drawText("обычно до минуты", left, bottom - aside.descent() * 0.4f, aside);
            postInvalidateDelayed(40);
        } else {
            label.setTextAlign(Paint.Align.LEFT);
            String sub = wol == WOL_FAILED ? "Нажми ещё раз" : pcName;
            c.drawText(fit(sub, label, right - left), left, bottom - label.descent() * 0.4f, label);
        }
    }

    /** Заголовок у иконки: в одну или две строки, по центру иконки. */
    private void drawTitle(Canvas c, String text, float left, float right, float cy) {
        int width = Math.max(1, (int) (right - left));
        StaticLayout l = new StaticLayout(text, body, width, Layout.Alignment.ALIGN_NORMAL, 1f, 0, false);
        c.save();
        c.translate(left, cy - l.getHeight() / 2f);
        l.draw(c);
        c.restore();
    }

    static String hoursDiff(int minutes) {
        if (minutes == 0) return "то же время";
        int a = Math.abs(minutes);
        String num = a % 60 == 0 ? String.valueOf(a / 60) : (a / 60) + "," + (a % 60 * 10 / 60);
        return (minutes > 0 ? "+" : "−") + num + " ч";
    }

    private static String fit(String s, Paint p, float width) {
        if (s == null) return "";
        if (p.measureText(s) <= width) return s;
        while (s.length() > 1 && p.measureText(s + "…") > width) s = s.substring(0, s.length() - 1);
        return s + "…";
    }
}

package com.tab3.nowplaying;

import android.content.Context;
import android.graphics.Canvas;
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

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/** Один виджет на экране часов: подложка по стилю, подпись сверху, крупное значение или текст снизу.
 *  Тот же вид — кнопка «Включить компьютер» (Wake on LAN) со своими состояниями. */
public class WidgetView extends View {
    static final int WOL_NONE = 0, WOL_READY = 1, WOL_WAKING = 2, WOL_FAILED = 3;
    private static final int NIGHT = 0xffa3281e;

    private final TextPaint label = new TextPaint(Paint.ANTI_ALIAS_FLAG), value = new TextPaint(Paint.ANTI_ALIAS_FLAG),
            aside = new TextPaint(Paint.ANTI_ALIAS_FLAG), body = new TextPaint(Paint.ANTI_ALIAS_FLAG);
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
        wol = WOL_NONE;
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
        for (Paint p : new Paint[]{label, value, aside, body}) {
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
        Paint.Align al = alignRight ? Paint.Align.RIGHT : Paint.Align.LEFT;
        float x = alignRight ? right : left;
        label.setTextAlign(al);
        c.drawText(fit(labelText, label, right - left), x, top - label.ascent(), label);

        if ("note".equals(kind)) {
            drawBody(c, bodyText, left, right, bottom, 2);
            return;
        }
        String v = valueText, as = asideText;
        if ("city".equals(kind)) {
            // разницу считаем от часов самого планшета, чтобы она сходилась с тем, что на экране
            long now = System.currentTimeMillis();
            v = hm.format(new Date(now + offsetMin * 60000L));
            as = hoursDiff(offsetMin - TimeZone.getDefault().getOffset(now) / 60000);
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

    /** Текст жирным снизу, не больше maxLines строк (лишнее — с многоточием). */
    private void drawBody(Canvas c, String text, float left, float right, float bottom, int maxLines) {
        int width = Math.max(1, (int) (right - left));
        Layout.Alignment a = alignRight ? Layout.Alignment.ALIGN_OPPOSITE : Layout.Alignment.ALIGN_NORMAL;
        StaticLayout l = new StaticLayout(text, body, width, a, 1f, 0, false);
        if (l.getLineCount() > maxLines) {
            String cut = text.substring(0, l.getLineEnd(maxLines - 1)).trim();
            do {
                cut = cut.substring(0, cut.length() - 1).trim();
                l = new StaticLayout(cut + "…", body, width, a, 1f, 0, false);
            } while (cut.length() > 1 && l.getLineCount() > maxLines);
        }
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

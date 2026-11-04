package com.tab3.nowplaying;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.TypedValue;
import android.view.View;

/** Визуализатор внизу экрана трека: полосы частот с ПК (0–99, 24 штуки), три вида на выбор.
 *  bars — столбики, wave — мягкая волна, dots — колонки точек. */
public class VisualizerView extends View {
    static final int N = 24;
    private final float[] target = new float[N], shown = new float[N];
    private volatile boolean fresh;
    private String style = "off";
    private int color = 0xffffffff;
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG),
            dim = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF r = new RectF();
    private final Path path = new Path();
    private boolean running;

    public VisualizerView(Context c) {
        super(c);
        line.setStyle(Paint.Style.STROKE);
        line.setStrokeWidth(dp(2));
        line.setStrokeJoin(Paint.Join.ROUND);
        dim.setColor(0x1fffffff);
    }

    private float dp(float v) {
        return TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    void setStyle(String s) {
        if (!s.equals(style)) {
            style = s;
            invalidate();
        }
    }

    String style() { return style; }

    /** Цвет — самый яркий из цветов обложки (тот же, что у свечения). */
    void setColor(int c) {
        color = c | 0xff000000;
        fill.setShader(null);
        invalidate();
    }

    /** Строка с ПК: «12 40 87 …». Вызывается из сетевого потока. */
    void setLine(String s) {
        String[] p = s.trim().split(" ");
        if (p.length < N) return;
        synchronized (target) {
            for (int i = 0; i < N; i++) {
                try { target[i] = Integer.parseInt(p[i]) / 99f; } catch (NumberFormatException ignored) { }
            }
        }
        fresh = true;
    }

    void clear() {
        synchronized (target) {
            for (int i = 0; i < N; i++) target[i] = 0;
        }
    }

    /** Пока виден — перерисовываемся ~30 раз в секунду. */
    void setRunning(boolean on) {
        if (on == running) return;
        running = on;
        if (on) postInvalidate();
        else {
            clear();
            for (int i = 0; i < N; i++) shown[i] = 0;
        }
    }

    @Override
    protected void onDraw(Canvas c) {
        synchronized (target) {
            for (int i = 0; i < N; i++) {
                float t = target[i];
                shown[i] += (t - shown[i]) * (t > shown[i] ? 0.6f : 0.2f);
            }
        }
        float w = getWidth(), h = getHeight();
        if ("bars".equals(style)) drawBars(c, w, h);
        else if ("wave".equals(style)) drawWave(c, w, h);
        else if ("dots".equals(style)) drawDots(c, w, h);
        if (running) postInvalidateDelayed(33);
    }

    private void drawBars(Canvas c, float w, float h) {
        float side = dp(40), bw = dp(12);
        float gap = (w - 2 * side - bw * N) / (N - 1);   // тонкие столбики, ровно по всей ширине
        fill.setShader(null);
        fill.setColor((color & 0x00ffffff) | 0xd9000000);
        float bottom = h - dp(16), maxH = h - dp(24);
        for (int i = 0; i < N; i++) {
            float bh = bw + shown[i] * (maxH - bw);   // в тишине — точка
            float x = side + i * (bw + gap);
            r.set(x, bottom - bh, x + bw, bottom);
            c.drawRoundRect(r, bw / 2, bw / 2, fill);
        }
    }

    private void drawWave(Canvas c, float w, float h) {
        float side = 0, bottom = h, maxH = h - dp(10);
        float step = (w - 2 * side) / (N - 1);
        path.reset();
        path.moveTo(side, bottom);
        float px = side, py = bottom - dp(4) - shown[0] * maxH;
        path.lineTo(px, py);
        for (int i = 1; i < N; i++) {
            float x = side + i * step, y = bottom - dp(4) - shown[i] * maxH;
            float mx = (px + x) / 2;
            path.cubicTo(mx, py, mx, y, x, y);   // плавная кривая через точки
            px = x;
            py = y;
        }
        path.lineTo(w - side, bottom);
        path.close();
        fill.setShader(new LinearGradient(0, bottom - maxH, 0, bottom,
                (color & 0x00ffffff) | 0x99000000, color & 0x00ffffff, Shader.TileMode.CLAMP));
        c.drawPath(path, fill);
        // контур сверху
        path.reset();
        px = side;
        py = bottom - dp(4) - shown[0] * maxH;
        path.moveTo(px, py);
        for (int i = 1; i < N; i++) {
            float x = side + i * step, y = bottom - dp(4) - shown[i] * maxH;
            float mx = (px + x) / 2;
            path.cubicTo(mx, py, mx, y, x, y);
            px = x;
            py = y;
        }
        line.setColor((color & 0x00ffffff) | 0xe6000000);
        c.drawPath(path, line);
    }

    private void drawDots(Canvas c, float w, float h) {
        int rows = 10;
        float side = dp(40), bottom = h - dp(16);
        float colW = (w - 2 * side) / N;
        float rad = Math.min(colW * 0.32f, (h - dp(24)) / rows * 0.36f);
        float rowStep = (h - dp(24)) / rows;
        fill.setShader(null);
        fill.setColor(color);
        for (int i = 0; i < N; i++) {
            float cx = side + colW * (i + 0.5f);
            int lit = Math.round(shown[i] * rows);
            for (int k = 0; k < rows; k++) {
                float cy = bottom - rad - k * rowStep;
                c.drawCircle(cx, cy, rad, k < Math.max(1, lit) ? fill : dim);
            }
        }
    }
}

package com.nowplaylab.nowplaying;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Shader;
import android.view.View;
import android.view.animation.LinearInterpolator;

/** Пластинка для темы «Винил»: чёрный диск с бороздками и обложкой-наклейкой в центре.
 *  Крутится, пока играет музыка; вращается весь вид на аппаратном слое — рисуется один раз. */
public class VinylView extends View {
    private final Paint disc = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint groove = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint label = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint hole = new Paint(Paint.ANTI_ALIAS_FLAG);
    private Bitmap cover;
    private ObjectAnimator spin;

    public VinylView(Context c) {
        super(c);
        disc.setColor(0xff0e0e10);
        groove.setStyle(Paint.Style.STROKE);
        groove.setColor(0xff222226);
        groove.setStrokeWidth(1f);
        hole.setColor(0xff0a0a0a);
        setLayerType(LAYER_TYPE_HARDWARE, null);
    }

    public void setLabel(Bitmap bmp) {
        cover = bmp;
        updateShader();
        invalidate();
    }

    @Override
    protected void onSizeChanged(int w, int h, int ow, int oh) {
        updateShader();
    }

    private void updateShader() {
        int size = Math.min(getWidth(), getHeight());
        if (cover == null || size == 0) {
            label.setShader(null);
            return;
        }
        float labelR = size * 0.19f;
        BitmapShader sh = new BitmapShader(cover, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP);
        Matrix m = new Matrix();
        float k = labelR * 2 / Math.min(cover.getWidth(), cover.getHeight());
        m.setScale(k, k);
        m.postTranslate(size / 2f - labelR, size / 2f - labelR);
        sh.setLocalMatrix(m);
        label.setShader(sh);
    }

    @Override
    protected void onDraw(Canvas c) {
        float size = Math.min(getWidth(), getHeight()), r = size / 2f, cx = r, cy = r;
        c.drawCircle(cx, cy, r, disc);
        for (float k = r - size * 0.03f; k > size * 0.21f; k -= size * 0.012f) c.drawCircle(cx, cy, k, groove);
        if (label.getShader() != null) c.drawCircle(cx, cy, size * 0.19f, label);
        c.drawCircle(cx, cy, size * 0.014f, hole);
    }

    /** Крутить или остановить (с того же места). */
    public void setSpinning(boolean on) {
        if (on && (spin == null || !spin.isRunning())) {
            float from = getRotation() % 360;
            spin = ObjectAnimator.ofFloat(this, "rotation", from, from + 360);
            spin.setDuration(8000);
            spin.setRepeatCount(ValueAnimator.INFINITE);
            spin.setInterpolator(new LinearInterpolator());
            spin.start();
        } else if (!on && spin != null && spin.isRunning()) {
            spin.cancel();
        }
    }
}

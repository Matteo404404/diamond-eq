package dev.diamondeq

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.view.View
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.pow

/** Plot of the EQ curve (20 Hz to 20 kHz, logarithmic), preamp included. */
class CurveView(context: Context) : View(context) {
    var profile: Profile? = null
        set(value) {
            field = value
            invalidate()
        }

    private val dp = resources.displayMetrics.density
    private val grid = Paint().apply { color = 0x33888888; strokeWidth = dp }
    private val zero = Paint().apply { color = 0x99888888.toInt(); strokeWidth = dp }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF58B6FF.toInt(); strokeWidth = 2.5f * dp; style = Paint.Style.STROKE
    }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF888888.toInt(); textSize = 10 * dp }

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        setMeasuredDimension(MeasureSpec.getSize(widthSpec), (170 * dp).toInt())
    }

    override fun onDraw(canvas: Canvas) {
        val p = profile ?: return
        val left = 30 * dp
        val bottom = height - 16 * dp
        val w = width - left - 4 * dp
        val freqs = DoubleArray(160) { 20.0 * 1000.0.pow(it / 159.0) }
        val gains = freqs.map { p.gainDb(it) }
        val hi = ceil((gains.max() + 1) / 5) * 5
        val lo = floor((gains.min() - 1) / 5) * 5
        fun x(f: Double) = left + (ln(f / 20.0) / ln(1000.0) * w).toFloat()
        fun y(d: Double) = (4 * dp + (hi - d) / (hi - lo) * (bottom - 4 * dp)).toFloat()

        for ((f, label) in listOf(20.0 to "20", 100.0 to "100", 1000.0 to "1k", 10000.0 to "10k", 20000.0 to "20k")) {
            canvas.drawLine(x(f), 4 * dp, x(f), bottom, grid)
            canvas.drawText(label, x(f) - text.measureText(label) / 2, height - 3 * dp, text)
        }
        var d = lo
        while (d <= hi) {
            canvas.drawLine(left, y(d), left + w, y(d), if (d == 0.0) zero else grid)
            canvas.drawText(d.toInt().toString(), 2 * dp, y(d) + 4 * dp, text)
            d += 5
        }
        val path = Path()
        freqs.forEachIndexed { i, f -> if (i == 0) path.moveTo(x(f), y(gains[i])) else path.lineTo(x(f), y(gains[i])) }
        canvas.drawPath(path, line)
    }
}

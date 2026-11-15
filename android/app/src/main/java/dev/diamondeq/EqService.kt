package dev.diamondeq

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.audiofx.DynamicsProcessing
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import kotlin.math.abs
import kotlin.math.max

/**
 * Keeps the equalizer applied system-wide. The effect is Android's
 * DynamicsProcessing attached to the global output mix (audio session 0), so
 * every app's sound passes through it without root.
 */
class EqService : Service() {
    private lateinit var store: Store
    private val handler = Handler(Looper.getMainLooper())
    private var effect: DynamicsProcessing? = null
    private var applied: String? = null // what the running effect was built from
    private var makeUpDb = 0.0 // gain currently added on top of the profile's preamp
    private var message = "Starting"

    private val volumeReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = refresh()
    }

    private val deviceCallback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = refreshSoon()
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = refreshSoon()
    }

    // Routing can change without a device being added or removed (e.g. picking
    // another output in the media switcher), so re-check periodically as well.
    private val poll = object : Runnable {
        override fun run() {
            refresh()
            handler.postDelayed(this, 4000)
        }
    }

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Equalizer", NotificationManager.IMPORTANCE_LOW)
        )
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification("Starting"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, notification("Starting"))
        }
        getSystemService(AudioManager::class.java).registerAudioDeviceCallback(deviceCallback, handler)
        val volumeChanged = IntentFilter("android.media.VOLUME_CHANGED_ACTION")
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(volumeReceiver, volumeChanged, Context.RECEIVER_EXPORTED)
        else registerReceiver(volumeReceiver, volumeChanged)
        handler.postDelayed(poll, 4000) // onStartCommand does the first refresh
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        store = Store(this) // the app may have synced new profiles or changed settings
        applied = null
        refresh()
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        getSystemService(AudioManager::class.java).unregisterAudioDeviceCallback(deviceCallback)
        unregisterReceiver(volumeReceiver)
        release()
        status = Status(null, null, "Equalizer off")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun refreshSoon() {
        handler.removeCallbacks(refreshTask)
        handler.postDelayed(refreshTask, 600) // let the new route settle
    }

    private val refreshTask = Runnable { refresh() }

    private fun refresh() {
        val output = Output.current(this)
        val profile = store.profileFor(output)
        val precision = store.precision
        val wanted = if (profile == null) "none" else "${output.key}|${precision.name}|$profile"
        val rebuild = wanted != applied || (profile != null && effect?.hasControl() != true)
        if (rebuild) {
            release()
            message = if (profile == null) "No profile for ${output.label}" else "${profile.name} on ${output.label}"
            if (profile != null) {
                try {
                    effect = build(profile, precision)
                } catch (e: Exception) {
                    Log.e(TAG, "could not start the equalizer", e)
                    message = "Android refused the equalizer: ${e.message ?: e.javaClass.simpleName}"
                }
            }
            applied = wanted
            getSystemService(NotificationManager::class.java).notify(1, notification(message))
        }
        val gainChanged = applyMakeUp(output, profile)
        if (rebuild || gainChanged) {
            status = Status(output, if (effect != null) profile else null, message, makeUpDb)
            sendBroadcast(Intent(ACTION_STATUS).setPackage(packageName))
        }
    }

    /** Sets the make-up gain for the current volume; true if it changed. */
    private fun applyMakeUp(output: Output, profile: Profile?): Boolean {
        val dp = effect ?: return false
        val gain = if (profile != null && store.keepLoudness && output.softwareVolume) {
            makeUpGainDb(profile, volumeAttenuationDb(output))
        } else 0.0
        if (abs(gain - makeUpDb) < 0.05) return false
        try {
            dp.setInputGainAllChannelsTo(gain.toFloat())
        } catch (e: Exception) {
            Log.w(TAG, "could not set the make-up gain", e)
            return false
        }
        makeUpDb = gain
        return true
    }

    /** How far below full scale the media volume currently is, in dB (0 at maximum). */
    private fun volumeAttenuationDb(output: Output): Double {
        val am = getSystemService(AudioManager::class.java)
        val db = am.getStreamVolumeDb(AudioManager.STREAM_MUSIC, am.getStreamVolume(AudioManager.STREAM_MUSIC), output.type)
        // muted (minus infinity) or unknown: claim no headroom
        return if (db.isNaN() || db.isInfinite()) 0.0 else max(0.0, -db.toDouble())
    }

    private fun build(profile: Profile, precision: Precision): DynamicsProcessing {
        val bands = designBands(profile, precision)
        val eq = DynamicsProcessing.Eq(true, true, bands.cutoffs.size)
        for (i in bands.cutoffs.indices) {
            eq.setBand(i, DynamicsProcessing.EqBand(true, bands.cutoffs[i], bands.gains[i]))
        }
        // Only the pre-equalizer stage is used: no multi-band compressor, no
        // post-equalizer and no limiter, so nothing but the EQ curve is applied.
        val config = DynamicsProcessing.Config.Builder(
            DynamicsProcessing.VARIANT_FAVOR_FREQUENCY_RESOLUTION, 2,
            true, bands.cutoffs.size, false, 0, false, 0, false
        ).setPreferredFrameDuration(precision.frameMs).setPreEqAllChannelsTo(eq).build()
        val dp = DynamicsProcessing(Int.MAX_VALUE, 0, config)
        dp.setInputGainAllChannelsTo(0f)
        makeUpDb = 0.0
        dp.enabled = true
        Log.i(TAG, "equalizer on: ${profile.name}, ${bands.cutoffs.size} bands, frame ${precision.frameMs} ms")
        return dp
    }

    private fun release() {
        try {
            effect?.enabled = false
            effect?.release()
        } catch (e: Exception) {
            Log.w(TAG, "release failed", e)
        }
        effect = null
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_eq)
            .setContentTitle("Diamond EQ")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    /** What the service is doing, for the app screen. */
    class Status(val output: Output?, val profile: Profile?, val message: String, val makeUpDb: Double = 0.0)

    companion object {
        private const val TAG = "DiamondEQ"
        private const val CHANNEL = "eq"
        const val ACTION_STATUS = "dev.diamondeq.STATUS"

        @Volatile
        var status = Status(null, null, "Equalizer off")
            private set

        /** Start, refresh or stop the service to match the on/off switch. */
        fun sync(context: Context) {
            val intent = Intent(context, EqService::class.java)
            if (Store(context).enabled) context.startForegroundService(intent) else context.stopService(intent)
        }
    }
}

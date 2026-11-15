package dev.diamondeq

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.format.DateUtils
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var enabled: Switch
    private lateinit var outputName: TextView
    private lateinit var chooser: Button
    private lateinit var curve: CurveView
    private lateinit var statusText: TextView
    private lateinit var syncInfo: TextView
    private lateinit var syncButton: Button

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) = render()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        setContentView(buildUi())
        requestNeededPermissions()
        EqService.sync(this)
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter(EqService.ACTION_STATUS)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        else registerReceiver(statusReceiver, filter)
        render()
        syncWithPc(quiet = store.lastSync != 0L) // pick up new profiles whenever the app is opened
    }

    override fun onStop() {
        unregisterReceiver(statusReceiver)
        super.onStop()
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        EqService.sync(this) // the Bluetooth permission changes how devices are identified
    }

    private fun requestNeededPermissions() {
        val wanted = buildList {
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
            if (Build.VERSION.SDK_INT >= 31) add(Manifest.permission.BLUETOOTH_CONNECT)
        }.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (wanted.isNotEmpty()) requestPermissions(wanted.toTypedArray(), 1)
    }

    // ---- screen -----------------------------------------------------------------
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        textSize = 12f
        letterSpacing = 0.08f
        alpha = 0.6f
        setPadding(0, dp(22), 0, dp(4))
    }

    private fun buildUi(): View {
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), dp(32))
        }
        enabled = Switch(this).apply {
            text = "Equalizer"
            textSize = 20f
            isChecked = store.enabled
            setOnCheckedChangeListener { _, on ->
                store.enabled = on
                EqService.sync(this@MainActivity)
                render()
            }
        }
        outputName = TextView(this).apply { textSize = 20f }
        chooser = Button(this).apply { isAllCaps = false; setOnClickListener { chooseProfile() } }
        curve = CurveView(this)
        statusText = TextView(this).apply { alpha = 0.7f; setPadding(0, dp(6), 0, 0) }
        val precision = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item,
                Precision.entries.map { it.label })
            setSelection(store.precision.ordinal)
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (store.precision.ordinal == position) return
                    store.precision = Precision.entries[position]
                    EqService.sync(this@MainActivity)
                }
                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
        val keepLoudness = Switch(this).apply {
            text = "Keep the loudness"
            isChecked = store.keepLoudness
            setOnCheckedChangeListener { _, on ->
                store.keepLoudness = on
                EqService.sync(this@MainActivity)
            }
        }
        val keepLoudnessHelp = TextView(this).apply {
            text = "EQ lowers the level so boosted notes cannot clip. On wired and USB outputs this adds it back " +
                "from the part of the volume range you are not using. Close to full volume it fades out again."
            alpha = 0.7f
            textSize = 13f
        }
        syncInfo = TextView(this).apply { alpha = 0.7f }
        syncButton = Button(this).apply {
            text = "Sync with PC"
            isAllCaps = false
            setOnClickListener { syncWithPc() }
        }
        val wide = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        column.addView(enabled, wide)
        column.addView(label("PLAYING THROUGH"))
        column.addView(outputName, wide)
        column.addView(chooser, wide)
        column.addView(curve, wide)
        column.addView(statusText, wide)
        column.addView(label("VOLUME"))
        column.addView(keepLoudness, wide)
        column.addView(keepLoudnessHelp, wide)
        column.addView(label("ACCURACY IN THE BASS VS. DELAY"))
        column.addView(precision, wide)
        column.addView(label("PROFILES FROM THE PC"))
        column.addView(syncInfo, wide)
        column.addView(syncButton, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
        return ScrollView(this).apply { addView(column) }
    }

    private fun render() {
        val status = EqService.status
        val output = status.output ?: Output.current(this)
        val chosen = store.profileFor(output)
        outputName.text = output.label
        chooser.text = chosen?.let { "${it.name}  ▾" } ?: "No EQ: choose a profile  ▾"
        curve.profile = chosen
        curve.visibility = if (chosen != null) View.VISIBLE else View.GONE
        statusText.text = when {
            !store.enabled -> "The equalizer is switched off."
            status.profile != null && status.makeUpDb > 0.05 ->
                "Active: %d filters. Loudness kept with %+.1f dB from spare volume headroom.".format(
                    status.profile.filters.size, status.makeUpDb)
            status.profile != null -> "Active: preamp %+.1f dB, %d filters.".format(status.profile.preampDb, status.profile.filters.size)
            else -> status.message
        }
        val count = store.profiles.size
        syncInfo.text = if (store.lastSync == 0L) "Not synced yet." else
            "$count profiles, synced ${DateUtils.getRelativeTimeSpanString(store.lastSync)}."
    }

    private fun chooseProfile() {
        val output = EqService.status.output ?: Output.current(this)
        val profiles = store.profiles
        val names = listOf("No EQ") + profiles.map { if (it.source.isEmpty()) it.name else "${it.name} (${it.source})" }
        AlertDialog.Builder(this)
            .setTitle(output.label)
            .setItems(names.toTypedArray()) { _, which ->
                store.choose(output, if (which == 0) "" else profiles[which - 1].id)
                EqService.sync(this)
                render()
            }
            .show()
    }

    /** [quiet]: a background refresh; an unreachable PC is then not reported. */
    private fun syncWithPc(quiet: Boolean = false) {
        if (!syncButton.isEnabled) return // already running
        syncButton.isEnabled = false
        if (!quiet) syncInfo.text = "Syncing…"
        Thread {
            val before = store.lastSync
            val message = SyncClient.run(store)
            val synced = store.lastSync != before
            runOnUiThread {
                syncButton.isEnabled = true
                if (synced) EqService.sync(this)
                render()
                if (synced || !quiet) syncInfo.text = message
            }
        }.start()
    }
}

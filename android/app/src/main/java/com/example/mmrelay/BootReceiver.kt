package com.example.mmrelay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class BootReceiver : BroadcastReceiver() {

    companion object {
        private const val TAG = "BootReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED ||
            intent.action == "android.intent.action.QUICKBOOT_POWERON") {

            Log.i(TAG, "Device boot completed, checking if MMRelay should auto-start")

            val configManager = AndroidConfigManager(context)
            val prefs = context.getSharedPreferences("mmrelay_prefs", Context.MODE_PRIVATE)

            // Check if auto-start is enabled
            val autoStartEnabled = prefs.getBoolean("auto_start_enabled", false)

            if (autoStartEnabled) {
                Log.i(TAG, "Auto-start enabled, starting MMRelay service")

                // Start the relay service
                val serviceIntent = Intent(context, RelayService::class.java).apply {
                    action = "START"
                }

                // Use startForegroundService for Android O and above
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent)
                } else {
                    context.startService(serviceIntent)
                }

            } else {
                Log.i(TAG, "Auto-start disabled, skipping service start")
            }
        }
    }
}

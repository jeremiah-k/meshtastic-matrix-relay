package com.example.mmrelay

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.*
import android.util.Log
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.*
import java.util.concurrent.atomic.AtomicBoolean

class RelayService : Service() {

    companion object {
        private const val TAG = "RelayService"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "mmrelay_channel"
        private const val ACTION_START = "START"
        private const val ACTION_STOP = "STOP"
    }

    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val isRunning = AtomicBoolean(false)
    private lateinit var configManager: AndroidConfigManager
    private lateinit var notificationManager: NotificationManager

    private var pythonProcess: Process? = null
    private var relayJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        configManager = AndroidConfigManager(this)
        notificationManager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager

        createNotificationChannel()
        Log.i(TAG, "RelayService created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRelay()
            ACTION_STOP -> stopRelay()
            else -> {
                // Default action - start if not already running
                if (!isRunning.get()) {
                    startRelay()
                }
            }
        }

        return START_STICKY
    }

    private fun startRelay() {
        if (isRunning.getAndSet(true)) {
            Log.w(TAG, "Relay already running")
            return
        }

        Log.i(TAG, "Starting MMRelay service")

        // Start foreground service with notification
        startForeground(NOTIFICATION_ID, createNotification("Starting MMRelay..."))

        serviceScope.launch {
            try {
                // Initialize Python and configuration
                if (!configManager.initializePythonConfig()) {
                    throw Exception("Failed to initialize Python configuration")
                }

                // Create default config if it doesn't exist
                if (!configManager.hasConfigFile()) {
                    configManager.createDefaultConfig()
                }

                // Start the relay process
                startRelayProcess()

                // Update notification
                updateNotification("MMRelay is running", "Connected and relaying messages")

            } catch (e: Exception) {
                Log.e(TAG, "Failed to start relay", e)
                updateNotification("MMRelay Error", "Failed to start: ${e.message}")
                stopSelf()
            }
        }
    }

    private fun startRelayProcess() {
        relayJob = serviceScope.launch {
            try {
                // Initialize Python platform if needed
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this@RelayService))
                }

                val py = Python.getInstance()
                val mainModule = py.getModule("mmrelay.main")

                // Run the main MMRelay function in a coroutine
                withContext(Dispatchers.IO) {
                    try {
                        // This will block until MMRelay stops
                        mainModule.callAttr("main")
                    } catch (e: Exception) {
                        Log.e(TAG, "MMRelay main function failed", e)
                        throw e
                    }
                }

            } catch (e: Exception) {
                Log.e(TAG, "Failed to start relay process", e)
                isRunning.set(false)
                updateNotification("MMRelay Stopped", "Error: ${e.message}")
                stopSelf()
            }
        }
    }

    private fun stopRelay() {
        Log.i(TAG, "Stopping MMRelay service")

        isRunning.set(false)

        // Cancel the relay job
        relayJob?.cancel()
        relayJob = null

        // Terminate Python process if running
        pythonProcess?.destroy()
        pythonProcess = null

        updateNotification("MMRelay Stopped", "Service stopped by user")

        // Stop foreground service
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }

        stopSelf()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "MMRelay Service",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "MMRelay background service notifications"
                setShowBadge(false)
            }

            notificationManager.createNotificationChannel(channel)
        }
    }

    private fun createNotification(title: String, content: String): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        }

        val pendingIntent = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val stopIntent = Intent(this, RelayService::class.java).apply {
            action = ACTION_STOP
        }

        val stopPendingIntent = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(content)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pendingIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun updateNotification(title: String, content: String) {
        val notification = createNotification(title, content)
        notificationManager.notify(NOTIFICATION_ID, notification)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        Log.i(TAG, "RelayService destroyed")

        // Clean up
        serviceScope.cancel()
        relayJob?.cancel()
        pythonProcess?.destroy()

        isRunning.set(false)
    }
}

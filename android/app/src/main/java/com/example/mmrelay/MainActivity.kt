package com.example.mmrelay

import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import android.content.Intent
import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
    }

    private lateinit var configManager: AndroidConfigManager
    private lateinit var statusTextView: TextView
    private lateinit var startButton: Button
    private lateinit var stopButton: Button
    private lateinit var configButton: Button

    private var relayService: RelayService? = null
    private var isBound = false

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            Log.i(TAG, "Service connected")
            isBound = true
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            Log.i(TAG, "Service disconnected")
            relayService = null
            isBound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Initialize components
        configManager = AndroidConfigManager(this)
        statusTextView = findViewById(R.id.status_text)
        startButton = findViewById(R.id.start_button)
        stopButton = findViewById(R.id.stop_button)
        configButton = findViewById(R.id.config_button)

        // Initialize Python if needed
        initializePython()

        // Setup UI
        setupUI()

        // Bind to service if running
        bindToRelayService()

        updateStatus()
    }

    private fun initializePython() {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
                Log.i(TAG, "Python platform started")
            }

            // Initialize Android-specific configuration
            if (!configManager.initializePythonConfig()) {
                Log.w(TAG, "Failed to initialize Python config")
            }

        } catch (e: Exception) {
            Log.e(TAG, "Failed to initialize Python", e)
            Toast.makeText(this, "Failed to initialize Python: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun setupUI() {
        startButton.setOnClickListener {
            startRelayService()
        }

        stopButton.setOnClickListener {
            stopRelayService()
        }

        configButton.setOnClickListener {
            openConfiguration()
        }
    }

    private fun startRelayService() {
        try {
            val intent = Intent(this, RelayService::class.java).apply {
                action = "START"
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }

            Toast.makeText(this, "Starting MMRelay service...", Toast.LENGTH_SHORT).show()
            updateStatus()

        } catch (e: Exception) {
            Log.e(TAG, "Failed to start service", e)
            Toast.makeText(this, "Failed to start service: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun stopRelayService() {
        try {
            val intent = Intent(this, RelayService::class.java).apply {
                action = "STOP"
            }
            startService(intent)

            Toast.makeText(this, "Stopping MMRelay service...", Toast.LENGTH_SHORT).show()
            updateStatus()

        } catch (e: Exception) {
            Log.e(TAG, "Failed to stop service", e)
            Toast.makeText(this, "Failed to stop service: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun openConfiguration() {
        val intent = Intent(this, ConfigurationActivity::class.java)
        startActivity(intent)
    }

    private fun bindToRelayService() {
        val intent = Intent(this, RelayService::class.java)
        bindService(intent, serviceConnection, BIND_AUTO_CREATE)
    }

    private fun updateStatus() {
        val status = if (isServiceRunning()) {
            "MMRelay Service: RUNNING"
        } else {
            "MMRelay Service: STOPPED"
        }

        statusTextView.text = status

        // Update button states
        startButton.isEnabled = !isServiceRunning()
        stopButton.isEnabled = isServiceRunning()
    }

    private fun isServiceRunning(): Boolean {
        // Check if service is bound and running
        return isBound && relayService != null
    }

    override fun onResume() {
        super.onResume()
        updateStatus()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isBound) {
            unbindService(serviceConnection)
            isBound = false
        }
    }
}

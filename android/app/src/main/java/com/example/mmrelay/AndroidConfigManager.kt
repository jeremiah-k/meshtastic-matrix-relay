package com.example.mmrelay

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

class AndroidConfigManager(private val context: Context) {

    companion object {
        private const val TAG = "AndroidConfigManager"
        private const val PREFS_NAME = "mmrelay_prefs"
        private const val CONFIG_FILE_NAME = "config.yaml"
    }

    private val prefs: SharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    // Get Android-appropriate paths
    val configDir: File by lazy {
        File(context.filesDir, "config").apply {
            if (!exists()) mkdirs()
        }
    }

    val logDir: File by lazy {
        File(context.filesDir, "logs").apply {
            if (!exists()) mkdirs()
        }
    }

    val dataDir: File by lazy {
        File(context.filesDir, "data").apply {
            if (!exists()) mkdirs()
        }
    }

    val configFile: File by lazy {
        File(configDir, CONFIG_FILE_NAME)
    }

    // Initialize Python configuration with Android paths
    fun initializePythonConfig(): Boolean {
        return try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(context))
            }

            val py = Python.getInstance()
            val configModule = py.getModule("mmrelay.android")

            // Set Android-specific paths
            configModule.callAttr("set_android_paths",
                configDir.absolutePath,
                logDir.absolutePath,
                dataDir.absolutePath
            )

            Log.i(TAG, "Python configuration initialized with Android paths")
            true
        } catch (e: RuntimeException) {
            Log.e(TAG, "Failed to initialize Python config", e)
            false
        }
    }

    // Save configuration to Android SharedPreferences (for quick access)
    fun saveQuickConfig(matrixHomeserver: String, matrixUserId: String, meshtasticDevice: String) {
        prefs.edit().apply {
            putString("matrix_homeserver", matrixHomeserver)
            putString("matrix_user_id", matrixUserId)
            putString("meshtastic_device", meshtasticDevice)
            apply()
        }
    }

    // Load configuration from SharedPreferences
    fun loadQuickConfig(): Triple<String?, String?, String?> {
        return Triple(
            prefs.getString("matrix_homeserver", null),
            prefs.getString("matrix_user_id", null),
            prefs.getString("meshtastic_device", null)
        )
    }

    // Check if configuration file exists
    fun hasConfigFile(): Boolean = configFile.exists()

    // Get configuration file path for Python
    fun getConfigFilePath(): String = configFile.absolutePath

    // Create default configuration file
    fun createDefaultConfig(): Boolean {
        return try {
            val defaultConfig = """
                matrix:
                  homeserver: "https://matrix.org"
                  access_token: ""  # Will be set by user
                  bot_user_id: ""   # Will be set by user
                  password: ""      # Will be set by user

                meshtastic:
                  connection_type: "serial"  # or "tcp", "ble"
                  host: ""          # For TCP connection
                  device: ""        # For serial connection
                  meshnet_name: "MMRelay"

                matrix_rooms: []
                logging:
                  level: "INFO"
                  file: "mmrelay.log"

                plugins:
                  active: ["help", "ping", "nodes"]

            """.trimIndent()

            configFile.writeText(defaultConfig)
            Log.i(TAG, "Default configuration created at ${configFile.absolutePath}")
            true
        } catch (e: RuntimeException) {
            Log.e(TAG, "Failed to create default config", e)
            false
        }
    }

    // Get external storage directory for user-accessible files
    fun getExternalConfigDir(): File? {
        return try {
            val externalDir = context.getExternalFilesDir("config")
            externalDir?.apply {
                if (!exists()) mkdirs()
            }
        } catch (e: RuntimeException) {
            Log.w(TAG, "External storage not available", e)
            null
        }
    }
}

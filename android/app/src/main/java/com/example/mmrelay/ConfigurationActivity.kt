package com.example.mmrelay

import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.Toast
import android.util.Log

class ConfigurationActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "ConfigurationActivity"
    }

    private lateinit var configManager: AndroidConfigManager

    // UI elements
    private lateinit var matrixHomeserverEdit: EditText
    private lateinit var matrixUserIdEdit: EditText
    private lateinit var matrixPasswordEdit: EditText
    private lateinit var matrixAccessTokenEdit: EditText
    private lateinit var meshtasticConnectionTypeSpinner: Spinner
    private lateinit var meshtasticDeviceEdit: EditText
    private lateinit var meshtasticHostEdit: EditText
    private lateinit var saveButton: Button
    private lateinit var testButton: Button
    private lateinit var backButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_configuration)

        configManager = AndroidConfigManager(this)

        // Initialize UI elements
        initializeViews()

        // Load existing configuration
        loadConfiguration()

        // Setup event listeners
        setupListeners()
    }

    private fun initializeViews() {
        matrixHomeserverEdit = findViewById(R.id.matrix_homeserver_edit)
        matrixUserIdEdit = findViewById(R.id.matrix_user_id_edit)
        matrixPasswordEdit = findViewById(R.id.matrix_password_edit)
        matrixAccessTokenEdit = findViewById(R.id.matrix_access_token_edit)
        meshtasticConnectionTypeSpinner = findViewById(R.id.meshtastic_connection_type_spinner)
        meshtasticDeviceEdit = findViewById(R.id.meshtastic_device_edit)
        meshtasticHostEdit = findViewById(R.id.meshtastic_host_edit)
        saveButton = findViewById(R.id.save_button)
        testButton = findViewById(R.id.test_button)
        backButton = findViewById(R.id.back_button)

        // Setup connection type spinner
        val connectionTypes = arrayOf("serial", "tcp", "ble")
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, connectionTypes)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        meshtasticConnectionTypeSpinner.adapter = adapter
    }

    private fun loadConfiguration() {
        try {
            // Load quick config from SharedPreferences
            val (homeserver, userId, device) = configManager.loadQuickConfig()

            matrixHomeserverEdit.setText(homeserver ?: "https://matrix.org")
            matrixUserIdEdit.setText(userId ?: "")
            meshtasticDeviceEdit.setText(device ?: "")

            // NOTE: Full configuration loading from YAML file to be implemented
            Log.i(TAG, "Configuration loaded")

        } catch (e: RuntimeException) {
            Log.e(TAG, "Failed to load configuration", e)
            Toast.makeText(this, "Failed to load configuration", Toast.LENGTH_SHORT).show()
        }
    }

    private fun setupListeners() {
        saveButton.setOnClickListener {
            saveConfiguration()
        }

        testButton.setOnClickListener {
            testConfiguration()
        }

        backButton.setOnClickListener {
            finish()
        }

        // Update UI based on connection type selection
        meshtasticConnectionTypeSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                updateConnectionTypeUI()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {
                // No action needed when nothing is selected
            }
        }
    }

    private fun updateConnectionTypeUI() {
        val connectionType = meshtasticConnectionTypeSpinner.selectedItem as String

        when (connectionType) {
            "serial" -> {
                meshtasticDeviceEdit.isEnabled = true
                meshtasticHostEdit.isEnabled = false
                meshtasticHostEdit.setText("")
            }
            "tcp" -> {
                meshtasticDeviceEdit.isEnabled = false
                meshtasticDeviceEdit.setText("")
                meshtasticHostEdit.isEnabled = true
            }
            "ble" -> {
                meshtasticDeviceEdit.isEnabled = false
                meshtasticDeviceEdit.setText("")
                meshtasticHostEdit.isEnabled = false
                meshtasticHostEdit.setText("")
            }
        }
    }

    private fun saveConfiguration() {
        try {
            val homeserver = matrixHomeserverEdit.text.toString()
            val userId = matrixUserIdEdit.text.toString()
            val device = meshtasticDeviceEdit.text.toString()

            // Save to SharedPreferences for quick access
            configManager.saveQuickConfig(homeserver, userId, device)

            // NOTE: Full configuration saving to YAML file to be implemented

            Toast.makeText(this, "Configuration saved", Toast.LENGTH_SHORT).show()
            Log.i(TAG, "Configuration saved")

        } catch (e: RuntimeException) {
            Log.e(TAG, "Failed to save configuration", e)
            Toast.makeText(this, "Failed to save configuration", Toast.LENGTH_LONG).show()
        }
    }

    private fun testConfiguration() {
        // NOTE: Configuration testing to be implemented
        Toast.makeText(this, "Configuration testing not yet implemented", Toast.LENGTH_SHORT).show()
    }

    override fun onResume() {
        super.onResume()
        updateConnectionTypeUI()
    }
}

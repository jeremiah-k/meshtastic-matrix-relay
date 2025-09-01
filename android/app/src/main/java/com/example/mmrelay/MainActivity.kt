package com.example.mmrelay

import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val textViewResult = findViewById<TextView>(R.id.text_view_result)
        val buttonCallPython = findViewById<Button>(R.id.button_call_python)

        buttonCallPython.setOnClickListener {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
            }
            val py = Python.getInstance()
            val helloModule = py.getModule("hello")
            val greeting = helloModule.callAttr("greet", "world").toString()
            textViewResult.text = greeting
        }
    }
}

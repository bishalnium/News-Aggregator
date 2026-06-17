package com.example.newsbuddy

import android.os.Build
import android.os.Bundle
import android.util.Log
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.google.firebase.messaging.FirebaseMessaging
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : ComponentActivity() {
    private var webView: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // Request Push Notification permission for Android 13+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 101)
            }
        }

        // Fetch FCM Token and register with backend
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                val token = task.result
                Log.d("FCM", "Current token: $token")
                registerTokenWithBackend(token)
            } else {
                Log.w("FCM", "Fetching FCM registration token failed", task.exception)
            }
        }

        setContent {
            Surface(
                modifier = Modifier
                    .fillMaxSize()
                    .statusBarsPadding()
            ) {
                AndroidView(
                    factory = { context ->
                        WebView(context).apply {
                            webView = this
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.userAgentString = settings.userAgentString + " NewsBuddyAndroid"
                            
                            webViewClient = WebViewClient()
                            webChromeClient = WebChromeClient()

                            val websiteUrl = context.getString(R.string.website_url)
                            val bypassToken = context.getString(R.string.bypass_token)
                            val fullUrl = "$websiteUrl?bypass=$bypassToken"
                            
                            Log.d("WebView", "Loading URL: $fullUrl")
                            loadUrl(fullUrl)
                        }
                    },
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }

    private fun registerTokenWithBackend(token: String) {
        val backendUrl = getString(R.string.backend_api_url) + "/settings/register-fcm-token"
        Thread {
            try {
                val url = URL(backendUrl)
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                
                val json = "{\"fcm_token\":\"$token\",\"device_name\":\"${Build.MODEL}\"}"
                conn.outputStream.use { os ->
                    val input = json.toByteArray(charset("utf-8"))
                    os.write(input, 0, input.size)
                }
                
                val code = conn.responseCode
                Log.d("FCM", "Backend register response code: $code")
                if (code == 200) {
                    getSharedPreferences("newsbuddy_prefs", MODE_PRIVATE)
                        .edit()
                        .putBoolean("fcm_token_synced", true)
                        .apply()
                }
            } catch (e: Exception) {
                Log.e("FCM", "Failed to register FCM token with backend", e)
            }
        }.start()
    }

    override fun onBackPressed() {
        if (webView?.canGoBack() == true) {
            webView?.goBack()
        } else {
            super.onBackPressed()
        }
    }
}

package com.example.newsbuddy

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

class NewsCodexFCMService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d("FCM", "New token generated: $token")
        val sharedPrefs = getSharedPreferences("newsbuddy_prefs", Context.MODE_PRIVATE)
        sharedPrefs.edit().putString("fcm_token", token).putBoolean("fcm_token_synced", false).apply()
    }

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        Log.d("FCM", "Message received from: ${remoteMessage.from}")

        val title = remoteMessage.notification?.title ?: remoteMessage.data["title"] ?: "NewsBuddy Alert"
        val body = remoteMessage.notification?.body ?: remoteMessage.data["body"] ?: "New alert received."
        val alertType = remoteMessage.data["alert_type"] ?: "keyword"

        sendNotification(title, body, alertType)
    }

    private fun sendNotification(title: String, body: String, alertType: String) {
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        
        val channelId = if (alertType == "context") "context_alerts" else "keyword_alerts"
        val channelName = if (alertType == "context") "Context Alerts" else "Keyword Alerts"
        val soundResName = if (alertType == "context") "context_alert" else "keyword_alert"
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // Check if channel already exists
            var channel = notificationManager.getNotificationChannel(channelId)
            if (channel == null) {
                val soundUri = Uri.parse("android.resource://$packageName/raw/$soundResName")
                val audioAttributes = AudioAttributes.Builder()
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .build()
                
                channel = NotificationChannel(
                    channelId,
                    channelName,
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Notifications for $channelName"
                    setSound(soundUri, audioAttributes)
                    enableLights(true)
                    enableVibration(true)
                }
                notificationManager.createNotificationChannel(channel)
            }
        }

        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this, 
            0, 
            intent, 
            PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_IMMUTABLE
        )

        val soundUri = Uri.parse("android.resource://$packageName/raw/$soundResName")
        
        val notificationBuilder = NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setSound(soundUri)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent)

        val notificationId = System.currentTimeMillis().toInt()
        notificationManager.notify(notificationId, notificationBuilder.build())
    }
}

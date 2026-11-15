package com.clkyigit.robot

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Uygulama arka plandayken robot kendiliğinden bir şey yazarsa bildirim gösterir. */
object Notifier {
    private const val CHANNEL = "robot"
    private var nextId = 1000

    /** MainActivity günceller: uygulama ekrandayken bildirim göstermeye gerek yok */
    @Volatile var inForeground = false

    fun setup(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL, "Robotun mesajları", NotificationManager.IMPORTANCE_DEFAULT)
            ch.description = "Robot kendiliğinden yazdığında ya da sesli konuşmanıza cevap verdiğinde"
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }
    }

    fun show(context: Context, title: String, text: String) {
        if (inForeground) return
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_robot)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(context).notify(nextId++, n) }
    }
}

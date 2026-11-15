package com.clkyigit.robot

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

data class Msg(val mine: Boolean, val text: String, val photos: List<Bitmap> = emptyList())

class RobotViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = app.getSharedPreferences("robot", Context.MODE_PRIVATE)

    var url by mutableStateOf(prefs.getString("url", "").orEmpty())
        private set
    var token by mutableStateOf(prefs.getString("token", "").orEmpty())
        private set

    val messages = mutableStateListOf<Msg>()
    var thinking by mutableStateOf(false)
        private set
    var error by mutableStateOf<String?>(null)
    var status by mutableStateOf<RobotApi.Status?>(null)
        private set
    var connected by mutableStateOf(false)
        private set

    var frame by mutableStateOf<Bitmap?>(null)
        private set
    var streaming by mutableStateOf(false)
        private set
    var looking by mutableStateOf(false)
        private set
    var facts by mutableStateOf<List<String>>(emptyList())
        private set
    var testResults by mutableStateOf<List<RobotApi.ModelHealth>>(emptyList())
        private set
    var testing by mutableStateOf(false)
        private set

    private var streamJob: Job? = null
    private var cachedApi: RobotApi? = null
    private var cachedKey = ""

    val configured: Boolean get() = url.isNotBlank() && token.isNotBlank()

    private fun api(): RobotApi {
        val key = "$url|$token"
        if (cachedApi == null || key != cachedKey) {
            cachedApi = RobotApi(url, token)
            cachedKey = key
        }
        return cachedApi!!
    }

    var selfInfo by mutableStateOf<RobotApi.SelfInfo?>(null)
        private set
    var loadingSelf by mutableStateOf(false)
        private set
    var writingDiary by mutableStateOf(false)
        private set

    init {
        Notifier.setup(app)
        viewModelScope.launch {
            while (true) {
                if (configured) refreshStatus()
                delay(15_000)
            }
        }
        // Robotun kendiliğinden yazdıklarını ve sesli konuşmaları sohbete getir
        viewModelScope.launch {
            while (true) {
                if (configured) pollEvents()
                delay(5_000)
            }
        }
    }

    private var firstPoll = true

    private suspend fun pollEvents() {
        val since = prefs.getInt("event_id", 0)
        val boot = prefs.getString("event_boot", "").orEmpty()
        val r = try {
            api().events(since, boot)
        } catch (e: Exception) {
            return
        }
        // İlk açılışta eski olayları sel gibi doldurma: son birkaç tanesi yeter
        val fresh = if (firstPoll && boot.isEmpty()) r.events.takeLast(5) else r.events
        firstPoll = false
        for (e in fresh) {
            val photos = e.photoPaths.mapNotNull { runCatching { api().bitmap(it) }.getOrNull() }
            when (e.kind) {
                "ses" -> {
                    messages.add(Msg(true, "🎤 ${e.heard}"))
                    messages.add(Msg(false, e.text, photos))
                    Notifier.show(getApplication<Application>(), "🎤 ${e.heard}", e.text)
                }
                "oto" -> {
                    messages.add(Msg(false, "💭 ${e.text}", photos))
                    Notifier.show(getApplication<Application>(), "Robot sana yazdı", e.text)
                }
                else -> {
                    messages.add(Msg(false, e.text, photos))
                    Notifier.show(getApplication<Application>(), "Robot", e.text)
                }
            }
        }
        val last = r.events.lastOrNull()?.id ?: if (r.boot != boot) 0 else since
        prefs.edit().putInt("event_id", last).putString("event_boot", r.boot).apply()
    }

    fun loadSelf() {
        if (loadingSelf) return
        loadingSelf = true
        viewModelScope.launch {
            try {
                selfInfo = api().selfInfo()
            } catch (e: Exception) {
                error = e.message
            } finally {
                loadingSelf = false
            }
        }
    }

    fun writeDiary() {
        if (writingDiary) return
        writingDiary = true
        viewModelScope.launch {
            try {
                api().writeDiary()
                selfInfo = api().selfInfo()
            } catch (e: Exception) {
                error = e.message
            } finally {
                writingDiary = false
            }
        }
    }

    fun setMic(on: Boolean) = viewModelScope.launch {
        try { api().setMic(on); refreshStatus() } catch (e: Exception) { error = e.message }
    }

    fun setInitiative(on: Boolean) = viewModelScope.launch {
        try { api().setInitiative(enabled = on); refreshStatus() } catch (e: Exception) { error = e.message }
    }

    fun setPresence(on: Boolean) = viewModelScope.launch {
        try { api().setInitiative(presence = on); refreshStatus() } catch (e: Exception) { error = e.message }
    }

    /** Ayarları doğrular ve kaydeder. Hata varsa mesajını döner. */
    fun saveSettings(newUrl: String, newToken: String): String? {
        val u = newUrl.trim().trimEnd('/')
        val t = newToken.trim()
        if (!u.startsWith("http://") && !u.startsWith("https://")) return "Adres http:// ile başlamalı"
        if (t.isEmpty()) return "Anahtarı gir"
        url = u
        token = t
        prefs.edit().putString("url", u).putString("token", t).apply()
        viewModelScope.launch { refreshStatus() }
        return null
    }

    suspend fun refreshStatus() {
        try {
            status = api().status()
            connected = true
        } catch (e: Exception) {
            connected = false
            status = null
        }
    }

    /** Gönderilmeyi bekleyen fotoğraf (sohbet kutusunun üstünde önizlenir) */
    var pendingPhoto by mutableStateOf<Bitmap?>(null)

    fun attach(photo: Bitmap?) {
        if (photo == null) {
            error = "Fotoğraf açılamadı"
            return
        }
        error = null
        pendingPhoto = photo
    }

    fun send(text: String): Boolean {
        val t = text.trim()
        val photo = pendingPhoto
        if (t.isEmpty() && photo == null) {
            error = "Önce bir mesaj yaz ya da fotoğraf ekle"
            return false
        }
        if (thinking) return false
        error = null
        pendingPhoto = null
        messages.add(Msg(true, t, listOfNotNull(photo)))
        thinking = true
        viewModelScope.launch {
            try {
                val r = if (photo != null) api().chatPhoto(photo, t) else api().chat(t)
                messages.add(Msg(false, r.text, r.photos))
            } catch (e: Exception) {
                error = e.message ?: "Bağlantı hatası"
            } finally {
                thinking = false
            }
        }
        return true
    }

    fun look() {
        if (looking) return
        looking = true
        error = null
        viewModelScope.launch {
            try {
                val r = api().look()
                messages.add(Msg(false, r.text, r.photos))
            } catch (e: Exception) {
                error = e.message ?: "Bağlantı hatası"
            } finally {
                looking = false
            }
        }
    }

    fun toggleCamera() {
        val target = !(status?.cameraEnabled ?: true)
        viewModelScope.launch {
            try {
                api().setCamera(target)
                if (!target) stopStream()
                refreshStatus()
            } catch (e: Exception) {
                error = e.message
            }
        }
    }

    fun startStream() {
        if (streaming) return
        streaming = true
        error = null
        streamJob = viewModelScope.launch {
            try {
                api().frames().collect { frame = it }
            } catch (e: Exception) {
                error = e.message ?: "Görüntü kesildi"
            } finally {
                streaming = false
            }
        }
    }

    fun stopStream() {
        streamJob?.cancel()
        streamJob = null
        streaming = false
    }

    fun runSelfTest() {
        if (testing) return
        testing = true
        viewModelScope.launch {
            try {
                testResults = api().selfTest()
                refreshStatus()
            } catch (e: Exception) {
                error = e.message
            } finally {
                testing = false
            }
        }
    }

    fun loadFacts() {
        viewModelScope.launch {
            try {
                facts = api().facts().ifEmpty { listOf("Henüz kalıcı bir şey hatırlamıyor.") }
            } catch (e: Exception) {
                error = e.message
            }
        }
    }

    fun clearHistory() {
        viewModelScope.launch {
            try {
                api().clearHistory()
                messages.clear()
            } catch (e: Exception) {
                error = e.message
            }
        }
    }
}

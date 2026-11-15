package com.clkyigit.robot

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Pi'deki robot sunucusuyla konuşur. API anahtarları Pi'de kalır; uygulama sadece API_TOKEN bilir. */
class RobotApi(baseUrl: String, private val token: String) {

    private val base = baseUrl.trim().trimEnd('/')
    private val json = "application/json; charset=utf-8".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)   // robot düşünürken bekleyebilsin
        .build()

    private val streamClient = client.newBuilder()
        .readTimeout(0, TimeUnit.SECONDS)     // canlı görüntü sonsuz akar
        .build()

    private fun request(path: String): Request.Builder =
        Request.Builder().url(base + path).header("Authorization", "Bearer $token")

    private fun Response.ensureOk() {
        if (isSuccessful) return
        val detail = runCatching { JSONObject(body?.string().orEmpty()).optString("detail") }.getOrNull()
        throw IOException(
            when (code) {
                401 -> "Anahtar geçersiz (Ayarlar'dan kontrol et)"
                else -> detail?.takeIf { it.isNotBlank() } ?: "Hata: HTTP $code"
            }
        )
    }

    private suspend fun getJson(path: String): JSONObject = withContext(Dispatchers.IO) {
        client.newCall(request(path).get().build()).execute().use {
            it.ensureOk()
            JSONObject(it.body!!.string())
        }
    }

    private suspend fun postJson(path: String, body: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        client.newCall(request(path).post(body.toString().toRequestBody(json)).build()).execute().use {
            it.ensureOk()
            JSONObject(it.body!!.string())
        }
    }

    suspend fun bitmap(path: String): Bitmap? = withContext(Dispatchers.IO) {
        client.newCall(request(path).get().build()).execute().use {
            it.ensureOk()
            val bytes = it.body!!.bytes()
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }
    }

    private fun parseModels(models: JSONObject): List<ModelHealth> =
        models.keys().asSequence().map { name ->
            val m = models.getJSONObject(name)
            ModelHealth(
                name = name,
                model = m.optString("model"),
                active = m.optBoolean("active"),
                ok = if (m.isNull("ok")) null else m.optBoolean("ok"),
                error = m.optString("error"),
                ms = if (m.isNull("ms")) null else m.optInt("ms"),
            )
        }.toList()

    suspend fun status(): Status {
        val s = getJson("/api/status")
        val cam = s.getJSONObject("camera")
        val ears = s.optJSONObject("ears")
        val oto = s.optJSONObject("initiative")
        return Status(
            models = parseModels(s.getJSONObject("models")),
            cpuTemp = if (s.isNull("cpu_temp")) null else s.getDouble("cpu_temp"),
            cameraEnabled = cam.optBoolean("enabled", true),
            earsEnabled = ears?.optBoolean("enabled"),
            earsRunning = ears?.optBoolean("running") ?: false,
            earsError = ears?.optString("error").orEmpty(),
            wakeWord = ears?.optJSONArray("wake_words")?.optString(0).orEmpty(),
            initiativeEnabled = oto?.optBoolean("enabled"),
            presenceEnabled = oto?.optBoolean("presence"),
        )
    }

    /** Robotun kendiliğinden yazdıkları, sesli konuşmalar, uyarılar, günlükler. */
    suspend fun events(since: Int, boot: String): Events {
        val r = getJson("/api/events?since=$since&boot=$boot")
        val arr = r.getJSONArray("events")
        val list = (0 until arr.length()).map { i ->
            val e = arr.getJSONObject(i)
            val ph = e.optJSONArray("photos")
            Event(
                id = e.getInt("id"), kind = e.optString("kind"), text = e.optString("text"),
                heard = e.optString("heard"),
                photoPaths = if (ph == null) emptyList() else (0 until ph.length()).map { ph.getString(it) },
            )
        }
        return Events(r.getString("boot"), list, r.optInt("last"))
    }

    suspend fun selfInfo(): SelfInfo {
        val r = getJson("/api/self")
        val d = r.optJSONObject("diary")
        return SelfInfo(
            state = r.optString("state"),
            identity = r.optString("identity"),
            diaryDay = d?.optString("day").orEmpty(),
            diary = d?.optString("text").orEmpty(),
        )
    }

    suspend fun writeDiary(): String = postJson("/api/diary/write", JSONObject()).getString("text")

    suspend fun setMic(enabled: Boolean) {
        postJson("/api/privacy", JSONObject().put("mic", enabled))
    }

    suspend fun setInitiative(enabled: Boolean? = null, presence: Boolean? = null) {
        val b = JSONObject()
        enabled?.let { b.put("enabled", it) }
        presence?.let { b.put("presence", it) }
        postJson("/api/initiative", b)
    }

    /** Her modele kısa bir istek gönderip gerçekten çalışıp çalışmadığını ölçer. */
    suspend fun selfTest(): List<ModelHealth> =
        parseModels(postJson("/api/selftest", JSONObject()).getJSONObject("models"))

    /** Robota mesaj gönderir. Robot araç olarak fotoğraf çektiyse onlar da gelir. */
    suspend fun chat(text: String): Reply {
        val r = postJson("/api/chat", JSONObject().put("text", text))
        val paths = r.getJSONArray("photos")
        val photos = (0 until paths.length()).mapNotNull { runCatching { bitmap(paths.getString(it)) }.getOrNull() }
        return Reply(r.getString("reply"), photos)
    }

    /** Fotoğraf gönderir (isteğe bağlı mesajla). Fotoğraf göndermeden önce küçültülmüş olmalı. */
    suspend fun chatPhoto(photo: Bitmap, text: String): Reply {
        val b64 = withContext(Dispatchers.Default) {
            val out = ByteArrayOutputStream()
            photo.compress(Bitmap.CompressFormat.JPEG, 85, out)
            android.util.Base64.encodeToString(out.toByteArray(), android.util.Base64.NO_WRAP)
        }
        val r = postJson("/api/chat_photo", JSONObject().put("image", b64).put("text", text))
        val paths = r.getJSONArray("photos")
        val photos = (0 until paths.length()).mapNotNull { runCatching { bitmap(paths.getString(it)) }.getOrNull() }
        return Reply(r.getString("reply"), photos)
    }

    suspend fun look(question: String = "Ne görüyorsun?"): Reply {
        val r = postJson("/api/look", JSONObject().put("question", question))
        val photo = runCatching { bitmap(r.getString("photo")) }.getOrNull()
        return Reply(r.getString("description"), listOfNotNull(photo))
    }

    suspend fun setCamera(enabled: Boolean): Boolean =
        postJson("/api/privacy", JSONObject().put("camera", enabled)).getBoolean("camera")

    suspend fun facts(): List<String> {
        val arr = getJson("/api/memory").getJSONArray("facts")
        return (0 until arr.length()).map { arr.getString(it) }
    }

    suspend fun clearHistory() = withContext(Dispatchers.IO) {
        client.newCall(request("/api/history").delete().build()).execute().use { it.ensureOk() }
    }

    /** Canlı görüntü: MJPEG akışından JPEG karelerini ayıklar. */
    fun frames(): Flow<Bitmap> = flow {
        streamClient.newCall(request("/api/stream").get().build()).execute().use { resp ->
            resp.ensureOk()
            val input = BufferedInputStream(resp.body!!.byteStream(), 64 * 1024)
            val buf = ByteArrayOutputStream(200 * 1024)
            var prev = -1
            var inJpeg = false
            while (currentCoroutineContext().isActive) {
                val b = input.read()
                if (b < 0) break
                if (!inJpeg) {
                    if (prev == 0xFF && b == 0xD8) {   // JPEG başlangıcı
                        inJpeg = true
                        buf.reset()
                        buf.write(0xFF)
                        buf.write(0xD8)
                    }
                } else {
                    buf.write(b)
                    if (prev == 0xFF && b == 0xD9) {   // JPEG sonu
                        val bytes = buf.toByteArray()
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { emit(it) }
                        inJpeg = false
                    }
                }
                prev = b
            }
        }
    }.flowOn(Dispatchers.IO)

    data class ModelHealth(
        val name: String, val model: String, val active: Boolean,
        val ok: Boolean?, val error: String, val ms: Int?,
    )
    data class Status(
        val models: List<ModelHealth>, val cpuTemp: Double?, val cameraEnabled: Boolean,
        val earsEnabled: Boolean?, val earsRunning: Boolean, val earsError: String, val wakeWord: String,
        val initiativeEnabled: Boolean?, val presenceEnabled: Boolean?,
    )
    data class Event(val id: Int, val kind: String, val text: String, val heard: String, val photoPaths: List<String>)
    data class Events(val boot: String, val events: List<Event>, val last: Int)
    data class SelfInfo(val state: String, val identity: String, val diaryDay: String, val diary: String)
    data class Reply(val text: String, val photos: List<Bitmap>)
}

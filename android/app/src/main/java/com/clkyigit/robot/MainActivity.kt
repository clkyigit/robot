package com.clkyigit.robot

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

private val Accent = Color(0xFF5DCAA5)
private val MineBubble = Color(0xFF2C4A6E)
private val BotBubble = Color(0xFF2A2D36)
private val ErrorColor = Color(0xFFF09595)

class MainActivity : ComponentActivity() {
    override fun onResume() {
        super.onResume()
        Notifier.inForeground = true
    }

    override fun onPause() {
        super.onPause()
        Notifier.inForeground = false
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Robot kendiliğinden yazınca bildirim gelebilsin (Android 13+ izin ister)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Accent,
                    onPrimary = Color(0xFF06281F),
                    background = Color(0xFF14161C),
                    surface = Color(0xFF1E2129),
                )
            ) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    RobotApp()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RobotApp(vm: RobotViewModel = viewModel()) {
    var tab by rememberSaveable { mutableStateOf(if (vm.configured) 0 else 3) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("🤖 Robot") },
                actions = {
                    val s = vm.status
                    Text(
                        when {
                            !vm.configured -> "ayar gerekli"
                            !vm.connected -> "bağlantı yok"
                            else -> buildString {
                                append(s?.models.orEmpty().filter { it.active }
                                    .joinToString(" ") { (if (it.ok == false) "✗" else "") + it.name })
                                s?.cpuTemp?.let { append(" · %.0f°C".format(it)) }
                            }
                        },
                        style = MaterialTheme.typography.labelMedium,
                        color = if (vm.connected) Accent else ErrorColor,
                        modifier = Modifier.padding(end = 12.dp),
                    )
                },
            )
        },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(selected = tab == 0, onClick = { tab = 0 },
                    icon = { Icon(Icons.Filled.Face, null) }, label = { Text("Sohbet") })
                NavigationBarItem(selected = tab == 1, onClick = { tab = 1 },
                    icon = { Icon(Icons.Filled.Visibility, null) }, label = { Text("Göz") })
                NavigationBarItem(selected = tab == 2, onClick = { tab = 2 },
                    icon = { Icon(Icons.Filled.SmartToy, null) }, label = { Text("Robot") })
                NavigationBarItem(selected = tab == 3, onClick = { tab = 3 },
                    icon = { Icon(Icons.Filled.Settings, null) }, label = { Text("Ayarlar") })
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (tab) {
                0 -> ChatScreen(vm)
                1 -> EyeScreen(vm)
                2 -> SelfScreen(vm)
                else -> SettingsScreen(vm, onSaved = { tab = 0 })
            }
        }
    }
}

// ================================================================ Sohbet

@Composable
fun ChatScreen(vm: RobotViewModel) {
    var input by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()

    LaunchedEffect(vm.messages.size, vm.thinking) {
        val count = vm.messages.size + if (vm.thinking) 1 else 0
        if (count > 0) listState.animateScrollToItem(count - 1)
    }

    Column(Modifier.fillMaxSize().imePadding()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (vm.messages.isEmpty() && !vm.thinking) {
                item {
                    Text("Robota bir şey yaz ya da fotoğraf gönder. Etrafına bakmasını da isteyebilirsin. " +
                        "Robotun kendiliğinden yazdıkları (💭) ve ona sesli söylediklerin (🎤) de burada görünür.",
                        color = Color.Gray, modifier = Modifier.padding(top = 24.dp))
                }
            }
            itemsIndexed(vm.messages) { _, m -> Bubble(m) }
            if (vm.thinking) item { Bubble(Msg(false, "…")) }
        }

        vm.error?.let {
            Text(it, color = ErrorColor, style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        }

        val context = LocalContext.current
        val pickPhoto = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
            if (uri != null) vm.attach(loadScaledBitmap(context, uri))
        }
        var cameraUri by remember { mutableStateOf<Uri?>(null) }
        val takePhoto = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
            val u = cameraUri
            if (ok && u != null) vm.attach(loadScaledBitmap(context, u))
        }

        vm.pendingPhoto?.let { photo ->
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Image(photo.asImageBitmap(), "Gönderilecek fotoğraf",
                    Modifier.size(64.dp).clip(RoundedCornerShape(8.dp)), contentScale = ContentScale.Crop)
                Text("Fotoğraf eklendi", color = Color.Gray, modifier = Modifier.padding(start = 8.dp).weight(1f))
                IconButton(onClick = { vm.pendingPhoto = null }) { Icon(Icons.Filled.Close, "Fotoğrafı kaldır") }
            }
        }

        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = {
                pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
            }, enabled = !vm.thinking) {
                Icon(Icons.Filled.Image, "Galeriden fotoğraf", tint = Accent)
            }
            IconButton(onClick = {
                val u = newCameraUri(context)
                cameraUri = u
                takePhoto.launch(u)
            }, enabled = !vm.thinking) {
                Icon(Icons.Filled.PhotoCamera, "Fotoğraf çek", tint = Accent)
            }
            OutlinedTextField(
                value = input,
                onValueChange = { input = it; vm.error = null },
                placeholder = { Text(if (vm.pendingPhoto != null) "Fotoğraf hakkında sor…" else "Robota yaz…") },
                modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (vm.send(input)) input = "" }),
                maxLines = 4,
            )
            IconButton(onClick = { if (vm.send(input)) input = "" }, enabled = !vm.thinking) {
                Icon(Icons.AutoMirrored.Filled.Send, "Gönder", tint = Accent)
            }
        }
    }
}

@Composable
fun Bubble(m: Msg) {
    Box(Modifier.fillMaxWidth(), contentAlignment = if (m.mine) Alignment.CenterEnd else Alignment.CenterStart) {
        Column(
            Modifier
                .widthIn(max = 300.dp)
                .background(if (m.mine) MineBubble else BotBubble, RoundedCornerShape(14.dp))
                .padding(horizontal = 12.dp, vertical = 8.dp)
        ) {
            if (m.text.isNotEmpty()) Text(m.text, color = Color(0xFFE8E8EA))
            m.photos.forEach { bmp ->
                Spacer(Modifier.height(6.dp))
                Image(bmp.asImageBitmap(), null, Modifier.fillMaxWidth(), contentScale = ContentScale.FillWidth)
            }
        }
    }
}

// ================================================================ Göz

@Composable
fun EyeScreen(vm: RobotViewModel) {
    // Sekmeden çıkınca canlı görüntüyü kapat
    DisposableEffect(Unit) { onDispose { vm.stopStream() } }
    val camOn = vm.status?.cameraEnabled ?: true

    Column(Modifier.fillMaxSize().padding(12.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(
            Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(Color.Black, RoundedCornerShape(12.dp)),
            contentAlignment = Alignment.Center,
        ) {
            val f = vm.frame
            if (f != null && vm.streaming) {
                Image(f.asImageBitmap(), "Canlı görüntü", Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
            } else {
                Text(if (camOn) "Canlı görüntüyü başlat" else "Kamera kapalı (gizlilik)", color = Color.Gray)
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { if (vm.streaming) vm.stopStream() else vm.startStream() }, enabled = camOn) {
                Text(if (vm.streaming) "⏸ Durdur" else "▶ Canlı")
            }
            OutlinedButton(onClick = { vm.look() }, enabled = camOn && !vm.looking) {
                Text(if (vm.looking) "Bakıyor…" else "👁 Bak ve anlat")
            }
        }
        OutlinedButton(onClick = { vm.toggleCamera() }, modifier = Modifier.fillMaxWidth()) {
            Text(if (camOn) "🔒 Kamerayı kapat" else "🔓 Kamerayı aç")
        }

        vm.error?.let { Text(it, color = ErrorColor, style = MaterialTheme.typography.bodySmall) }

        // "Bak ve anlat" sonucu sohbete düşer; son cevabı burada da göster
        vm.messages.lastOrNull { !it.mine && it.photos.isNotEmpty() }?.let { Bubble(it) }
    }
}

// ================================================================ Robot (beden, kimlik, günlük, anahtarlar)

@Composable
fun ToggleRow(title: String, note: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (note.isNotEmpty()) Text(note, color = Color.Gray, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled,
            colors = SwitchDefaults.colors(checkedTrackColor = Accent))
    }
}

@Composable
fun SelfScreen(vm: RobotViewModel) {
    LaunchedEffect(vm.connected) { if (vm.connected) vm.loadSelf() }
    val s = vm.status
    val info = vm.selfInfo

    Column(Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (!vm.connected) {
            Text("Robota bağlı değil (Ayarlar).", color = Color.Gray)
            return@Column
        }

        Text("Duyular ve davranış", style = MaterialTheme.typography.titleMedium)
        val earsOn = s?.earsEnabled ?: false
        ToggleRow(
            "👂 Kulaklar",
            when {
                !earsOn -> "Kapalı"
                s?.earsRunning == true -> "Dinliyor — \"${s?.wakeWord.orEmpty().ifEmpty { "robot" }}\" de"
                else -> "Çalışmıyor: ${s?.earsError.orEmpty()}"
            },
            earsOn,
        ) { vm.setMic(it) }
        val camOn = s?.cameraEnabled ?: true
        ToggleRow("👁 Kamera", if (camOn) "Açık" else "Kapalı (gizlilik)", camOn) { vm.toggleCamera() }
        val otoOn = s?.initiativeEnabled ?: false
        ToggleRow("💭 Kendiliğinden davranma",
            "Sabah selamı, uzun sessizlikte halini sorma, biri gelince fark etme", otoOn) { vm.setInitiative(it) }
        ToggleRow("🚶 Hareket algılama", "Kamera dakikada bir bakar (Pi'de, internetsiz)",
            s?.presenceEnabled ?: false, enabled = otoOn && camOn) { vm.setPresence(it) }

        Spacer(Modifier.height(4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Bedeni", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            OutlinedButton(onClick = { vm.loadSelf() }, enabled = !vm.loadingSelf) {
                Text(if (vm.loadingSelf) "…" else "Yenile")
            }
        }
        Text(info?.state?.ifEmpty { "—" } ?: "Yükleniyor…", style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace, color = Color(0xFFCFD3DA))

        Text("Kimliği (kendini nasıl tanıyor)", style = MaterialTheme.typography.titleMedium)
        Text(info?.identity?.ifBlank { null } ?: "Henüz boş. İlk günlüğünü yazınca dolmaya başlar.",
            style = MaterialTheme.typography.bodyMedium)

        Text("Son günlüğü" + (info?.diaryDay?.takeIf { it.isNotEmpty() }?.let { " · $it" } ?: ""),
            style = MaterialTheme.typography.titleMedium)
        Text(info?.diary?.ifBlank { null } ?: "Henüz günlük yazmadı. Her gece 23:30'da yazar.",
            style = MaterialTheme.typography.bodyMedium)
        OutlinedButton(onClick = { vm.writeDiary() }, enabled = !vm.writingDiary, modifier = Modifier.fillMaxWidth()) {
            Text(if (vm.writingDiary) "Yazıyor…" else "📔 Bugünkü günlüğünü şimdi yazsın")
        }

        vm.error?.let { Text(it, color = ErrorColor, style = MaterialTheme.typography.bodySmall) }
    }
}

// ================================================================ Ayarlar

@Composable
fun SettingsScreen(vm: RobotViewModel, onSaved: () -> Unit) {
    var url by rememberSaveable { mutableStateOf(vm.url) }
    var token by rememberSaveable { mutableStateOf(vm.token) }
    var formError by remember { mutableStateOf<String?>(null) }

    Column(Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Bağlantı", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = url, onValueChange = { url = it; formError = null },
            label = { Text("Robot adresi") }, placeholder = { Text("http://192.168.x.x:8080") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = token, onValueChange = { token = it; formError = null },
            label = { Text("Erişim anahtarı (API_TOKEN)") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        formError?.let { Text(it, color = ErrorColor, style = MaterialTheme.typography.bodySmall) }
        Button(onClick = {
            val err = vm.saveSettings(url, token)
            formError = err
            if (err == null) onSaved()
        }, modifier = Modifier.fillMaxWidth()) { Text("Kaydet ve bağlan") }

        Text(
            if (vm.connected) "✅ Bağlı" else if (vm.configured) "❌ Robota ulaşılamıyor" else "Adresi ve anahtarı gir",
            color = if (vm.connected) Accent else Color.Gray,
        )

        Spacer(Modifier.height(8.dp))
        Text("Modeller", style = MaterialTheme.typography.titleMedium)
        OutlinedButton(onClick = { vm.runSelfTest() }, enabled = vm.connected && !vm.testing,
            modifier = Modifier.fillMaxWidth()) {
            Text(if (vm.testing) "Deneniyor…" else "Modelleri test et")
        }
        val shown = vm.testResults.ifEmpty { vm.status?.models.orEmpty() }
        shown.forEach { m ->
            val (icon, note) = when {
                !m.active -> "⚪" to "anahtar yok"
                m.ok == true -> "✅" to (m.ms?.let { "$it ms" } ?: "çalışıyor")
                m.ok == false -> "❌" to m.error
                else -> "⚪" to "henüz denenmedi"
            }
            Text("$icon ${m.name} · ${m.model}\n     $note", style = MaterialTheme.typography.bodyMedium,
                color = if (m.ok == false) ErrorColor else Color(0xFFE8E8EA))
        }

        Spacer(Modifier.height(8.dp))
        Text("Hafıza", style = MaterialTheme.typography.titleMedium)
        OutlinedButton(onClick = { vm.loadFacts() }, enabled = vm.connected, modifier = Modifier.fillMaxWidth()) {
            Text("Robotun hatırladıkları")
        }
        vm.facts.forEach { Text("• $it", style = MaterialTheme.typography.bodyMedium) }
        OutlinedButton(onClick = { vm.clearHistory() }, enabled = vm.connected, modifier = Modifier.fillMaxWidth()) {
            Text("Sohbet geçmişini temizle")
        }
        Text("Kalıcı hatıralar silinmez, sadece son konuşmalar temizlenir.",
            color = Color.Gray, style = MaterialTheme.typography.bodySmall)
    }
}

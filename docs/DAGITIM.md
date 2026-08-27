# EMPViewer — Dağıtım ve "Varsayılan Uygulama" Kurulumu

EMPViewer'ı nasıl dağıtırsın ve `.eml` / `.msg` / `.pst` / `.ost` dosyaları için
nasıl varsayılan yaparsın — **installer ile ve installer'sız**, **Windows** ve
**macOS** üzerinde.

---

## 0. Her yerde geçerli olan tek kural

> Normal ayarlı bir bilgisayarda **"bunu varsayılan yap" adımını en sonunda hep
> kullanıcı yapar.** Hiçbir installer, hiçbir script; başka bir uygulamanın zaten
> sahibi olduğu bir dosya türünü sessizce ele geçiremez (`.msg` ve `.pst`'nin
> sahibi Outlook'tur). Bu, Windows 8+ ve macOS'ta bilinçli bir "kaçırma önleme"
> davranışıdır.

Otomatikleştirebileceğin: EMPViewer'ı **aday** uygulama olarak kaydetmek — böylece
*Birlikte aç* menüsünde ve işletim sisteminin "Varsayılan uygulamalar" ekranında
görünür; ve o ekranı kullanıcı için açmak. Manuel kalan: "`.msg` için hep
EMPViewer kullan" diyen tıklama.

Tek istisna, yönetilen kurumsal dağıtımlardır (Group Policy / MDM) —
[§5](#5-kurumsal-sessiz-varsayılanlar).

---

## 1. Benim önerim (özet)

| Senaryo | Öneri |
|---|---|
| **Kurumsal / yönetilen PC** | **Installer (per-user) + kod imzalama.** Aşağıda [§1.1](#11-neden-kurumsal-pcde-installer). |
| Kendi makinen / birkaç kişi | Portable `EMPViewer.exe` + `Register.cmd` yeterli. |
| BT departmanı dağıtacaksa | İmzalı `EMPViewer-Setup.exe` + `appassoc.xml` verip GPO/Intune ile sessiz dağıtım. |
| macOS | Installer gereksiz — `.app`'i /Applications'a sürükle, imzalı+notarize et. |

### 1.1 Neden kurumsal PC'de installer?

Kurumsal bir bilgisayarda **installer daha güvenli ve daha uyumludur**, çünkü:

1. **Uygulama beyaz listeleri.** Kurumsal makinelerde çoğu zaman AppLocker /
   Windows Defender Application Control (WDAC) / "Attack Surface Reduction"
   kuralları vardır. `Downloads` veya `Desktop` altından **başıboş bir `.exe`
   çalıştırmak sıklıkla engellenir**; `%LOCALAPPDATA%\Programs` altına kurulmuş,
   "Yüklü uygulamalar" listesinde görünen bir program engellere daha az takılır.
2. **Roaming profil / klasör yönlendirme.** Kurumsal ortamda `Desktop` ve
   `Documents` genelde OneDrive'a veya bir sunucuya yönlendirilir. Portable exe
   oralarda durursa senkron sorunları çıkar, yol değişir, kayıt bozulur. Per-user
   installer'ın kullandığı `%LOCALAPPDATA%\Programs\EMPViewer` bu iş için
   ayrılmış, yönlendirilmeyen yerdir.
3. **Envanter ve kaldırma.** Installer, "Yüklü uygulamalar" altında bir kayıt
   bırakır — BT ekibi görebilir, sayabilir, gerekirse sessizce kaldırabilir.
   Portable exe görünmez.
4. **Sessiz dağıtım.** BT ekibi `EMPViewer-Setup.exe /VERYSILENT` ile Intune/SCCM
   üzerinden kurabilir; varsayılan atamalarını da GPO XML ile yapabilir. Portable
   exe'yi kurumsal kanaldan dağıtmak zordur.
5. **Uninstall = temiz geri alma.** Kaldırınca `--unregister` otomatik çalışır,
   kayıt defteri temizlenir.

**Ama asıl mesele imzalama.** Installer mı portable mı fark etmeksizin, kurumsal
bir makinede **imzasız bir çalıştırılabilir dosya SmartScreen / Defender /
AppLocker tarafından büyük olasılıkla engellenir.** Bir **Authenticode kod imzalama
sertifikası** (OV ~70–200 USD/yıl, ya da anında itibar için EV) al ve hem
`EMPViewer.exe`'yi hem `EMPViewer-Setup.exe`'yi imzala. Bu tek adım, kurumsal
uyumun %80'idir.

**Ek uyarı — `.pst` / `.ost`:** Kurumsal makinelerde bu dosyalar Outlook/Exchange
alanıdır. `.pst`'yi EMPViewer'a varsayılan yapmak Outlook iş akışlarını
bozabilir. Önerim: varsayılan olarak sadece **`.eml` ve `.msg`**'yi ata; `.pst` /
`.ost`'u "isteğe bağlı" bırak (kullanıcı elle seçsin ya da sadece "Birlikte aç" ile
kullansın).

---

## 2. Portable `.exe` mi installer mı? (Windows karşılaştırma)

| | Portable `EMPViewer.exe` (+ `Register.cmd`) | `EMPViewer-Setup.exe` (Inno Setup) |
|---|---|---|
| Kullanıcı adımı | zip'i aç → bir kez `Register.cmd` | setup'ı çalıştır → İleri → Bitir |
| Yönetici hakkı | asla | asla (per-user kurulum) |
| Başlat menüsü / kaldırma kaydı | ✗ (elle) | ✓ |
| Klasör taşınınca çalışmaya devam | ✗ — `Register.cmd`'yi tekrar çalıştır | ✓ (sabit kurulum yolu) |
| Kaldırınca otomatik kayıt silme | ✗ (`Unregister.cmd`) | ✓ |
| "Varsayılan uygulamalar" kaydı (`Capabilities`) | ✓ | ✓ |
| Kendini zorla varsayılan yapabilme | ✗ | ✗ |
| Kurumsal beyaz liste / GPO uyumu | zayıf | iyi |
| Üretme eforu | `python build.py` | `python build.py --installer` (Inno Setup gerekir) |

**Sonuç:** Kişisel kullanım veya birkaç kişi için portable yol gayet yeterli ve
daha basittir. Kurumsal ortamda installer'ı tercih et. İkisi de association'ları
tam olarak aynı şekilde kaydeder (ikisi de `EMPViewer.exe --register` çağırır).

---

## 3. Windows — portable, installer'sız

### 3.1 Portable exe'yi üret

```bash
python build.py                # -> dist/EMPViewer.exe  (tek dosya, ilk açılış ~15 sn)
# veya anında açılış için (tek dosya yerine klasör):
python build.py --onedir       # -> dist/EMPViewer/EMPViewer.exe
```

### 3.2 Paketle

Şunları zip'le:

```
EMPViewer/
├── EMPViewer.exe
├── Register.cmd          (packaging/windows/Register.cmd)
├── Unregister.cmd        (packaging/windows/Unregister.cmd)
└── ÖNCE BENİ OKU.txt
```

### 3.3 Kullanıcının yapacağı (bir kez)

1. **Önce klasörü kalıcı bir yere taşı** — örn.
   `%LOCALAPPDATA%\Programs\EMPViewer`. Kayıt, exe'nin **o anki yolunu** saklar;
   sonradan taşırsan çift tıklama bozulur ve `Register.cmd`'yi tekrar çalıştırman
   gerekir.
2. **`Register.cmd`**'ye çift tıkla. Şunu çalıştırır:
   ```bat
   "%~dp0EMPViewer.exe" --register
   "%~dp0EMPViewer.exe" --set-default   :: opsiyonel: Ayarlar ▸ Varsayılan uygulamalar'ı açar
   ```
3. Açılan **Ayarlar ▸ Varsayılan uygulamalar** ekranından her tür için
   EMPViewer'ı seç — ya da dosya bazında: bir `.msg`'ye sağ tık ▸ *Birlikte aç* ▸
   *Başka uygulama seç* ▸ **EMPViewer** ▸ *Her zaman* işaretle.

`.eml` çoğu zaman sahipsizdir ve hemen varsayılan olur. `.pst` / `.ost` de
genelde sahipsizdir (Outlook onları dosya ilişkilendirmesiyle değil kendi
mekanizmasıyla açar) — yani EMPViewer'ı bir kez seçince kalıcı olur.

### 3.4 `--register` kayıt defterine ne yazar (per-user, `HKCU`)

```
HKCU\Software\Classes\EMPViewer.eml\            (+ .msg .pst .ost)
    (default)                = "E-mail Message"
    FriendlyTypeName         = "E-mail Message"
    DefaultIcon              = "<yol>\EMPViewer.exe,0"
    shell\open\command       = "<yol>\EMPViewer.exe" "%1"
HKCU\Software\Classes\.eml\OpenWithProgids\EMPViewer.eml   (öne çıkar, çalma)
HKCU\Software\EMPViewer\Capabilities\
    ApplicationName          = "EMPViewer"
    ApplicationDescription   = "Viewer for .eml, .msg, .pst and .ost mail files"
    FileAssociations\.eml    = "EMPViewer.eml"   (+ diğer üçü)
HKCU\Software\RegisteredApplications\
    EMPViewer                = "Software\EMPViewer\Capabilities"
```

`--unregister` bunların hepsini kaldırır.

> **Neden sadece `assoc` / `ftype` değil?** Onlar Windows 8 öncesi yöntem.
> Windows 10/11'de **efektif varsayılan**
> `HKCU\...\FileExts\.eml\UserChoice` altında tutulur ve kullanıcıya özel bir
> hash ile imzalanır. Bunu script'ten yazamazsın — sadece işletim sisteminin
> "bunu neyle açmak istersin?" penceresi yazabilir. `--register` desteklenen
> modern yarıyı yapar; diğer yarıyı kullanıcı bir kez yapar.

### 3.5 SmartScreen / imzalama

İmzasız exe ilk çalıştırmada **"Windows bilgisayarınızı korudu"** uyarısı verir →
*Ek bilgi* ▸ *Yine de çalıştır*. Bu uyarıyı kaldırmak için **Authenticode kod
imzalama sertifikası** gerekir (OV ~70–200 USD/yıl, ya da anında itibar için EV):

```bash
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  /f sertifika.pfx /p <parola> dist\EMPViewer.exe
```

`EMPViewer.exe`'yi (ve üretiyorsan `EMPViewer-Setup.exe`'yi) imzala.

### 3.6 Çift tıklama çalışmayı bırakırsa

- Exe taşınmış/yeniden adlandırılmış → `Register.cmd`'yi tekrar çalıştır.
- Başka bir uygulama türü geri almış → *Varsayılan uygulamalar*'dan EMPViewer'ı
  tekrar seç.
- Kayıtlı komutu kontrol et:
  ```bat
  reg query "HKCU\Software\Classes\EMPViewer.msg\shell\open\command"
  ```

---

## 4. Windows — installer (`EMPViewer-Setup.exe`)

### 4.1 Üret

```bash
# Önce Inno Setup 6 kur:  https://jrsoftware.org/isdl.php  (ISCC.exe PATH'te olsun)
python build.py --installer          # PyInstaller --onedir  +  iscc packaging\EMPViewer.iss
# -> dist/EMPViewer-Setup.exe
```

### 4.2 Ne yapar

- **Yönetici istemi olmadan** per-user olarak `%LOCALAPPDATA%\Programs\EMPViewer`
  altına kurar.
- Başlat menüsü kısayolu; isteğe bağlı masaüstü kısayolu.
- Kurulumda `EMPViewer.exe --register`, kaldırmada `--unregister` çalıştırır.
- İsteğe bağlı kutucuk: *"Varsayılan uygulamalar'ı aç"*.
- *Ayarlar ▸ Uygulamalar ▸ Yüklü uygulamalar* altında çalışan **Kaldır** ile
  görünür.

Yine de varsayılanı zorlayamaz — portable yoldaki aynı tek tıklık onay.

### 4.3 Özelleştirme

`packaging/EMPViewer.iss` içinde:
- `AppVersion`, `AppPublisher`
- `PrivilegesRequired=lowest` → `admin` yaparsan `C:\Program Files` altına
  makine geneli kurulum olur (o zaman kayıt `HKLM`'e gitmeli — bkz.
  `win_integration.py`)
- Sertifikan varsa `SignTool` yönergesi ile setup kendini imzalar.

### 4.4 BT ekibine verilecek paket (sessiz dağıtım)

```bat
:: Intune / SCCM / GPO başlangıç script'i:
EMPViewer-Setup.exe /VERYSILENT /NORESTART
```

Varsayılan atamalarını GPO ile yap — bkz. [§6](#6-kurumsal--sessiz-varsayılanlar).

---

## 5. macOS

Installer gerekmez. `.app`'i (isteğe bağlı `.dmg` içinde) dağıt.

### 5.0 macOS çıktısı NEREDE alınır? (Windows'ta alınamaz)

PyInstaller **çapraz derleme yapmaz** — `.app` yalnızca **macOS üzerinde**
üretilir. Windows'tan çıktı alamazsın. Üç yol:

| Yol | Ücret | Notlar |
|---|---|---|
| **GitHub Actions** (önerilen) | Ücretsiz (public repo) / cüzi (private) | Repo'ya `.github/workflows/build-macos.yml` eklendi. Actions sekmesi ▸ *Build macOS app* ▸ *Run workflow* → arm64 ve x86_64 için `.app` + `.dmg` artifact'leri iner. `v*` tag'i atarsan Release'e de ekler. |
| **Gerçek bir Mac** (ödünç/kirala) | — | Aşağıdaki §5.1 adımlarını Mac'te çalıştır. |
| **Bulut Mac** | Saatlik ücretli | MacStadium, AWS EC2 Mac, MacinCloud, Scaleway. |

> macOS'u Windows'ta sanal makinede çalıştırmak Apple lisansına aykırıdır
> (Apple donanımı dışında); önerilmez.

**GitHub Actions ile (Mac'in yoksa en pratik):**
1. Projeyi bir GitHub reposuna it (`.github/workflows/build-macos.yml` zaten var).
2. Repo ▸ **Actions** ▸ **Build macOS app** ▸ **Run workflow**.
3. Bittiğinde iş sayfasının altındaki **Artifacts**'ten indir:
   `EMPViewer-macos-arm64-dmg`, `EMPViewer-macos-x86_64-dmg` vb.
4. `.dmg` imzasızdır — kullanıcı ilk açılışta **sağ tık ▸ Aç** yapar (§5.2).

### 5.1 Mac üzerinde üret

```bash
python build.py            # -> dist/EMPViewer.app   (+ yamalı Info.plist)
python build.py --dmg      # -> dist/EMPViewer.dmg
```

`build.py`, `EMPViewer.app/Contents/Info.plist` içine `CFBundleDocumentTypes`
yazar — böylece Finder uygulamanın `eml/msg/pst/ost` açabildiğini bilir ve çift
tıklamayı `QFileOpenEvent` olarak yönlendirir (`main.py` bunu işler). İkon,
`iconutil` ile çok çözünürlüklü `.icns`'e dönüştürülür.

### 5.2 Kullanıcının yapacağı

1. **`EMPViewer.app`**'i **/Applications** (veya `~/Applications`) içine sürükle.
2. **İmzasız derlemede ilk açılış:** uygulamaya sağ tık ▸ **Aç** ▸ *Aç*
   (Gatekeeper yalnızca ilk çift tıklamayı engeller). Ya da karantinayı temizle:
   ```bash
   xattr -dr com.apple.quarantine /Applications/EMPViewer.app
   ```
3. **Varsayılan yap**, tür bazında: Finder'da bir `.eml` seç ▸ **⌘I** (Bilgi Al)
   ▸ **Birlikte Aç** ▸ *EMPViewer* seç ▸ **Tümünü Değiştir…** ▸ *Devam*.
   `.msg`, `.pst`, `.ost` için tekrarla.

### 5.3 Script'le varsayılan atama (isteğe bağlı)

Bundle kimliği: **`com.empviewer.app`** (`build.py --osx-bundle-identifier`).

**`duti` ile** (`brew install duti`) — en temizi:

```bash
for ext in eml msg pst ost; do
  duti -s com.empviewer.app .$ext all
done
```

Hazır script: `packaging/macos/set-default.command` (Finder'da çift tıklanabilir).

### 5.4 Gatekeeper / notarization

Kendi makinelerin dışına dağıtacaksan, kullanıcıların *"EMPViewer.app zarar
görmüş / açılamıyor"* uyarısını görmemesi için imzala + notarize et:

```bash
codesign --deep --force --options runtime \
  --sign "Developer ID Application: Adın (TEAMID)" dist/EMPViewer.app
ditto -c -k --keepParent dist/EMPViewer.app EMPViewer.zip
xcrun notarytool submit EMPViewer.zip --apple-id sen@ornek.com \
  --team-id TEAMID --password <uygulamaya-özel-parola> --wait
xcrun stapler staple dist/EMPViewer.app
```

Ücretli **Apple Developer** üyeliği gerekir (99 USD/yıl). Üyelik olmadan da
uygulama sağ tık ▸ Aç ile çalışır.

### 5.5 macOS'ta `.pst` / `.ost`

Standart bir Mac'te bu uzantıların **hiç varsayılan uygulaması yoktur**, yani
5.2.3 adımı EMPViewer'ı çakışma olmadan sahibi yapar.

---

## 6. Kurumsal / sessiz varsayılanlar

Varsayılanları **hiç kullanıcı etkileşimi olmadan** atamanın tek yolu:

### Windows (domain'e bağlı / Intune)

1. Referans bir makinede varsayılanları ayarla, sonra dışa aktar:
   ```bat
   Dism /Online /Export-DefaultAppAssociations:C:\appassoc.xml
   ```
2. Group Policy ile dağıt: *Bilgisayar Yapılandırması ▸ Yönetim Şablonları ▸
   Windows Bileşenleri ▸ Dosya Gezgini ▸* **"Varsayılan ilişkilendirme
   yapılandırma dosyası ayarla"** → bir paylaşımdaki `appassoc.xml`'i göster.
   (Ya da görev dizisinde
   `Dism /Online /Import-DefaultAppAssociations:appassoc.xml`.)
3. İlgili XML satırları:
   ```xml
   <Association Identifier=".eml" ProgId="EMPViewer.eml" ApplicationName="EMPViewer" />
   <Association Identifier=".msg" ProgId="EMPViewer.msg" ApplicationName="EMPViewer" />
   <!-- .pst / .ost'u kasıtlı olarak dışarıda bırakmayı düşün — Outlook alanı -->
   ```
   Bunun için EMPViewer **makine geneli** (`HKLM`) kurulmalı — installer'ı
   `PrivilegesRequired=admin` ile üret.

### macOS (MDM)

`com.apple.LaunchServices` yükü içeren bir **yapılandırma profili** dağıt; ya da
§5.3'teki `set-default.command`'i kayıt sırasında MDM script'i olarak çalıştır.
`.pst`/`.ost` profil gerektirmez (rakip uygulama yok).

---

## 7. Hızlı başvuru

| Amaç | Komut |
|---|---|
| Portable Windows exe üret | `python build.py` |
| Windows installer üret | `python build.py --installer` |
| macOS app / dmg üret (yalnızca Mac'te) | `python build.py --dmg` |
| Mac'siz macOS çıktısı | GitHub Actions ▸ *Build macOS app* ▸ Run workflow (§5.0) |
| Handler'ları kaydet (Windows, per-user) | `EMPViewer.exe --register` |
| Kaydet + Varsayılan uygulamalar'ı aç | `EMPViewer.exe --set-default` |
| Handler'ları kaldır (Windows) | `EMPViewer.exe --unregister` |
| macOS varsayılanlarını ata (script) | `duti -s com.empviewer.app .eml all` (×4) |
| Açılmayan bir `.pst`'yi teşhis et | `python -m parsers.pst_native "dosya.pst" --dump-first` |

---

## 8. Kısaca

- **Kurumsal PC → imzalı installer (per-user).** Portable exe kurumsal ortamda
  AppLocker/WDAC/SmartScreen'e takılma ihtimali yüksektir; installer "Yüklü
  uygulamalar"a girer, envantere görünür, GPO ile sessiz dağıtılabilir.
- **İmzalama, installer/portable ayrımından daha önemli.** İmzasız `.exe`
  kurumsal makinede muhtemelen çalışmaz.
- **`.pst` / `.ost`'u varsayılan yapmakta dikkatli ol** — Outlook alanı; sadece
  `.eml` ve `.msg`'yi ata, diğerlerini isteğe bağlı bırak.
- **Kişisel kullanım / birkaç kişi:** portable `EMPViewer.exe` + `Register.cmd`
  yeter, installer şart değil.
- **macOS:** `.app`'i /Applications'a sürükle, imzala + notarize et, sonra *Bilgi
  Al ▸ Birlikte Aç ▸ Tümünü Değiştir* (veya `duti`).
- **Tam sessiz varsayılan:** sadece Group Policy (Windows) veya MDM profili
  (macOS) ile.

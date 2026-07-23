# İş Ajandası Botu — Kurulum Rehberi

Bu bot tamamen **buton** ile çalışır. Telegram'da botunuzu ilk açtığınızda Telegram kendiliğinden bir "Start" düğmesi gösterir (bunu siz yazmazsınız, Telegram'ın standart arayüz elemanıdır) — ona bir kez dokunduktan sonra her şey buton: hatırlatıcı eklemek için tarih takvimden, saat ve dakika listeden seçilir; hatırlatıcı silmek için listeden ilgili butona dokunulur. Tek yazacağınız şey, hatırlatıcının kısa açıklaması (örn. "Vize randevusu"), çünkü bunu butonla seçmenin bir anlamı yok.

Bot ayrıca Gmail, Yandex Mail gibi hesaplarınızı arka planda izler, yeni gelen (isterseniz sadece vize ile ilgili anahtar kelimeleri içeren) e-postaları doğrudan Telegram'a düşürür. (Outlook/Hotmail desteklenmiyor — Microsoft'un OAuth zorunluluğu nedeniyle kaldırıldı.)

## Neye ihtiyacınız var

Ücretsiz hesaplar: Telegram (zaten var), GitHub, Render, MongoDB Atlas, UptimeRobot. E-posta bildirimi istiyorsanız EmailJS. Takvim istiyorsanız bir Google Cloud projesi. Gelen kutusu izleme istediğiniz her sağlayıcı için (Gmail/Yandex: uygulama şifresi; Outlook: Azure'da ücretsiz bir uygulama kaydı). SMS için kalıcı ücretsiz bir servis yok — koddan tamamen çıkarılabilir, isterseniz ileride ücretli Twilio ile eklenir.

Adımları sırayla takip edin.

## 1. Telegram Bot Oluşturma

Telegram'da **@BotFather** ile sohbet açın, `/newbot` yazın (bu, botu oluşturmak için BotFather'a yazdığınız tek seferlik kurulum komutudur, botunuzun kendisiyle ilgisi yok), isim ve kullanıcı adı verin. Size bir **token** verecek — `TELEGRAM_TOKEN` olarak kullanacaksınız.

## 2. Kodu GitHub'a Yükleme

[github.com](https://github.com) üzerinden ücretsiz hesap açın, yeni bir repo oluşturun (private seçebilirsiniz). "Add file > Upload files" ile `bot.py` ve `requirements.txt` dosyalarını yükleyip commit edin. (Diğer dosyaları da yükleyebilirsiniz; sadece gerçek şifre/token içeren bir dosyayı asla yüklemeyin.)

## 3. MongoDB Atlas (Veritabanı)

[mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas/register) üzerinden ücretsiz kayıt olun, **M0 (Free)** cluster oluşturun. Bir veritabanı kullanıcı adı/şifresi belirleyin. "Network Access" kısmında `0.0.0.0/0` ekleyin. "Connect > Drivers" ile bağlantı adresini kopyalayın, `<password>` yerine şifrenizi yazın — bu `MONGO_URI` değeriniz.

## 4. Render'da Web Service Oluşturma

[render.com](https://render.com) üzerinden GitHub hesabınızla giriş yapın, "New +" > "Web Service", 2. adımdaki reponuzu seçin.

- **Runtime**: Python 3
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn --workers 1 --threads 4 bot:app` (workers 1 şart, yoksa hatırlatmalar birden fazla kez gider. Render'ın ücretsiz planı kısıtlı bellek sunduğu için threads sayısını yüksek tutmayın — 8'e çıkarmak, arka plandaki paralel taramalarla birleşince bellek yetersizliğine [OOM] ve sürekli çökmeye yol açtı, 4'te kalın)
- **Instance Type**: Free

"Environment" sekmesinden en azından şunları ekleyin: `TELEGRAM_TOKEN`, `WEBHOOK_SECRET` (kendiniz uydurun, tahmin edilemez bir metin), `MONGO_URI`. Deploy edin, Render size bir adres verir (örn. `https://is-ajandasi.onrender.com`).

## 5. Telegram Webhook'unu Ayarlama

Tarayıcıda şunu açın (kendi değerlerinizle):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<RENDER_ADRESI>/webhook/<WEBHOOK_SECRET>
```

`{"ok":true,...}` görürseniz tamam. Telegram'da botunuzu açıp Telegram'ın gösterdiği "Start" düğmesine dokunun — ana menü butonlarla karşınıza çıkar.

## 6. UptimeRobot ile Botu Uyanık Tutma

Render'ın ücretsiz servisleri 15 dakika hareketsizlikten sonra uyur. [uptimerobot.com](https://uptimerobot.com) üzerinden ücretsiz kayıt olun, "Add New Monitor" > HTTP(s) > Render adresiniz > 5 dakikalık aralık.

## 7. E-posta ile Hatırlatıcı Bildirimi (Opsiyonel — EmailJS)

Bu, hatırlatıcı zamanı geldiğinde size **e-posta gönderilmesi** içindir (aşağıdaki madde 8 ise sizin gelen kutunuzu okuyup Telegram'a düşürmek içindir — ikisi farklı şeyler).

[emailjs.com](https://www.emailjs.com) üzerinden ücretsiz kayıt olun. "Email Services" > Gmail bağlayın. "Email Templates" içine `{{subject}}`, `{{message}}`, alıcı olarak `{{to_email}}` koyun. "Account" sekmesinden Public/Private Key alın. Render'a `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID`, `EMAILJS_PUBLIC_KEY`, `EMAILJS_PRIVATE_KEY`, `USER_EMAIL` olarak ekleyin.

## 8. Gelen Kutusu İzleme (Gmail, Yandex ve benzerleri)

Bunlar, o hesaplara gelen yeni mailleri otomatik olarak Telegram'a düşürür. Her hesap için Render'a iki satır eklemeniz yeterli, hiçbiri zorunlu değil.

**Herhangi bir sayıda hesap ekleyebilirsiniz** (Gmail, Yandex, Yahoo, iCloud, Zoho, GMX, Mail.ru, hatta özel/kurumsal bir mail sunucusu) — hepsi tek bir sistemle çalışır: `IMAP_ADDRESS_1`/`IMAP_APP_PASSWORD_1`, `IMAP_ADDRESS_2`/`IMAP_APP_PASSWORD_2`, `IMAP_ADDRESS_3`/`IMAP_APP_PASSWORD_3` şeklinde numarayı artırarak devam edin (30'a kadar). Bot, adresin `@`'dan sonraki kısmına bakarak hangi sunucuyu kullanacağını **otomatik anlıyor** — Gmail için `imap.gmail.com` yazmanıza gerek yok, sadece adresi ve şifreyi giriyorsunuz. Bilmediği özel bir domain olursa (şirket maili gibi) `IMAP_HOST_5` gibi elle sunucu adresi de girebilirsiniz. Yeni bir hesap eklemek istediğinizde tek yapmanız gereken bir sonraki boş numarayla iki satır eklemek — kod değişikliği gerekmez.

Not: **Outlook/Hotmail desteklenmiyor** — Microsoft kişisel hesaplar için basit şifreyle IMAP erişimini tamamen kapatıp karmaşık bir OAuth süreci zorunlu kıldığı için bu bottan çıkarıldı.

**Mail eklerinin gönderilmesi**: Bir mailde resim (örn. Fransa vizesinde gelen OTP kodu resmi) veya PDF/Word gibi bir dosya varsa, bot bunu otomatik olarak indirip Telegram'a da gönderir — ayrıca bir ayar yapmanıza gerek yok. Çok büyük dosyalar (varsayılan 20 MB üzeri) gönderilmez, bunu `MAIL_ATTACHMENT_MAX_MB` ile değiştirebilirsiniz.

### Gmail / Yandex / Yahoo / iCloud / Zoho / GMX / Mail.ru

Her hesapta 2 adımlı doğrulamayı açıp bir "uygulama şifresi" (app password) oluşturmanız gerekiyor — yeri sağlayıcıya göre değişir:
- **Gmail**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
- **Yandex**: Ayarlar > Tüm Ayarlar > Posta İstemcileri'nden IMAP'i açın ve "Uygulama şifreleri ve OAuth token'larına izin ver"i etkinleştirin, sonra [passport.yandex.com](https://passport.yandex.com) > Hesap Yönetimi > Şifreler ve Yetkilendirme > Uygulama Şifreleri
- **Yahoo**: [account.yahoo.com/security](https://login.yahoo.com/account/security) > "Generate app password"
- **iCloud**: [appleid.apple.com](https://appleid.apple.com) > Sign-In and Security > App-Specific Passwords
- **Zoho**: Zoho Mail Ayarlar > Security > App Passwords
- **GMX / Mail.ru**: hesap güvenlik ayarlarında benzer bir "uygulama şifresi" seçeneği arayın

Aldığınız her adres/şifre çiftini Render'a `IMAP_ADDRESS_N` / `IMAP_APP_PASSWORD_N` olarak, numarayı artırarak ekleyin.

### Bildirimleri Vize ile Sınırlama (Opsiyonel)

Her mail yerine sadece belirli kelimeleri içerenleri görmek isterseniz, Render'a `EMAIL_KEYWORDS` ekleyin, örn:
```
EMAIL_KEYWORDS=vize,visa,consulate,embassy,randevu,appointment,sefaret,konsolosluk
```
Boş bırakırsanız gelen her mail Telegram'a düşer.

## 9. Google Takvim Entegrasyonu (Opsiyonel)

1. [console.cloud.google.com](https://console.cloud.google.com) üzerinden yeni proje oluşturun, "Google Calendar API"yi etkinleştirin.
2. "OAuth consent screen": External, temel bilgileri doldurun, "Test users" kısmına kendi Gmail'inizi ekleyin.
3. "Credentials > Create Credentials > OAuth client ID": **Desktop app**. İndirdiğiniz JSON dosyasını `client_secret.json` adıyla bu klasöre koyun.
4. `pip install google-auth-oauthlib`, sonra `python get_google_refresh_token.py` çalıştırın, tarayıcıda izin verin.
5. Çıkan `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` değerlerini Render'a ekleyin.

**iPhone'da görünmesi için**: Ayarlar > Takvim > Hesaplar > Hesap Ekle > Google — hesabınızı ekleyin. Bot'un oluşturduğu etkinlikler doğrudan Apple Takvim'de görünür.

## 10. SMS Hakkında

Kalıcı ücretsiz bir SMS servisi yok. TextNow gibi ücretsiz görünen servislerin resmi bir API'si yok, kullanmak hesap askıya alınma riski taşır — önermiyorum. İsterseniz [twilio.com](https://www.twilio.com) üzerinden ücretli bir hesap açıp `TWILIO_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_TO_NUMBER` değerlerini Render'a ekleyebilirsiniz. Boş bırakırsanız bot SMS'siz, Telegram + e-posta + takvim ile tam çalışır.

## 11. Kullanım

Telegram'da botu açıp Telegram'ın "Start" düğmesine basın. Klavyenizin altında kalıcı bir **"☰ Menü"** butonu belirir — sohbet ne kadar uzarsa uzasın, her an bu butona basarak ana menüye dönebilirsiniz. Ana menüde:

- **➕ Hatırlatıcı Ekle**: takvimden günü seçin, ardından saati **yazarak** girin (örnek: `09:15`) — bu şekilde dakika hassasiyetinde, istediğiniz gibi girebilirsiniz. Sonra "ne zaman hatırlatayım" sorusunda birden fazla seçenek işaretleyebilirsiniz (örn. hem "1 gün önce" hem "tam zamanında") — vize randevusu için ikisini birden işaretlemenizi öneririm. En son kısa bir açıklama yazın (örn. "Vize randevusu - Alman Konsolosluğu"). Hatırlatıcı bir **ID numarası** ile kaydedilir hem (bağlıysa) takviminize hemen işlenir.
- **📋 Tüm Hatırlatıcıları Gör**: eklediğiniz tüm hatırlatıcıları (geçmiş/tamamlanmış olanlar dahil) ID numaralarıyla birlikte listeler.
- **🗑 Hatırlatıcı Sil**: bu butona bastıktan sonra silmek istediğiniz hatırlatıcının ID numarasını (örn. `3`) yazıp gönderin, bot o ID'ye sahip hatırlatıcıyı siler.
- **🛂 Pasaport Ekle**: pasaport kaydı ekler, aşağıdaki madde 12'ye bakın.
- **🕑 Sıraya Girdi**: bir kaydı "sırada bekliyor" (waitlist) olarak işaretler, aşağıdaki madde 12'ye bakın.
- **✅ Randevu Aldım**: bir kaydı "randevu alındı" olarak işaretler, aşağıdaki madde 12'ye bakın.

Bağladığınız mail hesaplarına yeni bir şey geldiğinde, ayrıca bir şey yapmanıza gerek kalmadan bot size Telegram'dan haber verir — artık gövde metni HTML karmaşası olmadan düzgün gösteriliyor, ekli/gömülü resimler (OTP kodları dahil) ve PDF/Word gibi dosyalar da otomatik olarak Telegram'a düşüyor. OTP kodlarının kısa sürede geçersiz olması nedeniyle mail kontrolü varsayılan olarak her **15 saniyede** bir çalışır (`MAIL_CHECK_INTERVAL_SECONDS`).

**Gürültüyü azaltmak için**: VFS Global gibi randevu sistemlerinden gelen mailleri önceliklendirip güvenlik bildirimi gibi alakasız mailleri filtrelemek isterseniz, `EMAIL_KEYWORDS` değişkenini şu şekilde ayarlamanızı öneririm:
```
EMAIL_KEYWORDS=vize,visa,vfsglobal,vfs global,appointment,randevu,consulate,embassy,sefaret,konsolosluk,appointment letter,group urn
```

## 12. Pasaport → Google Sheets Entegrasyonu (Opsiyonel)

Bu özellik, pasaport fotoğrafından bilgileri otomatik okuyup doğru ülke sayfanıza işler; kayıt sırada beklerken sarıya, randevu kesinleşince kırmızıya boyanıp master sayfaya kopyalanır.

**Kurulum (tek seferlik)**: adım adım talimatlar için `GOOGLE_SHEETS_KURULUM.md` dosyasına bakın. Özetle: bir Google Servis Hesabı oluşturup Sheets dosyanızı onunla paylaşacak, `GOOGLE_SERVICE_ACCOUNT_JSON` ve `SHEETS_SPREADSHEET_ID` değerlerini Render'a ekleyecek, her ülke sayfasına bir "ID" sütunu açacak ve [ocr.space](https://ocr.space/ocrapi/freekey) üzerinden ücretsiz bir `OCR_SPACE_API_KEY` alacaksınız. Yeni bir ülke sayfası eklediğinizde kodda hiçbir değişiklik gerekmez — bot sayfa listesini her seferinde canlı okur.

**Renk şeması** (ülke sayfalarında):
- **Renksiz**: sadece pasaport kaydı yapıldı, henüz hiçbir işlem yok.
- **Sarı**: kayıt sıraya girdi / waitlist'te bekliyor (bazı VFS sitelerinde — örn. Hollanda, Bulgaristan — önce sıraya alınıyor, kesin randevu daha sonra veriliyor).
- **Kırmızı**: randevu kesinleşti, `RANDEVU ALINMIŞLAR` (master) sayfasına kopyalandı.

**Kullanım — Pasaport Ekleme**:
1. Ana Menü > **🛂 Pasaport Ekle**, ardından hangi ülke sayfasına ekleneceğini seçin.
2. Pasaportun fotoğrafını gönderin (MRZ satırlarının — pasaportun alt kısmındaki iki satırın — net göründüğü bir açı).
3. Bot isim, soyisim, pasaport no, doğum tarihi, pasaport SKT, uyruk ve kimlik no bilgilerini otomatik okuyup size gösterir. "✅ Doğru, Devam Et" ile onaylayın, okuma hatalıysa "🔁 Tekrar Çek" ile yeni foto gönderin, hiç okunmuyorsa "✍️ Elle Gir" ile tüm bilgileri tek tek yazarak girebilirsiniz.
4. Onayladıktan sonra bot sırayla Vize Türü, İşlemi Yapan, Yönlendiren Kişi, Mail, Şifre, Tel bilgilerini soracak — yazıp gönderin. Son adımdan sonra kayıt ilgili ülke sayfasına bir ID numarasıyla, **renksiz** olarak eklenir. Eğer girilen pasaport numarası başka bir sayfada veya RANDEVU ALINMIŞLAR'da zaten kayıtlıysa, bot eklemeden önce uyarıp onay ister.

**Kullanım — Sıraya Girdi**:
1. Ana Menü > **🕑 Sıraya Girdi**, ülke sayfasını seçin, kaydın ID numarasını yazın.
2. Bot ek bir soru sormadan kaydı "Sırada Bekliyor" olarak işaretleyip satırı **sarıya** boyar. Master sayfaya kopyalanmaz, tarih/saat istenmez — bu sadece bir ön-aşama bilgisidir.
3. Gerçek randevu netleştiğinde aynı kayıt için **✅ Randevu Aldım** akışını kullanmaya devam edebilirsiniz (sıradaki kayıtlar da o listede görünür).

**Kullanım — Randevu Aldım**:
1. Ana Menü > **✅ Randevu Aldım**, ülke sayfasını seçin, kaydın ID numarasını yazın (sırada bekleyen kayıtlar da bu listede görünür).
2. Bot sırayla referans numarası, randevu günü (GG.AA.YYYY) ve saatini (SS:DD) soracak.
3. Girdikten sonra ilgili satır **kırmızıya** boyanır, otomatik olarak `RANDEVU ALINMIŞLAR` (master) sayfasına kopyalanır ve girdiğiniz randevu günü/saati için **otomatik bir hatırlatıcı** (Telegram + takvim) oluşturulur — ayrıca elle hatırlatıcı eklemenize gerek kalmaz.

**Otomatik yeni kayıt bildirimi**: Google Sheets'e (bot üzerinden ya da elle, doğrudan tabloya girilerek) yeni bir pasaport satırı eklendiğinde bot bunu periyodik olarak (varsayılan 60 saniyede bir, `PASSPORT_CHECK_INTERVAL_SECONDS` ile ayarlanabilir) fark edip Telegram'a "Yeni pasaport kaydı" bildirimi gönderir — böylece biri tabloya elle bir satır eklese bile haberdar olursunuz. Botun kendi eklediği kayıtlar için ayrıca bu bildirim gelmez (zaten anlık onay mesajı gösteriliyor).

Not: Pasaport numarası, kimlik no ve mail şifresi gibi hassas bilgiler düz metin olarak Google Sheets'e yazılıyor — tabloya kimlerin erişebildiğini kontrol etmenizi öneririm.

## 13. Arkadaşınızın/Ekibinizin de Bota Erişmesi (Opsiyonel)

Bota ikinci (veya daha fazla) bir kişinin de erişmesini, aynı hatırlatıcıları/pasaport kayıtlarını görmesini ve tüm bildirimleri (hatırlatıcı, yeni mail, randevu) almasını istiyorsanız:

1. Render'a `TEAM_ACCESS_CODE` adında bir ortam değişkeni ekleyin, değeri kendiniz belirleyeceğiniz gizli bir kod olsun (örn. `Vize2026Ekip`).
2. Bu kodu eklediğiniz kişiye (WhatsApp, SMS vb. güvenli bir yoldan) iletin.
3. O kişi Telegram'da botunuzu bulup Telegram'ın "Start" düğmesine bassın, bot ondan erişim kodunu isteyecek, kodu yazıp göndersin.
4. Kod doğruysa artık o kişi de sizinle **aynı** hatırlatıcıları, pasaport kayıtlarını görebilir/ekleyebilir/silebilir ve tüm bildirimleri alır.

`TEAM_ACCESS_CODE` boş bırakılırsa bot eskisi gibi tek kişilik çalışmaya devam eder, hiçbir şey değişmez.

## 14. Yeni Eklenen Özellikler (Arama, Mükerrer Kontrol, Günlük Özet, Rapor)

- **🔍 Kayıt Ara (İsim/ID)**: Ana menüden bu butona basıp bir isim ya da ID numarası yazın. Bot hem tüm ülke sayfalarını (pasaport kayıtları) hem de **RANDEVU ALINMIŞLAR** (master) sayfasını (randevu günü/saati/sonucu) tarayıp eşleşen kayıtları listeler.
- **Mükerrer pasaport kontrolü**: Pasaport ekleme akışı tamamlandığında (fotoğraftan ya da elle), bot girilen pasaport numarasının **başka bir ülke sayfasında veya RANDEVU ALINMIŞLAR sayfasında zaten kayıtlı olup olmadığını** otomatik kontrol eder. Kayıtlıysa hangi sayfada/ID'de olduğunu gösterip "Yine de ekle" veya "İptal" seçeneği sunar — yanlışlıkla aynı kişiyi iki kez eklemenizi engeller.
- **📊 Rapor Al**: Ana menüden bu butona basıp bir tarih aralığı yazın (örn. `01.07.2026-31.07.2026`). Bot **RANDEVU ALINMIŞLAR** sayfasındaki, o aralıkta randevu günü olan tüm kayıtları toplayıp bir **Excel (.xlsx)** dosyası olarak Telegram'a gönderir. Randevu günü/saati sadece bu sayfada tam tutulduğu için (ülke sayfalarına sadece pasaport kaydı işleniyor), rapor kaynağı budur.
- **Günlük özet**: Her sabah (varsayılan saat 09:00, Türkiye saati — `DAILY_DIGEST_HOUR`/`DAILY_DIGEST_MINUTE` ile değiştirilebilir) bot otomatik olarak şu bilgileri içeren bir özet mesajı gönderir: bugün randevusu olanlar (RANDEVU ALINMIŞLAR'dan), henüz randevu alınmamış (bekleyen) kayıt sayısı (ülke sayfalarından, RANDEVU ALINMIŞLAR'da zaten olanlar hariç tutularak) ve pasaport süresi yakında (varsayılan 180 gün içinde — `PASSPORT_EXPIRY_WARN_DAYS`) dolacak kişiler.

Bu 4 özellik için ek bir kurulum adımı gerekmez — Google Sheets bağlantınız (madde 12) zaten kuruluysa hepsi otomatik çalışır. `requirements.txt` dosyasına `openpyxl` eklendi (Excel rapor için); Render'da yeniden deploy ettiğinizde otomatik kurulur.

## Sorun Giderme

- Bot hiç cevap vermiyor: Render "Logs" sekmesine bakın, webhook adımını (5) kontrol edin.
- Servis "sleeping": UptimeRobot monitörünüzü kontrol edin.
- Mail bildirimi gelmiyor: Render loglarında ilgili sağlayıcı (Gmail/Yandex) için hata var mı bakın; Yandex için IMAP'ın hesap ayarlarından açık olduğundan emin olun (madde 8).
- Mail kontrolü "too many connections" gibi bir hata veriyor: `MAIL_CHECK_INTERVAL_SECONDS` değerini 15'ten 30-45'e çıkarın (çok sayıda hesapta sunucular çok sık bağlantıyı sınırlayabilir).
- Takvim etkinliği oluşmuyor: Render loglarında Google hatası var mı bakın; refresh token süresi dolmuş olabilir, madde 9'u tekrarlayın.
- Pasaport Ekle / Randevu Aldım butonu "Google Sheets bağlantısı kurulu değil" diyor: `GOOGLE_SHEETS_KURULUM.md` adımlarını tamamlayın, `GOOGLE_SERVICE_ACCOUNT_JSON` ve `SHEETS_SPREADSHEET_ID` değerlerini kontrol edin.
- Pasaporttan bilgi okunmuyor: fotoğrafın net, parlamasız ve MRZ satırlarının (alttaki iki satır) tam göründüğünden emin olun; olmazsa "✍️ Elle Gir" ile devam edin.
- Ekip arkadaşı botu açtığında hep "erişim kodu girin" diyor: `TEAM_ACCESS_CODE` değerini doğru yazdığından emin olun (büyük/küçük harf birebir eşleşmeli).

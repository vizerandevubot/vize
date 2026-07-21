# İş Ajandası Botu — Kurulum Rehberi

Bu bot tamamen **buton** ile çalışır. Telegram'da botunuzu ilk açtığınızda Telegram kendiliğinden bir "Start" düğmesi gösterir (bunu siz yazmazsınız, Telegram'ın standart arayüz elemanıdır) — ona bir kez dokunduktan sonra her şey buton: hatırlatıcı eklemek için tarih takvimden, saat ve dakika listeden seçilir; hatırlatıcı silmek için listeden ilgili butona dokunulur. Tek yazacağınız şey, hatırlatıcının kısa açıklaması (örn. "Vize randevusu"), çünkü bunu butonla seçmenin bir anlamı yok.

Bot ayrıca Gmail, Yandex Mail ve Outlook/Hotmail hesaplarınızı arka planda izler, yeni gelen (isterseniz sadece vize ile ilgili anahtar kelimeleri içeren) e-postaları doğrudan Telegram'a düşürür.

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
- **Start Command**: `gunicorn --workers 1 --threads 4 bot:app` (workers 1 şart, yoksa hatırlatmalar birden fazla kez gider)
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

## 8. Gelen Kutusu İzleme (Gmail, Yandex, Outlook)

Bunlar, o hesaplara gelen yeni mailleri otomatik olarak Telegram'a düşürür. Her sağlayıcı için ayrı kurulum var, istediğinizi/istediklerinizi ekleyin, hiçbiri zorunlu değil.

### Gmail

Gmail hesabınızda 2 adımlı doğrulama açık olmalı. [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) üzerinden "Mail" için bir uygulama şifresi oluşturun. Render'a `GMAIL_ADDRESS` (kendi adresiniz) ve `GMAIL_APP_PASSWORD` (oluşan 16 haneli şifre) olarak ekleyin.

### Yandex Mail

Yandex Mail'de Ayarlar > Tüm Ayarlar > Posta İstemcileri kısmından "imap.yandex.com üzerinden IMAP ile" seçeneğini açın. Sonra [passport.yandex.com](https://passport.yandex.com) > Hesap Yönetimi > Şifreler ve Yetkilendirme > Uygulama Şifreleri'nden yeni bir uygulama şifresi oluşturun. Render'a `YANDEX_ADDRESS` ve `YANDEX_APP_PASSWORD` olarak ekleyin.

### Outlook / Hotmail

Önemli: Microsoft, kişisel Outlook/Hotmail hesapları için eski usul "uygulama şifresi ile IMAP" erişimini 2026'da tamamen kapattı. Bu yüzden Outlook için farklı bir yol (OAuth) gerekiyor — biraz daha uzun ama tek seferlik:

1. [portal.azure.com](https://portal.azure.com) adresine kendi Microsoft hesabınızla girin (ücretsiz, kredi kartı istemez).
2. "Microsoft Entra ID" > "App registrations" > "New registration". İsim verin (örn. "Is Ajandasi Botu"). "Supported account types" için **"Personal Microsoft accounts only"** seçin. Redirect URI'yi boş bırakabilirsiniz.
3. Oluşan uygulamanın "Overview" sayfasından **Application (client) ID**'yi kopyalayın.
4. Sol menüden "Authentication" > aşağı inip **"Allow public client flows"** seçeneğini **Yes** yapın, kaydedin.
5. Kendi bilgisayarınızda: `pip install msal`, sonra bu klasördeki `get_outlook_refresh_token.py` dosyasını açıp içindeki `CLIENT_ID` değerini adım 3'teki ID ile değiştirin.
6. `python get_outlook_refresh_token.py` çalıştırın. Ekranda bir internet adresi ve kod göreceksiniz (`microsoft.com/devicelogin`); tarayıcıda o adrese gidip kodu girin, Outlook hesabınızla giriş yapıp izin verin.
7. Terminalde çıkan `OUTLOOK_CLIENT_ID` ve `OUTLOOK_REFRESH_TOKEN` değerlerini Render'a ekleyin.

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

Telegram'da botu açıp Telegram'ın "Start" düğmesine basın. Ana menüde:

- **➕ Hatırlatıcı Ekle**: takvimden gün, sonra saat, sonra dakika seçin. Ardından "ne zaman hatırlatayım" sorusunda birden fazla seçenek işaretleyebilirsiniz (örn. hem "1 gün önce" hem "tam zamanında") — vize randevusu için ikisini birden işaretlemenizi öneririm. En son kısa bir açıklama yazın (örn. "Vize randevusu - Alman Konsolosluğu"). Hatırlatıcı bir **ID numarası** ile kaydedilir hem (bağlıysa) takviminize hemen işlenir.
- **📋 Tüm Hatırlatıcıları Gör**: eklediğiniz tüm hatırlatıcıları (geçmiş/tamamlanmış olanlar dahil) ID numaralarıyla birlikte listeler.
- **🗑 Hatırlatıcı Sil**: bu butona bastıktan sonra silmek istediğiniz hatırlatıcının ID numarasını (örn. `3`) yazıp gönderin, bot o ID'ye sahip hatırlatıcıyı siler.

Bağladığınız mail hesaplarına yeni bir şey geldiğinde, ayrıca bir şey yapmanıza gerek kalmadan bot size Telegram'dan haber verir.

## Sorun Giderme

- Bot hiç cevap vermiyor: Render "Logs" sekmesine bakın, webhook adımını (5) kontrol edin.
- Servis "sleeping": UptimeRobot monitörünüzü kontrol edin.
- Mail bildirimi gelmiyor: Render loglarında ilgili sağlayıcı (Gmail/Yandex/Outlook) için hata var mı bakın; Outlook'ta refresh token süresi dolmuşsa madde 8'i tekrarlayın.
- Takvim etkinliği oluşmuyor: Render loglarında Google hatası var mı bakın; refresh token süresi dolmuş olabilir, madde 9'u tekrarlayın.

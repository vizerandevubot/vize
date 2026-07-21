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

**Herhangi bir sayıda hesap ekleyebilirsiniz** (Gmail, Yandex, Yahoo, iCloud, Zoho, GMX, Mail.ru, hatta özel/kurumsal bir mail sunucusu) — hepsi tek bir sistemle çalışır: `IMAP_ADDRESS_1`/`IMAP_APP_PASSWORD_1`, `IMAP_ADDRESS_2`/`IMAP_APP_PASSWORD_2`, `IMAP_ADDRESS_3`/`IMAP_APP_PASSWORD_3` şeklinde numarayı artırarak devam edin (20'ye kadar). Bot, adresin `@`'dan sonraki kısmına bakarak hangi sunucuyu kullanacağını **otomatik anlıyor** — Gmail için `imap.gmail.com` yazmanıza gerek yok, sadece adresi ve şifreyi giriyorsunuz. Bilmediği özel bir domain olursa (şirket maili gibi) `IMAP_HOST_5` gibi elle sunucu adresi de girebilirsiniz. Yeni bir hesap eklemek istediğinizde tek yapmanız gereken bir sonraki boş numarayla iki satır eklemek — kod değişikliği gerekmez.

Sadece **Outlook/Hotmail/Live** farklı çalışıyor (aşağıdaki ayrı bölüme bakın), çünkü Microsoft o adresler için basit şifreyi tamamen kapattı.

**Mail eklerinin gönderilmesi**: Bir mailde resim (örn. Fransa vizesinde gelen OTP kodu resmi) veya PDF/Word gibi bir dosya varsa, bot bunu otomatik olarak indirip Telegram'a da gönderir — ayrıca bir ayar yapmanıza gerek yok. Çok büyük dosyalar (varsayılan 20 MB üzeri) gönderilmez, bunu `MAIL_ATTACHMENT_MAX_MB` ile değiştirebilirsiniz.

### Gmail / Yandex / Yahoo / iCloud / Zoho / GMX / Mail.ru

Her hesapta 2 adımlı doğrulamayı açıp bir "uygulama şifresi" (app password) oluşturmanız gerekiyor — yeri sağlayıcıya göre değişir:
- **Gmail**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
- **Yandex**: Ayarlar > Tüm Ayarlar > Posta İstemcileri'nden IMAP'i açın, sonra [passport.yandex.com](https://passport.yandex.com) > Hesap Yönetimi > Şifreler ve Yetkilendirme > Uygulama Şifreleri
- **Yahoo**: [account.yahoo.com/security](https://login.yahoo.com/account/security) > "Generate app password"
- **iCloud**: [appleid.apple.com](https://appleid.apple.com) > Sign-In and Security > App-Specific Passwords
- **Zoho**: Zoho Mail Ayarlar > Security > App Passwords
- **GMX / Mail.ru**: hesap güvenlik ayarlarında benzer bir "uygulama şifresi" seçeneği arayın

Aldığınız her adres/şifre çiftini Render'a `IMAP_ADDRESS_N` / `IMAP_APP_PASSWORD_N` olarak, numarayı artırarak ekleyin.

### Outlook / Hotmail

Önemli: Microsoft, kişisel Outlook/Hotmail hesapları için eski usul "uygulama şifresi ile IMAP" erişimini 2026'da tamamen kapattı. Bu yüzden Outlook için farklı bir yol (OAuth) gerekiyor — biraz daha uzun ama tek seferlik. **Aynı Azure uygulamasını (aynı CLIENT_ID) tüm Outlook hesaplarınız için kullanabilirsiniz, sadece her hesap için ayrı bir REFRESH_TOKEN almanız gerekiyor.**

1. [portal.azure.com](https://portal.azure.com) adresine kendi Microsoft hesabınızla girin (ücretsiz, kredi kartı istemez).
2. "Microsoft Entra ID" > "App registrations" > "New registration". İsim verin (örn. "Is Ajandasi Botu"). "Supported account types" için **"Personal Microsoft accounts only"** seçin. Redirect URI'yi boş bırakabilirsiniz.
3. Oluşan uygulamanın "Overview" sayfasından **Application (client) ID**'yi kopyalayın.
4. Sol menüden "Authentication" > aşağı inip **"Allow public client flows"** seçeneğini **Yes** yapın, kaydedin.
5. Sol menüden "API permissions" ile devam etmenize gerek yok; ama "OAuth consent screen" bir "Test users" listesi isterse (bazı kurulumlarda sormaz), izlemek istediğiniz **her** Outlook/Hotmail adresini o listeye ekleyin — eklemezseniz o hesapla giriş yaparken "erişim reddedildi" hatası alırsınız.
6. Kendi bilgisayarınızda: `pip install msal`, sonra bu klasördeki `get_outlook_refresh_token.py` dosyasını açıp içindeki `CLIENT_ID` değerini adım 3'teki ID ile değiştirin.
7. `python get_outlook_refresh_token.py` çalıştırın. Ekranda bir internet adresi ve kod göreceksiniz (`microsoft.com/devicelogin`); tarayıcıda o adrese gidip kodu girin, **1. Outlook hesabınızla** giriş yapıp izin verin.
8. Terminalde çıkan `OUTLOOK_CLIENT_ID` ve `OUTLOOK_REFRESH_TOKEN` değerlerini Render'a `OUTLOOK_CLIENT_ID` ve `OUTLOOK_REFRESH_TOKEN_1` olarak ekleyin.
9. **2. Outlook hesabınız için**: `python get_outlook_refresh_token.py`'i tekrar çalıştırın, bu sefer tarayıcıda **2. hesabınızla** giriş yapın. Çıkan yeni refresh token'ı Render'a `OUTLOOK_REFRESH_TOKEN_2` olarak ekleyin (`OUTLOOK_CLIENT_ID` aynı kalır, tekrar eklemenize gerek yok).

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

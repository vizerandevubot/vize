# Google Sheets Entegrasyonu — Servis Hesabı Kurulumu

Bu adımlar, botun sizin Google Sheets tablonuza (pasaport kayıtları) doğrudan, sizin adınıza tekrar tekrar giriş yapmadan yazabilmesi için gereken tek seferlik kurulumdur.

## 1. Google Cloud Projesi ve Sheets API

1. [console.cloud.google.com](https://console.cloud.google.com) adresine gidin (Takvim entegrasyonu için zaten bir proje oluşturduysanız onu kullanabilirsiniz, yeni proje açmanıza gerek yok).
2. "APIs & Services > Library" içinden **"Google Sheets API"**'yi bulup etkinleştirin.

## 2. Servis Hesabı Oluşturma

1. "APIs & Services > Credentials" > **"Create Credentials" > "Service account"**.
2. Bir isim verin (örn. "vize-bot-sheets"), "Create and Continue" ile devam edin, rol atamadan (veya "Editor" seçip) "Done" ile bitirin.
3. Oluşan servis hesabına tıklayın, **"Keys"** sekmesi > **"Add Key" > "Create new key" > JSON** seçin. Bir JSON dosyası inecek — bu dosyayı güvenli bir yerde saklayın, bir daha indiremezsiniz.
4. Servis hesabının **e-posta adresini** not edin (örn. `vize-bot-sheets@proje-adi.iam.gserviceaccount.com`) — bir sonraki adımda lazım.

## 3. Google Sheets Dosyasını Servis Hesabıyla Paylaşma

Google Sheets dosyanızı açın, sağ üstteki **"Paylaş" (Share)** butonuna basın, adım 2.4'teki servis hesabı e-postasını **"Düzenleyen" (Editor)** yetkisiyle ekleyin. Bu olmadan bot tabloya yazamaz.

## 4. Render'a Ekleme

İndirdiğiniz JSON dosyasını bir metin editörüyle açın, **tüm içeriğini** kopyalayın. Render'daki Environment Variables kısmına:

- `GOOGLE_SERVICE_ACCOUNT_JSON` → JSON dosyasının tüm içeriği (tek satır/tek değer olarak yapıştırabilirsiniz, Render buna izin verir)
- `SHEETS_SPREADSHEET_ID` → Google Sheets linkinizdeki `/d/` ile `/edit` arasındaki kısım. Örneğin:
  ```
  https://docs.google.com/spreadsheets/d/1h1IwYTLb_5TuoFmpWqnThJsB2_EiaYJYULv_cHek0jo/edit
  ```
  buradaki ID: `1h1IwYTLb_5TuoFmpWqnThJsB2_EiaYJYULv_cHek0jo`

## 5. Tablo Hazırlığı (tek seferlik, elle)

Her ülke sayfasının **A sütununa** (mevcut sütunların soluna, yeni bir sütun ekleyerek) **"ID"** başlıklı boş bir sütun ekleyin. Bot, yeni pasaport eklerken bu sütuna otomatik sıra numarası (1, 2, 3...) yazacak ve daha sonra "Randevu Aldım" derken bu numarayı kullanacaksınız. ("RANDEVU ALINMIŞLAR" sayfasında zaten "SIRA" sütunu var, ona dokunmanıza gerek yok.)

## 6. OCR.space (Pasaport Okuma) API Anahtarı

[ocr.space/ocrapi/freekey](https://ocr.space/ocrapi/freekey) adresinden ücretsiz bir API anahtarı alın (e-posta ile, kart istemez, ayda 25.000 istek ücretsiz). Render'a `OCR_SPACE_API_KEY` olarak ekleyin.

Hepsini ekledikten sonra Render "Save Changes" ile yeniden deploy edecek.

"""
Bu script'i SADECE KENDI BILGISAYARINIZDA, bir KEZ calistirin.
Render sunucusunda calistirmayin - bir tarayici acmasi gerekiyor.

Ne yapar:
  1) Google hesabinizla tarayicida giris yaptirir, Takvim erisimi icin izin ister.
  2) Sonucunda bir REFRESH TOKEN uretir.
  3) Bu token'i (client id / secret ile birlikte) Render'daki Environment
     Variables kismina bir kez girersiniz, bot bundan sonra sizin adiniza
     Google Takvim'e etkinlik ekleyebilir.

Calistirmadan once:
  pip install google-auth-oauthlib

  ve Google Cloud Console'dan indirdiginiz OAuth istemci dosyasini bu script
  ile ayni klasore "client_secret.json" adiyla koyun (KURULUM.md'de anlatildi).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n\nBASARILI! Asagidaki degerleri Render'daki Environment Variables'a girin:\n")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("\nBu bilgileri kimseyle paylasmayin, tipki bir sifre gibi davranin.")


if __name__ == "__main__":
    main()

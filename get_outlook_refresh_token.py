"""
Bu script'i SADECE KENDI BILGISAYARINIZDA, bir KEZ calistirin.
Render sunucusunda calistirmayin.

Ne yapar:
  Microsoft hesabiniz (Outlook/Hotmail) icin "cihaz kodu" (device code) ile
  giris yaptirip, botun sizin adiniza gelen kutunuzu okuyabilmesi (Mail.Read)
  icin bir REFRESH TOKEN uretir. Outlook artik eski usul "uygulama sifresi"
  ile IMAP baglantisini kabul etmiyor (Microsoft 2026'da bunu tamamen
  kaldirdi), bu yuzden OAuth gerekiyor.

Calistirmadan once:
  pip install msal

  ve KURULUM.md'de anlatildigi gibi Azure Portal'da bir "App registration"
  olusturup CLIENT_ID degerini asagiya yapistirin.
"""

import msal

CLIENT_ID = "BURAYA_AZURE_CLIENT_ID_YAPISTIRIN"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["https://graph.microsoft.com/Mail.Read", "offline_access"]


def main():
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("Cihaz kodu akisi baslatilamadi:", flow)
        return

    print(flow["message"])  # "https://microsoft.com/devicelogin adresine gidin, XXXX-XXXX kodunu girin"
    result = app.acquire_token_by_device_flow(flow)

    if "refresh_token" not in result:
        print("Basarisiz:", result.get("error"), result.get("error_description"))
        return

    print("\n\nBASARILI! Asagidaki degerleri Render'daki Environment Variables'a girin:\n")
    print(f"OUTLOOK_CLIENT_ID={CLIENT_ID}")
    print(f"OUTLOOK_REFRESH_TOKEN={result['refresh_token']}")
    print("\nBu bilgileri kimseyle paylasmayin, tipki bir sifre gibi davranin.")


if __name__ == "__main__":
    main()

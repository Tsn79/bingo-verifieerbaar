import requests
for i in range(100):
    requests.get("https://jouw-app.vercel.app/generate_card")
    print(f"Kaart {i+1}/100 gegenereerd")

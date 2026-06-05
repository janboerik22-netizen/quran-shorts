#!/usr/bin/env python3
import os, json, time, random, subprocess, requests
from datetime import datetime
from pathlib import Path

# Reciteur editions voor alquran.cloud API
RECITEURS = [
    {"naam": "Al-Dosari",  "edition": "ar.muhammadayyoub"},
    {"naam": "Mishary",    "edition": "ar.alafasy"},
    {"naam": "Sudais",     "edition": "ar.abdurrahmaansudais"},
    {"naam": "Al-Turki",   "edition": "ar.hanirifai"},
    {"naam": "Shamsan",    "edition": "ar.shaatree"},
]

VIDEOS_PER_DAG = 5
MAX_SEC = 40
WERKMAP = Path("./tmp")
API_BASE = "http://api.alquran.cloud/v1"
CDN_BASE = "https://cdn.islamic.network/quran/audio/128"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

SEGMENTEN = [
    (1,1,7),(112,1,4),(113,1,5),(114,1,6),(2,255,255),
    (2,285,286),(67,1,5),(36,1,12),(55,1,13),(18,1,10),
    (19,1,11),(56,1,12),(78,1,16),(87,1,19),(93,1,11),
    (94,1,8),(95,1,8),(97,1,5),(99,1,8),(108,1,3),
    (3,1,10),(4,1,10),(5,1,10),(7,1,10),(10,1,10),
]

THEMAS = ["0d1117","0a1628","1a0a00","0d0d1a","051a05","1a0a1a"]

def haal_ayah_nummers(surah, start, eind, edition):
    """Haal absolute ayah nummers op via alquran.cloud API"""
    nummers = []
    try:
        url = f"{API_BASE}/surah/{surah}/{edition}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"    API fout {r.status_code}")
            return nummers
        ayahs = r.json()["data"]["ayahs"]
        for ayah in ayahs:
            nr_in_surah = ayah["numberInSurah"]
            if start <= nr_in_surah <= eind:
                # Absoluut nummer in hele Quran + audio URL
                audio_url = ayah.get("audio", "")
                abs_nummer = ayah["number"]
                nummers.append((abs_nummer, audio_url))
    except Exception as e:
        print(f"    Fout bij ophalen ayah nummers: {e}")
    return nummers

def download_audio(nummers, edition, output):
    delen = []
    for i, (abs_nr, api_url) in enumerate(nummers):
        tmp = WERKMAP / f"t{i}.mp3"
        
        # Gebruik audio URL direct uit API response
        urls_om_te_proberen = []
        if api_url:
            urls_om_te_proberen.append(api_url)
        # Fallback: CDN met absoluut nummer
        urls_om_te_proberen.append(f"{CDN_BASE}/{edition}/{abs_nr}.mp3")
        
        gedownload = False
        for url in urls_om_te_proberen:
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 500:
                    tmp.write_bytes(r.content)
                    delen.append(str(tmp))
                    print(f"    ✅ Ayah {i+1} ({len(r.content)//1024}KB)")
                    gedownload = True
                    break
                else:
                    print(f"    ⚠ {r.status_code} voor {url[:60]}")
            except Exception as e:
                print(f"    ⚠ {e}")
        
        if not gedownload:
            print(f"    ❌ Ayah {i+1} mislukt")

    if not delen:
        return False

    if len(delen) == 1:
        import shutil; shutil.move(delen[0], output)
    else:
        lp = WERKMAP / "concat.txt"
        lp.write_text("\n".join(f"file '{p}'" for p in delen))
        subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(lp),
                        "-c", "copy", str(output), "-y"], capture_output=True)
        for d in delen:
            try: os.remove(d)
            except: pass

    return Path(output).exists()

def maak_video(audio, surah, start, naam, output):
    k = random.choice(THEMAS)
    naam_veilig = naam.replace("'","").replace('"',"")
    vf = (
        f"drawtext=text='{naam_veilig}':fontsize=60:fontcolor=#FFD700:"
        f"x=(w-text_w)/2:y=160:shadowcolor=black@0.8:shadowx=3:shadowy=3,"
        f"drawtext=text='Surah {surah}  Ayah {start}':fontsize=44:fontcolor=white:"
        f"x=(w-text_w)/2:y=260:shadowcolor=black@0.6:shadowx=2:shadowy=2,"
        f"drawtext=text='Quran Daily Recitation':fontsize=36:fontcolor=white@0.7:"
        f"x=(w-text_w)/2:y=h/2-20,"
        f"drawtext=text='QuranShorts  Islam  Quran':fontsize=28:fontcolor=#FFD700@0.5:"
        f"x=(w-text_w)/2:y=h-160"
    )
    cmd = [
        "ffmpeg", "-f", "lavfi", "-i", f"color=c=#{k}:size=1080x1920:rate=30",
        "-i", str(audio), "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-shortest", "-t", str(MAX_SEC),
        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
        "-c:a", "aac", "-b:a", "128k",
        str(output), "-y"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ffmpeg fout: {r.stderr[-200:]}")
    return r.returncode == 0

def upload(video, titel, beschr, tags):
    client_id     = os.environ.get("YT_CLIENT_ID","")
    client_secret = os.environ.get("YT_CLIENT_SECRET","")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN","")
    if not all([client_id, client_secret, refresh_token]):
        print("    GEEN SECRETS"); return None

    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"
    })
    at = r.json().get("access_token")
    if not at:
        print(f"    Token fout: {r.text[:100]}"); return None

    meta = {
        "snippet": {"title": titel[:100], "description": beschr, "tags": tags, "categoryId": "22"},
        "status":  {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }
    r2 = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json",
                 "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(video.stat().st_size)},
        json=meta
    )
    if r2.status_code != 200:
        print(f"    Upload init fout: {r2.text[:100]}"); return None

    with open(video, "rb") as f:
        r3 = requests.put(r2.headers["Location"], headers={"Content-Type":"video/mp4"}, data=f)

    if r3.status_code in (200, 201):
        vid_id = r3.json().get("id","?")
        print(f"    ✅ https://youtu.be/{vid_id}")
        return vid_id
    print(f"    Upload fout: {r3.text[:100]}"); return None

def laad_voortgang():
    pad = Path("voortgang.json")
    return json.loads(pad.read_text()) if pad.exists() else {"idx":0,"totaal":0,"gedaan":[]}

def sla_voortgang(data):
    Path("voortgang.json").write_text(json.dumps(data, indent=2))

def run():
    print(f"\n{'='*52}")
    print(f"  QURAN SHORTS BOT v4  {datetime.now().strftime('%H:%M  %d/%m/%Y')}")
    print(f"{'='*52}\n")

    WERKMAP.mkdir(parents=True, exist_ok=True)
    vg = laad_voortgang()
    idx = vg["idx"]
    geplaatst = 0

    for i in range(VIDEOS_PER_DAG):
        rec = RECITEURS[i % len(RECITEURS)]
        seg = SEGMENTEN[idx % len(SEGMENTEN)]
        surah, start, eind = seg
        sleutel = f"{rec['naam']}_{surah}_{start}"

        if sleutel in vg["gedaan"]:
            idx += 1; continue

        print(f"\n[{i+1}/{VIDEOS_PER_DAG}] {rec['naam']} | Surah {surah}:{start}-{eind}")

        audio = WERKMAP / f"audio_{i}.mp3"
        video = WERKMAP / f"video_{i}.mp4"

        print("  Audio ophalen via alquran.cloud API...")
        nummers = haal_ayah_nummers(surah, start, eind, rec["edition"])
        if not nummers:
            print("  Geen ayah nummers gevonden"); idx += 1; continue

        print(f"  {len(nummers)} ayahs downloaden...")
        if not download_audio(nummers, rec["edition"], audio):
            print("  Download mislukt"); idx += 1; continue

        print("  Video renderen...")
        if not maak_video(audio, surah, start, rec["naam"], video):
            print("  Video mislukt"); idx += 1; continue

        nr = vg["totaal"] + 1
        titel = f"Surah {surah} | {rec['naam']} | Quran Short #{nr}"
        beschr = (f"Recitatie door {rec['naam']}\nSurah {surah}, Ayah {start}-{eind}\n\n"
                  f"#Quran #QuranShorts #Islam #{rec['naam'].replace(' ','')} #Shorts #DailyQuran")
        tags = ["Quran","QuranShorts","Islam","Shorts",rec["naam"],f"Surah{surah}","DailyQuran"]

        print("  Uploaden naar YouTube...")
        vid = upload(video, titel, beschr, tags)

        for p in [audio, video]:
            try: p.unlink()
            except: pass

        if vid:
            vg["totaal"] += 1
            vg["gedaan"].append(sleutel)
            geplaatst += 1
            sla_voortgang(vg)
            if i < VIDEOS_PER_DAG - 1:
                print("  Wachten 30s..."); time.sleep(30)

        idx += 1

    vg["idx"] = idx
    sla_voortgang(vg)
    print(f"\n{'='*52}")
    print(f"  Klaar! {geplaatst}/{VIDEOS_PER_DAG} geplaatst | Totaal: {vg['totaal']}")
    print(f"{'='*52}\n")

if __name__ == "__main__":
    run()

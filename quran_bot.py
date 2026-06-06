#!/usr/bin/env python3
import os, json, time, random, subprocess, requests, textwrap
from datetime import datetime
from pathlib import Path

# ─── RECITEURS ────────────────────────────────────────────────────────────────
RECITEURS = [
    {"naam": "Al-Dosari",  "edition": "ar.muhammadayyoub"},
    {"naam": "Mishary",    "edition": "ar.alafasy"},
    {"naam": "Sudais",     "edition": "ar.abdurrahmaansudais"},
    {"naam": "Al-Afasy",   "edition": "ar.alafasy"},      # vervanger voor Al-Turki
    {"naam": "Shamsan",    "edition": "ar.shaatree"},
]

# ─── INSTELLINGEN ─────────────────────────────────────────────────────────────
VIDEOS_PER_DAG = 5
MAX_SEC        = 59
WERKMAP        = Path("./tmp")
API_BASE       = "http://api.alquran.cloud/v1"
CDN_BASE       = "https://cdn.islamic.network/quran/audio/128"
HEADERS        = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

# Achtergrond — gratis Unsplash moskee foto (vaste URL, geen API key nodig)
BG_URL  = "https://images.unsplash.com/photo-1584551246679-0daf3d275d0f?w=1080&q=85"
BG_PAD  = WERKMAP / "moskee_bg.jpg"

# ─── SEGMENTEN ────────────────────────────────────────────────────────────────
SEGMENTEN = [
    (1,1,7),(112,1,4),(113,1,5),(114,1,6),(2,255,255),
    (2,285,286),(67,1,5),(36,1,12),(55,1,13),(18,1,10),
    (19,1,11),(56,1,12),(78,1,16),(87,1,19),(93,1,11),
    (94,1,8),(95,1,8),(97,1,5),(99,1,8),(108,1,3),
    (3,1,10),(4,1,10),(5,1,10),(7,1,10),(10,1,10),
]

# ─── VERTALINGEN API ──────────────────────────────────────────────────────────
# Engels  : en.sahih   (Saheeh International)
# Nederlands : nl.keyzer (Mohammed Keyzer)
EN_EDITION = "en.sahih"
NL_EDITION = "nl.keyzer"

# ─── ACHTERGROND DOWNLOADEN ───────────────────────────────────────────────────
def zorg_achtergrond():
    """Download moskee achtergrond eenmalig."""
    if BG_PAD.exists() and BG_PAD.stat().st_size > 10_000:
        return True
    print("  📥 Achtergrondafbeelding downloaden...")
    try:
        r = requests.get(BG_URL, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            BG_PAD.write_bytes(r.content)
            print(f"  ✅ Achtergrond opgeslagen ({len(r.content)//1024}KB)")
            return True
    except Exception as e:
        print(f"  ⚠ Achtergrond download mislukt: {e}")
    return False

# ─── AYAH DATA OPHALEN ────────────────────────────────────────────────────────
def haal_ayah_data(surah, start, eind, edition):
    """
    Haalt per ayah op:
      - absoluut nummer
      - audio URL
      - Arabische tekst
      - Engelse vertaling
      - Nederlandse vertaling
    Geeft lijst van dicts terug.
    """
    ayahs = []
    try:
        # Arabisch + audio
        url_ar = f"{API_BASE}/surah/{surah}/{edition}"
        r_ar   = requests.get(url_ar, headers=HEADERS, timeout=20)
        if r_ar.status_code != 200:
            print(f"    AR API fout {r_ar.status_code}"); return ayahs
        data_ar = r_ar.json()["data"]["ayahs"]

        # Engelse vertaling
        url_en = f"{API_BASE}/surah/{surah}/{EN_EDITION}"
        r_en   = requests.get(url_en, headers=HEADERS, timeout=20)
        data_en = r_en.json()["data"]["ayahs"] if r_en.status_code == 200 else []

        # Nederlandse vertaling
        url_nl = f"{API_BASE}/surah/{surah}/{NL_EDITION}"
        r_nl   = requests.get(url_nl, headers=HEADERS, timeout=20)
        data_nl = r_nl.json()["data"]["ayahs"] if r_nl.status_code == 200 else []

        en_map = {a["numberInSurah"]: a["text"] for a in data_en}
        nl_map = {a["numberInSurah"]: a["text"] for a in data_nl}

        for ayah in data_ar:
            nr = ayah["numberInSurah"]
            if start <= nr <= eind:
                ayahs.append({
                    "abs":   ayah["number"],
                    "nr":    nr,
                    "audio": ayah.get("audio", ""),
                    "ar":    ayah.get("text", ""),
                    "en":    en_map.get(nr, ""),
                    "nl":    nl_map.get(nr, ""),
                })
    except Exception as e:
        print(f"    Fout bij ophalen data: {e}")
    return ayahs

# ─── AUDIO DOWNLOADEN ─────────────────────────────────────────────────────────
def download_audio_lijst(ayahs, edition, output):
    delen = []
    for i, ay in enumerate(ayahs):
        tmp  = WERKMAP / f"t{i}.mp3"
        urls = []
        if ay["audio"]:
            urls.append(ay["audio"])
        urls.append(f"{CDN_BASE}/{edition}/{ay['abs']}.mp3")

        ok = False
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 500:
                    tmp.write_bytes(r.content)
                    ay["audio_pad"] = str(tmp)
                    delen.append(tmp)
                    print(f"    ✅ Ayah {i+1} ({len(r.content)//1024}KB)")
                    ok = True
                    break
            except Exception as e:
                print(f"    ⚠ {e}")
        if not ok:
            ay["audio_pad"] = None

    print(f"    {len(delen)}/{len(ayahs)} ayahs succesvol gedownload")
    if not delen:
        return False

    output = Path(output)
    if len(delen) == 1:
        delen[0].rename(output)
    else:
        lp = WERKMAP / "concat.txt"
        lp.write_text("\n".join(f"file '{p.resolve()}'" for p in delen))
        result = subprocess.run(
            ["ffmpeg", "-f", "concat", "-safe", "0",
             "-i", str(lp.resolve()), "-c", "copy", str(output), "-y"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"    concat fout: {result.stderr[-150:]}")
        for d in delen:
            try: d.unlink()
            except: pass

    return output.exists()

# ─── AUDIO DURATIES METEN ─────────────────────────────────────────────────────
def meet_duraties(ayahs, edition):
    """
    Meet de duur van elk ayah-audiobestand zodat we weten
    hoe lang elke tekst getoond moet worden.
    Vult ayah["duur"] in (seconden, float).
    """
    for i, ay in enumerate(ayahs):
        pad = WERKMAP / f"dur_{i}.mp3"
        urls = []
        if ay["audio"]:
            urls.append(ay["audio"])
        urls.append(f"{CDN_BASE}/{edition}/{ay['abs']}.mp3")

        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 500:
                    pad.write_bytes(r.content)
                    result = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                         str(pad)],
                        capture_output=True, text=True
                    )
                    try:
                        ay["duur"] = float(result.stdout.strip())
                    except:
                        ay["duur"] = 4.0
                    try: pad.unlink()
                    except: pass
                    break
            except:
                pass
        if "duur" not in ay:
            ay["duur"] = 4.0

# ─── TEKST WRAPPEN ────────────────────────────────────────────────────────────
def wrap(tekst, breedte=32):
    """Wikkel lange tekst naar meerdere regels."""
    if not tekst:
        return ""
    regels = textwrap.wrap(tekst, width=breedte)
    return r"\n".join(regels)  # ffmpeg escape

def escape_ffmpeg(tekst):
    """Escape speciale tekens voor ffmpeg drawtext."""
    return (tekst
        .replace("\\", "\\\\")
        .replace("'",  "\u2019")   # rechte apostrof → typografisch
        .replace(":",  r"\:")
        .replace(",",  r"\,")
        .replace("[",  r"\[")
        .replace("]",  r"\]")
    )

# ─── VIDEO RENDEREN ───────────────────────────────────────────────────────────
def maak_video(audio, ayahs, surah, naam, output, gebruik_bg):
    """
    Bouwt een 1080×1920 Short met:
      - Moskee achtergrond (donker overlay)
      - Reciteur naam + Surah info bovenin
      - Per-ayah gesynchroniseerde tekst onderin:
          Arabisch (groot, wit/goud)
          Engels   (medium, wit)
          Nederlands (medium, lichtgrijs)
    """
    naam_veilig = escape_ffmpeg(naam)

    # ── Bouw drawtext filters ──────────────────────────────────────────────
    filters = []

    # 1) Achtergrond schalen naar 1080×1920
    if gebruik_bg:
        schaal = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        # Donker overlay via colorchannelmixer
        overlay = "colorchannelmixer=rr=0.4:gg=0.4:bb=0.4"
        vf_basis = f"{schaal},{overlay}"
    else:
        vf_basis = None   # color filter — zie cmd hieronder

    # 2) Header: reciteur naam
    filters.append(
        f"drawtext=text='{naam_veilig}':"
        f"fontsize=58:fontcolor=#FFD700:"
        f"x=(w-text_w)/2:y=120:"
        f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
        f"box=1:boxcolor=black@0.4:boxborderw=12"
    )
    # 3) Header: Surah info
    filters.append(
        f"drawtext=text='Surah {surah}':"
        f"fontsize=42:fontcolor=white:"
        f"x=(w-text_w)/2:y=200:"
        f"shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )

    # 4) Per-ayah tekst blokken (gesynchroniseerd via enable='between(t,...)')
    t = 0.0
    for ay in ayahs:
        duur   = ay.get("duur", 4.0)
        t_end  = t + duur
        tijdw  = f"between(t,{t:.2f},{t_end:.2f})"

        ar_tekst = escape_ffmpeg(wrap(ay.get("ar",""), 26))
        en_tekst = escape_ffmpeg(wrap(ay.get("en",""), 36))
        nl_tekst = escape_ffmpeg(wrap(ay.get("nl",""), 36))

        # Tekstblok gecentreerd iets onder het midden (y=960 = midden van 1920)
        y_ar = "680"
        y_en = "840"
        y_nl = "980"

        if ar_tekst:
            filters.append(
                f"drawtext=text='{ar_tekst}':"
                f"fontsize=44:fontcolor=#FFD700:"
                f"x=(w-text_w)/2:y={y_ar}:"
                f"shadowcolor=black@0.95:shadowx=3:shadowy=3:"
                f"box=1:boxcolor=black@0.6:boxborderw=16:"
                f"enable='{tijdw}'"
            )
        if en_tekst:
            filters.append(
                f"drawtext=text='{en_tekst}':"
                f"fontsize=30:fontcolor=white:"
                f"x=(w-text_w)/2:y={y_en}:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
                f"box=1:boxcolor=black@0.55:boxborderw=12:"
                f"enable='{tijdw}'"
            )
        if nl_tekst:
            filters.append(
                f"drawtext=text='{nl_tekst}':"
                f"fontsize=30:fontcolor=#DDDDDD:"
                f"x=(w-text_w)/2:y={y_nl}:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
                f"box=1:boxcolor=black@0.55:boxborderw=12:"
                f"enable='{tijdw}'"
            )
        t = t_end

    # 5) Footer label
    filters.append(
        f"drawtext=text='QuranShorts  |  Islam  |  Quran':"
        f"fontsize=26:fontcolor=#FFD700@0.6:"
        f"x=(w-text_w)/2:y=h-80:"
        f"shadowcolor=black@0.7:shadowx=1:shadowy=1"
    )

    vf_tekst = ",".join(filters)

    # ── ffmpeg commando ────────────────────────────────────────────────────
    if gebruik_bg and BG_PAD.exists():
        vf_volledig = f"{vf_basis},{vf_tekst}"
        cmd = [
            "ffmpeg",
            "-loop", "1", "-i", str(BG_PAD),
            "-i", str(audio),
            "-vf", vf_volledig,
            "-map", "0:v", "-map", "1:a",
            "-shortest", "-t", str(MAX_SEC),
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            str(output), "-y"
        ]
    else:
        # Fallback: donkerblauwe achtergrond
        cmd = [
            "ffmpeg",
            "-f", "lavfi", "-i", "color=c=#0a1628:size=1080x1920:rate=30",
            "-i", str(audio),
            "-vf", vf_tekst,
            "-map", "0:v", "-map", "1:a",
            "-shortest", "-t", str(MAX_SEC),
            "-c:v", "libx264", "-preset", "fast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            str(output), "-y"
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ffmpeg fout: {r.stderr[-300:]}")
    return r.returncode == 0

# ─── YOUTUBE UPLOAD ───────────────────────────────────────────────────────────
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
        "snippet": {
            "title": titel[:100],
            "description": beschr,
            "tags": tags,
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    r2 = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {at}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video.stat().st_size)
        },
        json=meta
    )
    if r2.status_code != 200:
        print(f"    Upload init fout: {r2.text[:100]}"); return None

    with open(video, "rb") as f:
        r3 = requests.put(
            r2.headers["Location"],
            headers={"Content-Type": "video/mp4"},
            data=f
        )

    if r3.status_code in (200, 201):
        vid_id = r3.json().get("id","?")
        print(f"    ✅ https://youtu.be/{vid_id}")
        return vid_id
    print(f"    Upload fout: {r3.text[:100]}"); return None

# ─── VOORTGANG ────────────────────────────────────────────────────────────────
def laad_voortgang():
    pad = Path("voortgang.json")
    return json.loads(pad.read_text()) if pad.exists() else {"idx":0,"totaal":0,"gedaan":[]}

def sla_voortgang(data):
    Path("voortgang.json").write_text(json.dumps(data, indent=2))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print(f"\n{'='*52}")
    print(f"  QURAN SHORTS BOT v5  {datetime.now().strftime('%H:%M  %d/%m/%Y')}")
    print(f"{'='*52}\n")

    WERKMAP.mkdir(parents=True, exist_ok=True)

    # Achtergrond downloaden
    gebruik_bg = zorg_achtergrond()

    vg       = laad_voortgang()
    idx      = vg["idx"]
    geplaatst = 0

    for i in range(VIDEOS_PER_DAG):
        rec    = RECITEURS[i % len(RECITEURS)]
        seg    = SEGMENTEN[idx % len(SEGMENTEN)]
        surah, start, eind = seg
        sleutel = f"{rec['naam']}_{surah}_{start}"

        if sleutel in vg["gedaan"]:
            idx += 1; continue

        print(f"\n[{i+1}/{VIDEOS_PER_DAG}] {rec['naam']} | Surah {surah}:{start}-{eind}")

        audio = WERKMAP / f"audio_{i}.mp3"
        video = WERKMAP / f"video_{i}.mp4"

        # ── Data ophalen (audio + 3 talen) ──────────────────────────────
        print("  Data ophalen (Arabisch + vertalingen)...")
        ayahs = haal_ayah_data(surah, start, eind, rec["edition"])
        if not ayahs:
            print("  Geen ayah data"); idx += 1; continue

        # ── Audio duraties meten ─────────────────────────────────────────
        print("  Ayah duraties meten...")
        meet_duraties(ayahs, rec["edition"])

        # ── Audio downloaden ─────────────────────────────────────────────
        print(f"  {len(ayahs)} ayahs downloaden...")
        if not download_audio_lijst(ayahs, rec["edition"], audio):
            print("  Download mislukt"); idx += 1; continue

        # ── Video renderen ───────────────────────────────────────────────
        print("  Video renderen...")
        if not maak_video(audio, ayahs, surah, rec["naam"], video, gebruik_bg):
            print("  Video mislukt"); idx += 1; continue

        # ── Upload ──────────────────────────────────────────────────────
        nr     = vg["totaal"] + 1
        titel  = f"Surah {surah} | {rec['naam']} | Quran Short #{nr}"
        beschr = (
            f"📖 Recitatie door {rec['naam']}\n"
            f"Surah {surah}, Ayah {start}-{eind}\n\n"
            f"🌍 Arabisch • Engels • Nederlands\n\n"
            f"#Quran #QuranShorts #Islam #{rec['naam'].replace(' ','')} "
            f"#Shorts #DailyQuran #Surah{surah}"
        )
        tags = ["Quran","QuranShorts","Islam","Shorts",
                rec["naam"], f"Surah{surah}", "DailyQuran",
                "QuranRecitation","IslamicContent"]

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

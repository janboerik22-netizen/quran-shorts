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

# ─── TEKST HELPERS ────────────────────────────────────────────────────────────
def escape_ffmpeg(tekst):
    return (tekst
        .replace("\\", "\\\\")
        .replace("'",  "\u2019")
        .replace(":",  r"\:")
        .replace(",",  r"\,")
        .replace("[",  r"\[")
        .replace("]",  r"\]")
    )

def wrap_en_escape(tekst, breedte=32):
    if not tekst:
        return ""
    regels = textwrap.wrap(tekst, width=breedte)
    regels_escaped = [escape_ffmpeg(r) for r in regels]
    return r"\n".join(regels_escaped)

# ─── VIDEO RENDEREN ────────────────────────────────────────────────────────────
def maak_video(audio, ayahs, surah, naam, output, bg_pad, totale_duur):
    naam_veilig = escape_ffmpeg(naam)
    filters = []

    ar_font = f"font='{ARABISCH_FONTNAAM}':" if _fontconfig_beschikbaar() else ""
    std_font = f"font='{STANDAARD_FONTNAAM}':" if _fontconfig_beschikbaar() else ""

    if bg_pad and Path(bg_pad).exists():
        fps = 30
        totaal_frames = max(1, int(totale_duur * fps))
        zoom_expr = f"zoom+0.0006"
        vf_basis = (
            f"scale=1350:2400,"
            f"zoompan=z='min({zoom_expr},1.25)':d={totaal_frames}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps},"
            f"colorchannelmixer=rr=0.35:gg=0.35:bb=0.35,"
            f"vignette=PI/3.2"
        )
    else:
        vf_basis = None

    filters.append(
        f"drawtext={std_font}text='{naam_veilig}':"
        f"fontsize=58:fontcolor=#FFD700:"
        f"x=(w-text_w)/2:y=110:"
        f"shadowcolor=black@0.9:shadowx=3:shadowy=3:"
        f"box=1:boxcolor=black@0.45:boxborderw=14"
    )
    filters.append(
        f"drawtext={std_font}text='Surah {surah}':"
        f"fontsize=40:fontcolor=white:"
        f"x=(w-text_w)/2:y=195:"
        f"shadowcolor=black@0.8:shadowx=2:shadowy=2"
    )

    filters.append(
        f"drawbox=x=0:y=h-10:w='iw*t/{totale_duur:.2f}':h=8:"
        f"color=#FFD700@0.85:t=fill"
    )
    filters.append(
        f"drawbox=x=0:y=h-10:w=iw:h=8:color=white@0.15:t=fill:enable='lt(t,0)'"
    )

    t = 0.0
    for ay in ayahs:
        duur   = ay.get("duur", 4.0)
        t_end  = t + duur
        tijdw  = f"between(t,{t:.2f},{t_end:.2f})"

        ar_tekst = wrap_en_escape(ay.get("ar",""), 26)
        en_tekst = wrap_en_escape(ay.get("en",""), 36)
        nl_tekst = wrap_en_escape(ay.get("nl",""), 36)

        y_ar = "660"
        y_en = "830"
        y_nl = "975"

        if ar_tekst:
            filters.append(
                f"drawtext={ar_font}text='{ar_tekst}':"
                f"fontsize=46:fontcolor=#FFD700:"
                f"x=(w-text_w)/2:y={y_ar}:"
                f"shadowcolor=black@0.95:shadowx=3:shadowy=3:"
                f"box=1:boxcolor=black@0.65:boxborderw=18:"
                f"line_spacing=10:"
                f"enable='{tijdw}'"
            )
        if en_tekst:
            filters.append(
                f"drawtext={std_font}text='{en_tekst}':"
                f"fontsize=30:fontcolor=white:"
                f"x=(w-text_w)/2:y={y_en}:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
                f"box=1:boxcolor=black@0.55:boxborderw=12:"
                f"enable='{tijdw}'"
            )
        if nl_tekst:
            filters.append(
                f"drawtext={std_font}text='{nl_tekst}':"
                f"fontsize=30:fontcolor=#DDDDDD:"
                f"x=(w-text_w)/2:y={y_nl}:"
                f"shadowcolor=black@0.9:shadowx=2:shadowy=2:"
                f"box=1:boxcolor=black@0.55:boxborderw=12:"
                f"enable='{tijdw}'"
            )
        t = t_end

    filters.append(
        f"drawtext={std_font}text='QuranShorts  |  Islam  |  Quran':"
        f"fontsize=26:fontcolor=#FFD700@0.6:"
        f"x=(w-text_w)/2:y=h-45:"
        f"shadowcolor=black@0.7:shadowx=1:shadowy=1"
    )

    vf_tekst = ",".join(filters)

    if vf_basis:
        vf_volledig = f"{vf_basis},{vf_tekst}"
        cmd = [
            "ffmpeg",
            "-loop", "1", "-i", str(bg_pad),
            "-i", str(audio),
            "-vf", vf_volledig,
            "-map", "0:v", "-map", "1:a",
            "-shortest", "-t", str(MAX_SEC),
            "-c:v", "libx264", "-preset", "fast", "-crf", "24",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            str(output), "-y"
        ]
    else:
        cmd = [
            "ffmpeg",
            "-f", "lavfi", "-i", "color=c=#0a1628:size=1080x1920:rate=30",
            "-i", str(audio),
            "-vf", vf_tekst,
            "-map", "0:v", "-map", "1:a",
            "-shortest", "-t", str(MAX_SEC),
            "-c:v", "libx264", "-preset", "fast", "-crf", "24",
            "-c:a", "aac", "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            str(output), "-y"
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    ffmpeg fout: {r.stderr[-300:]}")
    return r.returncode == 0

# ─── YOUTUBE UPLOAD ────────────────────────────────────────────────────────────
class QuotaExceeded(Exception):
    pass

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
        body = r2.text.lower()
        if r2.status_code == 403 and ("quotaexceeded" in body or "quota" in body):
            print(f"    ⛔ QUOTA OP voor vandaag: {r2.text[:150]}")
            raise QuotaExceeded(r2.text[:200])
        print(f"    Upload init fout: {r2.text[:150]}")
        return None

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

    body = r3.text.lower()
    if r3.status_code == 403 and "quota" in body:
        print(f"    ⛔ QUOTA OP tijdens upload: {r3.text[:150]}")
        raise QuotaExceeded(r3.text[:200])

    print(f"    Upload fout: {r3.text[:150]}"); return None

# ─── VOORTGANG ────────────────────────────────────────────────────────────────
def laad_voortgang():
    pad = Path("voortgang.json")
    return json.loads(pad.read_text()) if pad.exists() else {"idx":0,"totaal":0,"gedaan":[]}

def sla_voortgang(data):
    Path("voortgang.json").write_text(json.dumps(data, indent=2))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print(f"\n{'='*52}")
    print(f"  QURAN SHORTS BOT v6  {datetime.now().strftime('%H:%M  %d/%m/%Y')}")
    print(f"{'='*52}\n")

    WERKMAP.mkdir(parents=True, exist_ok=True)

    bg_paden = zorg_achtergronden()

    vg        = laad_voortgang()
    idx       = vg["idx"]
    geplaatst = 0
    quota_op  = False

    for i in range(VIDEOS_PER_DAG):
        if quota_op:
            break

        rec    = RECITEURS[i % len(RECITEURS)]
        seg    = SEGMENTEN[idx % len(SEGMENTEN)]
        surah, start, eind = seg
        sleutel = f"{rec['naam']}_{surah}_{start}"

        if sleutel in vg["gedaan"]:
            idx += 1; continue

        print(f"\n[{i+1}/{VIDEOS_PER_DAG}] {rec['naam']} | Surah {surah}:{start}-{eind}")

        audio = WERKMAP / f"audio_{i}.mp3"
        video = WERKMAP / f"video_{i}.mp4"

        print("  Data ophalen (Arabisch + vertalingen)...")
        ayahs = haal_ayah_data(surah, start, eind, rec["edition"])
        if not ayahs:
            print("  Geen ayah data"); idx += 1; continue

        print("  Ayah duraties meten...")
        meet_duraties(ayahs, rec["edition"])
        totale_duur = min(sum(a.get("duur", 4.0) for a in ayahs), MAX_SEC)

        print(f"  {len(ayahs)} ayahs downloaden...")
        if not download_audio_lijst(ayahs, rec["edition"], audio):
            print("  Download mislukt"); idx += 1; continue

        print("  Video renderen (met zoom + voortgangsbalk)...")
        bg_pad = random.choice(bg_paden) if bg_paden else None
        if not maak_video(audio, ayahs, surah, rec["naam"], video, bg_pad, totale_duur):
            print("  Video mislukt"); idx += 1; continue

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

        # ── TESTMODUS: upload uitgeschakeld, geen quota verbruik ───────────
        print(f"  🎬 Video opgeslagen: {video.resolve()}")
        input("  Bekijk 'm, druk Enter om door te gaan...")
        vid = "test"

        for p in [audio]:
            try: p.unlink()
            except: pass

        if vid:
            vg["totaal"] += 1
            vg["gedaan"].append(sleutel)
            geplaatst += 1
            sla_voortgang(vg)
            if i < VIDEOS_PER_DAG - 1 and not quota_op:
                print("  Wachten 30s..."); time.sleep(30)

        idx += 1

    vg["idx"] = idx
    sla_voortgang(vg)
    print(f"\n{'='*52}")
    if quota_op:
        print("  ⛔ Gestopt: YouTube quota voor vandaag is op.")
        print("     Morgen gaat de bot automatisch verder (voortgang.json bewaard).")
    print(f"  Klaar! {geplaatst}/{VIDEOS_PER_DAG} geplaatst | Totaal: {vg['totaal']}")
    print(f"{'='*52}\n")

if __name__ == "__main__":
    run()

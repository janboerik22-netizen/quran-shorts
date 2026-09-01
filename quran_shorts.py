#!/usr/bin/env python3
"""
Quran Shorts Bot v7
===================

Eén bestand. Arabisch + Engels + Nederlands, per-ayah gesynchroniseerd
met de recitatie.

Gebruik:
    python quran_shorts.py --demo            # offline testrender, geen netwerk
    python quran_shorts.py --count 1 --dry-run   # echte data, geen upload
    python quran_shorts.py --count 5             # volledige run

Belangrijkste verschil met v6: de tekst wordt door Pillow gerenderd naar
transparante PNG's die FFmpeg alleen nog overheen legt. FFmpeg raakt geen
letter Arabisch aan. Zie NOTES onderaan dit bestand voor het waarom.

FIX (v7.1): reciteur-selectie gebruikte i % len(RECITEURS). Met
--count 1 (zoals de workflow gebruikt) is i altijd 0, dus werd ALTIJD
dezelfde reciteur gekozen. Zodra alle SEGMENTEN met die ene reciteur
ooit geplaatst waren, sloeg de bot voor altijd alles over (stille no-op,
geen fout). Nu gebruikt reciteur-selectie idx, die wél persistent
doorloopt over losse workflow-runs heen. Zie main().
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, features

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).parent.resolve()
WERKMAP = ROOT / "werk"
OUTPUT = ROOT / "output"
ACHTERGRONDEN = ROOT / "assets" / "backgrounds"
VOORTGANG = ROOT / "voortgang.json"

VIDEOS_PER_DAG = 5
MAX_SEC = 58                      # Shorts-limiet is 60s, marge houden
FPS = 30
W, H = 1080, 1920

API = "https://api.alquran.cloud/v1"
CDN_BASE = "https://cdn.islamic.network/quran/audio/128"
HEADERS = {"User-Agent": "quran-shorts-bot/7.0"}

# Tekst-edities (alquran.cloud). Controleer met --lijst-edities.
ED_ARABISCH = "quran-uthmani"      # mét harakat
ED_ENGELS = "en.sahih"
ED_NEDERLANDS = "nl.siregar"

RECITEURS = [
    {"naam": "Mishary Alafasy", "edition": "ar.alafasy"},
    {"naam": "Abdul Basit", "edition": "ar.abdulbasitmurattal"},
    {"naam": "Husary", "edition": "ar.husary"},
]

# (surah, start_ayah, eind_ayah) - houd segmenten kort genoeg voor 58s
SEGMENTEN = [
    (94, 1, 8), (103, 1, 3), (108, 1, 3), (110, 1, 3),
    (112, 1, 4), (113, 1, 5), (114, 1, 6), (99, 1, 8),
    (2, 255, 255), (55, 1, 13), (36, 1, 10), (67, 1, 5),
]

KLEUR_GOUD = (255, 215, 0, 255)
KLEUR_WIT = (255, 255, 255, 255)
KLEUR_GRIJS = (215, 215, 215, 255)

FONT_KANDIDATEN = {
    "quran": [
        ROOT / "assets/UthmanicHafs.ttf",
        Path("/usr/share/fonts/opentype/fonts-hosny-amiri/AmiriQuran.ttf"),
        Path("/usr/share/fonts/truetype/fonts-hosny-amiri/AmiriQuran.ttf"),
        Path("/usr/share/fonts/truetype/scheherazade/ScheherazadeNew-Regular.ttf"),
    ],
    "latin": [
        ROOT / "assets/NotoSans-Regular.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    ],
    "latin_bold": [
        ROOT / "assets/NotoSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ],
}


# ══════════════════════════════════════════════════════════════════════════
#  PREFLIGHT  —  faal luid en vroeg, niet stil en laat
# ══════════════════════════════════════════════════════════════════════════

def preflight() -> dict:
    """Controleert alles wat stil kan falen. Crasht liever nu dan na 5 uploads."""
    if not features.check("raqm"):
        raise SystemExit(
            "FOUT: Pillow is gebouwd zonder Raqm.\n"
            "Arabisch wordt dan niet geshaped: losse letters, verkeerde volgorde,\n"
            "en je krijgt GEEN foutmelding.\n"
            "Fix: sudo apt-get install -y libraqm0"
        )

    for tool in ("ffmpeg", "ffprobe"):
        r = subprocess.run([tool, "-version"], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"FOUT: {tool} niet gevonden op PATH.")

    fonts = {}
    for rol, kandidaten in FONT_KANDIDATEN.items():
        for pad in kandidaten:
            if Path(pad).exists():
                fonts[rol] = str(pad)
                break
        else:
            raise SystemExit(
                f"FOUT: geen font voor '{rol}'. Gezocht op:\n  "
                + "\n  ".join(str(k) for k in kandidaten)
                + "\n\nGitHub runners hebben geen Arabische fonts. Voeg toe aan je workflow:\n"
                  "  sudo apt-get install -y fonts-hosny-amiri fonts-dejavu-core"
            )

    print("  preflight  raqm OK | ffmpeg OK | fonts OK")
    return fonts


# ══════════════════════════════════════════════════════════════════════════
#  DATA OPHALEN
# ══════════════════════════════════════════════════════════════════════════

def _get_json(url: str, timeout: int = 30) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def haal_ayah_data(surah: int, start: int, eind: int, audio_edition: str) -> list[dict]:
    """
    Haalt Arabisch + EN + NL + audio-URLs op.

    Geen try/except om de hele functie heen: als de API faalt wil je de
    échte foutmelding zien, niet een lege lijst en 'Geen ayah data'.
    """
    ar = _get_json(f"{API}/surah/{surah}/{audio_edition}")["data"]["ayahs"]
    ar_txt = _get_json(f"{API}/surah/{surah}/{ED_ARABISCH}")["data"]["ayahs"]
    en = _get_json(f"{API}/surah/{surah}/{ED_ENGELS}")["data"]["ayahs"]
    nl = _get_json(f"{API}/surah/{surah}/{ED_NEDERLANDS}")["data"]["ayahs"]

    ar_map = {a["numberInSurah"]: a.get("text", "") for a in ar_txt}
    en_map = {a["numberInSurah"]: a.get("text", "") for a in en}
    nl_map = {a["numberInSurah"]: a.get("text", "") for a in nl}

    ayahs = []
    for a in ar:
        nr = a["numberInSurah"]
        if not (start <= nr <= eind):
            continue
        ayahs.append({
            "abs": a["number"],
            "nr": nr,
            "audio_url": a.get("audio", ""),
            "ar": ar_map.get(nr, "").strip(),
            "en": en_map.get(nr, "").strip(),
            "nl": nl_map.get(nr, "").strip(),
        })

    ontbreekt = [a["nr"] for a in ayahs if not (a["ar"] and a["en"] and a["nl"])]
    if ontbreekt:
        print(f"    let op: ayah {ontbreekt} mist een vertaling")
    return ayahs


# ══════════════════════════════════════════════════════════════════════════
#  AUDIO  —  één keer downloaden, daarna lokaal meten
# ══════════════════════════════════════════════════════════════════════════

def ffprobe_duur(pad: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(pad)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"ffprobe kon {pad.name} niet lezen: {r.stderr[-200:]}")
    return float(r.stdout.strip())


def download_audio(ayahs: list[dict], edition: str, out_mp3: Path) -> bool:
    """
    Downloadt elke ayah ÉÉN keer, meet meteen de duur van het lokale bestand,
    en plakt alles achter elkaar.

    v6 downloadde alles twee keer: één keer in meet_duraties(), één keer hier.
    """
    delen: list[Path] = []

    for i, ay in enumerate(ayahs):
        tmp = WERKMAP / f"ayah_{i:02d}.mp3"
        urls = [u for u in (ay.get("audio_url"),
                            f"{CDN_BASE}/{edition}/{ay['abs']}.mp3") if u]

        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
            except requests.RequestException as e:
                print(f"    netwerkfout ayah {ay['nr']}: {e}")
                continue
            if r.status_code == 200 and len(r.content) > 500:
                tmp.write_bytes(r.content)
                ay["duur"] = ffprobe_duur(tmp)      # meteen meten, lokaal
                delen.append(tmp)
                print(f"    ayah {ay['nr']:>3}  {len(r.content)//1024:>4}KB  "
                      f"{ay['duur']:.1f}s")
                break
        else:
            print(f"    ayah {ay['nr']}: geen audio gevonden, overgeslagen")
            ay["duur"] = None

    # Ayat zonder audio horen niet in de video: anders loopt je tekst
    # uit de pas met de recitatie.
    ayahs[:] = [a for a in ayahs if a.get("duur")]
    if not delen:
        return False

    if len(delen) == 1:
        delen[0].replace(out_mp3)
    else:
        lijst = WERKMAP / "concat.txt"
        lijst.write_text("\n".join(f"file '{p.resolve()}'" for p in delen))
        # Hercoderen i.p.v. -c copy: bronbestanden kunnen verschillende
        # samplerates hebben en dan krijg je kapotte timestamps.
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(lijst.resolve()),
             "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100",
             str(out_mp3)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"audio concat faalde: {r.stderr[-400:]}")
        for d in delen:
            d.unlink(missing_ok=True)

    return out_mp3.exists()


def kort_in_op_maxsec(ayahs: list[dict]) -> float:
    """Gooit ayat weg die niet meer binnen MAX_SEC passen."""
    totaal, houden = 0.0, []
    for ay in ayahs:
        if totaal + ay["duur"] > MAX_SEC:
            break
        houden.append(ay)
        totaal += ay["duur"]
    if not houden:                       # eerste ayah is al te lang
        houden = ayahs[:1]
        totaal = min(ayahs[0]["duur"], MAX_SEC)
    ayahs[:] = houden
    return totaal


# ══════════════════════════════════════════════════════════════════════════
#  TEKST RENDEREN  —  Pillow + Raqm, géén arabic-reshaper
# ══════════════════════════════════════════════════════════════════════════

def _wrap_px(d: ImageDraw.ImageDraw, tekst: str, font, max_w: int, **kw) -> list[str]:
    """
    Regelafbreking op GEMETEN PIXELBREEDTE.

    textwrap.wrap() telt tekens, en bij Uthmani-tekst is ~40% van de tekens
    een harakat zonder breedte. Je regels breken dan willekeurig veel te vroeg.
    """
    if not tekst:
        return []
    regels, huidig = [], ""
    for woord in tekst.split():
        test = f"{huidig} {woord}".strip()
        if d.textlength(test, font=font, **kw) <= max_w:
            huidig = test
        else:
            if huidig:
                regels.append(huidig)
            huidig = woord
    if huidig:
        regels.append(huidig)
    return regels


def _passend_font(d, tekst, font_pad, max_w, max_regels, start, minimum, **kw):
    """Verkleint het font tot de tekst in max_regels past."""
    grootte = start
    while grootte > minimum:
        f = ImageFont.truetype(font_pad, grootte)
        regels = _wrap_px(d, tekst, f, max_w, **kw)
        if len(regels) <= max_regels:
            return f, regels
        grootte -= 3
    f = ImageFont.truetype(font_pad, minimum)
    return f, _wrap_px(d, tekst, f, max_w, **kw)


def render_overlay(ayah: dict, fonts: dict, uit: Path) -> Path:
    """Transparante PNG met Arabisch + Engels + Nederlands voor één ayah."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    marge = 100
    max_w = W - 2 * marge
    ar_w = int(max_w * 0.95)          # speling voor RTL-meetafwijking

    # Ruwe Unicode + direction='rtl'. GEEN reshaper: die zet tekst om naar
    # Arabic Presentation Forms, en Qur'an-fonts bevatten dat blok niet.
    f_ar, ar_regels = _passend_font(
        d, ayah["ar"], fonts["quran"], ar_w,
        max_regels=3, start=64, minimum=38, language="ar", direction="rtl")

    f_lat = ImageFont.truetype(fonts["latin"], 34)
    en_regels = _wrap_px(d, ayah["en"], f_lat, max_w)
    nl_regels = _wrap_px(d, ayah["nl"], f_lat, max_w)

    AR_LH, LAT_LH = int(f_ar.size * 1.8), 46
    hoogte = (len(ar_regels) * AR_LH + 50 + len(en_regels) * LAT_LH
              + 30 + len(nl_regels) * LAT_LH)
    y = (H - hoogte) // 2

    d.rounded_rectangle(
        [marge - 40, y - 35, W - marge + 40, y + hoogte + 25],
        radius=28, fill=(0, 0, 0, 140))

    for regel in ar_regels:
        d.text((W // 2, y), regel, font=f_ar, fill=KLEUR_GOUD, anchor="ma",
               language="ar", direction="rtl")
        y += AR_LH

    y += 50
    for regel in en_regels:
        d.text((W // 2, y), regel, font=f_lat, fill=KLEUR_WIT, anchor="ma")
        y += LAT_LH
    y += 30
    for regel in nl_regels:
        d.text((W // 2, y), regel, font=f_lat, fill=KLEUR_GRIJS, anchor="ma")
        y += LAT_LH

    img.save(uit)
    return uit


def render_kop(surah: int, reciteur: str, fonts: dict, uit: Path) -> Path:
    """Statische kop- en voettekst. Blijft de hele video staan."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_titel = ImageFont.truetype(fonts["latin_bold"], 54)
    f_sub = ImageFont.truetype(fonts["latin"], 38)
    f_klein = ImageFont.truetype(fonts["latin"], 26)

    d.text((W // 2, 120), reciteur, font=f_titel, fill=KLEUR_GOUD, anchor="ma")
    d.text((W // 2, 195), f"Surah {surah}", font=f_sub, fill=KLEUR_WIT, anchor="ma")
    d.text((W // 2, H - 60), "Arabisch · English · Nederlands",
           font=f_klein, fill=(255, 215, 0, 160), anchor="ma")
    img.save(uit)
    return uit


def maak_voortgangsbalk(uit: Path, hoogte: int = 8) -> Path:
    Image.new("RGBA", (W, hoogte), (255, 215, 0, 217)).save(uit)
    return uit


# ══════════════════════════════════════════════════════════════════════════
#  VIDEO
# ══════════════════════════════════════════════════════════════════════════

def bouw_filtergraph(ayahs: list[dict], totale_duur: float, heeft_bg: bool,
                     n_overlays: int) -> str:
    if heeft_bg:
        # Ver boven de doelresolutie schalen vóór het zoomen. Zoompan rondt
        # af op hele pixels; bij een kleine bron zie je dat als trilling.
        basis = (f"[0:v]scale=3240:5760,"
                 f"zoompan=z='min(zoom+0.00035,1.20)':d=1:"
                 f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                 f"s={W}x{H}:fps={FPS},"
                 f"colorchannelmixer=rr=0.4:gg=0.4:bb=0.4,"
                 f"vignette=PI/3.2[bg]")
    else:
        basis = "[0:v]null[bg]"

    delen = [basis]
    vorig = "bg"
    t = 0.0
    for i, ay in enumerate(ayahs):
        eind = min(t + ay["duur"], totale_duur)
        label = f"v{i}"
        delen.append(
            f"[{vorig}][{2 + i}:v]overlay=0:0:"
            f"enable='between(t,{t:.3f},{eind:.3f})'[{label}]")
        vorig = label
        t = eind

    kop_idx = 2 + len(ayahs)
    delen.append(f"[{vorig}][{kop_idx}:v]overlay=0:0[vk]")

    # Voortgangsbalk via overlay, NIET via drawbox: drawbox heeft geen
    # eval=frame, dus daar wordt 't' eenmalig bij init berekend en staat
    # je balk altijd vol.
    balk_idx = kop_idx + 1
    delen.append(
        f"[vk][{balk_idx}:v]overlay="
        f"x='-{W}+{W}*t/{totale_duur:.2f}':y={H - 10}[outv]")

    return ";".join(delen)


def maak_video(audio: Path, ayahs: list[dict], overlays: list[Path],
               kop: Path, balk: Path, bg: Path | None,
               totale_duur: float, uit: Path) -> Path:
    heeft_bg = bool(bg and Path(bg).exists())

    cmd = ["ffmpeg", "-y", "-v", "error"]
    if heeft_bg:
        cmd += ["-loop", "1", "-i", str(bg)]
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c=#0a1628:size={W}x{H}:rate={FPS}"]
    cmd += ["-i", str(audio)]
    for p in overlays:
        cmd += ["-loop", "1", "-i", str(p)]
    cmd += ["-loop", "1", "-i", str(kop)]
    cmd += ["-loop", "1", "-i", str(balk)]

    cmd += [
        "-filter_complex", bouw_filtergraph(ayahs, totale_duur, heeft_bg, len(overlays)),
        "-map", "[outv]", "-map", "1:a",
        "-t", f"{totale_duur:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-movflags", "+faststart",
        str(uit),
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg faalde:\n{r.stderr[-2500:]}")
    return uit


# ══════════════════════════════════════════════════════════════════════════
#  YOUTUBE
# ══════════════════════════════════════════════════════════════════════════

class QuotaOp(Exception):
    pass


def upload(video: Path, titel: str, beschrijving: str, tags: list[str],
           privacy: str = "unlisted") -> str | None:
    """
    Resumable upload met echte hervatting bij een onderbreking.

    privacy staat bewust op 'unlisted'. Zet pas op 'public' als je een week
    output met eigen ogen hebt gecontroleerd.
    """
    cid = os.environ.get("YT_CLIENT_ID", "")
    csec = os.environ.get("YT_CLIENT_SECRET", "")
    rtok = os.environ.get("YT_REFRESH_TOKEN", "")
    if not all([cid, csec, rtok]):
        print("    geen YT-secrets gezet, upload overgeslagen")
        return None

    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": csec,
        "refresh_token": rtok, "grant_type": "refresh_token",
    }, timeout=30)
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"OAuth-token ophalen faalde: {r.text[:300]}")

    meta = {
        "snippet": {"title": titel[:100], "description": beschrijving[:4900],
                    "tags": tags, "categoryId": "22"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    grootte = video.stat().st_size

    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(grootte)},
        json=meta, timeout=60,
    )
    if init.status_code != 200:
        if init.status_code == 403 and "quota" in init.text.lower():
            raise QuotaOp(init.text[:300])
        raise RuntimeError(f"upload-init faalde ({init.status_code}): {init.text[:300]}")

    sessie = init.headers["Location"]
    CHUNK = 8 * 1024 * 1024
    offset = 0

    with open(video, "rb") as f:
        while offset < grootte:
            f.seek(offset)
            blok = f.read(CHUNK)
            eind = offset + len(blok) - 1
            r = requests.put(
                sessie,
                headers={"Content-Length": str(len(blok)),
                         "Content-Range": f"bytes {offset}-{eind}/{grootte}"},
                data=blok, timeout=300,
            )
            if r.status_code in (200, 201):
                vid = r.json().get("id")
                print(f"    geupload: https://youtu.be/{vid}  ({privacy})")
                return vid
            if r.status_code == 308:
                # Server vertelt hoeveel hij écht heeft ontvangen.
                bereik = r.headers.get("Range")
                offset = int(bereik.split("-")[1]) + 1 if bereik else offset + len(blok)
                continue
            if r.status_code == 403 and "quota" in r.text.lower():
                raise QuotaOp(r.text[:300])
            raise RuntimeError(f"upload faalde ({r.status_code}): {r.text[:300]}")

    return None


# ══════════════════════════════════════════════════════════════════════════
#  VOORTGANG
# ══════════════════════════════════════════════════════════════════════════

def laad_voortgang() -> dict:
    if VOORTGANG.exists():
        return json.loads(VOORTGANG.read_text())
    return {"idx": 0, "totaal": 0, "gedaan": []}


def sla_voortgang(data: dict) -> None:
    VOORTGANG.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def zoek_achtergrond() -> Path | None:
    if not ACHTERGRONDEN.exists():
        return None
    kandidaten = [p for p in ACHTERGRONDEN.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    return random.choice(kandidaten) if kandidaten else None


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

DEMO_AYAHS = [
    {"abs": 6194, "nr": 5, "audio_url": "", "duur": 4.2,
     "ar": "فَإِنَّ مَعَ ٱلْعُسْرِ يُسْرًا",
     "en": "So, surely with hardship comes ease.",
     "nl": "Voorwaar, met de moeilijkheid komt gemak."},
    {"abs": 6195, "nr": 6, "audio_url": "", "duur": 3.6,
     "ar": "إِنَّ مَعَ ٱلْعُسْرِ يُسْرًا",
     "en": "Surely with that hardship comes more ease.",
     "nl": "Voorwaar, met de moeilijkheid komt gemak."},
]


def verwerk_een(seg, rec, fonts, i, dry_run) -> dict | None:
    surah, start, eind = seg
    print(f"\n[{i}] {rec['naam']} | Surah {surah}:{start}-{eind}")

    print("  data ophalen...")
    ayahs = haal_ayah_data(surah, start, eind, rec["edition"])
    if not ayahs:
        print("  geen ayat gevonden")
        return None

    print(f"  {len(ayahs)} ayahs downloaden...")
    audio = WERKMAP / f"audio_{i}.mp3"
    if not download_audio(ayahs, rec["edition"], audio):
        print("  audio mislukt")
        return None

    totale_duur = kort_in_op_maxsec(ayahs)
    print(f"  totale duur: {totale_duur:.1f}s over {len(ayahs)} ayahs")

    return render_en_bouw(ayahs, audio, surah, rec, fonts, i, totale_duur)


def render_en_bouw(ayahs, audio, surah, rec, fonts, i, totale_duur) -> dict:
    print("  tekstlagen renderen...")
    overlays = [render_overlay(ay, fonts, WERKMAP / f"ov_{i}_{n:02d}.png")
                for n, ay in enumerate(ayahs)]
    kop = render_kop(surah, rec["naam"], fonts, WERKMAP / f"kop_{i}.png")
    balk = maak_voortgangsbalk(WERKMAP / f"balk_{i}.png")

    print("  video renderen...")
    video = OUTPUT / f"s{surah}_{ayahs[0]['nr']}-{ayahs[-1]['nr']}_{i}.mp4"
    maak_video(audio, ayahs, overlays, kop, balk,
               zoek_achtergrond(), totale_duur, video)
    print(f"  klaar: {video.name}  ({video.stat().st_size // 1024}KB)")

    for p in overlays + [kop, balk]:
        p.unlink(missing_ok=True)

    return {"video": video, "surah": surah, "ayahs": ayahs, "reciteur": rec["naam"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=VIDEOS_PER_DAG)
    ap.add_argument("--dry-run", action="store_true", help="renderen, niet uploaden")
    ap.add_argument("--demo", action="store_true",
                    help="offline testrender met vaste tekst, geen netwerk")
    ap.add_argument("--public", action="store_true",
                    help="upload als public i.p.v. unlisted")
    args = ap.parse_args()

    print(f"\n{'=' * 56}")
    print(f"  QURAN SHORTS BOT v7   {datetime.now():%H:%M  %d-%m-%Y}")
    print(f"{'=' * 56}\n")

    WERKMAP.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fonts = preflight()

    if args.demo:
        print("\nDEMO: offline render met vaste tekst")
        ayahs = [dict(a) for a in DEMO_AYAHS]
        audio = WERKMAP / "demo.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "anullsrc=r=44100:cl=stereo", "-t", "7.8", str(audio)],
            check=True)
        render_en_bouw(ayahs, audio, 94, RECITEURS[0], fonts, 0, 7.8)
        print("\nBekijk output/ en controleer het Arabisch met eigen ogen.")
        return

    vg = laad_voortgang()
    idx, geplaatst = vg["idx"], 0

    for i in range(args.count):
        seg = SEGMENTEN[idx % len(SEGMENTEN)]
        # FIX: was RECITEURS[i % len(RECITEURS)] — met --count 1 (zoals de
        # workflow gebruikt) is i altijd 0, dus werd ALTIJD dezelfde
        # reciteur gekozen. idx loopt wel persistent door over losse
        # workflow-runs heen (bewaard in voortgang.json), dus daarmee
        # roteren de reciteurs nu ook echt.
        rec = RECITEURS[idx % len(RECITEURS)]
        sleutel = f"{rec['naam']}_{seg[0]}_{seg[1]}"
        idx += 1

        if sleutel in vg["gedaan"]:
            print(f"[{i + 1}] {sleutel} al gedaan, overslaan")
            continue

        try:
            resultaat = verwerk_een(seg, rec, fonts, i, args.dry_run)
        except Exception as e:
            # Wel opvangen zodat één kapot segment de dag niet sloopt,
            # maar de fout WEL tonen. v6 slikte alles.
            print(f"  MISLUKT: {type(e).__name__}: {e}")
            continue

        if not resultaat:
            continue

        if args.dry_run:
            print("  dry-run: niet geuploaded")
            geplaatst += 1
            continue

        nr = vg["totaal"] + 1
        titel = f"Surah {resultaat['surah']} | {resultaat['reciteur']} | Quran Short #{nr}"
        beschrijving = (
            f"Recitatie door {resultaat['reciteur']}\n"
            f"Surah {resultaat['surah']}\n\n"
            + "\n\n".join(f"{a['ar']}\n\nEN: {a['en']}\nNL: {a['nl']}"
                          for a in resultaat["ayahs"])
            + "\n\n#Quran #QuranShorts #Islam #Shorts #DailyQuran"
        )
        tags = ["Quran", "QuranShorts", "Islam", "Shorts", "DailyQuran",
                resultaat["reciteur"], f"Surah{resultaat['surah']}"]

        try:
            vid = upload(resultaat["video"], titel, beschrijving, tags,
                         privacy="public" if args.public else "unlisted")
        except QuotaOp:
            print("\n  QUOTA OP voor vandaag. Voortgang bewaard, morgen verder.")
            break

        if vid:
            vg["totaal"] += 1
            vg["gedaan"].append(sleutel)
            geplaatst += 1
            sla_voortgang(vg)
            if i < args.count - 1:
                time.sleep(30)

    vg["idx"] = idx
    sla_voortgang(vg)
    print(f"\n{'=' * 56}")
    print(f"  Klaar: {geplaatst}/{args.count} | Totaal ooit: {vg['totaal']}")
    print(f"{'=' * 56}\n")


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════
#  NOTES — wat er t.o.v. v6 is veranderd en waarom
# ══════════════════════════════════════════════════════════════════════════
#
# 1. Tekst gaat via Pillow/Raqm naar PNG i.p.v. via drawtext.
#    - textwrap.wrap() telde tekens; harakat zijn tekens zonder breedte,
#      dus regels braken willekeurig. Nu wordt in pixels gemeten.
#    - x=(w-text_w)/2 centreert de box maar lijnt regels links uit; fout
#      voor meerregelig Arabisch. Nu regel-voor-regel gecentreerd.
#    - escape_ffmpeg() verving ' door ' en mangelde je Engelse tekst.
#      Nu is escaping helemaal niet meer nodig.
#    - werkt ook als je FFmpeg-build geen libharfbuzz heeft.
#
# 2. drawbox y=h-10 -> in drawbox is h de BOXhoogte, niet de framehoogte.
#    De balk kwam bovenaan terecht. En drawbox heeft geen eval=frame, dus
#    de balk animeerde sowieso niet. Nu een overlay met x-expressie.
#
# 3. Audio wordt één keer gedownload i.p.v. twee keer (meet_duraties deed
#    dezelfde downloads nog eens over).
#
# 4. concat hercodeert nu i.p.v. -c copy: verschillende reciteurs kunnen
#    verschillende samplerates hebben.
#
# 5. zoompan: bron nu 3240x5760 i.p.v. 1350x2400 en d=1 i.p.v. totaal_frames,
#    tegen de zichtbare zoompan-trilling.
#
# 6. Bare except: weg. Fouten worden getoond, alleen op segmentniveau
#    opgevangen zodat één kapotte surah de dagrun niet sloopt.
#
# 7. input() weg (blokkeerde eeuwig op een CI-runner). Gebruik --dry-run.
#
# 8. Upload: echte chunked resumable upload met 308-afhandeling, en
#    privacy staat standaard op unlisted.
#
# 9. Ayat zonder audio worden verwijderd i.p.v. met een gegokte duur van
#    4.0s meegenomen — anders loopt alle tekst erna uit de pas.
#
# 10. (v7.1) Reciteur-selectie gebruikte i % len(RECITEURS) i.p.v.
#     idx % len(RECITEURS). Met --count 1 was i altijd 0, dus werd nooit
#     geroteerd tussen reciteurs. Zodra alle 12 SEGMENTEN met reciteur #1
#     ooit geplaatst waren, sloeg de bot stilzwijgend en voor altijd alles
#     over: geen foutmelding, workflow "succeeded", maar 0 nieuwe video's.

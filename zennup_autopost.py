"""
Zennup Auto-Poster
==================
Her calistiginda media/queue/ klasorundeki EN ESKI dosyayi alir,
Gemini ile Ingilizce bir caption uretir, Instagram + Facebook'a paylasir,
sonra dosyayi media/posted/ klasorune tasir.

Gerekli ortam degiskenleri (GitHub Secrets olarak tanimlanir):
  META_ACCESS_TOKEN  -> Facebook Sayfa erisim token'i (uzun omurlu)
  IG_USER_ID         -> Instagram Business hesap ID'si
  FB_PAGE_ID         -> Facebook Sayfa ID'si
  GEMINI_API_KEY     -> Google AI Studio'dan ucretsiz anahtar

GITHUB_REPOSITORY ve GITHUB_REF_NAME degiskenlerini GitHub Actions
otomatik saglar; lokal calistirirken elle vermeniz gerekir.
"""

import os
import sys
import time
import shutil
import urllib.parse
from pathlib import Path

import requests
import yaml

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = Path(__file__).parent
QUEUE_DIR = ROOT / "media" / "queue"
POSTED_DIR = ROOT / "media" / "posted"
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov"}


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"HATA: {name} ortam degiskeni tanimli degil. KURULUM.md'ye bakin.")
    return value


def load_config() -> dict:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def pick_next_media() -> Path | None:
    files = [p for p in QUEUE_DIR.iterdir()
             if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime)  # en eski dosya once
    return files[0]


def public_url_for(path: Path) -> str:
    """GitHub public repo'daki dosyanin raw URL'ini uretir."""
    repo = env("GITHUB_REPOSITORY")           # ornek: kullanici/zennup-autopost
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    rel = path.relative_to(ROOT).as_posix()
    rel_encoded = "/".join(urllib.parse.quote(part) for part in rel.split("/"))
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_encoded}"


def generate_caption(cfg: dict, filename: str) -> str:
    """Gemini ucretsiz API ile Ingilizce caption uretir; basarisiz olursa yedek caption doner."""
    prompt = (
        f"{cfg['brand_prompt']}\n\n"
        f"The media file being posted is named '{filename}' "
        f"(the name may hint at the dish or scene).\n"
        f"Write ONE Instagram/Facebook caption in English. Rules:\n"
        f"- 1-3 short sentences, warm and appetizing, no emojis overload (max 2 emojis)\n"
        f"- End with these hashtags: {cfg['hashtags']}\n"
        f"- Return ONLY the caption text, nothing else."
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg.get('gemini_model', 'gemini-2.0-flash')}:generateContent"
        f"?key={env('GEMINI_API_KEY')}"
    )
    try:
        r = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        r.raise_for_status()
        caption = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if caption:
            return caption
    except Exception as exc:  # API hatasinda paylasim durmasin
        print(f"UYARI: Gemini caption uretemedi ({exc}). Yedek caption kullaniliyor.")
    return cfg["fallback_caption"] + "\n\n" + cfg["hashtags"]


# ---------------------------- INSTAGRAM ----------------------------

def ig_create_container(media_url: str, caption: str, is_video: bool) -> str:
    params = {"access_token": env("META_ACCESS_TOKEN"), "caption": caption}
    if is_video:
        params.update({"media_type": "REELS", "video_url": media_url})
    else:
        params["image_url"] = media_url
    r = requests.post(f"{GRAPH}/{env('IG_USER_ID')}/media", data=params, timeout=120)
    _check(r, "Instagram container olusturma")
    return r.json()["id"]


def ig_wait_until_ready(container_id: str, timeout_sec: int = 300) -> None:
    """Video islenene kadar bekler (resimler genelde aninda hazirdir)."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code",
                    "access_token": env("META_ACCESS_TOKEN")},
            timeout=60,
        )
        _check(r, "Instagram container durumu")
        status = r.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            sys.exit("HATA: Instagram videoyu isleyemedi. Dosya formatini kontrol edin (MP4/H.264 onerilir).")
        time.sleep(10)
    sys.exit("HATA: Instagram video isleme zaman asimina ugradi.")


def ig_publish(container_id: str) -> str:
    r = requests.post(
        f"{GRAPH}/{env('IG_USER_ID')}/media_publish",
        data={"creation_id": container_id,
              "access_token": env("META_ACCESS_TOKEN")},
        timeout=120,
    )
    _check(r, "Instagram yayinlama")
    return r.json()["id"]


# ---------------------------- FACEBOOK ----------------------------

def fb_post(media_url: str, caption: str, is_video: bool) -> str:
    if is_video:
        endpoint = f"{GRAPH}/{env('FB_PAGE_ID')}/videos"
        data = {"file_url": media_url, "description": caption}
    else:
        endpoint = f"{GRAPH}/{env('FB_PAGE_ID')}/photos"
        data = {"url": media_url, "message": caption}
    data["access_token"] = env("META_ACCESS_TOKEN")
    r = requests.post(endpoint, data=data, timeout=300)
    _check(r, "Facebook paylasimi")
    return r.json().get("id", "?")


def _check(r: requests.Response, step: str) -> None:
    if r.status_code >= 400:
        sys.exit(f"HATA ({step}): {r.status_code} -> {r.text}")


# ------------------------------ MAIN -------------------------------

def main() -> None:
    cfg = load_config()
    media = pick_next_media()
    if media is None:
        print("Kuyruk bos: media/queue/ klasorunde paylasacak dosya yok. Cikiliyor.")
        return

    is_video = media.suffix.lower() in VIDEO_EXT
    media_url = public_url_for(media)
    print(f"Paylasilacak dosya: {media.name}  ({'video' if is_video else 'foto'})")
    print(f"Public URL: {media_url}")

    caption = generate_caption(cfg, media.name)
    print(f"Caption:\n{caption}\n")

    # Instagram (IG_USER_ID secret'i tanimliysa; degilse atlanir)
    if os.environ.get("IG_USER_ID", "").strip():
        container = ig_create_container(media_url, caption, is_video)
        if is_video:
            ig_wait_until_ready(container)
        ig_post_id = ig_publish(container)
        print(f"Instagram'da yayinlandi (post id: {ig_post_id})")
    else:
        print("IG_USER_ID tanimli degil -> Instagram atlandi (sadece Facebook).")

    # Facebook
    fb_post_id = fb_post(media_url, caption, is_video)
    print(f"Facebook'ta yayinlandi (post id: {fb_post_id})")

    # Dosyayi posted klasorune tasi (commit'i GitHub Actions yapar)
    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(media), str(POSTED_DIR / media.name))
    print(f"{media.name} -> media/posted/ klasorune tasindi. Bitti!")


if __name__ == "__main__":
    main()

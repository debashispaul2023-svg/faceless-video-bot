# GTA 6 Stream → YouTube Shorts Auto Clipper

Ei bot ta lomba GTA 6 stream/VOD (IShowSpeed, MrBeast ba tomar dewa onno channel) theke **30–40 second er best moment** khuje ber kore, **karaoke caption + zoom in/out** diye edit kore, ar **roj 1 ta kore Short** YouTube e schedule kore dey. Puro kaj GitHub Actions e chole, tomar PC lagbe na.

> Repo ta ekhon shudhu clipper er jonno. Purono cartoon pipeline, `main.py`, ar onno unused file gulo delete kora hoyeche.

---

## 1. Puro flow ek nojore

```
GitHub Actions (roj 08:00 UTC)
        │
        ▼
clipper.py --auto
        │
        ├─ 1. Queue check ── YouTube e already 7 din er Short scheduled thakle → kichu na kore thame
        │
        ├─ 2. clip_sources.py ── @IShowSpeed, @MrBeast er /streams tab dekhe
        │        newest, GTA keyword wala, ager-e-clip-kora-na stream ta nei
        │
        ├─ 3. clip_yt.py ── stream er transcript (YouTube caption, na thakle Gemini audio transcribe)
        │
        ├─ 4. clip_ai.py ── Gemini transcript porhe best 30–40s moment gulo pick kore
        │        (chase, crash, funny reaction, heist, "wait you can do that" etc)
        │
        ├─ 5. clip_yt.py ── shudhu oi 30–40s tuku download kore (poora stream na)
        │
        ├─ 6. clip_edit.py + clip_captions.py ── 9:16 edit, karaoke caption, zoom, hook, SFX
        │
        └─ 7. youtube_uploader.py ── private upload + scheduled publish (roj 11 PM IST, din e 1 ta)
```

---

## 2. File guide

| File | Kaj |
|---|---|
| `clipper.py` | Main entry. `process_url()` (ek URL clip kore) ar `run_auto()` (daily auto mode) |
| `clip_sources.py` | Channel theke next stream khuje ber kore, processed stream mone rakhe |
| `clip_yt.py` | yt-dlp diye caption/audio/segment download, Gemini audio transcription fallback |
| `clip_ai.py` | Gemini prompt + multi-key/multi-model fallback, best clip selection |
| `clip_edit.py` | FFmpeg edit: layout, zoom, audio mix, SFX, voiceover |
| `clip_captions.py` | Word-level karaoke caption (ASS file generate) |
| `clip_core.py` | yt-dlp options, cookies, error classification (bot-block / 429 / network), alert email |
| `youtube_uploader.py` | YouTube Data API: free slot khuje ber kora, upload + schedule |
| `requirements.txt` | Python package list |
| `config.py` | Folder path ar optional ElevenLabs setting |
| `assets/processed_streams.json` | Je stream gulo already clip hoyeche tar id list |
| `assets/sfx/` | Whoosh sound effect |
| `.github/workflows/daily_bot.yml` | Daily automation |

---

## 3. Edit style ta ki ki kore

### Karaoke caption
- Screen e ek sathe max **3 ta word**, BIG BOLD UPPERCASE, bottom e.
- Je word bola hocche shetai **yellow** hoy ar ektu "pop" (scale up → normal) kore. Baki word gulo white.
- Word timing er priority:
  1. `faster-whisper` clip er nijer audio theke (sabcheye accurate)
  2. YouTube auto-caption er per-word offset
  3. Segment ke word count diye even bhag kora (fallback, kom accurate)
- `[Music]`, `(laughs)` type bracket text caption theke bad jay.

### Zoom in / zoom out
- Smooth cosine curve: 100% → 114% → 100%, ~5 second e ek cycle, puro clip jure repeat.
- Blur layout e shudhu gameplay part zoom hoy, background fixed thake.

### Layout (`CLIP_LAYOUT`)
- `blur` (default): 16:9 gameplay puro ta majhkhane, upore-niche oi video-ei blurred version. Kono action kete jay na, caption er jonno jaiga thake.
- `crop`: fullscreen 9:16, gameplay er majhkhaner ongsho kete nei. Beshi "immersive" kintu duidik er action hariye jay.

### Onno edit
- **Hook text**: prothom ~3 second upore likhe (Gemini likhe, max 6 word, curiosity line).
- **Whoosh SFX**: clip er shuru te ar protita 2nd zoom-in e (max 5 bar).
- **Voiceover** (optional, default **off**): ElevenLabs narrator. Chalate `CLIP_VOICEOVER=1` ar `ELEVENLABS_API_KEYS` dao.
- Output: 1080×1920, 30fps, H.264 + AAC, `+faststart`.

---

## 4. Daily 1 Short ta kivabe kaj kore

Bot protidin ekta kore Short **publish** kore na; bodole ekbare onek gulo Short **scheduled upload** kore rakhe, ar YouTube nijei roj publish kore.

1. Roj cron chole. Bot dekhe tomar channel e koto din er Short already scheduled (`find_next_available_slots`).
2. Jodi `CLIP_QUEUE_DAYS` (default **7**) ba tar beshi din bhora thake → "queue full", kichu na kore bondho.
3. Na hole kom por jaiga gulo bhorar jonno notun stream nei, oto gulo clip banay (max 10), prottek ta ke porer free din er **11:00 PM IST** e schedule kore.
4. Stream ta `processed_streams.json` e jog hoy, workflow ta repo te commit kore deyar por next run e abar ei stream use hoy na.

Ei jonno 1 ta stream theke 7–10 clip banale ~1–1.5 shoptaho er content ready thake.

---

## 5. Setup (prothom bar)

### 5.1 Repo
GitHub e notun repo bano, ei folder er sob file push koro (`.github/workflows/` shoho).

### 5.2 Secrets (Settings → Secrets and variables → Actions → **Secrets**)

| Secret | Kothay pabe | Lagbe? |
|---|---|---|
| `GEMINI_API_KEY` (ba `GEMINI_API_KEYS`, comma diye onek key) | Google AI Studio | **Must** |
| `YOUTUBE_CLIENT_ID` | Google Cloud Console → OAuth client (Desktop app) | **Must** (upload er jonno) |
| `YOUTUBE_CLIENT_SECRET` | same | **Must** |
| `YOUTUBE_REFRESH_TOKEN` | OAuth consent flow e tomar channel er account diye ekbar login kore | **Must** |
| `YOUTUBE_COOKIES` | Browser theke Netscape format `cookies.txt` | Prai lage — YouTube datacenter IP block korle |
| `ELEVENLABS_API_KEYS` | ElevenLabs | Shudhu voiceover chaile |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL` | Tomar email | Optional — extraction fail korle email alert |

Notes:
- OAuth scope: `youtube.upload` ar `youtube`. Google Cloud project e **YouTube Data API v3** enable korte hobe.
- OAuth app "Testing" mode e thakle refresh token ~7 din por expire hoy. Long-term chaile app ke "In production" e nao.
- Cookies expire hoy; extraction abar fail korle notun cookies dao.
- YouTube API er upload quota ache (default 10,000 unit/din; ek upload ~1,600 unit). Tai ek run e ~6 er beshi upload korle quota sesh hote pare. Queue top-up kom kore kora (daily 1–2 clip) kintu safe.

### 5.2b Gemini backup key (ekadhik key)
- GitHub Secret `GEMINI_API_KEYS` e shob key **comma diye** (ba protita notun line e) dao: `key1,key2,key3`. `GEMINI_API_KEY` khali thakleo cholbe.
- Bot prothom key use kore; key **invalid/blocked**, **daily quota sesh**, ba onno kono error dile **automatic porer key** e jay. Log e `Switching to key ...xxxx` dekhabe (shudhu shesh 4 character, puro key kokhono print hoy na).
- Key kokhono code, `config.py`, issue ba chat e paste korona. Shudhu GitHub Secrets e.
- **Gurutopurno:** Gemini er free quota **Google Cloud project** onujayi, key onujayi na. Ek-i project theke 3 ta key banale quota 3 gun hoy na. Alada alada project (ba alada Google account) theke key banao, tahole-i backup asol e kaj kore.

### 5.3 Variables (optional, **Variables** tab e)

| Variable | Default | Mane |
|---|---|---|
| `CLIP_CHANNELS` | `@IShowSpeed,@MrBeast` | Kon kon channel theke stream nibe |
| `CLIP_TITLE_KEYWORDS` | `gta` | Title te ei word thakte hobe. Khali rakhle sob stream nibe |
| `CLIP_LAYOUT` | `blur` | `blur` ba `crop` |

---

## 6. Kivabe chalabe

### Test (prothom e eta koro)
Actions → **Clip & Schedule Pipeline** → Run workflow:
- `mode` = `auto`
- `upload` = **off** ✅ (tahole YouTube e kichu jabe na)
- `max_clips` = `2`

Run sesh hole **Artifacts → clip-output** theke mp4 download kore dekho edit ta moto hoyeche kina.

### Production
Upload on rekhe ekbar manually chalao, tarpor cron nijei roj chalabe.

### Specific URL clip korte
`mode` = `clip`, `video_url` e stream link dao. (Mode ekhon shudhu duto: `auto` ar `clip`.)

### Local PC te
```bash
sudo apt install ffmpeg fonts-dejavu-core      # ba Windows/Mac e ffmpeg install
pip install -r requirements.txt
export GEMINI_API_KEY=xxxx

python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID"          # shudhu clip, upload na
python clipper.py "https://www.youtube.com/watch?v=VIDEO_ID" --upload  # clip + schedule
python clipper.py --auto --upload                                      # channel theke auto
```
Output: `output/clips/<video_id>/short_N.mp4` (sathe `meta_N.json`, `short_N.ass`).

Yt-dlp er jonno **Deno** lagte pare (JS challenge). Actions e workflow nijei install kore.

---

## 7. Sob settings (environment variables)

| Variable | Default | Kaj |
|---|---|---|
| `CLIP_CHANNELS` | `@IShowSpeed,@MrBeast` | Source channel list |
| `CLIP_TITLE_KEYWORDS` | `gta` | Title filter |
| `CLIP_MIN_MINUTES` | `20` | Er cheye choto video skip |
| `CLIP_MAX_AGE_VIDEOS` | `15` | Protita channel er koto ta recent video dekhbe |
| `CLIP_QUEUE_DAYS` | `7` | Koto din er Short age thekei scheduled rakhbe |
| `CLIP_LAYOUT` | `blur` | `blur` / `crop` |
| `CLIP_HEURISTIC_FALLBACK` | `1` | Gemini kono clip na dile transcript er hype word/energy dekhe nijei pick kore. `0` = bondho |
| `CLIP_ZOOM_AMOUNT` | `0.14` | Max zoom (0.14 = 114%) |
| `CLIP_ZOOM_PERIOD` | `5` | Ek zoom in+out cycle koto second |
| `CLIP_SFX` | `1` | `0` dile whoosh bondho |
| `CLIP_FONT` | `DejaVu Sans` | Caption font (system e install thakte hobe) |
| `CLIP_FONT_SIZE` | `92` | Caption size |
| `CLIP_WORDS_PER_CHUNK` | `3` | Ek sathe koto word |
| `CLIP_WHISPER` | `1` | `0` dile whisper bondho (fast, kintu caption timing kom accurate) |
| `CLIP_WHISPER_MODEL` | `base` | `tiny`/`base`/`small`/`medium` |
| `CLIP_LANGUAGE` | auto | Whisper language force korte (`en`, `hi`) |
| `CLIP_VOICEOVER` | `0` | `1` = ElevenLabs narrator on |
| `VOICEOVER_LANGUAGE` | `English` | Narrator er language |
| `GEMINI_MODEL` | — | Specific Gemini model force korte |
| `GEMINI_HTTP_TIMEOUT_SEC` | `180` | Gemini request timeout |
| `MAX_AUDIO_MINUTES` | `90` | Caption na thakle stream er prothom koto minute transcribe korbe |
| `CLIP_AUDIO_BUDGET_MIN` | `25` | Audio transcription step er max somoy (minute) |
| `CLIP_TIME_BUDGET_MIN` | `80` | Er por ar notun clip banabe na, jotogulo hoyeche shegulo-i thakbe |

---

## 8. Customize

- **Onno channel / onno game**: `CLIP_CHANNELS` ar `CLIP_TITLE_KEYWORDS` bodlao. Title er "GTA 6" prefix ar tags `clipper.py` er `process_url()` e hardcoded, shekhane bodlao. Gemini prompt `clip_ai.py` er `pick_best_clips()` e.
- **Upload time**: `youtube_uploader.py` e `DAILY_SLOTS = [(23, 0)]` (IST). Din e 2 ta chaile `[(14, 0), (23, 0)]`.
- **Caption color/style**: `clip_captions.py` er `HIGHLIGHT`, `WHITE`, style line.
- **SFX**: `assets/sfx/` e `whoosh.mp3` ba `impact.mp3` rakho.
- **Clip length**: `clipper.py` er `MIN_LEN`, `MAX_LEN` (ar `clip_ai.py` prompt er "30–40 seconds" line).

---

## 9. Troubleshooting

| Problem | Karon / Solution |
|---|---|
| `Sign in to confirm you're not a bot` | YouTube Actions er IP block korche. `YOUTUBE_COOKIES` secret dao/refresh koro. Bot sathe sathe thame, email alert pathay |
| `HTTP 429` | Rate limit. Kichukkhon por abar chalao |
| `No new matching stream found` | Keyword match korena ba sob stream already processed. `CLIP_TITLE_KEYWORDS` khali kore dekho; MrBeast er jonno eta likely |
| `AI selected 0 clips` | Log e er age ki ache dekho: `Gemini API failure` = key/quota/model problem; `Could not parse clips` = Gemini bhul format dilo (raw response head dekhabe); `Dropped N ... outside the transcript range` = bhul timestamp; `flagged as spoilers` = sob spoiler mark korechilo (ekhon enough clip na thakle keep kore). Ekhon bot 2 bar try kore, tarpor heuristic fallback e pick kore, tai 0 hoyar kotha na |
| Caption timing khanik off | Whisper chole ni (install/model download fail). Log e `faster-whisper unavailable` dekho |
| Caption e font ta bhinno | Runner e font nai. `fonts-dejavu-core` install ache kina dekho, ba `CLIP_FONT` bodlao |
| Anek ghonta chole kintu result nai | Prai sobsomoy stream e caption nai, tai bot audio transcribe korche. Log e `No captions - trying audio transcription` ar `chunk N/M` line dekho. Ekhon 25 min er budget ache; tobuo dhimi hole `MAX_AUDIO_MINUTES` kom dao ba choto stream e kaj koro |
| Upload fail kintu clip ache | Clip `output/clips/...` e preserved + artifact e paba. OAuth/quota check koro |
| Upload er por video private dekhay | Normal. Scheduled video publish time porjonto private thake |
| Job red hoy "No clips produced" | Segment download fail ba AI 0 clip. Log er upor er `[!]` line gulo dekho |

---

## 10. Limitation / sotorkota

- **Copyright**: Onner stream clip kore nijer channel e upload korle Content ID claim, "reused content" demonetization ba strike hote pare. Description e credit deya thake kintu eta legal protection na. Safe thakte creator er permission nao, ba clip e nijer commentary/transformation jog koro.
- Gemini transcript ar clip selection 100% perfect na; kichu clip "meh" hote pare. Shuru te schedule queue ta ekbar dekhe bad dewa jay (YouTube Studio te private/scheduled video delete/edit kora jay).
- Story spoiler: AI ke spoiler moment skip korte bola ache, kintu guarantee na.
- Tool ta YouTube er public stream er jonno baniye; private/members-only video kaj korbe na.
- Caption na thaka lomba stream (6+ ghonta) e shudhu prothom `MAX_AUDIO_MINUTES` (default 90) transcribe hoy, tai shesh er moment miss hote pare. Caption thakle puro stream fast process hoy.
- Bot er time budget ache (clip step 80 min, pipeline step 110 min); budget sesh hole jotogulo clip ready, shegulo-i upload hoy.

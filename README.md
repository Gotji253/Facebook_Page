# Facebook Page Football Poster

สคริปต์ Python แบบ standalone สำหรับดึงข่าวฟุตบอลจาก RSS ของ BBC Sport, ESPN และ Goal.com คัดเลือกข่าวใหม่ด้วย OpenAI `gpt-5-mini` สร้างภาพขนาด 1200×630 พร้อม hook ภาษาไทยด้วย Pillow แล้วโพสต์เป็น **รูปภาพพร้อมแคปชั่น** ไปยัง Facebook Page ผ่าน Graph API โดยเลือกโพสต์ไม่เกินหนึ่งข่าวต่อรอบและเก็บ `state.json` เพื่อป้องกันข่าวซ้ำ

> การโพสต์จริงต้องใช้ Page Access Token ที่มีสิทธิ์เหมาะสมกับเพจ และควรทดสอบด้วย `--dry-run` ก่อนเสมอ

## การติดตั้ง

```bash
git clone https://github.com/Gotji253/Facebook_Page.git
cd Facebook_Page
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

โหลดค่าจาก `.env` ก่อนรันด้วย `set -a; . ./.env; set +a` โดยไม่ commit ไฟล์นี้

## Environment variables

| ตัวแปร | จำเป็น | รายละเอียด |
|---|---:|---|
| `OPENAI_API_KEY` | ใช่ | API key สำหรับเรียกโมเดล |
| `OPENAI_MODEL` | ไม่ | ค่าเริ่มต้น `gpt-5-mini` |
| `FB_PAGE_ID` | ใช่เมื่อโพสต์จริง | Page ID ของเพจ “รอบรู้ : Insight” |
| `FB_PAGE_TOKEN` | ใช่เมื่อโพสต์จริง | Page Access Token |
| `FONT_PATH` | ใช่ | absolute path ไปยัง `.ttf` ที่รองรับภาษาไทย |
| `FB_API_VERSION` | ไม่ | ค่าเริ่มต้น `v23.0`; เปลี่ยนได้ตาม Graph API version ที่แอปใช้งาน |
| `STATE_FILE` | ไม่ | ค่าเริ่มต้น `state.json` |
| `OUTPUT_IMAGE` | ไม่ | ค่าเริ่มต้น `output/latest.jpg` |
| `IMAGE_PROVIDER` | ไม่ | `auto`, `rss`, `wikimedia`, `unsplash`, `reddit`, `bing` หรือ `google`; ใช้ `none` เพื่อใช้พื้นหลังแบรนด์ |
| `IMAGE_TEMPLATE` | ไม่ | `auto`, `news`, `stats` หรือ `match`; ค่าเริ่มต้น `auto` จะเลือกตามคำสำคัญของข่าว |
| `UNSPLASH_ACCESS_KEY` | เฉพาะ Unsplash | Unsplash Access Key เมื่อเลือก `IMAGE_PROVIDER=unsplash` |
| `IMAGE_MIN_WIDTH` / `IMAGE_MIN_HEIGHT` | ไม่ | ค่าเริ่มต้น `900` / `500`; ภาพเล็กกว่านี้จะถูกข้าม |

ค่าเริ่มต้นของแหล่งข่าวคือ BBC Sport, ESPN, The Guardian และ FourFourTwo โดยแก้ URL ได้ด้วย `RSS_BBC_URL`, `RSS_ESPN_URL`, `RSS_GUARDIAN_URL` และ `RSS_FOURFOURTWO_URL` หากผู้ให้บริการเปลี่ยน endpoint ส่วน Goal.com ถูกถอดจากค่าเริ่มต้นเพราะ endpoint สาธารณะเดิมตอบ 404 และจะเปิดใช้เฉพาะเมื่อกำหนด `RSS_GOAL_URL` ที่ตรวจสอบแล้วเอง

## การค้นหาภาพที่เกี่ยวข้อง

ระบบมี template ภาพ 3 แบบ ได้แก่ `news` สำหรับข่าวทั่วไป, `stats` สำหรับข่าวที่มีตัวเลข/สถิติ และ `match` สำหรับข่าวการแข่งขัน โดย `IMAGE_TEMPLATE=auto` จะเลือกแบบให้เองจากหัวข้อและสรุปข่าว ทั้งสามแบบยังใช้รูปข่าวจริงเป็นพื้นหลังและส่งออกเป็น JPEG 1200×630

ระบบจะพยายามใช้รูปภาพที่แนบมากับ RSS ของข่าวจาก BBC Sport, ESPN, The Guardian หรือ FourFourTwo ก่อน โดยจะลองขยาย URL thumbnail ของ publisher เช่น BBC จาก 240 เป็น 976 ก่อนตรวจสอบว่าเป็นรูปที่ดาวน์โหลดได้และมีขนาดผ่านเกณฑ์ จากนั้นจึงค้นหาจาก Wikimedia Commons, Unsplash, Reddit, Bing และ Google ตามลำดับ รูปจากข่าวจะใส่เครดิตกลับไปยังบทความต้นทาง และผู้ใช้ควรตรวจสอบสิทธิ์การใช้งานภาพก่อนเผยแพร่เชิงพาณิชย์

โดยค่าเริ่มต้น `IMAGE_PROVIDER=auto` สคริปต์จะลองใช้รูปจาก RSS ก่อน แล้วค้นหาตามลำดับ Wikimedia Commons, Unsplash, Reddit, Bing และ Google เฉพาะ provider ที่มีการตั้งค่า key/ข้อมูลจำเป็นแล้ว โดยจะรับเฉพาะภาพที่มีขนาดอย่างน้อย `900×500` และกรองคำที่สื่อว่าเป็น collage, montage, banner, poster, logo, screenshot หรือ thumbnail ระบบจะไม่สร้าง brand fallback และจะไม่โพสต์จนกว่าจะได้รูปข่าวจริงที่ผ่านการตรวจสอบ

เลือก provider เดียวได้ด้วย `IMAGE_PROVIDER=rss`, `wikimedia`, `unsplash`, `reddit`, `bing` หรือ `google` โดย `IMAGE_PROVIDER=none` จะไม่เหมาะกับโหมดบังคับรูปข่าวจริง และระบบจะหยุดโดยไม่โพสต์หากไม่มีรูปที่ผ่านการตรวจสอบ

สำหรับ Unsplash ให้ตั้งค่า:

```env
IMAGE_PROVIDER=unsplash
UNSPLASH_ACCESS_KEY=your_access_key
UNSPLASH_FALLBACK_QUERIES=Tottenham football|Manchester City football|football stadium|soccer match
```

ระบบจะลองคำค้นจาก headline ก่อน หากไม่พบภาพที่ผ่านตัวกรอง จะลองคำค้นใน `UNSPLASH_FALLBACK_QUERIES` ตามลำดับ โดยใช้เครื่องหมาย `|` คั่นแต่ละคำค้น

สำหรับ Reddit ใช้ subreddit ค่าเริ่มต้น `soccer` หรือเปลี่ยนด้วย `REDDIT_SUBREDDIT` ระบบจะเลือกเฉพาะโพสต์ประเภท image ที่ไม่ติด NSFW และมี permalink สำหรับเครดิต แต่รูปจากผู้ใช้ Reddit ยังต้องตรวจสิทธิ์ก่อนใช้

สำหรับ Bing Image Search ให้ตั้งค่า:

```env
IMAGE_PROVIDER=bing
BING_IMAGE_SEARCH_KEY=your_key
```

สำหรับ Google Custom Search ให้สร้าง Programmable Search Engine ที่เปิด image search แล้วตั้งค่า:

```env
IMAGE_PROVIDER=google
GOOGLE_CUSTOM_SEARCH_KEY=your_key
GOOGLE_CUSTOM_SEARCH_CX=your_search_engine_id
```

ผลค้นหาจาก Bing และ Google เป็นเพียงตัวชี้ไปยังรูปบนเว็บไซต์อื่น ไม่ใช่การรับประกัน license แม้ API จะกรอง `imageType`, `safeSearch` และ `rights` แล้วก็ตาม ผู้ใช้ต้องตรวจสอบ license และสิทธิ์การใช้ภาพแต่ละรายการก่อนใช้งานเชิงพาณิชย์เสมอ

## การตั้งค่า Page Access Token

สร้าง Facebook App และให้ user token มีสิทธิ์ที่จำเป็น จากนั้นเรียก `/me/accounts` พร้อม user token เพื่อดูเพจที่ผู้ใช้จัดการและอ่าน `id` กับ `access_token` ของเพจ:

```bash
curl "https://graph.facebook.com/v23.0/me/accounts?fields=id,name,access_token&access_token=USER_ACCESS_TOKEN"
```

เลือก object ที่มีชื่อ **รอบรู้ : Insight** แล้วนำ `id` ไปใส่ใน `FB_PAGE_ID` และ `access_token` ไปใส่ใน `FB_PAGE_TOKEN` ห้ามใส่ token ใน source code หรือ log และควรตรวจสอบสิทธิ์/อายุ token ใน [Meta for Developers](https://developers.facebook.com/docs/pages-api)

## ฟอนต์ภาษาไทยจาก Google Fonts

โปรเจกต์นี้รวม **Noto Sans Thai** ไว้แล้วที่ `fonts/NotoSansThai-Regular.ttf` และไฟล์ใบอนุญาตไว้ที่ `fonts/OFL.txt` โดยดาวน์โหลดจาก [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+Thai) และคลัง [Google Fonts บน GitHub](https://github.com/google/fonts/tree/main/ofl/notosansthai) ดังนั้นไม่ต้องดาวน์โหลดฟอนต์เพิ่มเมื่อใช้โค้ดใน repository นี้

ตั้งค่า path ได้ดังนี้:

```bash
export FONT_PATH="$PWD/fonts/NotoSansThai-Regular.ttf"
```

ฟอนต์เป็นสิ่งจำเป็นสำหรับการวาดภาษาไทยที่ถูกต้องบนรูป หาก path ใช้ไม่ได้ สคริปต์จะใช้ฟอนต์สำรองของ Pillow ซึ่งอาจแสดงภาษาไทยไม่ครบ

## การรัน

ทดสอบโดยไม่โพสต์และไม่แก้ state:

```bash
set -a; . ./.env; set +a
python football_poster.py --dry-run
```

โพสต์จริงหนึ่งรอบ:

```bash
python football_poster.py
```

ในแต่ละรอบ สคริปต์จะดึง RSS ทั้งหมดแบบ best-effort, ข้ามฟีดที่ล้มเหลว, ส่งข่าวที่ยังไม่อยู่ใน state ให้ AI ให้คะแนนใน **หนึ่ง API call**, เลือกข่าวคะแนนสูงสุด, ค้นหารูปจริงจาก provider chain และลองขยาย URL thumbnail ของ publisher ก่อนเรียก AI เพื่อเขียนโพสต์ หากไม่มีรูปที่ผ่านเกณฑ์ ระบบจะคืนข้อผิดพลาดและหยุดโดยไม่เรียก Facebook APIและไม่บันทึกข่าวลง state

## cron ทุก 2 ชั่วโมง

เพิ่มใน `crontab -e` โดยเปลี่ยน path ให้ตรงกับเครื่องจริง:

```cron
0 */2 * * * flock -n /tmp/facebook-page-football.lock sh -c 'cd /opt/Facebook_Page && . .venv/bin/activate && set -a && . ./.env && set +a && python football_poster.py' >> /opt/Facebook_Page/poster.log 2>&1
```

`flock` ช่วยป้องกันการรันซ้อนกันซึ่งอาจทำให้เกิดโพสต์ซ้ำ และ `state.json` ต้องอยู่บน disk ที่คงอยู่หลังรีสตาร์ท

## GitHub Actions

ไฟล์ `.github/workflows/hourly.yml` เป็นตัวอย่างสำหรับ runner รายชั่วโมง โดยต้องเพิ่ม repository secrets ชื่อ `OPENAI_API_KEY`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` และ `FONT_TTF_BASE64` ก่อนใช้งาน Workflow จะ restore และ commit `state.json` กลับเข้า repository; สำหรับ production ควรใช้ persistent storage ที่ปลอดภัยกว่าเพื่อไม่ให้ token หรือ state ผูกกับ runner ชั่วคราว

## ความเสถียรและการตรวจสอบเพิ่มเติม

ระบบจะ retry คำขอ HTTP ที่ตอบ 429 หรือ 5xx สูงสุด 3 ครั้งด้วย exponential backoff และจะใช้ canonical URL เป็นตัวระบุข่าวเพื่อลดการโพสต์ซ้ำข้ามแหล่งข่าว ผลลัพธ์จาก AI จะถูกตรวจชนิดข้อมูลและจำกัดช่วงคะแนน ความยาวข้อความ และจำนวน hashtags ก่อนนำไปใช้

GitHub Actions มี concurrency group เพื่อป้องกัน schedule กับ manual dispatch รันพร้อมกัน และมี preflight ตรวจ `OPENAI_API_KEY`, `FB_PAGE_ID` และ `FB_PAGE_TOKEN` ก่อนเริ่มดึงข่าว หากไม่พบ secret ที่จำเป็น workflow จะหยุดพร้อมข้อความแจ้งสาเหตุ

## Error handling

ฟีดที่ดาวน์โหลดไม่ได้ เช่น endpoint ที่เปลี่ยนแปลงหรือไม่พร้อมใช้งาน รูปต้นฉบับที่ใช้ไม่ได้ และข้อผิดพลาดจาก API จะถูกบันทึกใน log แทนการทำให้ขั้นตอน RSS ทั้งหมดล้มเหลว แต่หากไม่พบรูปข่าวจริงหรือไฟล์รูปภาพไม่ใช่ JPEG ขนาด 1200×630 สคริปต์จะหยุดก่อนโพสต์และคืน exit code ไม่เป็นศูนย์ จะไม่มีการใช้พื้นหลังสีน้ำเงินแทนรูปข่าว หาก AI หรือการโพสต์ล้มเหลว สคริปต์จะไม่ทำเครื่องหมายข่าวเป็นโพสต์แล้วก่อน Facebook ยืนยันสำเร็จ

## References

[1]: https://developers.facebook.com/docs/pages-api "Meta Pages API documentation"
[2]: https://fonts.google.com/noto/specimen/Noto+Sans+Thai "Google Fonts: Noto Sans Thai"

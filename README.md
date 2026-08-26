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

URL RSS แก้ได้ด้วย `RSS_BBC_URL`, `RSS_ESPN_URL` และ `RSS_GOAL_URL` หากผู้ให้บริการเปลี่ยน endpoint

## การตั้งค่า Page Access Token

สร้าง Facebook App และให้ user token มีสิทธิ์ที่จำเป็น จากนั้นเรียก `/me/accounts` พร้อม user token เพื่อดูเพจที่ผู้ใช้จัดการและอ่าน `id` กับ `access_token` ของเพจ:

```bash
curl "https://graph.facebook.com/v23.0/me/accounts?fields=id,name,access_token&access_token=USER_ACCESS_TOKEN"
```

เลือก object ที่มีชื่อ **รอบรู้ : Insight** แล้วนำ `id` ไปใส่ใน `FB_PAGE_ID` และ `access_token` ไปใส่ใน `FB_PAGE_TOKEN` ห้ามใส่ token ใน source code หรือ log และควรตรวจสอบสิทธิ์/อายุ token ใน [Meta for Developers](https://developers.facebook.com/docs/pages-api)

## ฟอนต์ภาษาไทยจาก Google Fonts

ดาวน์โหลด **Noto Sans Thai** จาก [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+Thai) แล้วแตกไฟล์ จากนั้นตั้งค่า `FONT_PATH` ไปยังไฟล์ `.ttf` เช่น:

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

ในแต่ละรอบ สคริปต์จะดึง RSS ทั้งหมดแบบ best-effort, ข้ามฟีดหรือรูปที่ล้มเหลว, ส่งข่าวที่ยังไม่อยู่ใน state ให้ AI ให้คะแนนใน **หนึ่ง API call**, เลือกข่าวคะแนนสูงสุด, เรียก AI เพื่อเขียนโพสต์, สร้างภาพ และเรียก `/{page-id}/photos` ก่อนบันทึก id ข่าวลง state เฉพาะเมื่อ Facebook ตอบสำเร็จ

## cron ทุกชั่วโมง

เพิ่มใน `crontab -e` โดยเปลี่ยน path ให้ตรงกับเครื่องจริง:

```cron
0 * * * * flock -n /tmp/facebook-page-football.lock sh -c 'cd /opt/Facebook_Page && . .venv/bin/activate && set -a && . ./.env && set +a && python football_poster.py' >> /opt/Facebook_Page/poster.log 2>&1
```

`flock` ช่วยป้องกันการรันซ้อนกันซึ่งอาจทำให้เกิดโพสต์ซ้ำ และ `state.json` ต้องอยู่บน disk ที่คงอยู่หลังรีสตาร์ท

## GitHub Actions

ไฟล์ `.github/workflows/hourly.yml` เป็นตัวอย่างสำหรับ runner รายชั่วโมง โดยต้องเพิ่ม repository secrets ชื่อ `OPENAI_API_KEY`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` และ `FONT_TTF_BASE64` ก่อนใช้งาน Workflow จะ restore และ commit `state.json` กลับเข้า repository; สำหรับ production ควรใช้ persistent storage ที่ปลอดภัยกว่าเพื่อไม่ให้ token หรือ state ผูกกับ runner ชั่วคราว

## Error handling

ฟีดที่ดาวน์โหลดไม่ได้ รูปที่ใช้ไม่ได้ และข้อผิดพลาดจาก API จะถูกบันทึกใน log แทนการทำให้ขั้นตอน RSS ทั้งหมดล้มเหลว หาก AI หรือการโพสต์ล้มเหลว สคริปต์จะคืน exit code ไม่เป็นศูนย์ และจะไม่ทำเครื่องหมายข่าวเป็นโพสต์แล้วก่อน Facebook ยืนยันสำเร็จ

## References

[1]: https://developers.facebook.com/docs/pages-api "Meta Pages API documentation"
[2]: https://fonts.google.com/noto/specimen/Noto+Sans+Thai "Google Fonts: Noto Sans Thai"

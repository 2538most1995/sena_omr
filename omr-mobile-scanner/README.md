# OMR Smart Checker - Mobile Web App

เว็บแอปตรวจข้อสอบจากมือถือสำหรับแบบฟอร์ม 1 หรือ 2 หน้า สูงสุด 100 ข้อ ตามภาพตัวอย่างที่ให้มา

## ความสามารถ
- ใช้กล้องหลังของมือถือผ่านเว็บ หรือเลือกรูปจากเครื่อง
- ปรับ perspective ของกระดาษเป็นแม่แบบ 1600×1200 อัตโนมัติ
- ตรวจ registration marks และ timing marks เพื่อยืนยันด้าน/แนวกระดาษ พร้อม local mesh correction รายวงกลม
- ใช้ OMR แบบหลายคุณลักษณะ (ไม่ใช้ OCR อ่านคำตอบ), adaptive threshold, local illumination normalization และ per-sheet normalization
- มี Top1/Top2 confidence, ตรวจระบายซ้ำ/รอยก้ำกึ่ง และใช้ตัวจำแนกเฉพาะช่วงก้ำกึ่ง
- มี Quality Gate บล็อกภาพเบลอ แสงสะท้อน เงาหนัก ผิดด้าน หรือจับตำแหน่งไม่ครบก่อนคิดคะแนน
- ถ่ายหรือเลือกรูปเพียงครั้งเดียว ระบบวิเคราะห์ต่อทันทีและแจ้งวิธีถ่ายใหม่เฉพาะเมื่อภาพใช้ไม่ได้
- อ่านคำตอบ 1-50 (หน้า) และอ่าน 51-100 (หลัง) เฉพาะวิชาที่มีเฉลยเกินข้อ 50
- ตรวจสถานะ `ok / blank / multiple` และ confidence
- อ่านรหัสผู้เข้าสอบ 10 หลักจากด้านหน้า
- อ่านรหัสรายวิชา 7 หลักและรหัสสถานศึกษาจากด้านหน้า
- ตรวจรหัสผู้เข้าสอบ รหัสรายวิชา และรหัสสถานศึกษาให้ตรงกับการตั้งค่าก่อนคิดคะแนน
- เลือกรายวิชาจาก SDL_school และนำเข้าเฉลยจาก TXT, CSV หรือ JSON
- แสดงรหัสวิชาบนกระดาษแบบ 7 ตำแหน่งสำหรับ `สค0200035`-`สค0200038` เป็น `สค02035`-`สค02038` โดยยังใช้รหัสเต็มกับฐานข้อมูลและ API
- แก้คำตอบที่ระบบไม่มั่นใจด้วยมือ
- บันทึกเฉลยและคำนวณคะแนนผ่านฐานข้อมูล MySQL กลาง
- เก็บเฉลยแยกตามรายวิชา/ภาคเรียน และเก็บคะแนนแยกตามรายวิชา/กลุ่มเรียน
- เก็บคุณภาพภาพ จำนวนข้อที่ตรวจทาน และจำนวนคำตอบที่แก้ด้วยมือ
- กู้รหัสผู้เข้าสอบที่อ่านขาดบางหลักจากรายชื่อเรียน เมื่อพบผู้ตรงกันเพียงคนเดียว
- ค้นหากลุ่มเรียนอัตโนมัติจากรหัสผู้เข้าสอบ รายวิชา และภาคเรียน
- จับคู่รหัสผู้เข้าสอบ 10 หลักบนกระดาษกับรหัสเต็ม 20 หลักในตารางนำเข้า SDL_school โดยอัตโนมัติ
- มีแดชบอร์ดรายงานพร้อมตัวกรองรายวิชา กลุ่มเรียน สถานะ และการค้นหานักศึกษา
- ยกเลิกผลตรวจจากหน้ารายงานได้ เพื่อคืนสถานะเป็น “ยังไม่ตรวจ” แล้วตรวจใหม่
- ตรวจเลขข้อของเฉลยให้ต่อเนื่องและแจ้งข้อที่ขาดหรือซ้ำก่อนบันทึก
- บันทึกคะแนนที่ตรวจเสร็จลง MySQL และส่งต่อไป API ภายนอกได้เมื่อมี endpoint สำหรับรับคะแนน
- มีภาพ debug overlay สำหรับตรวจตำแหน่งที่อ่าน
- UI responsive และติดตั้งเป็น PWA ได้

## ฐานข้อมูล MySQL หลังบ้าน

ระบบใช้ฐาน `sena_omr` และมีตารางหลัก 3 ตาราง:

- `omr_answer_keys` เก็บเฉลย รหัสวิชาบนกระดาษ รหัสสถานศึกษา ภาคเรียน และเลขเวอร์ชัน
- `omr_scores` เก็บคะแนน คำตอบที่อ่านได้ คุณภาพภาพ จำนวนข้อที่แก้ และเวลาตรวจ
- `omr_scan_audit` เก็บ scan ID, quality gate, ปัญหาที่พบ และ pipeline audit โดยไม่เก็บภาพหรือรหัสนักศึกษา

ตั้งค่า `OMR_MYSQL_*` ใน `.env` แล้วสร้างฐานและตารางด้วยคำสั่ง:

```bash
cd omr-mobile-scanner
.venv/bin/python scripts/init-mysql.py
```

หากสร้างฐานไว้แล้วจาก Plesk หรือผู้ให้บริการ และบัญชี MySQL มีสิทธิ์เฉพาะฐานนั้น ให้ใช้:

```bash
.venv/bin/python scripts/init-mysql.py --tables-only
```

โครงสร้าง SQL อยู่ที่ `database/schema.sql` และนำเข้าจากหน้าแรกของ phpMyAdmin ได้โดยตรง
ไฟล์จะสร้างและเลือกฐาน `sena_omr` ให้อัตโนมัติ จึงไม่เกิดข้อผิดพลาด `#1046 No database selected`
เมื่อไม่ได้กำหนด `OMR_MYSQL_DATABASE` ระบบจะใช้ SQLite เฉพาะเป็น fallback สำหรับพัฒนา

## โครงสร้าง
- `backend/` FastAPI + OpenCV
- `frontend/` HTML/CSS/JS แบบไม่ต้อง build เหมาะกับการทดสอบเร็วและมือถือ

## วิธีรัน
```bash
cd omr-mobile-scanner
python3 -m venv .venv          # ใช้ Python 3.10 ขึ้นไป
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
จากนั้นเปิด `http://localhost:8000`

> กล้องผ่าน `getUserMedia` บนมือถือจริงควรเปิดผ่าน HTTPS (ยกเว้น localhost)

ไฟล์ JPEG/HEIC จากมือถือสมัยใหม่ผ่าน lens correction ของอุปกรณ์อยู่แล้ว หากใช้กล้อง
kiosk ที่ผ่านการ calibration สามารถกำหนด `OMR_LENS_COEFFICIENTS=k1,k2,p1,p2,k3`
และ `OMR_CAMERA_FOCAL_RATIO` ใน `.env` เพื่อให้ OpenCV แก้ lens distortion ก่อนหาเอกสาร

## ทดสอบบน macOS ด้วย MAMP

ชุดนี้ตั้งให้ MAMP Apache ที่ `http://localhost:8888/` เป็น reverse proxy ไปยัง
FastAPI ที่ `127.0.0.1:8000` เพื่อให้ frontend และ API อยู่ภายใต้ origin เดียวกัน

1. ตั้งค่า Apache ของ MAMP หนึ่งครั้ง แล้วเปิด MAMP/กด Start Servers:

```bash
cd /Applications/MAMP/htdocs/sena_omr/omr-mobile-scanner
./scripts/mamp-configure.sh
```

สคริปต์จะเปิดโมดูล reverse proxy, ตรวจ syntax และเก็บไฟล์สำรองไว้ก่อนแก้ไข
หากเปลี่ยน Document Root หรือการตั้งค่า MAMP ภายหลังจนไฟล์ config ถูกสร้างใหม่ ให้รันคำสั่งนี้ซ้ำ

2. เริ่ม FastAPI เบื้องหลัง:

```bash
./scripts/macos-start.sh
```

3. เปิด `http://localhost:8888/`

ตรวจสถานะหรือหยุด backend:

```bash
./scripts/macos-status.sh
./scripts/macos-stop.sh
```

Log ของ FastAPI อยู่ที่ `.run/uvicorn.log` ส่วน Apache/MySQL ใช้หน้า Log ของ MAMP

### ตั้งค่า SDL_school

บน production ให้ backend อ่านรายวิชา กลุ่มเรียน และรายชื่อผ่าน Student Data API
ของ SDL_school โดยตรง ส่วน MAMP สามารถใช้ฐานข้อมูล import batch สำหรับพัฒนาในเครื่องได้:

```bash
cp .env.example .env
# แก้ค่า SDL_MYSQL_* ให้ตรงกับ MySQL ของ MAMP
./scripts/macos-stop.sh && ./scripts/macos-start.sh
```

สคริปต์เริ่มระบบจะโหลด `.env` ให้อัตโนมัติ หน้าเว็บเรียกเฉพาะ API ภายในดังนี้
จึงไม่มีรหัสผ่านฐานข้อมูลหรือ token ถูกส่งไปที่เบราว์เซอร์:

```http
GET  /api/subjects?term=1%2F2569
GET  /api/subjects/{subject_code}/class-groups?term=1%2F2569
GET  /api/class-groups/{group_id}/students?term=1%2F2569&subject_code={subject_code}
GET  /api/subjects/{subject_code}/students/{student_code}/class-group?term=1%2F2569
GET  /api/subjects/{subject_code}/resolve-student?term=1%2F2569&observed_code={code}
GET  /api/reports/students?term=1%2F2569&subject_code={subject_code}
GET  /api/answer-keys?term=1%2F2569
PUT  /api/answer-keys/{subject_code}
DELETE /api/answer-keys/{subject_code}?term=1%2F2569
POST /api/preflight?side=front|back
POST /api/scores
```

`POST /api/scores` จะบันทึกหรืออัปเดตผลในตาราง `omr_scores` เสมอ
ถ้ามี API ภายนอกที่เขียนคะแนนได้จริง จึงค่อยกำหนด `SDL_SCHOOL_BASE_URL`,
`SDL_SCHOOL_TOKEN` และ `SDL_SCHOOL_SCORES_PATH` เพื่อส่งต่อ โดย path รองรับ
ตัวแปร `{subject_code}` และ `{group_id}` ตามตัวอย่างใน `.env.example`

> การเปิดผ่าน `localhost` บน Mac ใช้กล้องได้ แต่หากทดสอบจากโทรศัพท์ผ่าน IP ของ
> Mac จะต้องตั้ง HTTPS เพิ่ม เพราะเบราว์เซอร์มือถือไม่อนุญาตกล้องบน HTTP ทั่วไป

## การใช้งาน
1. หน้า “ตั้งค่าการตรวจ” ดึงรายวิชาทั้งหมดจาก SDL_school
2. เลือกวิชา ตรวจ/กรอกรหัสสถานศึกษา แล้ววางหรือนำเข้าไฟล์เฉลย
3. กด “บันทึกการตั้งค่า” ระบบจะเก็บเฉลยแยกตามรหัสวิชา แล้วไปหน้า “สแกนกระดาษ”
4. เปิดกล้อง/เลือกรูปด้านหน้า แล้วกด “วิเคราะห์กระดาษคำตอบ”
5. ระบบจะค้นหากลุ่มเรียนจากรหัสผู้เข้าสอบโดยอัตโนมัติ พร้อมตรวจรายวิชาและสถานศึกษา หากไม่ตรงจะไม่คิดคะแนนและไม่ให้บันทึก
6. เมื่อรหัสตรงกัน วิชาที่มีเฉลยไม่เกินข้อ 50 สามารถตรวจคะแนนและบันทึกได้ทันที ส่วนวิชาที่มีเฉลยเกินข้อ 50 ให้สแกนด้านหลังต่อ แล้วตรวจข้อสีส้ม/ว่างและแก้ไขหากจำเป็น
7. กดบันทึกผลการตรวจ ระบบจะเก็บผลในฐานข้อมูลภายในก่อน และส่งต่อไป API ภายนอกเมื่อกำหนด endpoint ที่เขียนคะแนนได้
8. หน้า “รายงานการตรวจ” แสดงจำนวนทั้งหมด ตรวจแล้ว ยังไม่ตรวจ และกรองตามรายวิชา กลุ่มเรียน สถานะ หรือค้นหานักศึกษาได้

## ติดตั้งบน Plesk ที่ path `/sena_omr`

ไฟล์ `sena-omr-plesk-deploy.zip` เป็นชุดติดตั้งพร้อมใช้และมี Linux runtime
bootstrap ที่ตรวจ checksum แล้ว จึงติดตั้งได้แม้ jailed shell จะไม่มี Python หรือ `uname`
แตกไฟล์ไว้ที่ `httpdocs/sena_omr` แล้วรัน (ไม่ต้องใช้ root):

```bash
bash httpdocs/sena_omr/omr-mobile-scanner/scripts/plesk-bootstrap.sh
bash httpdocs/sena_omr/omr-mobile-scanner/scripts/plesk-start.sh
```

นำไฟล์ `.htaccess` ที่อยู่เหนือโฟลเดอร์แอปขึ้นไว้ที่ `httpdocs/sena_omr/.htaccess`
ไฟล์นี้จะแสดง frontend ที่ `/sena_omr/` และ proxy `/sena_omr/api/*` ไปยัง
FastAPI ที่ `127.0.0.1:18080` ควรสร้าง Scheduled Task ให้เรียก `plesk-start.sh`
ทุกนาทีเพื่อเริ่ม API ใหม่อัตโนมัติหากโปรเซสถูกรีสตาร์ต

เมื่อต้องสร้างแพ็กเกจใหม่หลังแก้โค้ด ให้รัน `./scripts/build-plesk-package.sh`
จากเครื่องพัฒนา สคริปต์จะรวมเฉพาะไฟล์ production และป้องกันไฟล์ภายใน เช่น
`.env`, ฐานคะแนน, log, source code และไฟล์ deploy ไม่ให้ดาวน์โหลดผ่านเว็บโดยตรง

บน production ให้ใช้ `.env.production.example` เป็น `.env` ระบบจะอ่านค่าฐานข้อมูล
จาก `httpdocs/SDL_school/laravel-app/.env` โดยตรง จึงไม่ต้องคัดลอกรหัสผ่านฐานข้อมูล
มาไว้ในโฟลเดอร์ OMR และไม่ส่งค่าลับใด ๆ ไปยังเบราว์เซอร์

ระบบถือว่า `blank` (ไม่ได้ตอบ) เป็นผลการอ่านปกติ ไม่ใช่ข้อผิดพลาด ตัวนับ
“ต้องตรวจทาน” จะนับเฉพาะระบายซ้ำ รอยระบายที่กำกวม หรือคำตอบที่ความเชื่อมั่นต่ำ

## ทดสอบกับภาพตัวอย่าง

ไฟล์ทดสอบจะอ่าน `IMG_4962.jpeg` และ `IMG_4963.jpeg` จากโฟลเดอร์ Downloads
เดิมโดยอัตโนมัติ หรือกำหนดตำแหน่งอื่นผ่าน `OMR_FRONT_IMAGE` และ
`OMR_BACK_IMAGE` ได้:

```bash
python -m unittest discover -s tests -v
```

## ข้อสำคัญสำหรับใช้จริงจำนวนมาก
แม่แบบใน `backend/omr.py` ถูก calibrate จากกระดาษตัวอย่างที่แนบมา หากโรงพิมพ์/รุ่นฟอร์มมีการขยับตำแหน่ง ควรสร้าง template profile เพิ่ม (เช่น `form_v1`, `form_v2`) และ calibrate ด้วยภาพกระดาษเปล่า 3-5 ใบต่อรุ่น เพื่อความแม่นยำสูงสุด

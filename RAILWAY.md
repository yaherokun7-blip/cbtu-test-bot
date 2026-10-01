# ขึ้นระบบบน Railway จาก GitHub

เวอร์ชันนี้รันบอต Discord และเว็บแอดมินร่วมกันในบริการเดียวด้วย `python serve.py` และใช้ PostgreSQL เก็บรายชื่อ รหัสยืนยัน และสถานะถาวร ปิดคอมได้หลัง deploy สำเร็จ ใช้บอตเพียงหนึ่ง instance ต่อ token และปิดบอตที่รันในเครื่องก่อนเปิดตัวจริง

## 1. อัปโหลด GitHub

นำ **ไฟล์ภายในโฟลเดอร์ discord-admin-bot** ขึ้น root ของรีโป `yaherokun7-blip/cbtu-test-bot` ให้เห็น `Dockerfile`, `requirements.txt` และ `railway.json` ทันทีในหน้าแรก ถ้าอัปโหลดทั้งโฟลเดอร์ ให้ตั้ง Root Directory ใน Railway เป็น `/discord-admin-bot` และ Config File Path เป็น `/discord-admin-bot/railway.json`

ห้ามอัปโหลด `.env`, `.venv`, `data`, รายชื่อจริง หรือรหัสยืนยันผู้เรียน การอัปโหลดผ่านเว็บ GitHub ไม่ได้ใช้กฎ `.gitignore` กรองไฟล์ให้ ใช้เฉพาะไฟล์จากชุดอัปโหลดที่เตรียมไว้ หรือใช้ Git ที่ตรวจรายการไฟล์ก่อน push แล้ว

โฟลเดอร์ `cloud/` เป็นทางเลือก Cloudflare แยกต่างหาก ไม่ต้องใช้กับ Railway และไม่ต้องตั้ง Discord Interactions Endpoint URL สำหรับเวอร์ชัน Railway นี้

## 2. สร้างบริการ

1. Railway → New Project → Deploy from GitHub repo → เลือก `cbtu-test-bot`
2. เพิ่ม PostgreSQL ในโปรเจกต์เดียวกัน (ฐานข้อมูลนี้ใช้เครดิตด้วย)
3. ในบริการแอป → Networking → Generate Domain แล้วเก็บ URL HTTPS ที่ได้
4. ตั้ง Variables ตามตารางด้านล่าง แล้ว Redeploy การ deploy ครั้งแรกอาจล้มเหลวจนกว่าจะตั้งค่าครบ
5. ใช้ replica เดียว และปิด Serverless / App Sleeping สำหรับบอตที่ต้องเชื่อม Discord ตลอดเวลา

| Variable | ค่า |
| --- | --- |
| `APP_ENV` | `production` |
| `VERIFICATION_MODE` | `classes` |
| `AUTHORIZED_USER_IDS` | `474113669295505409` |
| `PUBLIC_URL` | URL HTTPS ของบริการ ไม่มี `/` ท้าย |
| `DATABASE_URL` | Add Reference → PostgreSQL → DATABASE_URL (ใช้เครือข่ายภายใน) |
| `DISCORD_TOKEN` | Bot Token เดิม ตั้งใน Railway เท่านั้น |
| `DISCORD_CLIENT_ID` | Application ID ของบอต |
| `DISCORD_CLIENT_SECRET` | OAuth2 Client Secret ของแอปเดียวกัน |
| `GUILD_ID` | ID เซิร์ฟเวอร์ Discord |

Railway กำหนด `PORT` ให้เอง ไม่ต้องตั้ง start command เพิ่มเพราะมีไฟล์ config แล้ว ไม่ต้องใส่ `GEMINI_API_KEY` สำหรับอัปโหลดรายชื่อและกดปุ่มยืนยัน

## 3. ตั้ง Discord

ใน Discord Developer Portal เลือกแอปบอต → OAuth2 → Redirects เพิ่ม `https://โดเมนของคุณ/auth/callback` ให้ตรงกับ PUBLIC_URL ทุกตัวอักษร

ในหน้า Bot เปิด **Server Members Intent** และ **Message Content Intent** เพราะบอต Python นี้รองรับสมาชิกและคำสั่งแชทเดิมด้วย หากเคยตั้ง Interactions Endpoint URL ไว้สำหรับ Cloudflare ให้ล้างค่านั้นเมื่อเปลี่ยนมาใช้ Railway Gateway

เชิญบอตด้วย scopes `bot` และ `applications.commands` ให้สิทธิ์ Manage Roles รวมถึงสิทธิ์ที่จำเป็นต่อคำสั่งจัดการที่ต้องการใช้ วางยศบอตสูงกว่ายศผู้เรียน บัญชีแอดมินที่สั่งจัดการต้องมี Administrator ในเซิร์ฟเวอร์ด้วย

## 4. เริ่มใช้งาน

1. รอ `/health` ตอบ `{"status":"ok"}` ซึ่งตรวจทั้งฐานข้อมูลและบอตเชื่อมต่อ Discord
2. เปิดเว็บ → เข้าสู่ระบบ Discord ด้วยบัญชีแอดมินที่กำหนด
3. สร้างคลาส ระบุชื่อ ยศ จำนวนคน เช่น 30 และโค้ดร่วม เช่น `AI-CLASS-01` หรือใช้ `/create_code code:AI-CLASS-01 max_uses:30 role_name:ผู้เรียน-AI` (ชื่อคลาสใช้ชื่อยศ)
4. ส่งโค้ดร่วมให้สมาชิกคลาส แล้วใช้ `/setup_verify` เพื่อติดตั้งปุ่ม ผู้เรียนกรอก **ชื่อ–นามสกุลของตนเอง + โค้ดคลาส** เพื่อรับยศ ไม่ต้องอัปโหลดรายชื่อล่วงหน้า
5. ระบบเก็บชื่อที่กรอกพร้อม Discord ID คลาส สถานะ และเวลารับยศ แอดมินเปิด “ดูสมาชิก” ในหน้าเว็บเพื่อดูข้อมูลได้
6. หากมี Excel/CSV อยู่แล้ว สามารถนำเข้าเพื่อสร้างคลาสและโควต้าตามจำนวนรายชื่อได้ (ไม่บังคับ) คอลัมน์ `รหัสผู้เรียน`, `ชื่อ-นามสกุล`, `หลักสูตร`, `ยศ` รองรับ 2 MB / 5,000 แถว คลาสเดิมคงโค้ดและโควต้าเดิม รายชื่อในไฟล์ใช้เป็นข้อมูลอ้างอิงเท่านั้น ไม่จำกัดผู้ที่ใช้โค้ด

ชื่อที่กรอกต้องไม่ว่างและยาวไม่เกิน 100 ตัวอักษร ชื่อซ้ำกันได้ ระบบแยกสมาชิกด้วย Discord ID ต่อคลาส บัญชีที่รับยศแล้วกรอกซ้ำไม่ใช้โควต้าเพิ่มและไม่ทับชื่อที่บันทึกสำเร็จไว้ มอบยศไม่สำเร็จไม่ใช้ที่นั่ง ขณะรอผล Discord ระบบจองที่นั่งเพื่อกันยอดเกิน เมื่อได้รับเหตุการณ์สมาชิกออกหรือถูกถอดยศจะคืนที่ว่าง

ชื่อเป็นข้อมูลที่ผู้ใช้แจ้งเอง ผู้ที่มีโค้ดใช้รับยศได้เมื่อยังมีที่ว่าง รายชื่อและบัญชีที่บันทึกไว้ก่อนเปลี่ยนระบบยังคงอยู่ แต่จะไม่ใช้รายชื่อเดิมกีดกันการรับยศอีกต่อไป ข้อความปุ่มเก่าที่ส่งใน Discord แล้วไม่เปลี่ยนอัตโนมัติ ใช้ `/setup_verify` เพื่อส่งข้อความอธิบายแบบใหม่

เวอร์ชันนี้ใช้ตารางคลาสใหม่ ไม่ลบตารางรายบุคคลหรือ JSON เดิม และไม่ย้ายโค้ดเก่าอัตโนมัติ หากเคยตั้ง `VERIFICATION_MODE=roster` ให้เปลี่ยนเป็น `classes` และสร้างคลาส โฟลเดอร์ Cloudflare ยังเป็นเวอร์ชันเก่า ไม่ใช่แพ็กเกจคลาสชุดนี้

## เครดิตและการเก็บข้อมูล

Trial ให้เครดิต $5 ใช้ได้ถึง 30 วันหรือจนเครดิตหมด จากนั้น Free มีเครดิต $1 ต่อเดือน ข้อมูล ณ 23 กันยายน 2026; ตรวจยอดจริงที่หน้า Usage ของบัญชี ไม่มีการรับประกันว่าเพียงพอสำหรับแอปและ PostgreSQL ทั้งเดือน ไม่ต้องเปิด Hobby เพื่อทดลอง แต่บริการอาจหยุดเมื่อเครดิตหมด

ส่งออกรายชื่อและสำรอง PostgreSQL ก่อนเครดิตหมด โดยเฉพาะ Trial ซึ่ง volume อาจถูกลบ 30 วันหลังเครดิตหมด อย่าเก็บฐานข้อมูล SQLite บนดิสก์ชั่วคราวของแอป ใช้ PostgreSQL ตามตัวแปรด้านบน

เอกสาร: [Railway Trial](https://docs.railway.com/pricing/free-trial), [Healthchecks](https://docs.railway.com/deployments/healthchecks), [Config as Code](https://docs.railway.com/config-as-code/reference)

## ตรวจในเครื่อง

`python -m unittest discover -s tests -p "test_*.py"`

ชุดทดสอบใช้ฐานข้อมูลชั่วคราวและจำลอง Discord ไม่ติดต่อบอตจริง ส่วน Docker build และ PostgreSQL บน Railway ต้องตรวจอีกครั้งเมื่อ deploy บัญชีจริง

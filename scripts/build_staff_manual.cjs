/* Build the staff manual from the same role content used by the website.
 * Requires Python, Playwright and a browser. No production database is accessed.
 * Optional env: STAFF_GUIDE_ARTIFACT_PYTHON, STAFF_GUIDE_ARTIFACT_NODE_MODULES.
 */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '..');
const python = process.env.STAFF_GUIDE_ARTIFACT_PYTHON || 'python';
const packages = process.env.STAFF_GUIDE_ARTIFACT_NODE_MODULES;
const { chromium } = require(require.resolve('playwright', { paths: [packages, path.join(root, '../patient-portal/node_modules')].filter(Boolean) }));
const guides = JSON.parse(execFileSync(python, ['-c', "import json,runpy; print(json.dumps(runpy.run_path('accounts/role_guides.py')['ROLE_GUIDES'],ensure_ascii=False))"], {
  cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
}));
const output = path.join(root, 'output/pdf');
const temporary = path.join(root, '../tmp/pdfs/staff-manual');
const stem = 'รายงานคู่มือการใช้งานระบบและฟีเจอร์ที่เพิ่ม';
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(temporary, { recursive: true });
const escape = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const lines = [];
const paragraph = text => { lines.push(text, ''); return `<p>${escape(text)}</p>`; };
const heading = (text, level = 2) => { lines.push(text, '='.repeat(Math.min(text.length, 65)), ''); return `<h${level}>${escape(text)}</h${level}>`; };
const list = items => { lines.push(...items.map((t, i) => `${i + 1}. ${t}`), ''); return '<ol>' + items.map(t => `<li>${escape(t)}</li>`).join('') + '</ol>'; };
const page = content => `<section class="chapter">${content}</section>`;
const roles = ['STAFF', 'NURSE_ASSISTANT', 'NURSE', 'QUEUE_OPERATOR', 'DOCTOR', 'EMERGENCY', 'BIOMEDICAL', 'PHARMACIST', 'CASHIER', 'ADMIN'];
const menus = {
  STAFF: 'ลงทะเบียน และ ผู้ป่วย', NURSE_ASSISTANT: 'วัดค่า', NURSE: 'คัดกรอง ติดตาม และ ผู้ป่วยของฉัน',
  QUEUE_OPERATOR: 'คิว และ ปิด Visit', DOCTOR: 'ห้องตรวจ', EMERGENCY: 'ฉุกเฉิน',
  BIOMEDICAL: 'อุปกรณ์', PHARMACIST: 'ห้องยา', CASHIER: 'การเงิน', ADMIN: 'ภาพรวม บุคลากร เวร Admin และ รายงาน',
};
const newFeatures = [
  'ป๊อปอัปคู่มือหลังเข้าสู่ระบบ แสดงเนื้อหาตามหน้าที่ของบัญชี ครบ 9 ตำแหน่งและผู้ดูแลระบบสูงสุด ผู้ใช้เลือกอ่านหรือข้ามได้ก่อนเริ่มงาน',
  'คู่มือทีละหัวข้อ มีสารบัญ ปุ่มหัวข้อก่อนหน้าและหัวข้อถัดไป ตัวบอกความคืบหน้า และปุ่มอ่านครบแล้วเพื่อปิดหน้าต่าง',
  'เนื้อหาอธิบายวิธีเข้าหน้างาน ปุ่มที่ต้องใช้ ความหมายสถานะ จุดส่งต่อ และข้อควรตรวจ ก่อนเปลี่ยนสถานะผู้ป่วย',
  'ลิงก์เปิดหน้าที่เกี่ยวข้องจากคู่มือ ใช้ชื่อเส้นทางของ Django และเลือกให้ตรงกับสิทธิ์ของตำแหน่ง ลิงก์ผู้ป่วยของฉันของพยาบาลเปิดแท็บผู้ป่วยโดยตรง',
  'เมนูคู่มือการใช้งานในแถบซ้าย เปิดอ่านซ้ำได้เสมอ แม้ผู้ใช้จะเคยกดข้ามหรือเลือกไม่แสดงวันนี้',
  'ตัวเลือกไม่แสดงอีกในวันนี้ จำแยกบัญชีและบทบาทในเบราว์เซอร์เดียวกันจนถึงวันถัดไป ตามวันปฏิทิน Asia/Bangkok ไม่ใช่ระยะเวลา 24 ชั่วโมงนับจากที่กด',
  'ไม่รบกวนซ้ำทุกครั้งที่เปลี่ยนหน้า เมื่อเปิดคู่มือแล้วจะจำการแสดงในแท็บและการเข้าสู่ระบบครั้งเดิม หากไม่ได้ติ๊กซ่อนวันนี้ การเข้าสู่ระบบครั้งใหม่จะแสดงอีก',
  'รองรับการสวมบทบาทของผู้ดูแล คู่มือจะเปลี่ยนตามบทบาทที่กำลังทดสอบ โดยไม่เพิ่มสิทธิ์ให้ผู้ใช้ทั่วไป',
  'รองรับจอคอมและมือถือ ปิดด้วยปุ่มกากบาทหรือ Escape ได้ และแจ้งผู้ใช้เมื่อเบราว์เซอร์ไม่อนุญาตให้จำตัวเลือก',
];
const existingFeatures = [
  'ห้องยาและการเงินมีคิวบริการแยกกัน เรียงตามเวลาที่เข้าสู่จุดบริการ และใช้หมายเลขคิวเดิมพร้อมลำดับของจุดบริการนั้น',
  'เมื่อเรียกแล้วผู้ป่วยไม่มา สามารถข้ามคิวเพื่อเรียกรายถัดไป และส่งรายที่กลับมาเข้าคิวท้ายแถว โดยไม่ลบใบสั่งยาหรือบิลเดิม',
  'จอคิวกลางแยก OPD ห้องยา และการเงิน จอสาธารณะไม่แสดงชื่อ HN หรือยอดชำระของผู้ป่วย',
  'หน้าผู้ป่วยของเวชระเบียนมีภาพรวม Visit ล่าสุด แท็บสถานะ และยอดค้าง เพื่อช่วยตรวจว่าค้างขั้นตอนใด',
  'ผู้ป่วยเดิมที่มียอดค้างสามารถเริ่ม Visit ใหม่กรณีเร่งด่วนตามสิทธิ์และเหตุผลที่กำหนด และโอนยอดค้างเข้าไปในบิลใหม่ บิลต้นทางเป็นโอนยอดแล้วเพื่อป้องกันรับชำระซ้ำ',
  'แพทย์เลือกยาจากรายการค้นหาได้ รวมยารับประทานและยาทา ห้องยาและการเงินมีหน้ารายการกับรายละเอียดผู้ป่วยในหน้างานเดียวกัน',
];
const flow = [
  'เวชระเบียนค้นหาและลงทะเบียนผู้ป่วย หรือเปิด Visit ใหม่สำหรับผู้ป่วยเดิม',
  'ผู้ช่วยพยาบาลวัดและบันทึกสัญญาณชีพ ส่งผลไปให้พยาบาลยืนยัน',
  'พยาบาลทบทวนผล AI และกฎความเสี่ยง ยืนยันระดับสุดท้าย และมอบหมายการเฝ้าระวังตามเงื่อนไข',
  'เจ้าหน้าที่จัดคิวเรียกเข้าห้องตรวจ แพทย์บันทึกผลตรวจและแผนหลังตรวจ',
  'ห้องยาดำเนินการตามใบสั่งยา การเงินตรวจสิทธิและรับชำระ แต่ละจุดมีสถานะและคิวของตน',
  'เจ้าหน้าที่จัดคิวปิด Visit เมื่อขั้นตอนก่อนหน้าครบและผู้ป่วยออกจริง ผู้ป่วยจึงกลับมาใช้บริการครั้งใหม่ได้',
];

let html = '';
html += page(
  heading('รายงานคู่มือการใช้งานระบบและฟีเจอร์ที่เพิ่ม', 1).replace('ระบบและ', 'ระบบ<br>และ') +
  paragraph('ระบบจัดการคิวผู้ป่วยนอกและติดตามสัญญาณชีพ') +
  paragraph('ฉบับวันที่ 7 ตุลาคม 2569  |  สำหรับเจ้าหน้าที่และผู้ดูแลระบบ') +
  heading('วัตถุประสงค์และขอบเขต') +
  paragraph('คู่มือนี้อธิบายการใช้งานเว็บเจ้าหน้าที่ตามหน้าที่ ตั้งแต่ลงทะเบียนจนจบการรับบริการ และสรุปฟีเจอร์คู่มือในเว็บที่เพิ่มรอบนี้ ผู้ใช้ควรอ่านส่วนร่วมแล้วไปยังตำแหน่งของตนก่อนเริ่มงานจริง') +
  paragraph('ระบบเป็นต้นแบบสำหรับ OPD และการรับช่วงฉุกเฉิน ไม่ครอบคลุมการจัดเตียงหรือการดูแลผู้ป่วยนอนโรงพยาบาล ผล AI ใช้ช่วยการประเมิน โดยพยาบาลเป็นผู้ยืนยันระดับคัดกรองและแพทย์เป็นผู้วินิจฉัย') +
  heading('ภาพรวมการส่งต่องาน') + list(flow) +
  paragraph('กรณีฉุกเฉินหรือส่ง ER จะเข้าสู่รายการรับช่วงฉุกเฉินตามเงื่อนไขของระบบ ไม่เดินเส้นทาง OPD ปกติทั้งหมด และสถานะชำระเงินกับจ่ายยาแยกกัน')
);
html += page(heading('ฟีเจอร์คู่มือที่เพิ่มในรอบนี้') +
  paragraph('เราเพิ่มคู่มือในเว็บเพื่อให้ผู้ใช้รู้ว่าหน้าที่ของตนต้องเริ่มตรงไหนและส่งต่องานอย่างไร การเปลี่ยนแปลงรอบนี้เป็นส่วนอธิบายการใช้งาน ไม่เปลี่ยนกฎคิว การคำนวณบิล หรือผลคัดกรอง') + list(newFeatures));
html += page(heading('ฟีเจอร์ของระบบที่มีอยู่ก่อนรอบคู่มือ') +
  paragraph('ส่วนต่อไปนี้เป็นฟีเจอร์ของระบบปัจจุบันที่คู่มือนำมาอธิบายวิธีใช้ ไม่ใช่ฟีเจอร์ที่สร้างเพิ่มด้วยงานป๊อปอัปคู่มือรอบนี้') + list(existingFeatures) +
  heading('ตำแหน่งและเมนูหลัก') +
  '<table><thead><tr><th>ตำแหน่ง</th><th>เมนูเริ่มต้นของงาน</th></tr></thead><tbody>' + roles.map(role => {
    const title = guides[role].title.replace(/^คู่มือ/, '');
    lines.push(`${title} : ${menus[role]}`);
    return `<tr><td>${escape(title)}</td><td>${escape(menus[role])}</td></tr>`;
  }).join('') + '</tbody></table>');
const popupSteps = [
  'เข้าสู่ระบบด้วยบัญชีของตน ตรวจชื่อและตำแหน่งในแถบเมนู ป๊อปอัปจะแสดงคู่มือของบทบาทที่ใช้งานอยู่',
  'กดอ่านคู่มือเพื่อเปิดสารบัญ เลือกหัวข้อหรือกดหัวข้อถัดไปเพื่ออ่านทีละส่วน ปุ่มเปิดหน้างานจะปิดคู่มือแล้วพาไปหน้าที่เกี่ยวข้อง',
  'หากต้องเริ่มงานทันที กดข้าม หรือปิดด้วยกากบาท/Escape ระบบจะไม่เด้งซ้ำเมื่อเปลี่ยนหน้าในแท็บและการเข้าสู่ระบบครั้งนั้น',
  'ถ้าไม่ต้องการให้เด้งอีกในวันเดียวกัน ให้ติ๊กไม่แสดงอีกในวันนี้ก่อนอ่านหรือข้าม วันถัดไปเมื่อเปิดหน้าเว็บใหม่หรือเข้าสู่ระบบอีกครั้งจะแสดงได้ตามปกติ',
  'เปิดอ่านซ้ำจากเมนูคู่มือการใช้งานด้านซ้ายได้ทุกเวลา หากต้องการกลับมาแสดงอัตโนมัติ ให้เปิดคู่มือ เอาติ๊กไม่แสดงอีกในวันนี้ออก แล้วปิดคู่มือ คู่มือจะแสดงอีกเมื่อเข้าสู่ระบบครั้งใหม่ ไม่เด้งซ้ำในครั้งเดิม',
];
const asset = path.join(root, 'docs/assets/staff-guide-welcome.png');
let image = '';
if (fs.existsSync(asset)) image = `<figure><img src="data:image/png;base64,${fs.readFileSync(asset).toString('base64')}" alt="ตัวอย่างป๊อปอัปคู่มือพยาบาลจากฐานข้อมูลทดสอบ"><figcaption>ตัวอย่างหน้าต่างคู่มือพยาบาลจากการทดสอบในเครื่อง</figcaption></figure>`;
html += page(heading('วิธีใช้คู่มือในเว็บ') + list(popupSteps) + image);
html += page(heading('การเริ่มและจบงานที่ใช้ร่วมกันทุกตำแหน่ง') + list(guides.STAFF.sections[0].steps) +
  heading('การระบุตัวผู้ป่วยและการอ่านสถานะ') + paragraph('HN คือเลขทะเบียนประจำตัวผู้ป่วย Visit คือการมารับบริการหนึ่งครั้ง คนเดิมอาจมีหลาย Visit ส่วนเลขคิวใช้ระบุตัวผู้ป่วยในคิวของการมาครั้งนั้น ก่อนบันทึกให้ตรวจชื่อ HN และ Visit ให้ตรงเสมอ') +
  paragraph('การตรวจเสร็จไม่เท่ากับชำระเงินแล้วหรือรับยาแล้ว แต่ละจุดต้องยืนยันงานของตนหลังดำเนินการจริง การปิด Visit ทำได้เมื่อเงื่อนไขก่อนหน้าครบและผู้ป่วยออกจากโรงพยาบาลจริง') +
  heading('สิทธิ์และข้อมูลส่วนบุคคล') + paragraph('เมนูที่เห็นขึ้นกับสิทธิ์ของบทบาท ส่วนผู้ป่วยที่รับผิดชอบและการแจ้งเตือนขึ้นกับการมอบหมาย คู่มือไม่ปลดล็อกสิทธิ์เพิ่ม หากไม่เห็นเมนูที่ต้องทำ ให้ผู้ดูแลตรวจบัญชีและบทบาท หากติดปัญหาการรับเคส ให้ตรวจเวรและการมอบหมายด้วย ไม่ใช้บัญชีผู้ดูแลร่วมกันเพื่อข้ามสิทธิ์ และไม่เปิดข้อมูลผู้ป่วยบนจอคิวสาธารณะ'));

for (const role of roles) {
  const guide = guides[role];
  const sections = guide.sections.slice(1);
  // Keep longer five-topic roles balanced across two pages, not a lone tail topic.
  const parts = sections.length > 4 ? [sections.slice(0, 3), sections.slice(3)] : [sections];
  for (const [index, group] of parts.entries()) {
    let body = heading(guide.title + (index ? ' ต่อ' : ''));
    if (!index) body += paragraph(guide.summary) + paragraph('เมนูหลักที่ใช้  ' + menus[role]);
    for (const section of group) {
      body += `<div class="task">${heading(section.title, 3)}${list(section.steps)}</div>`;
    }
    html += page(body);
  }
}
const issues = [
  'คู่มือไม่เด้ง: อาจเคยเปิดแล้วในแท็บ/การเข้าสู่ระบบเดิม หรือเลือกไม่แสดงวันนี้ไว้ ให้เปิดจากเมนูคู่มือการใช้งาน ถ้ายังไม่เห็นฟีเจอร์ ให้ตรวจว่ารุ่นใหม่ถูก deploy และ static files ถูกเผยแพร่แล้ว',
  'เปลี่ยนบัญชีแล้วต้องได้คู่มือของบัญชีใหม่: ออกจากระบบก่อนเข้าบัญชีอื่น คู่มือและตัวเลือกซ่อนวันนี้แยกตามบัญชีและบทบาทในเบราว์เซอร์นั้น',
  'เปลี่ยนเครื่อง เบราว์เซอร์ หรือใช้โหมดส่วนตัว: การจำตัวเลือกอาจไม่ตามไป หรือถูกล้างเมื่อปิดโหมดส่วนตัว การเลือกซ่อนวันนี้ไม่ได้เก็บในฐานข้อมูลส่วนกลาง',
  'เบราว์เซอร์แจ้งว่าจำการตั้งค่าไม่ได้: ยังอ่าน ข้าม และเปิดคู่มือได้ แต่คู่มืออาจเด้งอีกเมื่อเปิดหน้าใหม่ ตรวจการอนุญาต localStorage/sessionStorage ของเบราว์เซอร์',
  'ไม่เห็นผู้ป่วยในหน้างาน: ตรวจแท็บสถานะ ช่วงงานของหน้าจอ และ Visit ที่เลือก คนที่เสร็จแล้วอาจถูกซ่อนจากคิวรอ แต่ยังมีประวัติอยู่',
  'active แต่ buzzer ไม่ดัง: active เป็นการอนุญาตอุปกรณ์ใช้งาน ไม่ยืนยันออนไลน์หรือส่งเสียงแล้ว ต้องตรวจเวลารับข้อมูล การจับคู่ การรับ/ตอบคำสั่ง และตัวอุปกรณ์',
  'ผู้ป่วยไม่มาตามคิวห้องยา/การเงิน: ใช้ไม่มาและข้ามคิว จากนั้นกลับเข้าคิวท้ายแถวเมื่อกลับมา สถานะไม่มาไม่ใช่การยกเลิกบิลหรือยกหนี้',
];
html += page(heading('กรณีใช้งานและปัญหาที่พบบ่อย') + list(issues));
html += page(heading('ผลการตรวจสอบฟีเจอร์คู่มือ') +
  paragraph('วันที่ 7 ตุลาคม 2569 รันทดสอบอัตโนมัติฝั่ง Django 24 รายการ และ JavaScript DOM 9 รายการ ผ่านทั้งหมด 33 รายการ ผลชุดนี้ตรวจฟีเจอร์คู่มือและสิทธิ์ที่เกี่ยวข้อง ไม่ใช่ผลทดสอบทุกส่วนของระบบหรือค่าความแม่นยำของ AI') +
  list([
    'Django ตรวจว่าทุกบทบาทมีคู่มือ ลิงก์ตรงสิทธิ์ ผู้ไม่เข้าสู่ระบบไม่มีคู่มือ ตัวระบุเปลี่ยนเมื่อเข้าสู่ระบบ และผู้ดูแลสวมบทบาทแล้วได้คู่มือที่ถูกต้อง',
    'JavaScript ตรวจเปิดครั้งแรก ข้ามแล้วเปลี่ยนหน้า การซ่อนข้ามการเข้าสู่ระบบในวันเดิม การแสดงวันใหม่ การแยกบัญชี/บทบาท สารบัญ ปิดด้วย Escape ลิงก์หน้างาน และกรณี storage ถูกปิดกั้น',
    'วันที่ 6 ตุลาคม 2569 ตรวจใน Chrome ด้วยฐานข้อมูลทดสอบแยก ครบ 9 ตำแหน่งและผู้ดูแล ตรวจหน้าเข้าใช้งาน การเปิดซ้ำ การซ่อนวันนี้ จอคอมและจอมือถือ โดยไม่พบ JavaScript page error',
    'การตรวจ Django มีคำเตือนเดิมเรื่องตำแหน่ง cache แบบ relative ในสภาพแวดล้อมทดสอบ ไม่พบข้อผิดพลาดที่ทำให้ชุดทดสอบล้มเหลว',
  ]) +
  heading('รายการสำหรับตรวจรับบนระบบใช้งานจริง') + list([
    'เข้าสู่ระบบด้วยบัญชีทดสอบของแต่ละตำแหน่ง และตรวจชื่อคู่มือกับเมนูที่เห็น',
    'กดอ่าน เปลี่ยนหัวข้อ กดลิงก์หน้างาน แล้วเปิดคู่มือซ้ำจากแถบเมนู',
    'กดข้ามแล้วเปลี่ยนหน้า ต้องไม่เด้งซ้ำในแท็บ/การเข้าสู่ระบบเดิม',
    'ติ๊กไม่แสดงวันนี้ ออกจากระบบแล้วเข้าใหม่ ต้องไม่เด้ง แต่เมนูเปิดซ้ำต้องยังใช้ได้',
    'ตรวจมือถือ และตรวจวันถัดไปตามเวลาไทยเมื่อเปิดหน้าใหม่',
  ]) + paragraph('ควรทำรายการตรวจรับหลัง deploy ก่อนให้ผู้ใช้จริงเริ่มงาน และใช้บัญชี/ข้อมูลทดสอบที่อนุญาต'));
html += page(heading('รายละเอียดสำหรับผู้ดูแลและผู้พัฒนา') +
  paragraph('ฟีเจอร์คู่มือไม่เพิ่มตารางหรือ migration และไม่เปลี่ยน API ผู้ป่วย โมเดล AI หรือกฎการรับชำระ การนำขึ้นระบบใช้ขั้นตอน deploy Django และเผยแพร่ static files ตามเดิม ไม่ต้อง build เว็บผู้ป่วย Next.js สำหรับการเปลี่ยนแปลงชุดนี้') +
  heading('ตำแหน่งไฟล์ที่เกี่ยวข้อง', 3) + list([
    'accounts/role_guides.py เก็บเนื้อหาตามบทบาท เป็นแหล่งเดียวกันสำหรับคู่มือในเว็บและรายงานฉบับนี้',
    'accounts/guide_context.py และ accounts/context_processors.py เลือกคู่มือ แปลงลิงก์ และส่งข้อมูลบัญชี บทบาท วันตามเวลาไทย และตัวระบุการเข้าใช้งานไปหน้าเว็บ',
    'accounts/apps.py กำหนดตัวระบุใหม่เมื่อเข้าสู่ระบบ ตัวระบุคู่มือไม่ใช่ session key ที่ใช้ยืนยันตัวตน',
    'queues/templates/includes/main_nav.html และ staff_role_guide.html แสดงเมนูและโครงสร้างป๊อปอัป',
    'static/css/staff-role-guide.css และ static/js/staff-role-guide.js กำหนดหน้าตา การอ่าน และการจำตัวเลือกในเบราว์เซอร์',
    'accounts/test_role_guides.py และ scripts/test_staff_role_guide.mjs เป็นชุดทดสอบ ส่วน docs/staff-role-guides.md อธิบายการดูแลฟีเจอร์',
    'scripts/build_staff_manual.cjs สร้างรายงาน PDF และ TXT จากเนื้อหาคู่มือ โดยไม่เชื่อมฐานข้อมูล',
  ]) +
  heading('แหล่งตรวจสอบฟีเจอร์ที่มีอยู่ก่อน', 3) + paragraph('คิวห้องยา/การเงินและจอแยกอยู่ใน opd/workflow_views.py และ opd/templates/service_queue_display.html การตรวจสถานะผู้ป่วยและเริ่ม Visit ใหม่กรณีเร่งด่วนอยู่ใน patients/views.py การคำนวณและโอนยอดอยู่ใน opd/models.py สิทธิ์แต่ละบทบาทอยู่ใน accounts/access.py') +
  heading('ข้อควรปฏิบัติเมื่อปรับปรุงคู่มือ', 3) + paragraph('เมื่อเปลี่ยนชื่อปุ่มหรือ workflow ให้แก้เนื้อหาใน role_guides.py ตรวจลิงก์และสิทธิ์ รันทดสอบ และสร้างรายงานใหม่ ระบุวันและรุ่นที่ตรวจจริง ไม่ใช้คู่มือแทนการตรวจสถานะจากระบบหรือการตัดสินใจของเจ้าหน้าที่'));

const documentHtml = `<!doctype html><html lang="th"><head><meta charset="utf-8"><title>${stem}</title><style>
@page{size:A4;margin:18mm 18mm 20mm}*{box-sizing:border-box}body{font-family:Tahoma,"Leelawadee UI",sans-serif;font-size:11pt;line-height:1.5;color:#17242e;margin:0}h1,h2,h3{color:#000;font-weight:700;break-after:avoid;page-break-after:avoid}h1{font-size:25pt;line-height:1.45;margin:0 0 18pt}h2{font-size:19pt;line-height:1.45;margin:0 0 13pt}h3{font-size:13pt;line-height:1.5;margin:13pt 0 7pt}p{margin:0 0 9pt;orphans:3;widows:3}ol{margin:0 0 10pt;padding-left:22pt}li{margin:0 0 5pt;padding-left:3pt;orphans:3;widows:3}.chapter{break-before:page}.chapter:first-child{break-before:auto}.task{break-inside:avoid}.task h3{margin-top:11pt}table{width:100%;border-collapse:collapse;margin:12pt 0;font-size:10pt}td,th{border:1px solid #d9d9d9;padding:7pt 9pt;text-align:left;vertical-align:middle}th{background:#e9f0f4;color:#000}thead{display:table-header-group}tr{break-inside:avoid}td:first-child{width:39%}figure{margin:16pt 0;break-inside:avoid}img{display:block;width:100%;height:auto}figcaption{font-size:9pt;color:#4c5e69;margin-top:7pt}a{color:inherit}
</style></head><body>${html}</body></html>`;
const htmlPath = path.join(temporary, 'staff-manual.html');
fs.writeFileSync(htmlPath, documentHtml, 'utf8');
fs.writeFileSync(path.join(output, stem + '.txt'), '\uFEFF' + lines.join('\n'), 'utf8');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.platform === 'win32' ? { channel: 'chrome' } : {}) });
  try {
    const page = await browser.newPage();
    await page.goto(pathToFileURL(htmlPath).href);
    await page.evaluate(() => document.fonts.ready);
    await page.pdf({ path: path.join(output, stem + '.pdf'), printBackground: true, preferCSSPageSize: true, displayHeaderFooter: true, tagged: true, outline: true,
      headerTemplate: '<span></span>', footerTemplate: '<div style="font-family:Tahoma;font-size:8px;color:#536472;width:100%;text-align:center">คู่มือระบบ OPD  |  <span class="pageNumber"></span> / <span class="totalPages"></span></div>' });
    console.log('Manual PDF and TXT created in ' + output);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

# Implementation Plan: Medical UI/UX Layout & Typography Redesign

## Overview
ยกระดับหน้าจอ Frontend ของระบบ Triage และ OPD Queue ให้เหมาะสมกับลักษณะการทำงานของบุคลากรทางการแพทย์ (แพทย์และพยาบาล) โดยเน้น ความเร็วในการกวาดสายตา (Visual Scanning), ความแม่นยำในการอ่านค่าสัญญาณชีพ (Vital Signs Precision), และการลดความล้าสายตา (Cognitive Ergonomics)

## Typography & Color Decisions
1. **Body & UI Font:** `IBM Plex Sans Thai` ร่วมกับ `Sarabun`
2. **Numeric & Vitals Font:** `Inter` / `JetBrains Mono` พร้อม `tabular-nums`
3. **Medical Triage Palette:**
   - RED (ฉุกเฉินวิกฤต): `#DC2626` / Alert BG: `#3B181E`
   - YELLOW (เร่งด่วน): `#D97706` / Alert BG: `#332410`
   - GREEN (ไม่เร่งด่วน): `#059669` / Alert BG: `#0E2E20`

## Task List

### Phase 1: Foundation & Typography
- [ ] Task 1: นำเข้า Web Fonts และนิยาม CSS Variables สำหรับ Typography Scale, Tabular Numbers และ Semantic Colors
- [ ] Task 2: ปรับปรุง Base Styles ใน `app.css` ให้มี Text Hierarchy ที่ชัดเจน

### Phase 3: Medical Components
- [ ] Task 3: สร้าง Vital Sign Card Component พร้อม Indicator สำหรับค่าปกติ/ผิดปกติ
- [ ] Task 4: ปรับปรุง Triage Severity Badge & Patient Quick Summary Bar

### Phase 4: Page Layout Refactoring
- [ ] Task 5: ปรับปรุง Layout หน้า Nurse Triage Assessment ให้รองรับการทำงานรวดเร็ว
- [ ] Task 6: ปรับปรุง Layout หน้า Doctor Station (OPD Room List) แบบ Split-View
- [ ] Task 7: ปรับปรุง Monitor Queue Overview

### Checkpoint: Review & Testing
- [ ] ทดสอบความชัดเจนของตัวอักษรและตัวเลข
- [ ] ตรวจสอบ Responsive บนจอ Monitor / Tablet

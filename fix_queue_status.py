#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from queues.models import Queue

# เปลี่ยน status ทั้ง 3 Queue เป็น WAITING
queues = Queue.objects.all()
for q in queues:
    q.status = 'WAITING'
    q.priority = {'RED': 1, 'YELLOW': 2, 'GREEN': 3}.get(q.visit.final_severity, 3)
    q.save()

print(f"✅ อัปเดต {queues.count()} Queue ไปเป็น WAITING")
for q in Queue.objects.all():
    print(f"  Queue ID: {q.id}, Visit ID: {q.visit.id}, Status: {q.status}, Priority: {q.priority}")

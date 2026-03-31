#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from queues.models import Queue

# ดูลำดับ Queue ทั้งหมด
q_items = Queue.objects.select_related('visit')
print(f"TOTAL QUEUES: {q_items.count()}\n")

print("ALL QUEUES (sorted by visit__id):")
q_items2 = q_items.order_by('visit__id')
for q in q_items2:
    print(f"  Queue ID: {q.id}, Visit ID: {q.visit.id}, Status: {q.status}, Priority: {q.priority}, Patient: {q.visit.patient.first_name}")

print("\n\nWAITING ONLY:")
q_items3 = q_items.filter(status='WAITING').order_by('visit__id')
print(f"Total WAITING: {q_items3.count()}")
for q in q_items3:
    print(f"  Queue ID: {q.id}, Visit ID: {q.visit.id}, Status: {q.status}, Priority: {q.priority}, Patient: {q.visit.patient.first_name}")

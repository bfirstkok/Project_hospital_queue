# Public KTAS dataset review

This note records the public KTAS datasets checked for possible use in the
triage model.

## 1. Triage accuracy and causes of mistriage using KTAS

Source:
- Figshare article 9779267
- PLOS ONE: *Triage accuracy and causes of mistriage using the Korean Triage and Acuity Scale*

Public license:
- CC BY 4.0

Main file:
- `pone.0216972.s001.xlsx`
- 1,267 records
- 24 columns

Relevant fields:
- Group
- Sex
- Age
- Patients number per hour
- Arrival mode
- Injury
- Chief_complain
- Mental
- Pain
- NRS_pain
- SBP
- DBP
- HR
- RR
- BT
- Saturation
- KTAS_RN
- KTAS_expert
- Diagnosis in ED
- Disposition
- Error_group
- Length of stay_min
- KTAS duration_min
- mistriage

Expert KTAS distribution:
- Level 1: 26
- Level 2: 220
- Level 3: 487
- Level 4: 459
- Level 5: 75

This is the same source population and schema already used by the project's
current `ai_triage/data/triage_dataset.csv`. The project's current cleaned
label distribution exactly matches the public expert labels:

- RED: 26
- PINK: 220
- YELLOW: 487
- GREEN: 459
- WHITE: 75

Therefore this public dataset is useful for provenance/reproducibility, but it
does not add new training records.

## 2. Over-triage occurs when considering pain in KTAS

Source:
- Figshare article 8044793
- PLOS ONE: *Over-triage occurs when considering the patient's pain in Korean Triage and Acuity Scale (KTAS)*

Main file:
- `Data sharing.xlsx`
- 16,716 records
- 15 columns

KTAS distribution:
- Level 1: 167
- Level 2: 2,509
- Level 3: 8,776
- Level 4: 4,217
- Level 5: 1,047

Available fields include:
- KTAS Level
- Sex
- Age
- Mode of arrival
- Complaint category
- several downstream ED outcomes

However, this public file does **not** include the vital-sign inputs required by
the production model (SBP, DBP, HR, RR, BT, SpO2), and it does not provide the
same free-text chief complaint used by the model.

It should therefore not be appended directly to the current runtime-feature
training corpus.

## 3. Larger KTAS studies checked

Recent and large KTAS studies exist with rich vital signs, including studies
with more than 100,000 ED visits. Their patient-level datasets are generally
institutional EMR/NEDIS data and are not available as unrestricted public
downloads. Some may be requested subject to institutional/IRB review.

## Conclusion

For an open, reproducible model using the same runtime features as this
project, the 1,267-record KTAS dataset remains the most directly compatible
public dataset found.

The 16,716-record KTAS dataset is valuable as supporting evidence and for
separate reduced-feature or epidemiological analyses, but direct augmentation
would create a feature-domain mismatch.

The next meaningful improvements should therefore focus on:
1. collecting additional locally confirmed triage cases,
2. improving validation and class-imbalance handling,
3. obtaining an institutionally approved larger KTAS/NEDIS dataset if
   available,
rather than mixing incompatible public rows into the production model.

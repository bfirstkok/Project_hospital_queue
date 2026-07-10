\# AGENTS.md



\## Project Overview



This project is Project\_hospital\_queue, a hospital queue and AI-assisted triage system.



The system flow is:

1\. Patient submits registration and symptoms.

2\. Nurse records vital signs.

3\. AI predicts triage severity: RED, YELLOW, GREEN.

4\. Rule-based guardrail checks dangerous cases.

5\. Nurse confirms or overrides the AI result.

6\. Patient enters queue based on confirmed severity.



The AI is a decision-support tool, not an autonomous medical diagnosis system.



\## Main Goal



Improve the AI triage module by replacing synthetic training data with a CSV-based dataset pipeline.



The dataset should be loaded from:



ai\_triage/data/triage\_dataset.csv



The training script should:

1\. Read the CSV dataset.

2\. Clean and normalize column names.

3\. Map triage acuity labels to RED, YELLOW, GREEN.

4\. Train a DecisionTreeClassifier.

5\. Save the model to ai\_triage/models/triage\_dt\_v1.pkl.

6\. Save reports to ai\_triage/reports/.



\## Important Files



\- ai\_triage/ml/train\_dt.py

&#x20; - Current training script.

&#x20; - Should be changed from synthetic dataset generation to CSV dataset loading.



\- ai\_triage/ml/predictor.py

&#x20; - Loads the trained model and predicts severity.



\- ai\_triage/services.py

&#x20; - Combines AI prediction with rule-based guardrail.

&#x20; - Do not remove guardrail logic.



\- ai\_triage/models/triage\_dt\_v1.pkl

&#x20; - Saved trained model.



\- ai\_triage/reports/

&#x20; - Store training metrics, confusion matrix, and cleaned dataset.



\## Dataset Columns



The CSV dataset may contain different column names. Normalize them into these internal feature names:



\- rr: respiratory rate

\- pr: pulse rate or heart rate

\- sys\_bp: systolic blood pressure

\- bt: body temperature

\- o2sat: oxygen saturation

\- acuity: original triage level



Use these features for training:



rr, pr, sys\_bp, bt, o2sat



\## Label Mapping



Map original triage levels into system labels:



\- acuity 1 or 2 -> RED

\- acuity 3 -> YELLOW

\- acuity 4 or 5 -> GREEN



If the dataset already has labels RED, YELLOW, GREEN, use them directly.



\## Safety Rules



Do not make AI the final medical decision maker.



Always keep:

1\. Rule-based guardrail

2\. Nurse confirmation

3\. Override reason when nurse changes AI result



The final triage result must be confirmed by a human nurse.



\## Code Style



\- Keep code simple and readable for a university project.

\- Add comments where the logic may be hard to understand.

\- Do not over-engineer.

\- Prefer pandas, scikit-learn, and joblib.

\- Keep existing Django structure unless necessary.

\- Do not delete existing working features.



\## Commands



Use these commands when testing:



python manage.py check



python ai\_triage/ml/train\_dt.py



python manage.py runserver

